"""Failure boundaries must preserve the complete last-good publication."""
import importlib.util
import hashlib
import json
import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codrss.fetch import Fetcher, SourceError, check_html


def refresh_module():
    spec = importlib.util.spec_from_file_location("project_refresh", ROOT / "scripts/refresh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchTests(unittest.TestCase):
    def test_invalid_cache_shape_and_cached_error_html_fetch_normally(self):
        url = "https://www.callofduty.com/blog"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (hashlib.sha256(url.encode()).hexdigest() + ".json")
            config = {"user_agent": "test", "allowed_hosts": ["www.callofduty.com"], "max_requests": 5, "timeout_seconds": 5}
            for cached in ([], {"body": 123}, {"body": "<html><title>Access Denied</title>" + "x" * 150, "etag": '"cached-error"'}):
                with self.subTest(cached_type=type(cached).__name__):
                    path.write_text(json.dumps(cached), encoding="utf-8")
                    fetcher = Fetcher(config, Path(directory))
                    response = Mock(status_code=200, is_redirect=False, headers={})
                    response.iter_content.return_value = [b"<html><main>" + b"valid source " * 20 + b"</main></html>"]
                    response.raise_for_status.return_value = None
                    fetcher.session.get = Mock(return_value=response)
                    self.assertIn("valid source", fetcher.get(url))
                    self.assertEqual(fetcher.session.get.call_args.kwargs["headers"], {})
                    response.close.assert_called()

    def test_retry_and_304_responses_are_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"user_agent": "test", "allowed_hosts": ["www.callofduty.com"], "max_requests": 5, "timeout_seconds": 5}
            fetcher = Fetcher(config, Path(directory))
            retry = Mock(status_code=503, is_redirect=False, headers={})
            full = Mock(status_code=200, is_redirect=False, headers={})
            full.iter_content.return_value = [b"<html><main>" + b"valid source " * 20 + b"</main></html>"]
            full.raise_for_status.return_value = None
            fetcher.session.get = Mock(side_effect=[retry, full])
            with patch("codrss.fetch.time.sleep"):
                fetcher.get("https://www.callofduty.com/blog")
            retry.close.assert_called()
            full.close.assert_called()
            unchanged = Mock(status_code=304, is_redirect=False)
            fetcher.session.get = Mock(return_value=unchanged)
            self.assertIn("valid source", fetcher.get("https://www.callofduty.com/blog"))
            unchanged.close.assert_called()

    def test_html_robots_and_credential_source_urls_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"user_agent": "test", "robots_url": "https://www.callofduty.com/robots.txt", "allowed_hosts": ["www.callofduty.com"], "max_requests": 5, "timeout_seconds": 5}
            fetcher = Fetcher(config, Path(directory))
            with patch.object(fetcher, "get", return_value="<html><title>Access denied</title></html>"):
                with self.assertRaisesRegex(SourceError, "robots.txt returned HTML"):
                    fetcher.load_robots()
            with self.assertRaisesRegex(SourceError, "Rejected source URL"):
                fetcher.get("https://user:" + "pass@" + "www.callofduty.com/blog")

    def test_http_200_error_html_is_not_a_valid_representation(self):
        for body in ("<html><title>Access Denied</title>" + "x" * 150, "<html><title>Just a moment</title>" + "x" * 150):
            with self.assertRaises(SourceError):
                check_html(body, "https://www.callofduty.com/blog")

    def test_304_without_usable_prior_body_refetches(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"user_agent": "test", "allowed_hosts": ["www.callofduty.com"], "max_requests": 5, "timeout_seconds": 5}
            fetcher = Fetcher(config, Path(directory))
            empty = Mock(status_code=304, is_redirect=False)
            full = Mock(status_code=200, is_redirect=False, headers={})
            full.iter_content.return_value = [b"<html><main>" + b"valid source " * 20 + b"</main></html>"]
            full.raise_for_status.return_value = None
            fetcher.session.get = Mock(side_effect=[empty, full])
            result = fetcher.get("https://www.callofduty.com/blog")
            self.assertIn("valid source", result)
            self.assertEqual(fetcher.session.get.call_count, 2)


