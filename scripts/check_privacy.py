"""Fail publication when tracked content or commit identities expose private data.

This is a small project-specific guard, not a replacement for GitHub secret
scanning. Diagnostics identify locations and finding types, never matched values.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
NOREPLY = re.compile(r"(?:\d+\+)?[A-Za-z0-9\-\[\]]+@users\.noreply\.github\.com", re.I)
EMAIL = re.compile(r"(?<![A-Za-z0-9.!#$%&'*+/=?^_{|}~-])[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}(?![A-Za-z0-9.-])")
URL = re.compile(r"https?://[^\s<>\"'`]+", re.I)
PATTERNS = {
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "openai-key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{30,}\b"),
    "discord-webhook": re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+", re.I),
    "credential-url": re.compile(r"\b(?:https?|postgres(?:ql)?|mongodb(?:\+srv)?|mysql|redis)://[^/\s:@]+:[^/\s@]+@", re.I),
    "credential-assignment": re.compile(r"(?im)^\s*[\"']?(?:api[_-]?key|access[_-]?token|secret[_-]?key|client[_-]?secret|password|bot[_-]?token)[\"']?\s*[:=]\s*[\"']([^\"'\r\n]{8,})[\"']"),
}


def sensitive_path(path: str) -> bool:
    """The public repository must never track local state or credential files."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    lower = [part.casefold() for part in parts]
    if any(part in {".git", ".venv", ".cache", ".scratch", ".ssh"} for part in lower):
        return True
    name = lower[-1] if lower else ""
    return (name == ".env" or name.startswith(".env.")
            or name in {".npmrc", ".netrc", "hosts.yml", "credentials", "credentials.json",
                        "cod_rss_autonomous_codex_plan.txt"}
            or name.startswith(("id_rsa", "id_ed25519"))
            or name.endswith((".p12", ".pfx", ".key")))


def allowed_email(address: str, path: str = "") -> bool:
    if NOREPLY.fullmatch(address):
        return True
    # Only clearly synthetic fixtures can use example addresses. Production
    # sources/docs must use public handles and GitHub no-reply identities.
    normalized = path.replace("\\", "/")
    if not normalized.startswith("tests/"):
        return False
    domain = address.rsplit("@", 1)[-1].casefold()
    return domain in {"example.com", "example.net", "example.org"} or domain.endswith(".invalid")


def scan_text(text: str, path: str) -> list[dict]:
    findings = []
    normalized = text.replace("\\/", "/")

    def add(kind, offset):
        findings.append({"kind": kind, "path": path, "line": normalized.count("\n", 0, offset) + 1})

    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(normalized):
            add(kind, match.start())
    for match in EMAIL.finditer(normalized):
        if not allowed_email(match.group(), path):
            add("personal-email", match.start())
    for match in URL.finditer(normalized):
        try:
            host = urlsplit(match.group()).hostname or ""
        except ValueError:
            continue
        if host.casefold().endswith(".chatgpt.site"):
            add("personal-site-url", match.start())
    return findings


def check_commit_identity(name: str, email: str, allowed_names: set[str]) -> list[str]:
    kinds = []
    if name not in allowed_names:
        kinds.append("commit-author-name")
    if not NOREPLY.fullmatch(email):
        kinds.append("commit-author-email")
    return kinds


def _git(root: Path, *args) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode:
        raise RuntimeError("Git query failed during privacy verification")
    return result.stdout


def audit_repository(root: Path, ref: str = "HEAD") -> list[dict]:
    root = Path(root)
    config = json.loads((root / "config/feeds.json").read_text(encoding="utf-8"))
    owner = config["repository"].split("/", 1)[0]
    allowed_names = {owner, "github-actions[bot]"}
    findings = []
    for raw_path in _git(root, "ls-files", "-z").split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="replace")
        if sensitive_path(path):
            findings.append({"kind": "sensitive-tracked-path", "path": path})
            continue
        source = root / path
        if source.is_symlink():
            findings.append({"kind": "tracked-symlink", "path": path})
            continue
        if not source.is_file():
            findings.append({"kind": "missing-tracked-file", "path": path})
            continue
        if source.stat().st_size > 8 * 1024 * 1024:
            findings.append({"kind": "file-exceeds-scan-bound", "path": path})
            continue
        data = source.read_bytes()
        if b"\0" not in data:
            findings.extend(scan_text(data.decode("utf-8", errors="replace"), path))
    fields = _git(root, "log", "-z", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce", ref).split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 5:
        raise RuntimeError("Malformed commit metadata during privacy verification")
    for index in range(0, len(fields), 5):
        commit, author, author_email, committer, committer_email = [value.decode("utf-8", errors="replace") for value in fields[index:index + 5]]
        for role, name, email in (("author", author, author_email), ("committer", committer, committer_email)):
            for kind in check_commit_identity(name, email, allowed_names):
                findings.append({"kind": kind.replace("author", role), "path": "commit:" + commit[:12]})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ref", default="HEAD", help="Reachable commit identities to inspect")
    args = parser.parse_args()
    findings = audit_repository(args.root, args.ref)
    print(json.dumps({"privacy_check": "failed" if findings else "passed", "findings": findings}, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
