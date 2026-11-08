"""Exercise public-state persistence against isolated real Git repositories."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("project_persist", ROOT / "scripts/persist.py")
PERSIST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PERSIST)

REFRESH = '''import argparse,json,os
from pathlib import Path
root=Path.cwd()
parser=argparse.ArgumentParser();parser.add_argument('--pending-state');args=parser.parse_args()
state=json.loads((root/'data/state.json').read_text())
control=json.loads(Path(os.environ['PERSIST_TEST_CONTROL']).read_text())
if args.pending_state:
    pending=json.loads(Path(args.pending_state).read_text())
    for key,value in pending['events'].items():state['events'].setdefault(key,value)
if control.get('event'):
    state['events'].setdefault(control['event'],control['observed'])
text=json.dumps(state,sort_keys=True)+'\\n'
(root/'data/state.json').write_text(text)
(root/'public/snapshot.json').write_text(text)
'''

VALIDATE = '''import json
from pathlib import Path
root=Path.cwd()
assert json.loads((root/'data/state.json').read_text())==json.loads((root/'public/snapshot.json').read_text())
'''

PRIVACY = '''import json,os,subprocess
from pathlib import Path
root=Path.cwd();control=json.loads(Path(os.environ['PERSIST_TEST_CONTROL']).read_text())
state=json.loads((root/'data/state.json').read_text())
if control.get('reject_generated') and state['events']:
    raise SystemExit('Generated fixture rejected before publication')
emails=subprocess.run(['git','log','--format=%ae%x00%ce'],check=True,capture_output=True,text=True).stdout.replace('\\n','\\0').split('\\0')
if any(email and not email.endswith('@users.noreply.github.com')for email in emails):
    raise SystemExit('Commit identity rejected before publication')
'''


class PersistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.origin, self.writer, self.producer = base / "origin.git", base / "writer", base / "producer"
        self.control = base / "control.json"
        self.control.write_text("{}")
        self.git(base, "init", "--bare", "--initial-branch=main", str(self.origin))
        self.git(base, "clone", str(self.origin), str(self.writer))
        self.identity(self.writer)
        for name in ("scripts", "data", "public", "tests"):
            (self.writer / name).mkdir()
        for name, body in (("refresh.py", REFRESH), ("validate.py", VALIDATE), ("check_privacy.py", PRIVACY)):
            (self.writer / "scripts" / name).write_text(body, encoding="utf-8")
        (self.writer / "tests/__init__.py").write_text("")
        (self.writer / "tests/test_fixture.py").write_text('''import json,unittest
from pathlib import Path
class LatestCodeTests(unittest.TestCase):
    def test_state_shape(self):
        self.assertIsInstance(json.loads(Path('data/state.json').read_text())['events'],dict)
''')
        self.write_state(self.writer, {})
        self.git(self.writer, "add", ".")
        self.git(self.writer, "commit", "-m", "Initial fixture")
        self.git(self.writer, "push", "origin", "main")
        self.initial = self.git(self.writer, "rev-parse", "HEAD")
        self.git(base, "clone", str(self.origin), str(self.producer))
        self.identity(self.producer)
        self.root_patch = patch.object(PERSIST, "ROOT", self.writer)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.environment = patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "PERSIST_TEST_CONTROL": str(self.control)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.quiet_run = patch.object(PERSIST, "run", side_effect=self.execute)
        self.quiet_run.start()
        self.addCleanup(self.quiet_run.stop)

    def execute(self, *args, check=True):
        return subprocess.run(args, cwd=self.writer, text=True, check=check, capture_output=True)

    @staticmethod
    def git(root, *args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    def identity(self, root):
        self.git(root, "config", "user.name", "sample")
        self.git(root, "config", "user.email", "123+sample@users.noreply.github.com")

    @staticmethod
    def write_state(root, events):
        text = json.dumps({"events": events}, sort_keys=True) + "\n"
        (root / "data/state.json").write_text(text)
        (root / "public/snapshot.json").write_text(text)

    def advance_remote(self):
        self.write_state(self.producer, {"remote": "2026-11-08T00:01:00+00:00"})
        self.git(self.producer, "add", "data", "public")
        self.git(self.producer, "commit", "-m", "Concurrent fixture update")
        self.git(self.producer, "push", "origin", "main")
        return self.git(self.producer, "rev-parse", "HEAD")

    def test_rejected_generated_data_is_never_committed_or_pushed(self):
        self.control.write_text(json.dumps({"event": "pending", "observed": "2026-11-08T00:00:00+00:00", "reject_generated": True}))
        with self.assertRaises(subprocess.CalledProcessError):
            PERSIST.persist()
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), self.initial)
        self.assertEqual(self.git(self.writer, "rev-parse", "HEAD"), self.initial)

    def test_rejected_final_commit_identity_is_never_pushed(self):
        self.control.write_text(json.dumps({"event": "pending", "observed": "2026-11-08T00:00:00+00:00"}))
        self.git(self.writer, "config", "user.email", "not-public@" + "example.invalid")
        with self.assertRaises(subprocess.CalledProcessError):
            PERSIST.persist()
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), self.initial)
        self.assertNotEqual(self.git(self.writer, "rev-parse", "HEAD"), self.initial)

    def test_unchanged_collection_reconciles_a_remote_state_advance(self):
        original = PERSIST.run
        advanced = False
        refreshed = 0
        def concurrent_run(*args, **kwargs):
            nonlocal advanced, refreshed
            result = original(*args, **kwargs)
            if len(args) > 1 and args[1] == "scripts/refresh.py":
                refreshed += 1
                if not advanced:
                    advanced = True
                    self.advance_remote()
            return result
        with patch.object(PERSIST, "run", side_effect=concurrent_run):
            PERSIST.persist()
        self.assertEqual(refreshed, 2)
        self.assertEqual(self.git(self.writer, "rev-parse", "HEAD"), self.git(self.origin, "rev-parse", "main"))
        self.assertEqual(json.loads((self.writer / "data/state.json").read_text())["events"], {"remote": "2026-11-08T00:01:00+00:00"})

    def test_push_collision_preserves_pending_observation_time(self):
        observation = "2026-11-08T00:00:00+00:00"
        self.control.write_text(json.dumps({"event": "pending", "observed": observation}))
        original = PERSIST.run
        advanced = False
        def concurrent_run(*args, **kwargs):
            nonlocal advanced
            if args[:3] == ("git", "push", "origin") and not advanced:
                advanced = True
                self.advance_remote()
                self.control.write_text(json.dumps({"event": "pending", "observed": "2026-11-08T01:00:00+00:00"}))
            return original(*args, **kwargs)
        with patch.object(PERSIST, "run", side_effect=concurrent_run):
            PERSIST.persist()
        events = json.loads((self.writer / "data/state.json").read_text())["events"]
        self.assertEqual(events["pending"], observation)
        self.assertIn("remote", events)
        self.assertEqual(self.git(self.writer, "rev-parse", "HEAD"), self.git(self.origin, "rev-parse", "main"))

    def test_new_remote_code_must_pass_its_tests_before_regeneration(self):
        original = PERSIST.run
        advanced = False
        refreshed = 0
        def concurrent_run(*args, **kwargs):
            nonlocal advanced, refreshed
            result = original(*args, **kwargs)
            if len(args) > 1 and args[1] == "scripts/refresh.py":
                refreshed += 1
                if not advanced:
                    advanced = True
                    self.advance_remote()
                    (self.producer / "tests/test_fixture.py").write_text("import unittest\nclass LatestCodeTests(unittest.TestCase):\n def test_blocked(self): self.fail('New remote code fails validation')\n")
                    self.git(self.producer, "add", "tests")
                    self.git(self.producer, "commit", "-m", "Failing concurrent fixture code")
                    self.git(self.producer, "push", "origin", "main")
            return result
        with patch.object(PERSIST, "run", side_effect=concurrent_run):
            with self.assertRaises(subprocess.CalledProcessError):
                PERSIST.persist()
        self.assertEqual(refreshed, 1)
        self.assertEqual(self.git(self.writer, "rev-parse", "HEAD"), self.git(self.origin, "rev-parse", "main"))


if __name__ == "__main__":
    unittest.main()
