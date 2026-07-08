import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import mem0
from memory_doctor import mem0_checks

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXPORT_DIR = FIXTURES / "mem0_export"


class TestM1GhostSuspects(unittest.TestCase):
    def test_deleted_but_present_flagged_med(self):
        items = mem0.scan(EXPORT_DIR)
        findings = mem0_checks.m1_ghost_suspects(items)
        hit = [f for f in findings if "deleted-but-present" in f.item_id]
        self.assertTrue(hit)
        self.assertEqual(hit[0].severity, "med")
        self.assertIn("deleted-but-still-exported", hit[0].summary)

    def test_export_visible_half_wording_present(self):
        items = mem0.scan(EXPORT_DIR)
        findings = mem0_checks.m1_ghost_suspects(items)
        hit = [f for f in findings if "deleted-but-present" in f.item_id][0]
        self.assertIn("live access to every backing store", hit.suggestion)

    def test_non_deleted_objects_not_flagged(self):
        items = mem0.scan(EXPORT_DIR)
        findings = mem0_checks.m1_ghost_suspects(items)
        flagged_ids = {f.item_id for f in findings}
        self.assertEqual(len(flagged_ids), 1)


class TestM2SubjectConflicts(unittest.TestCase):
    def test_conflicting_subject_values_flagged_low(self):
        items = mem0.scan(EXPORT_DIR)
        findings = mem0_checks.m2_subject_conflicts(items)
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, "low")
        self.assertIn("timezone", findings[0].summary)
        self.assertIn("possible conflicting values", findings[0].summary)

    def test_no_subject_field_in_export_emits_nothing(self):
        items = mem0.scan(FIXTURES / "mem0_export_no_subject")
        findings = mem0_checks.m2_subject_conflicts(items)
        self.assertEqual(findings, [])


class TestM3AttributionSmell(unittest.TestCase):
    def test_assistant_voice_memory_flagged_low(self):
        items = mem0.scan(EXPORT_DIR)
        findings = mem0_checks.m3_attribution_smell(items)
        hit = [f for f in findings if "assistant-voice" in f.item_id]
        self.assertTrue(hit)
        self.assertEqual(hit[0].severity, "low")
        self.assertIn("attribution smell", hit[0].summary)

    def test_normal_memory_not_flagged(self):
        items = mem0.scan(EXPORT_DIR)
        findings = mem0_checks.m3_attribution_smell(items)
        flagged_ids = " ".join(f.item_id for f in findings)
        self.assertNotIn("healthy-control", flagged_ids)


class TestRunAllAndClaudeCodeIsolation(unittest.TestCase):
    def test_run_all_combines_all_three_rules(self):
        items = mem0.scan(EXPORT_DIR)
        findings = mem0_checks.run_all(items)
        rules = {f.rule for f in findings}
        self.assertEqual(rules, {"M1", "M2", "M3"})

    def test_claude_code_items_never_trigger_mem0_checks(self):
        from memory_doctor.adapters import claude_code
        cc_items = claude_code.scan(FIXTURES / "dotclaude")
        findings = mem0_checks.run_all(cc_items)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
