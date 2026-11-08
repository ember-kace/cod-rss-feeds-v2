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


if __name__ == "__main__":
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("persist.py operates only in a disposable GitHub Actions checkout; use refresh.py locally")
    pending = None
    for attempt in range(3):
        command = [sys.executable, "scripts/refresh.py"]
        if pending:
            command.extend(["--pending-state", pending])
        run(*command)
        run(sys.executable, "scripts/validate.py")
        run("git", "add", "--", *TRACKED)
        if run("git", "diff", "--cached", "--quiet", check=False).returncode == 0:
            break  # Workflow still uploads/deploys: repairs committed pending output.
        run("git", "commit", "-m", "Refresh validated RSS feeds and durable state")
        if run("git", "push", "origin", "HEAD:main", check=False).returncode == 0:
            break
        if attempt == 2:
            raise SystemExit("Branch moved repeatedly; pending valid events remain in this run's commit; retry workflow")
        if not pending:
            handle, pending = tempfile.mkstemp(prefix="cod-rss-pending-", suffix=".json")
            os.close(handle)
        Path(pending).write_bytes((ROOT / "data/state.json").read_bytes())
        # Dedicated disposable Actions checkout only. Preserve remote changes and
        # re-collect against latest identities rather than force pushing/rebasing state.
        run("git", "fetch", "origin", "main")
        run("git", "reset", "--hard", "origin/main")
    else:
        raise SystemExit("Could not persist durable state")
    if pending:
        Path(pending).unlink(missing_ok=True)
