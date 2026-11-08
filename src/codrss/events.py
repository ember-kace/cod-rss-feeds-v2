"""Durable event identities. No network or filesystem side effects occur here."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1
PARSER_VERSION = 2


def stable_hash(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def as_datetime(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A verified source date is required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def new_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "initialized": False,
        "articles": {},
        "sections": {},
        "events": {},
        "feeds": {
            "news": {"ids": [], "fingerprint": "", "last_build": None},
            "patch": {"ids": [], "fingerprint": "", "last_build": None},
        },
        "checkpoint": {},
    }


def validate_state(state, now=None):
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported or corrupt state; restore the last valid Git state")
    for name in ("articles", "sections", "events", "feeds", "checkpoint"):
        if not isinstance(state.get(name), dict):
            raise ValueError(f"Corrupt state: missing {name}")
    clock = as_datetime(now) if now else None
    section_records = {}
    for section in state["sections"].values():
        if not isinstance(section, dict) or not section.get("page_url") or not section.get("key"):
            raise ValueError("Corrupt section identity")
        digests = section.get("digests")
        if not isinstance(digests, list) or not digests or len(set(digests)) != len(digests) or section.get("digest") not in digests:
            raise ValueError("Corrupt section digest history")
        aliases = section.get("digest_aliases", {})
        if not isinstance(aliases, dict) or any(key not in digests or value not in digests for key, value in aliases.items()):
            raise ValueError("Corrupt section digest aliases")
        for key in [section["key"], *section.get("aliases", [])]:
            identity = (section["page_url"], key)
            if identity in section_records and section_records[identity] is not section:
                raise ValueError("Ambiguous stored section identity")
            section_records[identity] = section
    for name in ("news", "patch"):
        feed = state["feeds"].get(name)
        if not isinstance(feed, dict) or not isinstance(feed.get("ids"), list):
            raise ValueError(f"Corrupt state: missing {name} feed")
        if len(set(feed["ids"])) != len(feed["ids"]):
            raise ValueError("Corrupt state: duplicate feed identity")
        for identity in feed["ids"]:
            if identity not in state["events"]:
                raise ValueError("Corrupt state: feed refers to a missing event")
            if state["events"][identity].get("kind") != name:
                raise ValueError("Corrupt state: event is in the wrong feed")
    for identity, event in state["events"].items():
        if event.get("id") != identity or event.get("kind") not in ("news", "patch"):
            raise ValueError("Corrupt event identity")
        if event.get("guid") != "urn:cod-rss:" + identity:
            raise ValueError("Corrupt event GUID")
        for field in ("event_date", "source_date", "first_observed"):
            dated = as_datetime(event[field])
            if clock and dated > clock:
                raise ValueError("Unexplained future date in durable event state")
        if event["kind"] == "news":
            expected = stable_hash(event["page_url"])
        else:
            section = section_records.get((event["page_url"], event.get("section_key")))
            if section is None or event.get("digest") not in section["digests"]:
                raise ValueError("Corrupt state: published patch event lacks its section digest history")
            expected = stable_hash([event["page_url"], event["section_key"], event["digest"]])
        if identity != expected:
            raise ValueError("Corrupt deterministic event identity")
    return state


def _source_date(value, now):
    result = as_datetime(value)
    if result > now:
        raise ValueError(f"Unexplained future source date: {value}")
    return result


def _event(article, now, section=None, revision=False, baseline=False):
    source = section or article
    source_date = source["source_date"]
    digest = source.get("digest") or stable_hash(source.get("semantic", ""))
    if section:
        identity = stable_hash([article["url"], section["key"], digest])
        title = f"{article['title']} — {section['label']}"
        if revision:
            title = f"Update detected — {title}"
        anchor = (source.get("anchor") or "").lstrip("#")
        source_url = article["url"] + ("#" + anchor if anchor else "")
        summary = source.get("summary") or article.get("summary", "")
    else:
        identity = stable_hash(article["url"])
        title = article["title"]
        source_url = article["url"]
        summary = article.get("summary", "")
    return {
        "id": identity,
        "guid": "urn:cod-rss:" + identity,
        "kind": "patch" if section else "news",
        "title": title,
        "article_title": article["title"],
        "source_url": source_url,
        "page_url": article["url"],
        "source_date": source_date,
        "precision": source.get("precision", "date"),
        "event_date": source_date if baseline or not section else now,
        "first_observed": now,
        "revision": revision,
        "baseline": baseline,
        "summary": summary,
        "image": article.get("image"),
        "image_type": article.get("image_type") or "",
        "author": article.get("author") or "Call of Duty",
        "game": article.get("game", ""),
        "categories": article.get("categories", []),
        "product_categories": article.get("product_categories", []),
        "section_key": section["key"] if section else None,
        "anchor": source.get("anchor", ""),
        "digest": digest if section else None,
    }


def _update_label_identity(label):
    """Keep update qualifiers while ignoring date and weekday presentation."""
    from .parse import DATE_RE

    value = DATE_RE.sub(" ", str(label))
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", value)
    value = re.sub(r"\b(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", " ", value, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _find_section(state, url, section, live_keys=None):
    identity = stable_hash([url, section["key"]])
    if identity in state["sections"]:
        return identity, state["sections"][identity]
    candidates = []
    for known_id, known in state["sections"].items():
        if known["page_url"] != url:
            continue
        if section["key"] in known.get("aliases", []):
            return known_id, known
        if live_keys and known["key"] != section["key"] and known["key"] in live_keys:
            # A label match must not steal a different update that is still
            # explicitly present in this same source document or pending ledger.
            continue
        if known["source_date"] != section["source_date"]:
            continue
        anchor_match = section.get("anchor") and known.get("anchor") == section["anchor"]
        label_match = _update_label_identity(known.get("label", "")) == _update_label_identity(section["label"])
        if anchor_match or label_match:
            candidates.append((known_id, known))
    if len(candidates) == 1:
        known_id, known = candidates[0]
        known.setdefault("aliases", []).append(section["key"])
        return known_id, known
    if len(candidates) > 1:
        raise ValueError("Ambiguous section migration; reconcile identities before publishing")
    return identity, None


def update_state(state, articles, now, config, initialize=False):
    """Apply one complete successful collection, preserving every published event."""
    validate_state(state, now)
    result = copy.deepcopy(state)
    clock = as_datetime(now)
    first_run = not result.get("initialized")
    if first_run and not initialize:
        raise ValueError("First installation requires explicit initialization")
    if initialize and not first_run:
        raise ValueError("Refusing to reinitialize an established identity ledger")
    result.setdefault("created_at", now)
    cutoff = clock - timedelta(days=config.get("initial_days", 30))
    added = {"news": [], "patch": []}
    seen = set()
    for article in articles:
        kind = article.get("kind")
        if kind not in ("news", "patch"):
            continue
        url = article["url"]
        if url in seen:
            continue
        seen.add(url)
        if not article.get("title") or not article.get("game"):
            raise ValueError("Selected article lacks verified title or game evidence")
        published = _source_date(article.get("source_date"), clock)
        known_article = result["articles"].get(url)
        result["articles"][url] = {
            "kind": kind,
            "title": article["title"],
            "source_date": article["source_date"],
            "precision": article.get("precision", "date"),
            "first_observed": known_article["first_observed"] if known_article else now,
            "aliases": sorted(set((known_article or {}).get("aliases", []) + article.get("aliases", []))),
        }
        if kind == "news":
            event = _event(article, now, baseline=first_run)
            identity = event["id"]
            if identity in result["events"]:
                old = result["events"][identity]
                event["event_date"] = old["event_date"]
                event["first_observed"] = old["first_observed"]
                event["baseline"] = old["baseline"]
                result["events"][identity] = event
            elif not known_article and published >= cutoff:
                result["events"][identity] = event
                added["news"].append(identity)
            continue
        # Image/byline/presentation repairs do not create new patch events. Keep
        # original revision GUIDs, semantic digests and immutable event dates.
        for event in result["events"].values():
            if event["kind"] == "patch" and event["page_url"] == url:
                for field in ("image", "author", "game", "categories"):
                    if article.get(field) is not None:
                        event[field] = copy.deepcopy(article[field])
                event["image_type"] = article.get("image_type") or ""
                event["article_title"] = article["title"]
        sections = article.get("sections", [])
        if not sections:
            semantic = article.get("semantic", "")
            if not semantic.strip():
                raise ValueError("Patch article lacks dated sections and semantic fallback body")
            sections = [{
                "key": "whole-article", "label": "Official patch notes",
                "source_date": article["source_date"], "precision": article.get("precision", "date"),
                "anchor": "", "semantic": semantic, "summary": article.get("summary", ""),
            }]
        section_ids = set()
        live_keys = {section["key"] for section in sections}
        for original_section in sections:
            section = copy.deepcopy(original_section)
            dated = _source_date(section.get("source_date"), clock)
            if not section.get("key") or not section.get("label"):
                raise ValueError("Patch section lacks stable identity and source label")
            section_id, known = _find_section(result, url, section, live_keys)
            if section_id in section_ids:
                raise ValueError("Two patch updates have the same stable identity")
            section_ids.add(section_id)
            digest = section.get("digest") or stable_hash(section.get("semantic", ""))
            if not section.get("semantic", "").strip() and not section.get("digest"):
                raise ValueError("Patch section has no semantic content")
            section["digest"] = digest
            prior_digests = list(known.get("digests", [])) if known else []
            digest_aliases = dict(known.get("digest_aliases", {})) if known else {}
            already_seen = digest in prior_digests
            legacy_digest = section.get("legacy_digest")
            if not already_seen and legacy_digest and legacy_digest in prior_digests:
                # This exact previously observed body was hashed by the older
                # parser with its presentation-only dateline included. Alias the
                # new digest to the published legacy event instead of replaying.
                digest_aliases[digest] = digest_aliases.get(legacy_digest, legacy_digest)
                prior_digests.append(digest)
                already_seen = True
            if known:
                # Keep the original identity through parser/anchor migrations.
                section["key"] = known["key"]
            if already_seen:
                event_digest = digest_aliases.get(digest, digest)
                identity = stable_hash([url, section["key"], event_digest])
                if identity in result["events"] and section.get("summary"):
                    result["events"][identity]["summary"] = section["summary"]
            if not already_seen:
                event = _event(article, now, section, revision=known is not None, baseline=first_run)
                if known is not None or dated >= cutoff:
                    result["events"][event["id"]] = event
                    added["patch"].append(event["id"])
                prior_digests.append(digest)
            result["sections"][section_id] = {
                "page_url": url, "key": section["key"], "label": section["label"],
                "source_date": section["source_date"], "precision": section.get("precision", "date"),
                "anchor": section.get("anchor", ""), "digest": digest, "digests": prior_digests,
                "digest_aliases": digest_aliases,
                "first_observed": known["first_observed"] if known else now,
                "aliases": known.get("aliases", []) if known else [],
                "parser_version": PARSER_VERSION,
            }
    for kind in ("news", "patch"):
        feed = result["feeds"][kind]
        ids = set(feed["ids"]) | set(added[kind])
        ids = sorted(ids, key=lambda identity: (as_datetime(result["events"][identity]["event_date"]), identity), reverse=True)
        limit = config.get("initial_limit", 20) if first_run else config.get("retention", 60)
        feed["ids"] = ids[:limit]
        fingerprint = stable_hash([result["events"][identity] for identity in feed["ids"]])
        if fingerprint != feed["fingerprint"]:
            feed["fingerprint"] = fingerprint
            feed["last_build"] = now
    result["initialized"] = True
    result["parser_version"] = PARSER_VERSION
    validate_state(result, now)
    return result


def merge_pending_state(latest, pending, config):
    """Reconcile unpushed valid observations after a Git push race.

    The fetched branch is authoritative for current metadata. Previously observed
    pending events, their original detection dates, digest tombstones and aliases
    survive. No caller should reset a branch before saving its pending state.
    """
    validate_state(latest)
    validate_state(pending)
    if not latest.get("initialized") or not pending.get("initialized"):
        raise ValueError("Only established valid states can be reconciled")
    result = copy.deepcopy(latest)
    immutable_fields = (
        "id", "guid", "kind", "page_url", "source_url", "source_date", "precision",
        "event_date", "first_observed", "revision", "baseline", "section_key", "digest",
    )
    for identity, incoming in pending["events"].items():
        known = result["events"].get(identity)
        if known is None:
            result["events"][identity] = copy.deepcopy(incoming)
        elif any(known.get(field) != incoming.get(field) for field in immutable_fields):
            raise ValueError(f"Conflicting immutable event {identity}; reconcile the two ledgers before publishing")
    for url, incoming in pending["articles"].items():
        known = result["articles"].get(url)
        if known is None:
            result["articles"][url] = copy.deepcopy(incoming)
        else:
            known["aliases"] = sorted(set(known.get("aliases", [])) | set(incoming.get("aliases", [])))
    for incoming_id, original in pending["sections"].items():
        incoming = copy.deepcopy(original)
        live_keys = {section["key"] for section in pending["sections"].values() if section["page_url"] == incoming["page_url"]}
        identity, known = _find_section(result, incoming["page_url"], incoming, live_keys)
        if known is None:
            # Usually incoming_id == identity. Preserve its published stable key
            # even when an older parser used a different ledger-key algorithm.
            result["sections"][incoming_id] = incoming
            continue
        if incoming["page_url"] != known["page_url"]:
            raise ValueError("Conflicting pending section page identity")
        known["digests"] = list(dict.fromkeys(known["digests"] + incoming["digests"]))
        digest_aliases = known.setdefault("digest_aliases", {})
        for digest, legacy in incoming.get("digest_aliases", {}).items():
            digest_aliases.setdefault(digest, legacy)
        aliases = set(known.get("aliases", [])) | set(incoming.get("aliases", []))
        if incoming["key"] != known["key"]:
            aliases.add(incoming["key"])
        known["aliases"] = sorted(aliases)
    for kind in ("news", "patch"):
        feed = result["feeds"][kind]
        identities = set(feed["ids"]) | set(pending["feeds"][kind]["ids"])
        identities = sorted(identities, key=lambda identity: (as_datetime(result["events"][identity]["event_date"]), identity), reverse=True)
        feed["ids"] = identities[:config.get("retention", 60)]
        fingerprint = stable_hash([result["events"][identity] for identity in feed["ids"]])
        if fingerprint != feed["fingerprint"]:
            feed["fingerprint"] = fingerprint
            candidates = [value for value in (feed.get("last_build"), pending["feeds"][kind].get("last_build")) if value]
            feed["last_build"] = max(candidates, key=as_datetime) if candidates else result["created_at"]
    # A just-discovered page may no longer be indexed when the retry runs. Keep
    # its overlap observation so a new section/revision does not disappear.
    if pending.get("collection", {}).get("patch_pages"):
        pages = result.setdefault("collection", {}).setdefault("patch_pages", {})
        for url, observed in pending["collection"]["patch_pages"].items():
            if url not in pages or as_datetime(observed) > as_datetime(pages[url]):
                pages[url] = observed
    validate_state(result)
    return result
