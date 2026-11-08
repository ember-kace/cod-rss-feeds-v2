"""Collect, validate a complete candidate, then promote durable state and output."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jsonschema
from codrss.events import merge_pending_state, new_state, stable_hash, update_state, validate_state
from codrss.fetch import Fetcher, SourceError
from codrss.parse import SourceParseError, canonical_url, parse_article, parse_index
from codrss.render import PRESENTATION_VERSION, render_public, validate_public


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def valid_state(state):
    schema = read_json(ROOT / "data/state.schema.json")
    jsonschema.validate(state, schema)
    validate_state(state)
    if not state.get("initialized"):
        raise ValueError("Established state is not initialized")
    for name in ("news", "patch"):
        for event_id in state["feeds"][name]["ids"]:
            if event_id not in state["events"]:
                raise ValueError(f"State missing event {event_id}")
    return state


def preserves_public_identities(state):
    known = {event.get("guid") for event in state["events"].values()}
    for feed in (ROOT / "public/feeds").glob("*.xml"):
        published = {node.text for node in ET.parse(feed).findall("./channel/item/guid")}
        if not published.issubset(known):
            raise ValueError("State would discard published event identities")
    for page in (ROOT / "public/updates").glob("*.html"):
        if page.stem not in state["events"]:
            raise ValueError("State would discard a published permanent event page")
    return state


def load_state(initialize=False):
    state_path = ROOT / "data/state.json"
    established = state_path.exists() or (ROOT / "public/manifest.json").exists()
    history = subprocess.run(["git", "log", "--format=%H", "-30", "--", "data/state.json"], cwd=ROOT, capture_output=True, text=True)
    revisions = history.stdout.splitlines() if history.returncode == 0 else []
    if initialize:
        if established or revisions:
            raise ValueError("Refusing --initialize on an established project")
        return new_state()
    try:
        return preserves_public_identities(valid_state(read_json(state_path)))
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, jsonschema.ValidationError) as error:
        for revision in revisions:
            result = subprocess.run(["git", "show", f"{revision}:data/state.json"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            if result.returncode != 0:
                continue
            try:
                state = valid_state(json.loads(result.stdout))
                # A recovery cannot discard identities already in the public feeds.
                preserves_public_identities(state)
                print(f"Recovered last valid durable state from Git {revision}", file=sys.stderr)
                return state
            except (ValueError, KeyError, TypeError, ET.ParseError, jsonschema.ValidationError):
                continue
        raise ValueError("Missing/corrupt established state; restore data/state.json from a valid Git backup. Fresh setup requires explicit --initialize.") from error


def parse_date(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except ValueError:
        return None


def sitemap_candidates(fetcher, config):
    """Bound sitemap discovery; lastmod is deliberately never used as a source date."""
    queue, result, visited = [config["sitemap_url"]], {}, set()
    for _ in range(config["max_sitemaps"]):
        if not queue:
            break
        sitemap_url = queue.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        root = ET.fromstring(fetcher.get(sitemap_url, html=False))
        for node in root:
            loc = node.find("{*}loc")
            if loc is None or not loc.text:
                continue
            try:
                url = canonical_url(loc.text.strip(), sitemap_url, config["allowed_hosts"])
            except ValueError:
                continue
            if root.tag.endswith("sitemapindex"):
                if any(tag in url.lower() for tag in ("blog", "patch", "en_", "en-", "sitemap")):
                    queue.append(url)
            elif re.search(r"^https://[^/]+/(?:blog|patchnotes)/20\d{2}/\d{2}/", url):
                # Only canonical English paths, never locale-prefixed versions.
                if "/patchnotes/" in url or any(alias in url.lower() for alias in config["product_aliases"]["mw4"]):
                    result[url] = {"url": url, "title": ""}
    # URL year/month is only a bounded fetch priority, never a publication date.
    def priority(url):
        match = re.search(r"/(20\d{2})/(\d{2})/", url)
        return (int(match[1]), int(match[2]), url)
    return [result[url] for url in sorted(result, key=priority, reverse=True)[:config["max_candidates"]]]


def collect(state, config, now, fetcher):
    fetcher.load_robots()
    candidates, counts, current_patches = {}, {}, set()
    for url in config["discovery_sources"]:
        body = fetcher.get(url)
        try:
            cards = parse_index(body, url)
        except SourceParseError:
            # The parser deliberately raises on lost card selectors. The
            # verified sitemap is still a bounded discovery fallback; if its
            # article/date parsing also fails the whole publication fails safely.
            cards = []
        counts[url] = len(cards)
        used_fallback = not cards
        if not cards:
            cards = sitemap_candidates(fetcher, config)
            if not cards:
                raise SourceError(f"No article cards or sitemap fallback: selector/source failure at {url}")
            print(f"Article card extraction changed at {url}; using bounded verified sitemap discovery", file=sys.stderr)
        previous = state.get("collection", {}).get("index_counts", {}).get(url, 0)
        if previous >= 10 and len(cards) < previous * 0.30:
            raise SourceError(f"Suspicious extraction loss at {url}: {len(cards)} vs {previous}")
        for card in cards:
            try:
                identity = canonical_url(card["url"], url, config["allowed_hosts"])
            except ValueError:
                continue
            if not identity:
                continue
            card["url"] = identity
            if not used_fallback and url.rstrip("/") == "https://www.callofduty.com/patchnotes":
                current_patches.add(identity)
            if identity not in candidates:
                candidates[identity] = card
            else:
                candidates[identity].update({k: v for k, v in card.items() if v and not candidates[identity].get(k)})
    overlap = timedelta(days=config["active_overlap_days"])
    previous_pages = state.get("collection", {}).get("patch_pages", {})
    # Keep last-indexed tombstones even after overlap expires; otherwise a stale
    # broad-blog card could repeatedly restart the observation window.
    active_pages = dict(previous_pages)
    for url, checked in previous_pages.items():
        observed = parse_date(checked)
        if observed and now - observed <= overlap:
            candidates.setdefault(url, {"url": url, "title": ""})
    cutoff = now - timedelta(days=config["initial_days"])
    articles, diagnostics = [], []
    last_catchup = parse_date(state.get("collection", {}).get("last_catchup"))
    catchup = state.get("presentation_version") != PRESENTATION_VERSION or not last_catchup or now - last_catchup >= timedelta(days=config["catchup_days"])
    fetched = 0
    for url, hint in candidates.items():
        patch_tags = {str(value).casefold() for value in hint.get("categories", [])}
        patch_hint = ("/patchnotes/" in url
                      or state.get("articles", {}).get(url, {}).get("kind") == "patch"
                      or bool(patch_tags & {"patch-notes", "patch-updates", "hotfix"})
                      or any(term in hint.get("title", "").casefold() for term in ("patch notes", "hotfix")))
        known = url in state.get("articles", {})
        dated = parse_date(hint.get("source_date") or hint.get("date"))
        if dated and dated < cutoff and url not in previous_pages and url not in current_patches:
            continue
        if patch_hint and url in previous_pages and url not in current_patches:
            last_indexed = parse_date(previous_pages[url])
            if last_indexed and now - last_indexed > overlap:
                continue
        if known and not patch_hint and not catchup:
            continue
        if fetched >= config["max_candidates"]:
            raise SourceError("Candidate budget exhausted before complete collection")
        article = parse_article(fetcher.get(url), url, hint, config)
        fetched += 1
        if article["kind"] == "irrelevant":
            continue
        if not article.get("source_date"):
            diagnostics.append({"url": url, "reason": "quarantined: no verified source date"})
            continue
        if article["kind"] == "patch":
            if url not in current_patches and url not in previous_pages:
                section_dates = [parse_date(section.get("source_date")) for section in article.get("sections", [])]
                if section_dates and all(date is not None and date < cutoff for date in section_dates):
                    continue  # A historical sitemap entry is not an active page.
            # Pages in current indexes continue observation; removed pages retain
            # their original last-indexed time so the overlap expires.
            active_pages[url] = now.isoformat() if url in current_patches or url not in previous_pages else previous_pages[url]
            if not article.get("sections"):
                raise SourceError(f"Patch parser returned no dated sections or fallback: {url}")
        articles.append(article)
    if diagnostics:
        raise SourceError("Publication candidates missing source dates: " + json.dumps(diagnostics))
    if not any(a["kind"] == "patch" for a in articles):
        raise SourceError("No current qualifying patch pages parsed")
    if not state.get("initialized") and not any(a["kind"] == "news" for a in articles):
        raise SourceError("No current qualifying MW4 news parsed")
    collection = {"index_counts": counts, "patch_pages": active_pages, "last_catchup": now.isoformat() if catchup else state["collection"]["last_catchup"]}
    return articles, collection


def refresh(initialize=False, pending_state=None, refresh_metadata=False):
    config = read_json(ROOT / "config/feeds.json")
    if not config["base_url"].startswith("https://"):
        raise ValueError("Configure authoritative HTTPS Pages base_url before generating")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = load_state(initialize)
    if pending_state:
        state = merge_pending_state(state, valid_state(read_json(pending_state)), config)
    if refresh_metadata:
        config = dict(config, catchup_days=0)
    fetcher = Fetcher(config, ROOT / ".cache/http")
    articles, collection = collect(state, config, now, fetcher)
    from codrss.images import verified_image_type
    image_types = dict(state.get("image_types", {}))
    for article in articles:
        image = article.get("image")
        if image and image not in image_types:
            verified = verified_image_type(image)
            if verified:
                image_types[image] = verified
        article["image_type"] = image_types.get(image, "")
    candidate = update_state(state, articles, now.isoformat(), config, initialize=initialize)
    candidate["image_types"] = image_types
    candidate["presentation_version"] = PRESENTATION_VERSION
    signature = stable_hash({"version": PRESENTATION_VERSION, "summary_length": config["summary_length"], "base_url": config["base_url"]})
    if state.get("presentation_signature") != signature:
        for feed in candidate["feeds"].values():
            feed["last_build"] = now.isoformat()
    candidate["presentation_signature"] = signature
    candidate["collection"] = collection
    today = now.date().isoformat()
    old_checkpoint = state.get("checkpoint", {})
    if old_checkpoint.get("day") != today:
        candidate["checkpoint"] = {"day": today, "observation_time": now.isoformat(), "last_success": now.isoformat(), "source_data_stale": False, "checked_sources": sorted(set(fetcher.checked)), "item_counts": {key: len(candidate["feeds"][key]["ids"]) for key in ("news", "patch")}}
        history = list(state.get("operational_history", []))
        history.append({"day": today, "successful_source_checks": len(set(fetcher.checked)), "item_counts": candidate["checkpoint"]["item_counts"]})
        candidate["operational_history"] = history[-30:]
    else:
        candidate["checkpoint"] = old_checkpoint
    # Collection observation times need not create a commit on every poll.
    candidate["collection"]["patch_pages"] = {url: today + "T00:00:00+00:00" for url in collection["patch_pages"] if collection["patch_pages"][url].startswith(today)} | {url: checked for url, checked in collection["patch_pages"].items() if not checked.startswith(today)}
    valid_state(candidate)
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=ROOT) as temporary:
        temp = Path(temporary)
        staged_public = temp / "public"
        render_public(candidate, config, staged_public, now.isoformat())
        result = validate_public(staged_public, config)
        staged_state = temp / "state.json"
        staged_state.write_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
        # Both feeds are validated before any last-good output changes.
        backup = temp / "previous-public"
        existing = ROOT / "public"
        if existing.exists():
            existing.rename(backup)
        try:
            staged_public.rename(existing)
            (ROOT / "data").mkdir(exist_ok=True)
            os.replace(staged_state, ROOT / "data/state.json")
        except BaseException:
            if existing.exists():
                shutil.rmtree(existing)
            if backup.exists():
                backup.rename(existing)
            raise
    print(json.dumps({"source_requests": fetcher.count, "collection": "successful", "feeds": result}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", action="store_true", help="Explicit first installation only; refuses established state")
    parser.add_argument("--pending-state", type=Path, help="Reconcile a validated unpublished ledger after a branch collision")
    parser.add_argument("--refresh-metadata", action="store_true", help="Refetch current news metadata without changing event identities or dates")
    args = parser.parse_args()
    try:
        refresh(args.initialize, args.pending_state, args.refresh_metadata)
    except Exception as exc:
        print(f"Refresh failed; last-good public output preserved: {exc}", file=sys.stderr)
        sys.exit(1)
