"""Failure boundaries must preserve the complete last-good publication."""
import importlib.util
import json
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
