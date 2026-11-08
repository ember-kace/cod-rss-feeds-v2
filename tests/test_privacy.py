"""Prevent credentials and personal identifiers from returning to public files."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "scripts/check_privacy.py"
SPEC = importlib.util.spec_from_file_location("check_privacy", MODULE)
PRIVACY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRIVACY)


class PrivacyTextTests(unittest.TestCase):
    def test_recognized_credential_is_reported_without_exposing_value(self):
        value = "ghp_" + "A" * 36
        findings = PRIVACY.scan_text("access=" + value, "config/settings.json")
        self.assertEqual(findings, [{"kind": "github-token", "path": "config/settings.json", "line": 1}])
        self.assertNotIn(value, json.dumps(findings))

    def test_unexpected_personal_email_is_rejected_and_noreply_allowed(self):
        address = "reader@" + "work.example"
        self.assertEqual(PRIVACY.scan_text(address, "README.md")[0]["kind"], "personal-email")
        self.assertEqual(PRIVACY.scan_text("123+sample@users.noreply.github.com", "README.md"), [])
        self.assertEqual(PRIVACY.scan_text("`123+sample@users.noreply.github.com`", "AGENTS.md"), [])
        self.assertEqual(PRIVACY.scan_text("git config user.email '123+sample@users.noreply.github.com'", "README.md"), [])
        self.assertEqual(PRIVACY.scan_text("reader@example.com", "tests/fixture.txt"), [])
        self.assertTrue(PRIVACY.scan_text("reader@example.com", "public/index.html"))

    def test_personal_site_reference_is_rejected_without_adding_a_real_hostname(self):
        address = "https://" + "private-workspace.chatgpt" + ".site/feeds/news.xml"
        findings = PRIVACY.scan_text(address, "docs/reference.md")
        self.assertEqual(findings[0]["kind"], "personal-site-url")
        self.assertNotIn(address, json.dumps(findings))

    def test_public_provider_sources_and_documentation_are_allowed(self):
        text = "https://www.callofduty.com/blog https://yourbot.gg/docs/social-feeds https://docs.github.com/en/pages"
        self.assertEqual(PRIVACY.scan_text(text, "SOURCES.md"), [])

    def test_local_and_credential_files_are_never_publishable(self):
        for path in (".env", ".env.example", ".scratch/capture.html", ".venv/lib.py", ".cache/request.json", ".git/config", "private/id_ed25519", "local/credentials.json"):
            with self.subTest(path=path):
                self.assertTrue(PRIVACY.sensitive_path(path))
        for path in ("data/state.json", "config/feeds.json", "public/status.json"):
            self.assertFalse(PRIVACY.sensitive_path(path))

    def test_commit_identity_requires_public_handle_and_noreply(self):
        self.assertEqual(PRIVACY.check_commit_identity("sample", "123+sample@users.noreply.github.com", {"sample"}), [])
        findings = PRIVACY.check_commit_identity("Example Contributor", "reader@" + "work.example", {"sample"})
        self.assertEqual(findings, ["commit-author-name", "commit-author-email"])


class PrivacyRepositoryTests(unittest.TestCase):
    def test_reachable_commit_identity_is_checked_even_when_files_are_clean(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.git(root, "init", "-b", "main")
            self.git(root, "config", "user.name", "Example Contributor")
            self.git(root, "config", "user.email", "reader@" + "work.example")
            (root / "config").mkdir()
            (root / "config/feeds.json").write_text('{"repository":"sample/feeds"}\n', encoding="utf-8")
            self.git(root, "add", "config/feeds.json")
            self.git(root, "commit", "-m", "Fixture")
            findings = PRIVACY.audit_repository(root)
            kinds = {finding["kind"] for finding in findings}
            self.assertIn("commit-author-email", kinds)
            self.assertIn("commit-committer-name", kinds)
            self.git(root, "config", "user.name", "sample")
            self.git(root, "config", "user.email", "123+sample@users.noreply.github.com")
            self.git(root, "commit", "--amend", "--reset-author", "--no-edit")
            self.assertEqual(PRIVACY.audit_repository(root), [])

    @staticmethod
    def git(root, *args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