class TransactionTests(unittest.TestCase):
    def test_corrupt_section_history_recovers_last_complete_git_state(self):
        from codrss.events import new_state, update_state
        from codrss.render import render_public
        from test_events import CONFIG, NOW, news, patch as patch_article
        module = refresh_module()
        original = update_state(new_state(), [news(), patch_article()], NOW, CONFIG, initialize=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            shutil.copyfile(ROOT / "data/state.schema.json", root / "data/state.schema.json")
            state_path = root / "data/state.json"
            state_path.write_text(json.dumps(original), encoding="utf-8")
            render_public(original, dict(CONFIG, base_url="https://example.com/feeds/"), root / "public", NOW)
            def git(*args):
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            git("init", "-b", "main")
            git("config", "user.name", "sample")
            git("config", "user.email", "123+sample@users.noreply.github.com")
            git("add", "data/state.json")
            git("commit", "-m", "Valid durable baseline")
            broken = copy.deepcopy(original)
            broken["sections"] = {}
            state_path.write_text(json.dumps(broken), encoding="utf-8")
            git("add", "data/state.json")
            git("commit", "-m", "Corrupt section ledger fixture")
            with patch.object(module, "ROOT", root):
                recovered = module.load_state()
            self.assertEqual(recovered["events"], original["events"])
            self.assertEqual(recovered["sections"], original["sections"])
            self.assertEqual(recovered["feeds"], original["feeds"])

    def test_sitemap_fallback_deduplicates_english_urls_and_prioritizes_recent_paths(self):
        module = refresh_module()
        fetcher = Mock()
        fetcher.get.return_value = '''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://www.callofduty.com/patchnotes/2026/08/mw4-beta</loc><lastmod>2026-11-08</lastmod></url>
        <url><loc>https://www.callofduty.com/blog/2026/11/mw4-news</loc></url>
        <url><loc>https://www.callofduty.com/en/blog/2026/11/mw4-news.html?utm_source=listing</loc></url>
        <url><loc>https://www.callofduty.com/th/patchnotes/2026/11/mw4-patch</loc></url>
        <url><loc>https://example.invalid/patchnotes/2026/11/mw4-patch</loc></url>
        <url><loc>https://www.callofduty.com/blog/2026/11/unrelated</loc></url>
        </urlset>'''
        config = {"sitemap_url": "https://www.callofduty.com/sitemap.xml", "max_sitemaps": 3, "max_candidates": 10, "allowed_hosts": ["www.callofduty.com"], "product_aliases": {"mw4": ["mw4"]}}
        result = module.sitemap_candidates(fetcher, config)
        self.assertEqual([card["url"] for card in result], ["https://www.callofduty.com/blog/2026/11/mw4-news", "https://www.callofduty.com/patchnotes/2026/08/mw4-beta"])
        self.assertTrue(all(not card.get("date") and not card.get("source_date") for card in result))

    def test_selector_failure_reaches_verified_sitemap_fallback(self):
        module = refresh_module()
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        url = "https://www.callofduty.com/patchnotes/2026/11/current"
        config = {"discovery_sources": ["https://www.callofduty.com/patchnotes"], "allowed_hosts": ["www.callofduty.com"], "active_overlap_days": 14, "initial_days": 30, "catchup_days": 7, "max_candidates": 10}
        state = {"initialized": True, "articles": {}, "collection": {}}
        article = {"kind": "patch", "source_date": "2026-11-07", "sections": [{"source_date": "2026-11-07"}]}
        with patch.object(module, "parse_index", side_effect=module.SourceParseError("Card layout changed")), patch.object(module, "sitemap_candidates", return_value=[{"url": url, "title": ""}]) as fallback, patch.object(module, "parse_article", return_value=article):
            articles, _ = module.collect(state, config, now, Mock())
        fallback.assert_called_once()
        self.assertEqual(articles, [article])

    def test_known_patch_on_blog_path_is_refetched_between_catchup_scans(self):
        module = refresh_module()
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        url = "https://www.callofduty.com/blog/2026/10/mw4-hotfix"
        config = {"discovery_sources": ["https://www.callofduty.com/patchnotes"], "allowed_hosts": ["www.callofduty.com"], "active_overlap_days": 14, "initial_days": 30, "catchup_days": 7, "max_candidates": 10}
        state = {"initialized": True, "presentation_version": module.PRESENTATION_VERSION, "articles": {url: {"kind": "patch"}}, "collection": {"patch_pages": {url: "2026-11-07T00:00:00+00:00"}, "last_catchup": now.isoformat()}}
        article = {"kind": "patch", "source_date": "2026-08-01", "sections": [{"source_date": "2026-11-07"}]}
        with patch.object(module, "parse_index", return_value=[{"url": url, "title": "MW4 hotfix", "date": "2026-08-01"}]), patch.object(module, "parse_article", return_value=article) as parser:
            articles, collection = module.collect(state, config, now, Mock())
        parser.assert_called_once()
        self.assertEqual(articles, [article])
        self.assertEqual(collection["patch_pages"][url], now.isoformat())

    def test_current_old_publication_is_monitored_and_retired_page_does_not_restart(self):
        module = refresh_module()
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        current = "https://www.callofduty.com/patchnotes/2026/08/current"
        retired = "https://www.callofduty.com/patchnotes/2026/09/retired"
        state = {"initialized": True, "articles": {}, "collection": {"patch_pages": {retired: "2026-10-01T00:00:00+00:00"}, "last_catchup": now.isoformat()}}
        config = {"discovery_sources": ["https://www.callofduty.com/patchnotes"], "allowed_hosts": ["www.callofduty.com"], "active_overlap_days": 14, "initial_days": 30, "catchup_days": 7, "max_candidates": 10}
        fetcher = Mock()
        cards = [{"url": current, "title": "Current patch", "date": "2026-08-01"}]
        article = {"kind": "patch", "source_date": "2026-08-01", "sections": [{"source_date": "2026-11-07"}]}
        with patch.object(module, "parse_index", return_value=cards), patch.object(module, "parse_article", return_value=article) as parser:
            _, collection = module.collect(state, config, now, fetcher)
            parser.assert_called_once()
            self.assertIn(current, collection["patch_pages"])
            self.assertEqual(collection["patch_pages"][retired], state["collection"]["patch_pages"][retired])
        # A broad news index still linking a retired page must not restart it.
        config["discovery_sources"] = ["https://www.callofduty.com/blog/modernwarfare4"]
        cards = [{"url": retired, "title": "Old patch still linked", "date": "2026-11-01"}]
        with patch.object(module, "parse_index", return_value=cards), patch.object(module, "parse_article") as parser:
            with self.assertRaisesRegex(SourceError, "No current"):
                module.collect(state, config, now, fetcher)
            parser.assert_not_called()

    def test_partial_source_outage_does_not_replace_either_feed(self):
        module = refresh_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config/feeds.json").write_text(json.dumps({"base_url": "https://example.com/project/", "user_agent": "test"}), encoding="utf-8")
            (root / "public/feeds").mkdir(parents=True)
            originals = {}
            for name in ("modern-warfare-4.xml", "patch-updates.xml"):
                path = root / "public/feeds" / name
                path.write_bytes(b"last valid feed: " + name.encode())
                originals[path] = path.read_bytes()
            with patch.object(module, "ROOT", root), patch.object(module, "load_state", return_value={"initialized": True}), patch.object(module, "collect", side_effect=SourceError("Second source unavailable after first source succeeded")):
                with self.assertRaises(SourceError):
                    module.refresh()
            self.assertTrue(all(path.read_bytes() == data for path, data in originals.items()))

    def test_missing_or_corrupt_established_state_never_initializes(self):
        module = refresh_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "public").mkdir()
            (root / "public/manifest.json").write_text("{}")
            with patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "Missing/corrupt"):
                    module.load_state()
                with self.assertRaisesRegex(ValueError, "Refusing"):
                    module.load_state(initialize=True)
                (root / "data").mkdir()
                (root / "data/state.json").write_text("not json")
                with self.assertRaisesRegex(ValueError, "Missing/corrupt"):
                    module.load_state()


if __name__ == "__main__":
    unittest.main()
