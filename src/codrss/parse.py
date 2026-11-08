"""Publisher HTML extraction. Only article/card-local evidence enters classification."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

PARSER_VERSION = 2
HOSTS = {"www.callofduty.com", "callofduty.com"}
MONTHS = {name.lower(): index for index, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}
MONTHS.update({name[:3]: value for name, value in list(MONTHS.items())})
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_RE = re.compile(rf"\b({MONTH_RE})\s*(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(20\d{{2}}))?\b", re.I)
MW4_RE = re.compile(r"\b(?:modern\s*warfare\s*[®™]?\s*4|mw\s*4)\b|modernwarfare4", re.I)
PATCH_RE = re.compile(r"\b(?:patch\s+notes?|hotfix(?:es)?)\b", re.I)


class SourceParseError(ValueError):
    """An expected publisher representation became unusable."""


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ").replace("\ufffd", " ")).strip()


def canonical_url(url: str, base: str = "", allowed_hosts=None) -> str:
    """Normalize official aliases while keeping section anchors separate."""
    parts = urlsplit(urljoin(base, str(url).strip()))
    allowed = set(allowed_hosts or HOSTS)
    if parts.scheme.lower() not in {"http", "https"} or parts.hostname not in allowed or parts.username or parts.password:
        raise SourceParseError(f"Rejected source URL: {url}")
    host = "www.callofduty.com" if parts.hostname in HOSTS else parts.hostname
    path = re.sub(r"/+", "/", parts.path).removesuffix(".html").rstrip("/") or "/"
    if path.startswith("/en/"):
        path = path[3:]
    # English locale aliases have the same article identity, never translate content.
    path = re.sub(r"^/(?:us|uk|ca|au)/en/", "/", path)
    params = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                    if not key.lower().startswith("utm_") and key.lower() not in
                    {"fbclid", "gclid", "dclid", "cid", "source", "ref", "campaign", "mc_cid", "mc_eid"})
    return urlunsplit(("https", host, path, urlencode(params), ""))


def parse_date(value: str | None, year: int | None = None):
    """Return source precision; a date alone is never presented as an exact time."""
    value = clean(value)
    if not value:
        return None, None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat(), "date"
        except ValueError:
            return None, None
    if re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.isoformat(), "datetime"
        except ValueError:
            pass
    match = DATE_RE.search(value)
    if not match:
        return None, None
    month, day, explicit_year = match.groups()
    source_year = int(explicit_year) if explicit_year else year
    if source_year is None:
        return None, None
    try:
        return date(source_year, MONTHS[month.lower()], int(day)).isoformat(), "date"
    except ValueError:
        return None, None


def _safe_image(value, base):
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url", "")
    value = str(value or "").strip()
    if not value or value.startswith("#"):
        return None
    value = urljoin(base, value)
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    if re.search(r"(?:^|[/_.-])(?:placeholder|transparent|spacer|blank|loading)(?:[/_.-]|$)", parts.path, re.I):
        return None
    return value


def _article_image(soup, main, metadata, hint, base):
    """Use the publisher's social image URL, preserving its canonical CDN host.

    Relative header image paths identify the same artwork but resolving them
    against the article host can produce a different, less compatible URL.
    Open Graph supplies the exact CDN URL intended for shared previews.
    """
    og = soup.select_one('meta[property="og:image"]')
    image = _safe_image(og.get("content"), base) if og else None
    image = image or _safe_image(metadata.get("image"), base)
    if image:
        return image
    for hero in main.select(".article-header-container .feature-img img, .article-header-container img.d-img"):
        for attribute in ("data-src", "src"):
            image = _safe_image(hero.get(attribute), base)
            if image:
                return image
    return _safe_image(hint.get("image"), base)


def _metadata(soup):
    result = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            objects = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        objects = objects if isinstance(objects, list) else [objects]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            for candidate in obj.get("@graph", [obj]):
                if not isinstance(candidate, dict):
                    continue
                types = candidate.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if isinstance(types, list) and any(value in {"BlogPosting", "NewsArticle", "Article"} for value in types if isinstance(value, str)):
                    result.update({key: value for key, value in candidate.items() if value})
    return result


def _product_categories(nodes):
    names = {"mw4": "Modern Warfare 4", "modernwarfare4": "Modern Warfare 4", "warzone": "Warzone", "code": "code", "cod-intel": "cod-intel"}
    result = []
    for node in nodes:
        label = next((names[value] for value in node.get("class", []) if value in names), None)
        if not label:
            text = clean(node.get_text(" ", strip=True)).casefold()
            label = {"mw4": "Modern Warfare 4", "modern warfare 4": "Modern Warfare 4", "wz": "Warzone", "warzone": "Warzone", "cod intel": "cod-intel"}.get(text)
        if label and label not in result:
            result.append(label)
    return result


def parse_index(html: str, url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for link in soup.select(".title a[href]"):
        card = link.find_parent(class_="card-inner") or link.find_parent("article")
        if card is None:
            continue
        try:
            target = canonical_url(link["href"], url)
        except SourceParseError:
            continue
        if not re.search(r"/(?:blog|patchnotes)/\d{4}/", target):
            continue
        date_node = card.select_one(".news-published[data-date], time[datetime]")
        source_date, precision = parse_date(date_node.get("data-date", date_node.get("datetime", ""))) if date_node else (None, None)
        product_nodes = card.select(".game-list .game-tile, .tags .game-tile")
        products = [clean(node.get_text(" ", strip=True)) for node in product_nodes]
        tags = [clean(node.get("data-value") or node.get_text(" ", strip=True)) for node in card.select(".tag-list .tag-item")]
        image_node = card.select_one("img.blog-image")
        hint = {
            "url": target, "title": clean(link.get_text(" ", strip=True)),
            "date": source_date, "source_date": source_date, "precision": precision,
            "products": products, "categories": tags,
            "product_categories": _product_categories(product_nodes),
            "image": _safe_image(image_node.get("data-src") or image_node.get("src"), url) if image_node else None,
            "index_url": canonical_url(url),
        }
        if target not in found:
            found[target] = hint
    if not found:
        raise SourceParseError(f"No enclosing article cards found at {url}; review publisher selectors")
    return list(found.values())


def _article_body(main):
    body = main.select_one(".article-body-container .body-content, .article-body, .entry-content")
    if body is None:
        body = main.select_one("article") or main
    rich = body.select(".article-rich-text")
    if rich:
        selected = BeautifulSoup("<article>" + "".join(str(node) for node in rich) + "</article>", "html.parser").article
    else:
        selected = BeautifulSoup(str(body), "html.parser")
    for node in selected.select("script,style,link,nav,footer,header,.side-nav-wrapper,.bylines,.tags,.cta,.related-articles,.cookie-notice,[aria-hidden=true]"):
        node.decompose()
    return selected


def _semantic_blocks(body):
    """Stable text structure, including table column and row associations."""
    blocks = []
    for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "blockquote", "pre"]):
        if any(parent.name in {"table", "blockquote", "pre"} for parent in node.parents if parent is not body):
            continue
        if node.name == "p" and any(parent.name == "li" for parent in node.parents if parent is not body):
            continue
        if node.name == "table":
            rows = []
            for row in node.find_all("tr"):
                cells = []
                for cell in row.find_all(["th", "td"], recursive=False):
                    span = ""
                    if cell.get("rowspan") or cell.get("colspan"):
                        span = f"[rows={cell.get('rowspan', '1')},cols={cell.get('colspan', '1')}]"
                    cells.append(span + clean(cell.get_text(" ", strip=True)))
                rows.append(" | ".join(cells))
            captions = [clean(caption.get_text(" ", strip=True)) for caption in node.find_all("caption", recursive=False)]
            caption_text = "".join("CAPTION: " + caption + "\n" for caption in captions if caption)
            text = "TABLE\n" + caption_text + "\n".join(rows)
        elif node.name == "li":
            # Each list item is its own record, keeping nested bullet boundaries.
            copy = BeautifulSoup(str(node), "html.parser").li
            for nested in copy.select("ul,ol"):
                nested.decompose()
            text = "LIST: " + clean(copy.get_text(" ", strip=True))
        else:
            text = clean(node.get_text(" ", strip=True))
            if node.name.startswith("h"):
                text = "HEADING: " + text
        if clean(text.removeprefix("HEADING: ").removeprefix("LIST: ")):
            blocks.append((node, text))
    return blocks


def _anchor(node):
    if node.get("id"):
        return node["id"]
    candidate = node.find(attrs={"id": True}) or node.find("a", attrs={"name": True})
    if candidate:
        return candidate.get("id") or candidate.get("name")
    previous = node.find_previous_sibling()
    if isinstance(previous, Tag) and previous.name == "a":
        return previous.get("id") or previous.get("name")
    return None


def _dated_update_heading(label, year):
    """Distinguish update datelines from headings that merely mention a date."""
    match = DATE_RE.search(label)
    if not match:
        return None, None
    prefix = clean(label[:match.start()]).strip(" ,:–—-")
    suffix = clean(label[match.end():]).strip(" ,:–—-.")
    weekday = r"(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
    if prefix and not re.fullmatch(weekday, prefix, re.I):
        return None, None
    if suffix and not re.search(r"\b(?:patch|update|updates|hotfix|hotfixes)\b", suffix, re.I):
        return None, None
    return parse_date(label, year)


def _sections(body, main, source_date, url):
    year = int(source_date[:4]) if source_date else int(re.search(r"/(20\d{2})/", url).group(1)) if re.search(r"/(20\d{2})/", url) else None
    nav_labels = {a.get("href", "")[1:]: clean(a.get_text(" ", strip=True))
                  for a in main.select('.side-nav a[href^="#"]')}
    blocks = _semantic_blocks(body)
    markers = []
    for index, (node, text) in enumerate(blocks):
        label = clean(node.get_text(" ", strip=True))
        value, precision = _dated_update_heading(label, year)
        anchor = _anchor(node)
        if node.name.startswith("h") and value and len(label) < 130:
            markers.append((index, label, value, precision, anchor))
        # Some launch notes are explicitly dated by the publisher's real anchor,
        # with a corresponding release/intro link in the article table of contents.
        elif anchor and re.fullmatch(r"(?:add)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d{1,2}", anchor, re.I) and anchor in nav_labels:
            match = re.fullmatch(r"(?:add)?([a-z]{3})(\d{1,2})", anchor, re.I)
            value, precision = parse_date(f"{match[1]} {match[2]}", year)
            if value:
                markers.append((index, nav_labels[anchor], value, precision, anchor))
    sections = []
    for pos, (start, label, value, precision, anchor) in enumerate(markers):
        end = markers[pos + 1][0] if pos + 1 < len(markers) else len(blocks)
        content_blocks = blocks[start:end]
        legacy_semantic = "\n".join(text for _, text in content_blocks)
        if len(legacy_semantic) < 30:
            raise SourceParseError(f"Suspiciously empty dated section {label} at {url}")
        semantic_blocks = content_blocks
        if content_blocks[0][0].name.startswith("h"):
            semantic_blocks = content_blocks[1:]
        semantic = "UPDATE DATE: " + value + "\n" + "\n".join(text for _, text in semantic_blocks)
        normalized_label = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        key = "anchor:" + anchor if anchor else "date:" + value + ":label:" + normalized_label
        # The reference feed previews actual changes, rather than the page's
        # patch-introduction prose. Semantic list records already omit nested
        # duplicates while retaining every meaningful number and word.
        list_parts = [text.removeprefix("LIST: ") for _, text in content_blocks if text.startswith("LIST: ")]
        if list_parts:
            summary = " ".join(part if part.endswith((".", "!", "?", ":")) else part + "." for part in list_parts)
        else:
            summary_parts = [clean(node.get_text(" ", strip=True)) for node, _ in content_blocks
                             if node.name in {"p", "li"} and len(clean(node.get_text(" ", strip=True))) > 30]
            summary = clean(" ".join(summary_parts))
        summary = summary[:450]
        sections.append({"key": key, "label": label, "source_date": value, "precision": precision,
                         "anchor": anchor, "semantic": semantic,
                         "digest": hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
                         "legacy_digest": hashlib.sha256(legacy_semantic.encode("utf-8")).hexdigest(),
                         "summary": summary or "Official patch notes describe this dated update. Read the official notes for the full details."})
    if len({item["key"] for item in sections}) != len(sections):
        raise SourceParseError(f"Ambiguous duplicate update identities at {url}")
    if not sections and source_date:
        semantic = "\n".join(text for _, text in blocks)
        if len(semantic) < 80:
            raise SourceParseError(f"No substantive patch body at {url}")
        sections = [{"key": "whole-article", "label": "Official patch notes", "source_date": source_date,
                     "precision": "datetime" if "T" in source_date else "date", "anchor": None,
                     "semantic": semantic, "digest": hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
                     "summary": "The official patch document has no reliably separable dated update headings. Its substantive article content is monitored for changes.",
                     "fallback": True}]
    return sections


def parse_article(html: str, url: str, hint: dict | None = None, config: dict | None = None) -> dict:
    hint = hint or {}
    config = config or {}
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("main#main-content.article-layout-content, main#main-content, article")
    if main is None:
        raise SourceParseError(f"Article main container missing at {url}")
    metadata = _metadata(soup)
    canonical = soup.select_one('link[rel="canonical"]')
    candidate = canonical.get("href") if canonical else None
    entity = metadata.get("mainEntityOfPage")
    if not candidate and isinstance(entity, dict):
        candidate = entity.get("@id")
    identity = canonical_url(candidate or url, url)
    title_node = main.select_one("h1.title, h1")
    title = clean(title_node.get_text(" ", strip=True)) if title_node else clean(metadata.get("headline") or hint.get("title"))
    if not title or re.search(r"^(?:access denied|page not found|error|just a moment)", title, re.I):
        raise SourceParseError(f"Unusable article title at {url}")
    body = _article_body(main)
    body_text = clean(body.get_text(" ", strip=True))
    if len(body_text) < 60:
        raise SourceParseError(f"Article body missing or too short at {url}")
    dateline = main.select_one(".dateline, .news-published[data-date], time[datetime]")
    visible_date = (dateline.get("data-date") or dateline.get("datetime") or dateline.get_text(" ", strip=True)) if dateline else None
    source_date, precision = parse_date(metadata.get("datePublished"))
    if not source_date:
        source_date, precision = parse_date(visible_date)
    if not source_date:
        source_date, precision = parse_date(hint.get("source_date") or hint.get("date"))
    byline = main.select_one(".byline, [rel=author]")
    author = re.sub(r"^by\s+", "", clean(byline.get_text(" ", strip=True)), flags=re.I) if byline else ""
    if not author:
        ld_author = metadata.get("author", {})
        author = clean(ld_author.get("name")) if isinstance(ld_author, dict) else clean(ld_author)
    products = [clean(node.get_text(" ", strip=True)) for node in main.select(".tags .game-tile")]
    categories = [clean(node.get("data-value") or node.get_text(" ", strip=True)) for node in main.select(".tags .tag-tile")]
    products = products or hint.get("products", [])
    product_categories = list(dict.fromkeys(hint.get("product_categories", []) + _product_categories(main.select(".tags .game-tile"))))
    categories = categories or hint.get("categories", [])
    # Only substantial content and local product metadata count, never global nav/CTA.
    evidence = " ".join([title, urlsplit(identity).path, " ".join(products), body_text])
    aliases = config.get("product_aliases", {}).get("mw4", [])
    mw4 = bool(MW4_RE.search(evidence)) or any(
        re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", evidence, re.I)
        for alias in aliases
    )
    wz = bool(re.search(r"\b(?:warzone|WZ)\b", " ".join([title, " ".join(products)]), re.I))
    mobile = bool(re.search(r"warzone[\s-]+mobile|call[\s-]+of[\s-]+duty[\s:-]+mobile|/mobile/|codm", title + " " + identity, re.I))
    patch = ("/patchnotes/" in urlsplit(identity).path
             or main.get("data-article-type") == "patchnotes"
             or bool(PATCH_RE.search(title))
             or any(tag.lower() in {"patch-notes", "patch-updates", "hotfix"} for tag in categories))
    current_context = bool(MW4_RE.search(identity + " " + title + " " + " ".join(products)))
    # Warzone's current era is explicitly configured; do not silently adopt an annual game.
    era_aliases = config.get("current_warzone_aliases", ["modern-warfare-4", "mw4", "modernwarfare4"])
    current_context = current_context or any(alias.lower() in identity.lower() for alias in era_aliases)
    if mobile:
        kind = "irrelevant"
    elif patch:
        kind = "patch" if current_context and (mw4 or wz) else "irrelevant"
    else:
        kind = "news" if mw4 else "irrelevant"
    game = "Modern Warfare 4 & Warzone" if mw4 and wz else "Modern Warfare 4" if mw4 else "Warzone" if wz else "Other"
    subtitle = main.select_one("p.subtitle")
    summary = clean(subtitle.get_text(" ", strip=True)) if subtitle else ""
    summary = summary or clean(metadata.get("description"))
    if not summary:
        summary = next((clean(p.get_text(" ", strip=True)) for p in body.select("p") if len(clean(p.get_text(" ", strip=True))) >= 80), "")
    # Presentation truncation belongs to the renderer, where a word boundary
    # and the ellipsis can be applied consistently for every reader.
    summary = summary[:min(int(config.get("summary_source_limit", 450)), 450)]
    image = _article_image(soup, main, metadata, hint, url)
    updated_date, updated_precision = parse_date(metadata.get("dateModified"))
    return {"url": identity, "title": title, "source_date": source_date, "precision": precision,
            "updated_date": updated_date, "updated_precision": updated_precision,
            "author": author or "Call of Duty", "summary": summary, "image": image,
            "products": products, "product_categories": product_categories, "categories": categories, "game": game, "kind": kind,
            "sections": _sections(body, main, source_date, identity) if kind == "patch" else [],
            "parser_version": PARSER_VERSION}
