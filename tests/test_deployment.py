"""Exercise interrupted deployments against an isolated fake anonymous origin."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_deployment.py"
SPEC = importlib.util.spec_from_file_location("verify_deployment", MODULE_PATH)
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.public = Path(self.temp.name)
        self.base = "https://example.github.io/project/"
        self.live = {}
        for relative in DEPLOY.FEEDS:
            link = self.base + "updates/event.html" if "patch" in relative else "https://www.callofduty.com/blog/example"
            xml = (
                '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
                '<title>Fixture</title><link>https://example.github.io/project/</link><description>Fixture</description>'
                f'<atom:link rel="self" href="{self.base}{relative}" type="application/rss+xml"/>'
                f'<item><title>Fixture</title><link>{link}</link><guid isPermaLink="false">{relative}</guid>'
                '<pubDate>Sun, 08 Nov 2026 00:00:00 GMT</pubDate><description>Fixture excerpt</description>'
                '</item></channel></rss>'
            ).encode()
            self.put(relative, xml, "application/xml")
        self.put("updates/event.html", b"<!doctype html><title>Event fixture</title>", "text/html")
        self.put("status.json", b'{"observation_time":"2026-11-08"}', "application/json")
        manifest = {"files": {key: hashlib.sha256(value[0]).hexdigest() for key, value in self.live.items()}}
        self.put("manifest.json", json.dumps(manifest, sort_keys=True).encode(), "application/json")

    def put(self, relative, body, mime):
        path = self.public / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self.live[relative] = (body, mime)

    def fetch(self, client, url):
        relative = url.removeprefix(self.base)
        if relative not in self.live:
            raise ValueError("Origin returned HTTP 404")
        return self.live[relative]

    def needed(self):
        with patch.object(DEPLOY, "fetch", side_effect=self.fetch):
            return DEPLOY.deployment_needed(self.base, self.public)

    def test_matching_committed_state_does_not_need_deployment(self):
        self.assertFalse(self.needed()[0])

    def test_committed_but_not_deployed_feed_is_retried_even_if_manifest_matches(self):
        body, mime = self.live[DEPLOY.FEEDS[1]]
        self.live[DEPLOY.FEEDS[1]] = (body.replace(b"Fixture excerpt", b"Old excerpt"), mime)
        needed, reason = self.needed()
        self.assertTrue(needed)
        self.assertIn("hash differs", reason)

    def test_missing_event_page_is_not_reported_successful(self):
        del self.live["updates/event.html"]
        self.assertTrue(self.needed()[0])

    def test_http_200_html_instead_of_rss_is_rejected(self):
        self.live[DEPLOY.FEEDS[0]] = (b"<html><title>Access denied</title></html>", "text/html")
        needed, reason = self.needed()
        self.assertTrue(needed)
        self.assertIn("Content-Type", reason)

    def test_daily_status_checkpoint_is_deployed_despite_unchanged_content_digest(self):
        self.live["status.json"] = (b'{"observation_time":"2026-11-07"}', "application/json")
        self.assertTrue(self.needed()[0])


if __name__ == "__main__":
    unittest.main()
