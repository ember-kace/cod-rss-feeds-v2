"""Anonymous HTTPS deployment checks, also used to repair pending publications."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

import feedparser
import requests


FEEDS = ("feeds/modern-warfare-4.xml", "feeds/patch-updates.xml")
ATOM = "http://www.w3.org/2005/Atom"
MEDIA = "http://search.yahoo.com/mrss/"
XML_TYPES = {"application/rss+xml", "application/xml", "text/xml", "application/atom+xml"}


class AnonymousAuth(requests.auth.AuthBase):
    def __call__(self, request):
        request.headers.pop("Authorization", None)
        return request


def session() -> requests.Session:
    client = requests.Session()
    client.trust_env = False
    client.auth = AnonymousAuth()
    client.headers.update({"User-Agent": "cod-rss-feeds-deployment-check/1.0"})
    client.max_redirects = 5
    return client


def fetch(client: requests.Session, url: str) -> tuple[bytes, str]:
    if urlsplit(url).scheme != "https":
        raise ValueError(f"HTTPS required: {url}")
    with client.get(url, timeout=(10, 30), stream=True) as response:
        response.raise_for_status()
        if any(urlsplit(r.url).scheme != "https" for r in [*response.history, response]):
            raise ValueError(f"Non-HTTPS redirect: {url}")
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError(f"Response exceeded 4 MiB: {url}")
        return bytes(data), response.headers.get("Content-Type", "").split(";", 1)[0].lower()


def absolute_https(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.netloc) and not parts.username


def verify(base_url: str, public: Path, require_items: bool = True, full_manifest: bool = False) -> dict:
    base_url = base_url.rstrip("/") + "/"
    report = {"base_url": base_url, "anonymous": True, "feeds": {}}
    with session() as client:
        local_manifest = json.loads((public / "manifest.json").read_text(encoding="utf-8"))
        manifest_files = local_manifest.get("files")
        if not isinstance(manifest_files, dict):
            raise ValueError("Candidate manifest lacks file hashes")
        if full_manifest:
            content_files = {
                path.relative_to(public).as_posix()
                for path in public.rglob("*")
                if path.is_file() and path.relative_to(public).as_posix()
                not in {"manifest.json", "status.json", ".nojekyll"}
            }
            if set(manifest_files) != content_files:
                raise ValueError("Candidate manifest does not cover the complete publication")
        for relative, digest in manifest_files.items():
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise ValueError("Invalid candidate manifest entry")
            local = (public / relative).resolve()
            if (not local.is_relative_to(public.resolve()) or not local.is_file()
                    or urlsplit(relative).scheme or relative.startswith(("/", "\\"))
                    or "\\" in relative):
                raise ValueError("Invalid candidate manifest path")
            if hashlib.sha256(local.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Candidate manifest hash mismatch: {relative}")
        remote_bytes, _ = fetch(client, urljoin(base_url, "manifest.json"))
        if json.loads(remote_bytes) != local_manifest:
            raise ValueError("Served manifest differs from candidate publication")
        checked_files: set[str] = set()
        for relative, label in (("index.html", "landing page"), ("status.json", "operational checkpoint")):
            if (public / relative).exists():
                body, mime = fetch(client, urljoin(base_url, relative))
                if body != (public / relative).read_bytes():
                    raise ValueError(f"Served {label} differs from candidate")
                if relative.endswith(".html") and mime != "text/html":
                    raise ValueError("Unexpected landing page Content-Type")
                checked_files.add(relative)
        checked_pages: set[str] = set()
        for relative in FEEDS:
            url = urljoin(base_url, relative)
            body, mime = fetch(client, url)
            if mime not in XML_TYPES:
                raise ValueError(f"Unexpected XML Content-Type {mime!r}: {url}")
            if body != (public / relative).read_bytes():
                raise ValueError(f"Served feed hash differs from candidate: {url}")
            checked_files.add(relative)
            root = ET.fromstring(body)
            if root.tag != "rss" or root.attrib.get("version") != "2.0":
                raise ValueError(f"Not RSS 2.0: {url}")
            self_link = root.find(f"channel/{{{ATOM}}}link[@rel='self']")
            if self_link is None or self_link.attrib.get("href") != url:
                raise ValueError(f"Incorrect Atom self-link: {url}")
            parsed = feedparser.parse(body)
            if parsed.bozo:
                raise ValueError(f"Independent RSS parser rejected {url}: {parsed.bozo_exception}")
            if require_items and not parsed.entries:
                raise ValueError(f"Feed contains no verified current items: {url}")
            identifiers = [item.get("id") for item in parsed.entries]
            if not all(identifiers) or len(set(identifiers)) != len(identifiers):
                raise ValueError(f"Missing or duplicate GUIDs: {url}")
            for item in root.findall("channel/item"):
                link = item.findtext("link") or ""
                if not absolute_https(link):
                    raise ValueError(f"Invalid item link: {link}")
                for tag in ("thumbnail", "content"):
                    for media in item.findall(f"{{{MEDIA}}}{tag}"):
                        if not absolute_https(media.attrib.get("url", "")):
                            raise ValueError("Invalid media image URL")
                if relative == FEEDS[1]:
                    if not link.startswith(urljoin(base_url, "updates/")):
                        raise ValueError(f"Patch item lacks its stable project permalink: {link}")
                    page_relative = link[len(base_url):]
                    if page_relative not in checked_pages:
                        page, page_type = fetch(client, link)
                        if page_type != "text/html" or page != (public / page_relative).read_bytes():
                            raise ValueError(f"Missing or mismatched event page: {link}")
                        checked_pages.add(page_relative)
                        checked_files.add(page_relative)
            report["feeds"][relative] = {
                "url": url,
                "http_status": 200,
                "content_type": mime,
                "sha256": hashlib.sha256(body).hexdigest(),
                "items": len(parsed.entries),
                "latest_event_date": parsed.entries[0].get("published") if parsed.entries else None,
            }
        report["verified_event_pages"] = len(checked_pages)
        if full_manifest:
            for relative in sorted(manifest_files):
                if relative in checked_files:
                    continue
                body, mime = fetch(client, urljoin(base_url, relative))
                if hashlib.sha256(body).hexdigest() != manifest_files[relative]:
                    raise ValueError(f"Served manifest content differs from candidate: {relative}")
                if relative.endswith(".html") and mime != "text/html":
                    raise ValueError(f"Unexpected HTML Content-Type: {relative}")
                checked_files.add(relative)
                if relative.startswith("updates/") and relative.endswith(".html"):
                    checked_pages.add(relative)
            report["verified_event_pages"] = len(checked_pages)
            report["full_manifest_verified"] = True
        report["verified_content_files"] = len(checked_files)
    return report


def deployment_needed(base_url: str, public: Path) -> tuple[bool, str]:
    """Compare both manifest and actual XML, including interrupted prior deployments."""
    try:
        verify(base_url, public)
        return False, "Live manifest, feeds, and referenced event pages match"
    except (OSError, ValueError, requests.RequestException, ET.ParseError) as exc:
        return True, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--config", type=Path, default=Path("config/feeds.json"))
    parser.add_argument("--public-dir", type=Path, default=Path("public"))
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--delay", type=float, default=10)
    parser.add_argument("--needs-deployment", action="store_true")
    parser.add_argument("--github-output", action="store_true")
    parser.add_argument("--full-manifest", action="store_true", help="Verify every retained public file, including historical patch pages")
    args = parser.parse_args()
    base_url = args.base_url or json.loads(args.config.read_text(encoding="utf-8"))["base_url"]
    if args.needs_deployment:
        needed, reason = deployment_needed(base_url, args.public_dir)
        if os.environ.get("FORCE_DEPLOY") == "true":
            needed, reason = True, "Explicitly requested publication of the validated artifact"
        if args.github_output:
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
                handle.write(f"deploy={str(needed).lower()}\n")
        print(json.dumps({"deploy": needed, "reason": reason}, ensure_ascii=False))
        return 0
    for attempt in range(args.attempts):
        try:
            print(json.dumps(verify(base_url, args.public_dir, full_manifest=args.full_manifest), indent=2, ensure_ascii=False))
            return 0
        except (OSError, ValueError, requests.RequestException, ET.ParseError) as exc:
            if attempt + 1 == args.attempts:
                raise SystemExit(f"Deployment verification failed after {args.attempts} attempts: {exc}")
            print(f"Propagation check {attempt + 1}/{args.attempts}: {exc}", flush=True)
            time.sleep(min(args.delay, 30))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
