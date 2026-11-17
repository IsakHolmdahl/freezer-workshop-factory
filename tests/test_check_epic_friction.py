"""Unit tests for check-epic-friction.py."""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parents[1] / "skills" / "beads-orchestration" / "scripts"
spec = importlib.util.spec_from_file_location("friction", HERE / "check-epic-friction.py")
friction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(friction)


class CheckEpicFrictionTests(unittest.TestCase):
    def test_clean_or_already_curated_issues_do_not_trigger(self):
        issues = [
            {"id": "sf-1", "title": "Task 1", "labels": [],
             "created_at": "2026-10-14T10:00:00Z", "closed_at": "2026-10-14T10:10:00Z"},
            {"id": "sf-2", "title": "Fix: old", "labels": ["agent-failed", "user-interjection", "curated"],
             "created_at": "2026-10-14T10:00:00Z", "closed_at": "2026-10-14T10:10:00Z"},
            {"id": "sf-3", "title": "Fast friction", "labels": ["friction"],
             "created_at": "2026-10-14T10:00:00Z", "closed_at": "2026-10-14T10:00:30Z"},
        ]
        should, _ = friction.evaluate(issues, {"CURATE_MIN_FRICTION": "2"})
        self.assertFalse(should)

    def test_single_friction_triggers_by_default(self):
        issues = [
            {"id": "sf-3", "title": "Fast friction", "labels": ["friction"],
             "created_at": "2026-10-14T10:00:00Z", "closed_at": "2026-10-14T10:00:30Z"},
        ]
        should, summary = friction.evaluate(issues, {})
        self.assertTrue(should)
        self.assertIn("friction=1 [sf-3]", summary)

    def test_friction_in_notes_or_close_reason_triggers(self):
        issues = [
            {"id": "sf-8", "title": "Task 8", "labels": [],
             "close_reason": "Done. FRICTION: pnpm lockfile missing"},
        ]
        should, summary = friction.evaluate(issues, {})
        self.assertTrue(should)
        self.assertIn("friction=1 [sf-8]", summary)

    def test_single_user_interjection_always_triggers_by_default(self):
        issues = [
            {"id": "sf-epic", "title": "Epic", "labels": ["user-interjection", "friction"],
             "created_at": "2026-10-14T10:00:00Z", "updated_at": "2026-10-14T10:00:05Z"},
        ]
        should, summary = friction.evaluate(issues, {})
        self.assertTrue(should)
        self.assertIn("user_interjection=1 [sf-epic]", summary)

    def test_single_slow_friction_over_1_minute_triggers_even_when_min_friction_raised(self):
        issues = [
            {"id": "sf-4", "title": "Task 4", "labels": ["friction"],
             "created_at": "2026-10-14T10:00:00Z", "closed_at": "2026-10-14T10:01:05Z"},
        ]
        should, summary = friction.evaluate(issues, {"CURATE_MIN_FRICTION": "2"})
        self.assertTrue(should)
        self.assertIn("slow_friction>=60s [sf-4(65s)]", summary)
        # Tweaking CURATE_MAX_DURATION_SEC to 120s suppresses the 65s item when min_friction=2:
        should2, _ = friction.evaluate(issues, {"CURATE_MIN_FRICTION": "2", "CURATE_MAX_DURATION_SEC": "120"})
        self.assertFalse(should2)

    def test_failed_or_multiple_frictions_trigger(self):
        should, summary = friction.evaluate(
            [{"id": "sf-5", "title": "Task 5", "labels": ["agent-failed"]}], {}
        )
        self.assertTrue(should)
        self.assertIn("failed/rework=1 [sf-5]", summary)

        should2, summary2 = friction.evaluate(
            [{"id": "sf-6", "labels": ["friction"], "created_at": "2026-10-14T10:00:00Z", "closed_at": "2026-10-14T10:00:10Z"},
             {"id": "sf-7", "labels": ["friction"], "created_at": "2026-10-14T10:00:00Z", "closed_at": "2026-10-14T10:00:15Z"}],
            {},
        )
        self.assertTrue(should2)
        self.assertIn("friction=2 [sf-6,sf-7]", summary2)


if __name__ == "__main__":
    unittest.main()
