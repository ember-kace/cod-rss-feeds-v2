"""Generate and independently validate the complete static publication."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.dom import minidom
from xml.etree import ElementTree as ET

from .events import as_datetime, stable_hash, validate_state

ATOM = "http://www.w3.org/2005/Atom"
MEDIA = "http://search.yahoo.com/mrss/"
CONTENT = "http://purl.org/rss/1.0/modules/content/"
DC = "http://purl.org/dc/elements/1.1/"
for prefix, uri in (("atom", ATOM), ("media", MEDIA), ("content", CONTENT), ("dc", DC)):
    ET.register_namespace(prefix, uri)

FEEDS = {
    "news": ("modern-warfare-4.xml", "Modern Warfare 4 — Official Blog"),
    "patch": ("patch-updates.xml", "MW4 & Warzone — Patch Updates"),
}
PRESENTATION_VERSION = 2
ATTRIBUTION = (
    "Independently maintained RSS feeds of official Call of Duty content. "
    "Activision does not operate or endorse this hosting project."
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def clean(value):
    return _CONTROL.sub("", str(value or ""))


def absolute_url(base, relative):
    base = base.rstrip("/") + "/"
    if urlparse(base).scheme != "https" or not urlparse(base).netloc:
        raise ValueError("A verified absolute HTTPS Pages base URL is required")
    return urljoin(base, relative.lstrip("/"))


def _date(value):
    return format_datetime(as_datetime(value), usegmt=True)


def _text(parent, tag, value, **attributes):
    element = ET.SubElement(parent, tag, attributes)
    element.text = clean(value)
    return element


def item_title(event):
    if event["kind"] != "patch":
        return clean(event["title"])
    game = "Warzone" if "warzone" in event["article_title"].casefold() else "MW4"
    date = as_datetime(event["source_date"])
    title = f"{game} patch update — {date.strftime('%B')} {date.day}, {date.year}"
    return "Update detected — " + title if event["revision"] else title


def item_categories(event):
    if event["kind"] == "patch":
        return ["Warzone" if "warzone" in event["article_title"].casefold() else "Modern Warfare 4", "Patch updates"]
    if event.get("product_categories"):
        return list(dict.fromkeys(event["product_categories"]))
    categories = ["Modern Warfare 4"]
    if "warzone" in event["game"].casefold():
        categories.append("Warzone")
    if any(category.casefold() == "code" for category in event.get("categories", [])):
        categories.append("code")
    return categories


def _description(event, limit, details=False):
    summary = clean(event.get("summary", ""))
    summary = " ".join(summary.split())
    if len(summary) > limit:
        summary = summary[:limit - 1].rsplit(" ", 1)[0] + "…"
    if event["revision"]:
        summary = "Update detected: the official notes changed. " + summary
    source = html.escape(clean(event["source_url"]), quote=True)
    result = f"<p>{html.escape(summary)}</p>"
    image = event.get("image")
    if image:
        result += f'<p><img src="{html.escape(clean(image), quote=True)}" alt="{html.escape(item_title(event), quote=True)}" /></p>'
    result += f'<p><a href="{source}">Read the official update</a></p>'
    if not details:
        return result.replace("&#x27;", "&apos;")
    dates = f"Official source date: {event['source_date']} ({event.get('precision', 'date')} precision)."
    if event["kind"] == "patch" and not event["baseline"]:
        dates += f" Feed update first observed: {event['first_observed']}."
    elif event["kind"] == "patch":
        dates += " Historical baseline; no earlier revision history is claimed."
    return (result + f"<p>{html.escape(dates)}</p>").replace("&#x27;", "&apos;")


def _serialize_rss(root):
    """Use standard XML DOM CDATA nodes, including safe splitting of ]]> text."""
    ET.indent(root, space="  ")
    document = minidom.parseString(ET.tostring(root, encoding="utf-8"))
    for name in ("description", "content:encoded"):
        for element in document.getElementsByTagName(name):
            if element.parentNode.tagName != "item":
                continue
            value = "".join(node.data for node in element.childNodes)
            for node in list(element.childNodes):
                element.removeChild(node)
            segments = value.split("]]>")
            for index, segment in enumerate(segments):
                if index:
                    segment = ">" + segment
                if index < len(segments) - 1:
                    segment += "]]"
                element.appendChild(document.createCDATASection(segment))
    return document.toxml(encoding="UTF-8") + b"\n"


def _rss(state, kind, config):
    filename, title = FEEDS[kind]
    base = config["base_url"]
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    _text(channel, "title", title)
    _text(channel, "link", "https://www.callofduty.com/blog/modernwarfare4" if kind == "news" else "https://www.callofduty.com/patchnotes")
    scope = "Official Call of Duty articles tagged Modern Warfare 4, including shared MW4/Warzone announcements. " if kind == "news" else "Dated update sections from current official Modern Warfare 4 and Warzone patch notes, with substantive revision tracking. "
    _text(channel, "description", scope + ATTRIBUTION)
    _text(channel, "language", "en-us")
    ET.SubElement(channel, f"{{{ATOM}}}link", {
        "href": absolute_url(base, "feeds/" + filename), "rel": "self", "type": "application/rss+xml",
    })
    feed = state["feeds"][kind]
    _text(channel, "lastBuildDate", _date(feed["last_build"] or state["created_at"]))
    _text(channel, "ttl", "15")
    _text(channel, "generator", "Call of Duty Feed Desk")
    _text(channel, "copyright", "Original articles and artwork © Activision Publishing, Inc. Feed conversion independently operated.")
    for identity in feed["ids"]:
        event = state["events"][identity]
        item = ET.SubElement(channel, "item")
        _text(item, "title", item_title(event))
        link = absolute_url(base, f"updates/{identity}.html") if kind == "patch" else event["source_url"]
        _text(item, "link", link)
        _text(item, "guid", event["guid"], isPermaLink="false")
        _text(item, "pubDate", _date(event["event_date"]))
        _text(item, f"{{{DC}}}creator", event["author"])
        description = _description(event, config.get("summary_length", 350))
        _text(item, "description", description)
        _text(item, f"{{{CONTENT}}}encoded", description)
        for category in item_categories(event):
            if category:
                _text(item, "category", category)
        if event.get("image"):
            attributes = {"url": clean(event["image"]), "medium": "image"}
            if (event.get("image_type") or "").startswith("image/"):
                attributes["type"] = clean(event["image_type"])
            ET.SubElement(item, f"{{{MEDIA}}}content", attributes)
            ET.SubElement(item, f"{{{MEDIA}}}thumbnail", {"url": clean(event["image"])})
    return _serialize_rss(root)


def _html_page(title, body, canonical, preview=None):
    metadata = ""
    if preview:
        values = {"og:type": "article", "og:title": title, "og:url": canonical, "og:description": clean(preview.get("summary", ""))[:350]}
        if preview.get("image"):
            values["og:image"] = preview["image"]
            if preview.get("image_type"):
                values["og:image:type"] = preview["image_type"]
        metadata = "".join(f'<meta property="{key}" content="{html.escape(clean(value), quote=True)}">' for key, value in values.items())
        if preview.get("image"):
            metadata += '<meta name="twitter:card" content="summary_large_image">'
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(clean(title))}</title><link rel="canonical" href="{html.escape(canonical, quote=True)}">'
        + metadata +
        '<style>body{font:18px/1.6 system-ui,sans-serif;max-width:850px;margin:3rem auto;padding:0 1rem;background:#101820;color:#edf3f5}'
        'a{color:#9cd8ff}h1{line-height:1.2}small{color:#bccad3}article{border-top:1px solid #53606a;margin-top:2rem;padding-top:1rem}'
        'img{max-width:100%;height:auto}code{overflow-wrap:anywhere}footer{margin-top:3rem}</style></head><body>'
        f"{body}<footer><small>{html.escape(ATTRIBUTION)}</small></footer></body></html>\n"
    )


def render_public(state, config, destination, now=None):
    """Render into an unpublished staging directory; caller swaps only after validation."""
    validate_state(state)
    if not state.get("initialized") or not state.get("created_at"):
        raise ValueError("Cannot publish an uninitialized ledger")
    target = Path(destination)
    (target / "feeds").mkdir(parents=True, exist_ok=True)
    (target / "updates").mkdir(parents=True, exist_ok=True)
    base = config["base_url"]
    for kind, (filename, _) in FEEDS.items():
        (target / "feeds" / filename).write_bytes(_rss(state, kind, config))
    # Retain all historical permalinks, including events pruned from the public feed.
    for identity, event in sorted(state["events"].items()):
        if event["kind"] != "patch":
            continue
        event_url = absolute_url(base, f"updates/{identity}.html")
        body = f"<h1>{html.escape(item_title(event))}</h1>"
        body += _description(event, config.get("summary_length", 350), details=True)
        body += f"<p>Feed event published: {html.escape(event['event_date'])}.</p>"
        body += f'<p><a href="{html.escape(absolute_url(base, ""), quote=True)}">All feeds and collection status</a></p>'
        (target / "updates" / (identity + ".html")).write_text(_html_page(item_title(event), body, event_url, preview=event), encoding="utf-8", newline="\n")
    summary = {}
    for kind, (_, title) in FEEDS.items():
        events = [state["events"][identity] for identity in state["feeds"][kind]["ids"]]
        summary[kind] = {
            "name": title, "count": len(events),
            "latest_source_date": max((event["source_date"] for event in events), key=as_datetime, default=None),
            "last_build": state["feeds"][kind]["last_build"],
        }
    checkpoint = state.get("checkpoint", {})
    observed = checkpoint.get("observed_at") or checkpoint.get("observation_time") or checkpoint.get("last_successful_collection") or state["created_at"]
    success = checkpoint.get("last_successful_collection") or checkpoint.get("last_success") or observed
    stale = (as_datetime(observed) - as_datetime(success)).total_seconds() > config.get("stale_after_hours", 2) * 3600
    status = {
        "schema_version": 1,
        "observation_time": observed,
        "last_successful_collection": success,
        "source_data_stale": stale,
        "feeds": summary,
        "note": "This deployed snapshot is not independent monitoring. Current failures appear in GitHub Actions.",
    }
    if checkpoint:
        status["collection"] = checkpoint
    (target / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    body = "<h1>Call of Duty RSS feeds</h1><p>Official articles and rolling patch updates, collected independently.</p>"
    for kind, (filename, title) in FEEDS.items():
        url = absolute_url(base, "feeds/" + filename)
        info = summary[kind]
        body += f'<article><h2><a href="{html.escape(url, quote=True)}">{html.escape(title)}</a></h2>'
        body += f"<p>{info['count']} items. Latest official source date: {html.escape(info['latest_source_date'] or 'No qualifying items')}.</p></article>"
    body += '<p>Target refresh: every 15 minutes, at minutes 7, 22, 37 and 52. GitHub scheduling can be delayed.</p>'
    body += '<p><a href="status.json">Collection status snapshot</a> · <a href="manifest.json">Publication hashes</a></p>'
    body += '<p>Patch item links have stable individual pages so consumers that deduplicate by URL can distinguish revisions. Every item links prominently to its exact official source.</p>'
    (target / "index.html").write_text(_html_page("Call of Duty RSS feeds", body, absolute_url(base, "")), encoding="utf-8", newline="\n")
    (target / ".nojekyll").write_text("", encoding="utf-8")
    files = {
        path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(target.rglob("*"))
        if path.is_file() and path.relative_to(target).as_posix() not in ("manifest.json", "status.json", ".nojekyll")
    }
    manifest = {"schema_version": 1, "algorithm": "sha256", "files": files, "content_digest": stable_hash(files)}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _https(value, label):
    parsed = urlparse(value or "")
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label} must be an absolute HTTPS URL: {value!r}")


def validate_public(path, config):
    """Use feedparser plus structural assertions, independently of serialization."""
    import feedparser

    target = Path(path)
    base = config["base_url"]
    summary = {}
    all_guids = set()
    for kind, (filename, expected_title) in FEEDS.items():
        body = (target / "feeds" / filename).read_bytes()
        decoded = body.decode("utf-8")
        if not decoded.startswith("<?xml") or "<!DOCTYPE" in decoded.upper():
            raise ValueError("Invalid RSS/XML document")
        root = ET.fromstring(body)
        if root.tag != "rss" or root.get("version") != "2.0":
            raise ValueError("Expected RSS 2.0")
        channel = root.find("channel")
        if channel is None or channel.findtext("title") != expected_title:
            raise ValueError("Unexpected feed identity")
        self_link = channel.find(f"{{{ATOM}}}link")
        expected_url = absolute_url(base, "feeds/" + filename)
        if self_link is None or self_link.get("href") != expected_url or self_link.get("rel") != "self":
            raise ValueError("Incorrect absolute Atom self link")
        parsedate_to_datetime(channel.findtext("lastBuildDate"))
        parsed = feedparser.parse(body)
        if parsed.bozo or parsed.version != "rss20":
            raise ValueError(f"Independent RSS parser rejected {filename}: {parsed.get('bozo_exception')}")
        if not parsed.entries and config.get("require_nonempty", True):
            raise ValueError(f"Refusing to publish empty {kind} feed")
        source_dates = []
        dates_and_guids = []
        for item, entry in zip(channel.findall("item"), parsed.entries, strict=True):
            guid = item.find("guid")
            if guid is None or guid.get("isPermaLink") != "false" or not guid.text or guid.text in all_guids:
                raise ValueError("Missing, duplicate or incorrectly typed GUID")
            all_guids.add(guid.text)
            if not entry.get("title"):
                raise ValueError("Missing item title")
            _https(entry.link, "Item link")
            date = parsedate_to_datetime(item.findtext("pubDate"))
            if date.tzinfo is None or not re.search(r"\b\d{4}\b", item.findtext("pubDate")):
                raise ValueError("Invalid RSS date")
            # Exact future-date checking happens against collection time in update_state.
            dates_and_guids.append((date, guid.text))
            categories = [node.text or "" for node in item.findall("category")]
            if kind == "news" and not any("modern warfare 4" in c.casefold() or c.casefold() == "mw4" for c in categories):
                raise ValueError("News item lacks MW4 scope evidence")
            if kind == "patch" and not any("warzone" in c.casefold() or "modern warfare 4" in c.casefold() or c.casefold() == "mw4" for c in categories):
                raise ValueError("Patch item lacks game scope evidence")
            description = item.findtext("description") or ""
            if "Read the official update" not in description or "https://www.callofduty.com/" not in description:
                raise ValueError("Missing official source attribution")
            for media in item.findall(f"{{{MEDIA}}}thumbnail") + item.findall(f"{{{MEDIA}}}content"):
                _https(media.get("url"), "Image")
            if kind == "patch":
                prefix = absolute_url(base, "updates/")
                if not entry.link.startswith(prefix):
                    raise ValueError("Patch event lacks stable project permalink")
                relative = entry.link.removeprefix(absolute_url(base, ""))
                event_path = target / relative
                if not event_path.is_file():
                    raise ValueError("Patch feed refers to a missing event page")
                if entry.link not in event_path.read_text(encoding="utf-8"):
                    raise ValueError("Patch event page lacks its own canonical identity")
                match = re.search(r"Official source date: ([^ ]+)", event_path.read_text(encoding="utf-8"))
                if not match:
                    raise ValueError("Patch event page lacks original source date")
                as_datetime(match.group(1))
                source_dates.append(match.group(1))
            else:
                source_dates.append(date.isoformat())
        if dates_and_guids != sorted(dates_and_guids, reverse=True):
            raise ValueError("Feed items are not deterministically newest first")
        summary[kind] = {
            "count": len(parsed.entries),
            "latest_source_date": max(source_dates, key=as_datetime, default=None),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    expected_files = {
        file.relative_to(target).as_posix()
        for file in target.rglob("*")
        if file.is_file() and file.relative_to(target).as_posix() not in ("manifest.json", "status.json", ".nojekyll")
    }
    if set(manifest.get("files", {})) != expected_files:
        raise ValueError("Manifest does not cover the complete publication")
    if manifest.get("content_digest") != stable_hash(manifest["files"]):
        raise ValueError("Invalid manifest content digest")
    for relative, digest in manifest["files"].items():
        actual = target / relative
        if not actual.resolve().is_relative_to(target.resolve()) or not actual.is_file():
            raise ValueError("Invalid manifest path")
        if hashlib.sha256(actual.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Manifest hash mismatch for {relative}")
    return summary
