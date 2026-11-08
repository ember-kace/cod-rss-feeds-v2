"""Refresh latest durable state, fast-forward commit, retry a moved branch safely."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ["data/state.json", "public"]


def run(*args, check=True):
    return subprocess.run(args, cwd=ROOT, text=True, check=check)


def revision(ref):
    return subprocess.run(["git", "rev-parse", ref], cwd=ROOT, text=True,
                          capture_output=True, check=True).stdout.strip()


def persist():
    """Persist one validated snapshot, reconciling a concurrently advanced main."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("persist.py operates only in a disposable GitHub Actions checkout; use refresh.py locally")
    pending = None
    try:
        for attempt in range(3):
            command = [sys.executable, "scripts/refresh.py"]
            if pending:
                command.extend(["--pending-state", pending])
            run(*command)
            run(sys.executable, "scripts/validate.py")
            run("git", "add", "--", *TRACKED)
            # Publication to the public repository happens before Pages upload.
            # Check generated/newly staged files before creating any public commit.
            run(sys.executable, "scripts/check_privacy.py")
            changed = run("git", "diff", "--cached", "--quiet", check=False).returncode != 0
            if changed:
                run("git", "commit", "-m", "Refresh validated RSS feeds and durable state")
                # Also verify the final author/committer identity before pushing.
                run(sys.executable, "scripts/check_privacy.py")
                if run("git", "push", "origin", "HEAD:main", check=False).returncode == 0:
                    return
            else:
                # Even unchanged local output can be older than a remote state
                # committed while sources were being collected.
                run("git", "fetch", "origin", "main")
                if revision("HEAD") == revision("origin/main"):
                    return  # The workflow still compares/uploads pending output.
            if attempt == 2:
                raise SystemExit("Could not persist after three attempts; inspect the Git push/fetch errors and retry the workflow")
            if not pending:
                handle, pending = tempfile.mkstemp(prefix="cod-rss-pending-", suffix=".json")
                os.close(handle)
            Path(pending).write_bytes((ROOT / "data/state.json").read_bytes())
            # Dedicated disposable Actions checkout only. Preserve remote changes
            # and re-collect using their code and identities, never force pushing.
            run("git", "fetch", "origin", "main")
            run("git", "reset", "--hard", "origin/main")
            run(sys.executable, "scripts/check_privacy.py")
            run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q")
    finally:
        if pending:
            Path(pending).unlink(missing_ok=True)


if __name__ == "__main__":
    persist()
