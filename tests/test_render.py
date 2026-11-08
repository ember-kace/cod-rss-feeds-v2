import json
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codrss.events import new_state, update_state
from codrss.render import ATOM, CONTENT, DC, MEDIA, absolute_url, render_public, validate_public
from test_events import CONFIG, LATER, NOW, news, patch

SETTINGS = dict(CONFIG, base_url="https://example.github.io/cod-rss-feeds/", require_nonempty=True)


class RenderTests(unittest.TestCase):
    def state(self):
        article = news()
        article.update(title="MW4 — Maps & modes <preview>\x01", summary="Weapons +12.5% & movement <balance> — café.", image="https://www.callofduty.com/content/image.jpg", author="Infinity Ward")
        document = patch()
        document["image"] = "https://www.callofduty.com/content/patch.jpg"
        return update_state(new_state(), [article, document], NOW, SETTINGS, initialize=True)

    def test_independent_parser_namespaces_escaping_images_and_basepath(self):
        with tempfile.TemporaryDirectory() as temporary:
            render_public(self.state(), SETTINGS, temporary, NOW)
            result = validate_public(temporary, SETTINGS)
            self.assertEqual(result["news"]["count"], 1)
            self.assertEqual(result["patch"]["count"], 1)
            xml = Path(temporary, "feeds/modern-warfare-4.xml").read_bytes()
            root = ET.fromstring(xml)
            self.assertIn("café", xml.decode("utf-8"))
            item = root.find("./channel/item")
            self.assertEqual(item.findtext("title"), "MW4 — Maps & modes <preview>")
            self.assertIsNotNone(item.find(f"{{{CONTENT}}}encoded"))
            self.assertEqual(item.findtext(f"{{{DC}}}creator"), "Infinity Ward")
            self.assertEqual(item.find(f"{{{MEDIA}}}content").get("medium"), "image")
            self.assertEqual(root.find(f"./channel/{{{ATOM}}}link").get("href"), "https://example.github.io/cod-rss-feeds/feeds/modern-warfare-4.xml")
            self.assertEqual(absolute_url(SETTINGS["base_url"], "updates/x.html"), "https://example.github.io/cod-rss-feeds/updates/x.html")

    def test_repeat_identical_feed_and_manifest_bytes_and_permanent_pages(self):
        first = self.state()
        second = update_state(first, [news(), patch()], LATER, SETTINGS)
        # News text actually changes above; test deterministic rerender of the same state.
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            render_public(first, SETTINGS, one, NOW)
            render_public(first, SETTINGS, two, LATER)
            self.assertEqual({p.relative_to(one).as_posix(): p.read_bytes() for p in Path(one).rglob("*") if p.is_file()}, {p.relative_to(two).as_posix(): p.read_bytes() for p in Path(two).rglob("*") if p.is_file()})
        pruned = update_state(first, [patch("Damage +25%")], LATER, dict(SETTINGS, retention=1))
        with tempfile.TemporaryDirectory() as temporary:
            render_public(pruned, SETTINGS, temporary, LATER)
            self.assertEqual(len(list(Path(temporary, "updates").glob("*.html"))), 2)
            validate_public(temporary, SETTINGS)

    def test_reference_layout_cdata_short_patch_title_and_verified_media_type(self):
        state = self.state()
        event = state["events"][state["feeds"]["patch"]["ids"][0]]
        event.update(summary="Damage increased by 12.5%.", image="https://imgs.callofduty.com/patch.jpg", image_type="image/jpeg")
        with tempfile.TemporaryDirectory() as temporary:
            render_public(state, SETTINGS, temporary, NOW)
            xml = Path(temporary, "feeds/patch-updates.xml").read_bytes()
            item = ET.fromstring(xml).find("./channel/item")
            title = "MW4 patch update — November 8, 2026"
            self.assertEqual(item.findtext("title"), title)
            expected = f'<p>Damage increased by 12.5%.</p><p><img src="https://imgs.callofduty.com/patch.jpg" alt="{title}" /></p><p><a href="{event["source_url"]}">Read the official update</a></p>'
            self.assertEqual(item.findtext("description"), expected)
            self.assertEqual(item.findtext(f"{{{CONTENT}}}encoded"), expected)
            self.assertIn(b"<description><![CDATA[<p>", xml)
            self.assertIn(b"<content:encoded><![CDATA[<p>", xml)
            self.assertEqual(item.find(f"{{{MEDIA}}}content").get("type"), "image/jpeg")
            self.assertEqual([node.text for node in item.findall("category")], ["Modern Warfare 4", "Patch updates"])
            page = Path(temporary, "updates", event["id"] + ".html").read_text(encoding="utf-8")
            self.assertIn("Official source date:", page)
            self.assertIn('<meta property="og:image" content="https://imgs.callofduty.com/patch.jpg">', page)
            self.assertIn('<meta property="og:image:type" content="image/jpeg">', page)
            self.assertIn('<meta name="twitter:card" content="summary_large_image">', page)
            self.assertNotIn("precision", item.findtext("description"))

    def test_cdata_terminator_cannot_break_feed_or_inject_markup(self):
        state = self.state()
        event = state["events"][state["feeds"]["news"]["ids"][0]]
        event["summary"] = "A literal ]]> and <script>alert(1)</script> remain harmless text."
        with tempfile.TemporaryDirectory() as temporary:
            render_public(state, SETTINGS, temporary, NOW)
            validate_public(temporary, SETTINGS)
            root = ET.parse(Path(temporary, "feeds/modern-warfare-4.xml"))
            description = root.findtext("./channel/item/description")
            self.assertIn("&lt;script&gt;", description)
            self.assertNotIn("<script>", description)

    def test_manifest_detects_tampering_and_missing_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            render_public(self.state(), SETTINGS, temporary, NOW)
            page = next(Path(temporary, "updates").glob("*.html"))
            page.unlink()
            with self.assertRaises(ValueError):
                validate_public(temporary, SETTINGS)
        with tempfile.TemporaryDirectory() as temporary:
            render_public(self.state(), SETTINGS, temporary, NOW)
            Path(temporary, "index.html").write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_public(temporary, SETTINGS)

    def test_empty_feed_is_explicitly_refused(self):
        empty = update_state(new_state(), [], NOW, SETTINGS, initialize=True)
        with tempfile.TemporaryDirectory() as temporary:
            render_public(empty, SETTINGS, temporary, NOW)
            with self.assertRaises(ValueError):
                validate_public(temporary, SETTINGS)


if __name__ == "__main__":
    unittest.main()
