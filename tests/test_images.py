"""Publisher sharing artwork must use the real CDN image, never placeholders."""

import unittest
import sys
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codrss.parse import parse_article
from codrss.images import verified_image_type
from test_parse import PATCH_BODY, PATCH_URL, article


class ArticleImageTests(unittest.TestCase):
    def test_social_image_keeps_publisher_cdn_url_instead_of_resolved_header(self):
        image = "https://imgs.callofduty.com/content/dam/season-1-TOUT.jpg"
        fixture = article(PATCH_BODY, extra=f'<meta property="og:image" content="{image}">')
        parsed = parse_article(fixture, PATCH_URL)
        self.assertEqual(parsed["image"], image)
        self.assertNotIn("image_type", parsed)  # Never invent a MIME type from .jpg.

    def test_lazy_header_uses_real_data_src_instead_of_placeholder(self):
        fixture = article(PATCH_BODY).replace('src="/content/dam/hero.jpg"', 'src="/images/placeholder.gif" data-src="https://imgs.callofduty.com/content/dam/actual.webp"')
        parsed = parse_article(fixture, PATCH_URL)
        self.assertEqual(parsed["image"], "https://imgs.callofduty.com/content/dam/actual.webp")

    def test_bad_social_metadata_falls_back_to_article_specific_image(self):
        for value in ("", "javascript:alert(1)", "data:image/png;base64,eA==", "https://imgs.callofduty.com/images/placeholder.png"):
            with self.subTest(value=value):
                fixture = article(PATCH_BODY, extra=f'<meta property="og:image" content="{value}">')
                self.assertEqual(parse_article(fixture, PATCH_URL)["image"], "https://www.callofduty.com/content/dam/hero.jpg")

    def test_absent_image_does_not_turn_article_url_into_an_image(self):
        fixture = article(PATCH_BODY).replace('<div class="feature-img"><img src="/content/dam/hero.jpg"></div>', "")
        self.assertIsNone(parse_article(fixture, PATCH_URL)["image"])

    def test_patch_display_summary_uses_deduplicated_changes_without_changing_digest(self):
        fixture = article(PATCH_BODY.replace("<h2>GLOBAL</h2>", "<h2>GLOBAL</h2><ul><li>Resolved the first bug</li><li>Updated weapons:<ul><li>Damage changed by +2.5%.</li></ul></li></ul>"))
        section = parse_article(fixture, PATCH_URL)["sections"][0]
        self.assertEqual(section["summary"], "Resolved the first bug. Updated weapons: Damage changed by +2.5%.")
        import hashlib
        self.assertEqual(section["digest"], hashlib.sha256(section["semantic"].encode()).hexdigest())


class VerifiedImageTypeTests(unittest.TestCase):
    def client(self, content_type="application/octet-stream", status=200):
        client = MagicMock()
        client.__enter__.return_value = client
        response = client.head.return_value
        response.is_redirect = False
        response.status_code = status
        response.headers = {"Content-Type": content_type}
        return client

    def test_octet_stream_jpeg_is_verified_from_bounded_binary_signature(self):
        client = self.client()
        prefix = client.get.return_value.__enter__.return_value
        prefix.status_code = 206
        prefix.iter_content.return_value = iter([b"\xff\xd8\xff\xe0" + b"x" * 124])
        with patch("codrss.images.requests.Session", return_value=client):
            self.assertEqual(verified_image_type("https://imgs.callofduty.com/asset"), "image/jpeg")
        self.assertEqual(client.get.call_args.kwargs["headers"]["Range"], "bytes=0-127")
        self.assertTrue(client.get.call_args.kwargs["stream"])

    def test_denied_or_html_image_never_gets_type_from_url_extension(self):
        client = self.client(status=403)
        with patch("codrss.images.requests.Session", return_value=client):
            self.assertIsNone(verified_image_type("https://imgs.callofduty.com/looks-like.jpg"))
        client.get.assert_not_called()

    def test_successful_verification_cache_avoids_another_request(self):
        client = self.client("image/webp")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "types.json"
            with patch("codrss.images.requests.Session", return_value=client):
                self.assertEqual(verified_image_type("https://imgs.callofduty.com/asset", path), "image/webp")
                self.assertEqual(verified_image_type("https://imgs.callofduty.com/asset", path), "image/webp")
            self.assertEqual(client.head.call_count, 1)


if __name__ == "__main__":
    unittest.main()
