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

    def test_same_day_identical_labels_do_not_steal_distinct_real_anchors(self):
        baseline = patch()
        first = update_state(new_state(), [baseline], NOW, CONFIG, initialize=True)
        updated = copy.deepcopy(baseline)
        newest = copy.deepcopy(updated["sections"][0])
        newest.update(key="nov8-evening", anchor="nov8-evening")
        # Newest-first publisher order exposes aliasing before the old section
        # has been visited; both genuine headings have exactly the same label.
        updated["sections"].insert(0, newest)
        second = update_state(first, [updated], LATER, CONFIG)
        self.assertEqual(len(second["feeds"]["patch"]["ids"]), 2)
        self.assertEqual(len(second["sections"]), 2)
        anchors = {event["anchor"] for event in second["events"].values()}
        self.assertEqual(anchors, {"nov8", "nov8-evening"})
        combined = merge_pending_state(first, second, CONFIG)
        self.assertEqual(len(combined["sections"]), 2)
        third = update_state(combined, [updated], "2026-11-08T18:30:00+00:00", CONFIG)
        self.assertEqual(combined["events"], third["events"])

    def test_corrupt_section_history_cannot_recreate_published_event_dates(self):
        baseline = update_state(new_state(), [patch()], NOW, CONFIG, initialize=True)
        for damage in ("sections", "digests", "guid", "feed"):
            with self.subTest(damage=damage):
                corrupt = copy.deepcopy(baseline)
                identity = next(iter(corrupt["events"]))
                if damage == "sections":
                    corrupt["sections"] = {}
                elif damage == "digests":
                    next(iter(corrupt["sections"].values()))["digests"] = []
                elif damage == "guid":
                    corrupt["events"][identity]["guid"] = "urn:unexpected:event"
                else:
                    corrupt["feeds"]["news"]["ids"] = [identity]
                with self.assertRaises(ValueError):
                    update_state(corrupt, [patch()], LATER, CONFIG)
        self.assertEqual(next(iter(baseline["events"].values()))["first_observed"], NOW)

    def test_preexisting_future_event_dates_fail_against_collection_clock(self):
        baseline = update_state(new_state(), [patch()], NOW, CONFIG, initialize=True)
        for field in ("event_date", "source_date", "first_observed"):
            with self.subTest(field=field):
                corrupt = copy.deepcopy(baseline)
                next(iter(corrupt["events"].values()))[field] = "2028-01-01T00:00:00+00:00"
                with self.assertRaisesRegex(ValueError, "future date"):
                    update_state(corrupt, [], LATER, CONFIG)

    def test_legacy_date_heading_migration_keeps_published_guids_and_real_revisions(self):
        from codrss.parse import parse_article
        from test_parse import PATCH_BODY, PATCH_URL, article

        parsed = parse_article(article(PATCH_BODY), PATCH_URL)
        legacy = copy.deepcopy(parsed)
        for section in legacy["sections"]:
            section["digest"] = section.pop("legacy_digest")
        baseline = update_state(new_state(), [legacy], NOW, CONFIG, initialize=True)
        baseline["parser_version"] = 1
        for section in baseline["sections"].values():
            section["parser_version"] = 1
        migrated = update_state(json.loads(json.dumps(baseline)), [parsed], LATER, CONFIG)
        self.assertEqual(baseline["events"], migrated["events"])
        self.assertEqual(baseline["feeds"], migrated["feeds"])
        self.assertEqual(migrated["parser_version"], 2)
        for section in migrated["sections"].values():
            self.assertEqual(len(section["digests"]), 2)
            self.assertIn(section["digest"], section["digest_aliases"])
        reformatted = parse_article(article(PATCH_BODY.replace("November 7, 2026", "SATURDAY NOV 7, 2026")), PATCH_URL)
        cosmetic = update_state(migrated, [reformatted], "2026-11-08T18:30:00+00:00", CONFIG)
        self.assertEqual(migrated["events"], cosmetic["events"])
        self.assertEqual(migrated["feeds"], cosmetic["feeds"])
        changed = parse_article(article(PATCH_BODY.replace("+10.5%", "+11.5%")), PATCH_URL)
        revised = update_state(cosmetic, [changed], "2026-11-08T18:45:00+00:00", CONFIG)
        self.assertEqual(len(revised["events"]), len(baseline["events"]) + 1)
        revision = next(event for event in revised["events"].values() if event["revision"])
        self.assertEqual(revision["event_date"], "2026-11-08T18:45:00+00:00")
        repeated = update_state(revised, [changed], "2026-11-08T19:00:00+00:00", CONFIG)
        self.assertEqual(revised["events"], repeated["events"])
        reverted = update_state(repeated, [parsed], "2026-11-08T19:15:00+00:00", CONFIG)
        self.assertEqual(repeated["events"], reverted["events"])
        # A genuine body edit made before the first migration is still detected;
        # a legacy label hash is not blindly treated as an unchanged baseline.
        immediate_revision = update_state(baseline, [changed], LATER, CONFIG)
        self.assertEqual(len(immediate_revision["events"]), len(baseline["events"]) + 1)

    def test_anchorless_date_spelling_migration_keeps_update_qualifiers_distinct(self):
        from codrss.parse import parse_article
        from test_parse import PATCH_URL, article

        body = '''<h2>November 7, 2026 — Morning hotfix</h2><p>Resolved a stability issue during morning multiplayer matches.</p>
        <h2>November 7, 2026 — Evening hotfix</h2><p>Corrected a distinct weapon damage issue in the evening.</p>'''
        parsed = parse_article(article(body), PATCH_URL)
        baseline = update_state(new_state(), [parsed], NOW, CONFIG, initialize=True)
        changed = parse_article(article(body.replace("November 7, 2026", "SATURDAY NOV7, 2026")), PATCH_URL)
        after = update_state(baseline, [changed], LATER, CONFIG)
        self.assertEqual(baseline["events"], after["events"])
        self.assertEqual(baseline["feeds"], after["feeds"])
        self.assertEqual(len(after["sections"]), 2)
        self.assertTrue(all(section["aliases"] for section in after["sections"].values()))
        plain = "<h2>November 7, 2026</h2><p>Resolved stability issues in this one dated update.</p>"
        first = update_state(new_state(), [parse_article(article(plain), PATCH_URL)], NOW, CONFIG, initialize=True)
        same = update_state(first, [parse_article(article(plain.replace("November 7, 2026", "NOV7, 2026")), PATCH_URL)], LATER, CONFIG)
        self.assertEqual(first["events"], same["events"])
        self.assertEqual(first["feeds"], same["feeds"])

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
