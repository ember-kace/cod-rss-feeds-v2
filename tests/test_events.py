import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codrss.events import merge_pending_state, new_state, stable_hash, update_state, validate_state

NOW = "2026-11-08T18:00:00+00:00"
LATER = "2026-11-08T18:15:00+00:00"
CONFIG = {"initial_days": 30, "initial_limit": 20, "retention": 60}


def patch(content="Damage +10% | Range 25.5 m", key="nov8", anchor="nov8", label="November 8, 2026"):
    return {
        "url": "https://www.callofduty.com/patchnotes/2026/11/mw4-season-1", "title": "MW4 patch notes",
        "source_date": "2026-11-01", "precision": "date", "author": "Infinity Ward",
        "summary": "Official MW4 changes.", "game": "Modern Warfare 4", "categories": ["Patch Notes"], "kind": "patch",
        "sections": [{"key": key, "label": label, "source_date": "2026-11-08", "precision": "date", "anchor": anchor, "semantic": content, "digest": stable_hash(content), "summary": content}],
    }


def news(day="2026-11-08", suffix="one"):
    return {"url": "https://www.callofduty.com/blog/2026/11/mw4-" + suffix, "title": "MW4 season details", "source_date": day, "precision": "date", "summary": "New maps and modes.", "game": "Modern Warfare 4", "kind": "news"}


