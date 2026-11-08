"""Small synthetic publisher-shaped fixtures; never used as live feed data."""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codrss.parse import SourceParseError, canonical_url, parse_article, parse_date, parse_index


BASE = "https://www.callofduty.com"
PATCH_URL = BASE + "/patchnotes/2026/11/call-of-duty-modern-warfare-4-season-1-patch-notes"
NEWS_URL = BASE + "/blog/2026/11/modern-warfare-4-news"


def article(body, title="Modern Warfare 4 Patch Notes", products="MW4", extra="", url=PATCH_URL):
    return f'''<html><head>{extra}</head><body><nav>Get MW4</nav>
    <main id="main-content" class="article-layout-content">
    <div class="article-header-container"><h1 class="title">{title}</h1>
    <p class="dateline">November 07, 2026</p><p class="byline">by Infinity Ward</p>
    <div class="tags"><li class="game-tile">{products}</li><li class="tag-tile" data-value="news">News</li></div>
    <div class="feature-img"><img src="/content/dam/hero.jpg"></div></div>
    <div class="article-body-container"><div class="body-content">
    <div class="cta">Get MW4</div><div class="article-rich-text"><div class="cmp-text">{body}</div></div>
    </div></div></main></body></html>'''


PATCH_BODY = '''<h2><a id="nov7"></a>November 7, 2026</h2>
<h2>GLOBAL</h2><p>Resolved a stability issue during multiplayer matches.</p>
<h2>MULTIPLAYER</h2><h3>Weapons</h3><table><tr><th>Weapon</th><th>Damage</th><th>Range</th></tr>
<tr><td>M4</td><td>+10.5%</td><td>30.25 m</td></tr></table>
<h2><a id="addnov5"></a>November 5th, 2026</h2><p>Additional changes to maps and progression have been released.</p>
<h2><a id="nov5"></a>November 5, 2026</h2><p>Season launch adjustments to equipment and weapon balance are available.</p>'''


