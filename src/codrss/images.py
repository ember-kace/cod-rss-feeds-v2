"""Verify publisher image MIME headers without downloading artwork."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests


def _mime(value):
    value = str(value or "").split(";", 1)[0].strip().lower()
    return value if value.startswith("image/") and len(value) < 100 else None


def _signature_type(prefix):
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix[4:8] == b"ftyp" and prefix[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    return None


def verified_image_type(url: str, cache_path=None):
    """Return a verified image MIME type, or None if HTTP cannot confirm it.

    Missing/denied artwork does not make otherwise valid source metadata fail.
    Callers persist successful URL-to-type records so unchanged images do not
    need a network request on every feed poll. No type is inferred from a suffix.
    """
    parts = urlsplit(str(url))
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    cache = {}
    path = Path(cache_path) if cache_path else None
    if path:
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if not isinstance(cache, dict):
            cache = {}
        if _mime(cache.get(url)):
            return _mime(cache[url])
    verified = None
    with requests.Session() as client:
        client.trust_env = False
        client.headers.update({"User-Agent": "cod-rss-feeds/1.0 source-verification"})
        current = url
        for redirect in range(4):
            if urlsplit(current).scheme != "https":
                return None
            response = None
            for attempt in range(2):
                try:
                    response = client.head(current, timeout=(5, 10), allow_redirects=False)
                except requests.RequestException:
                    if attempt == 0:
                        time.sleep(1)
                        continue
                    return None
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    # Respect a publisher backoff by omitting optional MIME now;
                    # the next regular refresh can try again after that interval.
                    if retry_after or attempt:
                        response.close()
                        return None
                    response.close()
                    time.sleep(1)
                    continue
                break
            if response is None:
                return None
            if response.is_redirect:
                target = urljoin(current, response.headers.get("Location", ""))
                response.close()
                if target == current:
                    return None
                current = target
                continue
            if 200 <= response.status_code < 300:
                verified = _mime(response.headers.get("Content-Type"))
            response.close()
            if verified is None and 200 <= response.status_code < 300:
                # The publisher's JPEG CDN can return application/octet-stream.
                # Verify a tiny actual file signature rather than guessing from
                # the URL suffix. Even if Range is ignored, streaming reads only
                # the first 128 bytes before closing the response.
                try:
                    with client.get(current, timeout=(5, 10), allow_redirects=False,
                                    headers={"Range": "bytes=0-127", "Accept-Encoding": "identity"},
                                    stream=True) as prefix_response:
                        if prefix_response.status_code in {200, 206}:
                            prefix = next(prefix_response.iter_content(128), b"")
                            verified = _signature_type(prefix)
                except requests.RequestException:
                    pass
            break
    if verified and path:
        cache[url] = verified
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return verified
