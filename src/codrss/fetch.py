"""Bounded, sequential, TLS-verified HTTP with usable-representation caching."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests


class SourceError(RuntimeError):
    pass


def check_html(body: str, url: str) -> None:
    text = body.lower()
    if len(body.strip()) < 100 or any(token in text for token in (
        "<title>access denied", "<title>just a moment", "cf-chl-",
        "<title>404", "<title>page not found", "<title>error",
        "<h1>access denied", "<h1>page not found", "verify you are human",
    )):
        raise SourceError(f"Source returned empty/error/challenge HTML: {url}")


class Fetcher:
    def __init__(self, config: dict, cache: Path):
        self.config, self.cache = config, Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        # Public source collection must not inherit workstation proxy/auth state.
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": config["user_agent"]})
        self.count = 0
        self.robots = None
        self.checked = []

    def load_robots(self):
        url = self.config["robots_url"]
        body = self.get(url, html=False, enforce_robots=False)
        if body.lstrip().startswith("<"):
            raise SourceError("Publisher robots.txt returned HTML instead of access rules")
        self.robots = RobotFileParser(url)
        self.robots.parse(body.splitlines())
        if not self.robots.can_fetch(self.config["user_agent"], self.config["discovery_sources"][0]):
            raise SourceError("Publisher robots rules disallow discovery")

    def get(self, url: str, *, html=True, enforce_robots=True, _redirects=0) -> str:
        if _redirects > 5:
            raise SourceError(f"Source redirect limit exceeded: {url}")
        parts = urlparse(url)
        if (parts.scheme != "https" or parts.hostname not in self.config["allowed_hosts"]
                or parts.username or parts.password):
            raise SourceError(f"Rejected source URL: {url}")
        if enforce_robots and self.robots and not self.robots.can_fetch(self.config["user_agent"], url):
            raise SourceError(f"Publisher robots rules disallow {url}")
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / f"{key}.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        if not isinstance(cached, dict) or not isinstance(cached.get("body"), str):
            cached = {}
        if html and cached.get("body", "").strip():
            try:
                check_html(cached["body"], url)
            except SourceError:
                cached = {}  # An error/challenge page is not a usable 304 body.
        headers = {}
        if cached.get("body", "").strip():
            if isinstance(cached.get("etag"), str) and cached["etag"]:
                headers["If-None-Match"] = cached["etag"]
            if isinstance(cached.get("modified"), str) and cached["modified"]:
                headers["If-Modified-Since"] = cached["modified"]
        for attempt in range(3):
            response = None
            self.count += 1
            if self.count > self.config["max_requests"]:
                raise SourceError("HTTP request budget exhausted; adjust verified discovery bounds")
            try:
                response = self.session.get(url, timeout=(10, self.config["timeout_seconds"]), headers=headers, allow_redirects=False, stream=True)
                if response.is_redirect:
                    from urllib.parse import urljoin
                    target = urljoin(url, response.headers["Location"])
                    if attempt == 2:
                        raise SourceError(f"Redirect loop: {url}")
                    response.close()
                    return self.get(target, html=html, enforce_robots=enforce_robots, _redirects=_redirects + 1)
                if response.status_code == 304:
                    if cached.get("body", "").strip():
                        body = cached["body"]
                        break
                    headers = {}
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    retry = response.headers.get("Retry-After", "")
                    try:
                        delay = float(retry)
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            delay = 2 ** attempt
                    if delay > 45:
                        raise SourceError(f"Source requests retry after {delay:.0f}s: {url}")
                    if attempt < 2:
                        response.close()
                        time.sleep(max(delay, 2 ** attempt))
                        continue
                response.raise_for_status()
                payload = bytearray()
                for chunk in response.iter_content(65536):
                    payload.extend(chunk)
                    if len(payload) > 8 * 1024 * 1024:
                        response.close()
                        raise SourceError(f"Source exceeds 8 MiB limit: {url}")
                body = bytes(payload).decode("utf-8")
                response.close()
                if html:
                    check_html(body, url)
                path.write_text(json.dumps({"url": url, "body": body, "etag": response.headers.get("ETag"), "modified": response.headers.get("Last-Modified")}, ensure_ascii=False), encoding="utf-8")
                break
            except requests.RequestException as exc:
                if response is not None:
                    response.close()
                if attempt == 2 or (getattr(exc, "response", None) is not None and exc.response.status_code < 500 and exc.response.status_code != 429):
                    raise SourceError(f"Source fetch failed: {url}: {exc}") from exc
                time.sleep(2 ** attempt)
            finally:
                if response is not None:
                    response.close()
        else:
            raise SourceError(f"No usable representation: {url}")
        if html:
            check_html(body, url)
        self.checked.append(url)
        return body