class EventTests(unittest.TestCase):
    def test_patch_presentation_repair_preserves_all_notification_identities(self):
        document = patch()
        before = update_state(new_state(), [news(), document], NOW, CONFIG, initialize=True)
        document.update(image="https://imgs.callofduty.com/repaired.jpg", image_type="image/jpeg")
        document["sections"][0]["summary"] = "A clearer excerpt of the same verified patch changes."
        after = update_state(before, [document], LATER, CONFIG)
        self.assertEqual(before["feeds"]["patch"]["ids"], after["feeds"]["patch"]["ids"])
        for identity in before["events"]:
            for key in ("guid", "event_date", "first_observed", "digest", "revision"):
                self.assertEqual(before["events"][identity][key], after["events"][identity][key])
        patch_event = after["events"][after["feeds"]["patch"]["ids"][0]]
        self.assertEqual(patch_event["image_type"], "image/jpeg")
        self.assertEqual(patch_event["summary"], document["sections"][0]["summary"])
    def test_explicit_initialization_and_corrupt_state(self):
        with self.assertRaises(ValueError):
            update_state(new_state(), [patch()], NOW, CONFIG)
        with self.assertRaises(ValueError):
            validate_state({})
        state = update_state(new_state(), [patch()], NOW, CONFIG, initialize=True)
        with self.assertRaises(ValueError):
            update_state(state, [patch()], LATER, CONFIG, initialize=True)

    def test_unchanged_state_reload_and_news_edits_keep_identity_dates(self):
        first = update_state(new_state(), [news(), patch()], NOW, CONFIG, initialize=True)
        second = update_state(json.loads(json.dumps(first)), [news(), patch()], LATER, CONFIG)
        self.assertEqual(first, second)
        changed = news()
        changed["summary"] = "An updated description."
        third = update_state(second, [changed], LATER, CONFIG)
        news_id = first["feeds"]["news"]["ids"][0]
        self.assertEqual(third["events"][news_id]["guid"], first["events"][news_id]["guid"])
        self.assertEqual(third["events"][news_id]["event_date"], first["events"][news_id]["event_date"])

    def test_new_sections_same_date_and_one_revision(self):
        first = update_state(new_state(), [patch()], NOW, CONFIG, initialize=True)
        two = patch()
        extra = copy.deepcopy(two["sections"][0])
        extra.update(key="nov8-evening", anchor="nov8-evening", label="November 8, 2026 — Evening hotfix")
        two["sections"].append(extra)
        second = update_state(first, [two], LATER, CONFIG)
        self.assertEqual(len(second["feeds"]["patch"]["ids"]), 2)
        dates = [second["events"][identity]["event_date"] for identity in second["feeds"]["patch"]["ids"]]
        self.assertIn(LATER, dates)
        changed = patch("Damage +11% | Range 25.5 m")
        third = update_state(second, [changed], "2026-11-08T18:30:00+00:00", CONFIG)
        revisions = [e for e in third["events"].values() if e["revision"]]
        self.assertEqual(len(revisions), 1)
        self.assertIn("Update detected", revisions[0]["title"])
        self.assertEqual(revisions[0]["source_date"], "2026-11-08")
        fourth = update_state(third, [changed], "2026-11-08T18:45:00+00:00", CONFIG)
        self.assertEqual(third, fourth)
        reverted = update_state(fourth, [patch()], "2026-11-08T19:00:00+00:00", CONFIG)
        self.assertEqual(len(reverted["events"]), len(third["events"]))

    def test_retention_preserves_ledger_and_does_not_replay_pruned_items(self):
        config = dict(CONFIG, initial_limit=1, retention=1)
        first = update_state(new_state(), [patch()], NOW, config, initialize=True)
        old_id = first["feeds"]["patch"]["ids"][0]
        second = update_state(first, [patch("Damage +12%")], LATER, config)
        self.assertEqual(len(second["feeds"]["patch"]["ids"]), 1)
        self.assertIn(old_id, second["events"])
        third = update_state(second, [patch()], "2026-11-08T18:30:00+00:00", config)
        self.assertEqual(second["feeds"], third["feeds"])
        self.assertEqual(len(third["sections"][next(iter(third["sections"]))]["digests"]), 2)

    def test_historical_baselines_window_and_missing_dates(self):
        old = patch()
        old["sections"][0]["source_date"] = "2026-09-01"
        state = update_state(new_state(), [old, news("2026-09-01")], NOW, CONFIG, initialize=True)
        self.assertFalse(state["events"])
        self.assertEqual(len(state["sections"]), 1)
        bad = news()
        del bad["source_date"]
        with self.assertRaises(ValueError):
            update_state(state, [bad], LATER, CONFIG)
        future = news("2028-01-01")
        with self.assertRaises(ValueError):
            update_state(state, [future], LATER, CONFIG)

    def test_parser_identity_alias_reconciliation(self):
        first = update_state(new_state(), [patch()], NOW, CONFIG, initialize=True)
        migrated = patch(key="normalized-new-key")
        second = update_state(first, [migrated], LATER, CONFIG)
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(first["feeds"], second["feeds"])
        changed = patch("Damage +15%", key="normalized-new-key")
        third = update_state(second, [changed], "2026-11-08T18:30:00+00:00", CONFIG)
        self.assertEqual(len(third["events"]), 2)
        revision = next(event for event in third["events"].values() if event["revision"])
        self.assertEqual(revision["section_key"], "nov8")

    def test_anchorless_source_sections_have_plain_official_url(self):
        article = patch(anchor=None)
        state = update_state(new_state(), [article], NOW, CONFIG, initialize=True)
        event = next(iter(state["events"].values()))
        self.assertEqual(event["source_url"], article["url"])

    def test_pending_push_race_preserves_event_detection_time_and_digest_history(self):
        baseline = update_state(new_state(), [patch(), news()], NOW, CONFIG, initialize=True)
        pending = update_state(baseline, [patch("Damage +20%")], LATER, CONFIG)
        pending_event = next(event for event in pending["events"].values() if event["revision"])
        latest = update_state(baseline, [news(suffix="two")], "2026-11-08T18:30:00+00:00", CONFIG)
        combined = merge_pending_state(latest, pending, CONFIG)
        self.assertEqual(combined["events"][pending_event["id"]], pending_event)
        self.assertEqual(len(combined["feeds"]["news"]["ids"]), 2)
        self.assertEqual(len(combined["feeds"]["patch"]["ids"]), 2)
        retry = update_state(combined, [patch("Damage +20%")], "2026-11-08T18:45:00+00:00", CONFIG)
        self.assertEqual(retry["events"], combined["events"])
        self.assertEqual(retry["feeds"], combined["feeds"])
        self.assertEqual(retry["events"][pending_event["id"]]["first_observed"], LATER)
        reverted = update_state(retry, [patch()], "2026-11-08T19:00:00+00:00", CONFIG)
        self.assertEqual(len(reverted["events"]), len(retry["events"]))
        tampered = copy.deepcopy(pending)
        tampered["events"][pending_event["id"]]["first_observed"] = "2026-11-08T18:31:00+00:00"
        with self.assertRaisesRegex(ValueError, "Conflicting immutable event"):
            merge_pending_state(combined, tampered, CONFIG)


if __name__ == "__main__":
    unittest.main()