class ParserTests(unittest.TestCase):
    def test_product_categories_keep_card_order_and_empty_publisher_badges(self):
        html = '''<nav><ul class="game-list"><li class="game-tile warzone">WZ</li></ul></nav>
        <div class="card-inner"><h2 class="title"><a href="/blog/2026/11/mw4-news">MW4 news</a></h2>
        <div class="news-published" data-date="2026-11-05"></div>
        <ul class="game-list"><li class="game-tile cod-intel">COD INTEL</li>
        <li class="game-tile mw4">MW4</li><li class="game-tile code"></li></ul></div>'''
        cards = parse_index(html, "https://www.callofduty.com/blog")
        self.assertEqual(cards[0]["product_categories"], ["cod-intel", "Modern Warfare 4", "code"])

    def test_news_classification_ignores_global_navigation(self):
        body = "<p>The latest multiplayer maps and modes arrive in Modern Warfare 4 for Season 1.</p>"
        self.assertEqual(parse_article(article(body, "Modern Warfare 4 Season 1", url=NEWS_URL), NEWS_URL)["kind"], "news")
        old = article("<p>Black Ops 7 introduces another set of multiplayer maps and modes this week.</p>", "Black Ops 7 Season 06", "BO7")
        self.assertEqual(parse_article(old, BASE + "/blog/2026/11/black-ops-7-news")["kind"], "irrelevant")

    def test_mobile_is_excluded(self):
        mobile = article("<p>Call of Duty Mobile brings a Modern Warfare 4 preorder blueprint to mobile players.</p>", "Call of Duty Mobile bonus", "MW4 CODM")
        self.assertEqual(parse_article(mobile, BASE + "/blog/2026/11/call-of-duty-mobile-modern-warfare-4-bonus")["kind"], "irrelevant")

    def test_patch_precedence_and_article_local_author(self):
        parsed = parse_article(article(PATCH_BODY), PATCH_URL)
        self.assertEqual(parsed["kind"], "patch")
        self.assertEqual(parsed["author"], "Infinity Ward")
        self.assertEqual(parsed["image"], BASE + "/content/dam/hero.jpg")

    def test_old_warzone_era_is_excluded(self):
        old = article("<p>Warzone gameplay changes and stability fixes for the Black Ops 7 season are available.</p>", "Warzone Season 06 Patch Notes", "WZ")
        self.assertEqual(parse_article(old, BASE + "/patchnotes/2026/09/call-of-duty-black-ops-7-warzone-season-06-patch-notes")["kind"], "irrelevant")

    def test_canonical_aliases_tracking_and_fragments(self):
        one = canonical_url("http://callofduty.com/en/blog/2026/11/example.html?utm_source=feed#nov7")
        two = canonical_url(BASE + "/blog/2026/11/example/")
        self.assertEqual(one, two)
        with self.assertRaises(SourceParseError):
            canonical_url("javascript:alert(1)")
        with self.assertRaises(SourceParseError):
            canonical_url("https://unrelated.example/blog/2026/11/anything")

    def test_date_precision_and_year_derivation(self):
        self.assertEqual(parse_date("SATURDAY NOVEMBER 7", 2026), ("2026-11-07", "date"))
        self.assertEqual(parse_date("November 5th, 2026"), ("2026-11-05", "date"))
        self.assertEqual(parse_date("November 5"), (None, None))
        parsed = parse_article(article(PATCH_BODY.replace("November 7, 2026", "SATURDAY NOVEMBER 7")), PATCH_URL)
        self.assertEqual(parsed["sections"][0]["source_date"], "2026-11-07")

    def test_dated_boundaries_ignore_arbitrary_h2(self):
        sections = parse_article(article(PATCH_BODY), PATCH_URL)["sections"]
        self.assertEqual(len(sections), 3)
        self.assertIn("GLOBAL", sections[0]["semantic"])
        self.assertIn("MULTIPLAYER", sections[0]["semantic"])
        self.assertIn("M4 | +10.5% | 30.25 m", sections[0]["semantic"])
        self.assertNotEqual(sections[1]["key"], sections[2]["key"])
        self.assertEqual(sections[1]["source_date"], sections[2]["source_date"])

    def test_cosmetic_changes_preserve_digest_and_table_changes_do_not(self):
        baseline = parse_article(article(PATCH_BODY), PATCH_URL)["sections"][0]
        cosmetic = PATCH_BODY.replace("Resolved a stability", "Resolved <strong>a</strong>   stability").replace('<table>', '<table class="presentation" style="color:red">')
        same = parse_article(article(cosmetic), PATCH_URL)["sections"][0]
        numeric = parse_article(article(PATCH_BODY.replace("+10.5%", "+10.6%")), PATCH_URL)["sections"][0]
        self.assertEqual(baseline["digest"], same["digest"])
        self.assertNotEqual(baseline["digest"], numeric["digest"])

    def test_empty_ld_dates_use_visible_source_date_specific_byline(self):
        extra = '<script type="application/ld+json">{"@type":"BlogPosting","datePublished":"","description":"","author":{"name":"Call of Duty Staff"}}</script>'
        parsed = parse_article(article(PATCH_BODY, extra=extra), PATCH_URL)
        self.assertEqual(parsed["source_date"], "2026-11-07")
        self.assertEqual(parsed["precision"], "date")
        self.assertEqual(parsed["author"], "Infinity Ward")

    def test_json_ld_type_array_uses_populated_article_metadata(self):
        extra = '<script type="application/ld+json">{"@type":["NewsArticle","Article"],"datePublished":"2026-11-05T19:00:00Z","description":"Verified publisher summary."}</script>'
        parsed = parse_article(article(PATCH_BODY, extra=extra), PATCH_URL)
        self.assertEqual(parsed["source_date"], "2026-11-05T19:00:00+00:00")
        self.assertEqual(parsed["precision"], "datetime")

    def test_whitespace_before_heading_preserves_actual_same_day_anchors(self):
        body = '''<a id="morning"></a>\n<h2>November 7, 2026</h2><p>Resolved stability issues in the morning update.</p>
        <a id="evening"></a>\n<h2>November 7, 2026</h2><p>Corrected weapon damage in the evening update.</p>'''
        sections = parse_article(article(body), PATCH_URL)["sections"]
        self.assertEqual([section["anchor"] for section in sections], ["morning", "evening"])
        self.assertEqual([section["key"] for section in sections], ["anchor:morning", "anchor:evening"])

    def test_table_caption_weapon_change_is_semantic_and_markup_is_cosmetic(self):
        body = PATCH_BODY.replace("<table>", "<table><caption>M4 weapon changes</caption>")
        baseline = parse_article(article(body), PATCH_URL)["sections"][0]
        cosmetic = parse_article(article(body.replace("M4 weapon changes</caption>", "<strong>M4</strong> weapon   changes</caption>")), PATCH_URL)["sections"][0]
        changed = parse_article(article(body.replace("M4 weapon changes</caption>", "AK-47 weapon changes</caption>")), PATCH_URL)["sections"][0]
        self.assertIn("CAPTION: M4 weapon changes", baseline["semantic"])
        self.assertEqual(baseline["digest"], cosmetic["digest"])
        self.assertNotEqual(baseline["digest"], changed["digest"])

    def test_heading_that_mentions_release_date_does_not_split_update(self):
        body = PATCH_BODY.replace("<h2>GLOBAL</h2>", "<h2>GLOBAL</h2><h3>Weapons arriving November 6</h3><p>These weapon adjustments belong to the November 7 update.</p>")
        sections = parse_article(article(body), PATCH_URL)["sections"]
        self.assertEqual(len(sections), 3)
        self.assertIn("Weapons arriving November 6", sections[0]["semantic"])
        self.assertIn("M4 | +10.5% | 30.25 m", sections[0]["semantic"])

    def test_dated_heading_presentation_does_not_change_body_digest(self):
        original = parse_article(article(PATCH_BODY), PATCH_URL)["sections"][0]
        reformatted = parse_article(article(PATCH_BODY.replace("November 7, 2026", "SATURDAY NOV 7, 2026")), PATCH_URL)["sections"][0]
        self.assertEqual(original["digest"], reformatted["digest"])
        self.assertNotEqual(original["legacy_digest"], reformatted["legacy_digest"])
        self.assertIn("UPDATE DATE: 2026-11-07", original["semantic"])

    def test_whole_article_fallback(self):
        body = "<h2>GLOBAL</h2><p>Resolved several stability problems and updated multiplayer weapon balance for the current season.</p>"
        sections = parse_article(article(body), PATCH_URL)["sections"]
        self.assertEqual(sections[0]["key"], "whole-article")
        self.assertTrue(sections[0]["fallback"])

    def test_index_card_scoping_and_dedup(self):
        card = '''<div class="card-inner"><div class="game-list"><span class="game-tile">MW4</span></div>
        <div class="news-published" data-date="November 05, 2026"></div>
        <div class="title"><a href="/blog/2026/11/modern-warfare-4-news?utm_source=x">Season News</a></div>
        <img class="blog-image" data-src="/content/dam/news.jpg"></div>'''
        out = parse_index('<nav>Get MW4</nav>' + card + card, BASE + "/blog")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["products"], ["MW4"])
        self.assertEqual(out[0]["source_date"], "2026-11-05")
        self.assertEqual(out[0]["image"], BASE + "/content/dam/news.jpg")

    def test_layout_loss_and_error_html_fail(self):
        with self.assertRaises(SourceParseError):
            parse_index("<html><h1>News</h1></html>", BASE + "/blog")
        with self.assertRaises(SourceParseError):
            parse_article("<html><h1>Access Denied</h1></html>", PATCH_URL)


if __name__ == "__main__":
    unittest.main()
