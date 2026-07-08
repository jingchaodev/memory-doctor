import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import claude_code, codex
from memory_doctor import detectors

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class TestNearCliffWarning(unittest.TestCase):
    def test_memory_index_near_cliff_flagged_med(self):
        items = claude_code.scan(FIXTURES / "dotclaude_nearcliff")
        idx = [it for it in items if it.kind == "memory_index"][0]
        self.assertEqual(idx.loaded_portion, 1.0)  # not truncated yet
        findings = detectors.s1_load_truncation(items)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule, "S1")
        self.assertEqual(f.severity, "med")
        self.assertIn("approaching the load cliff", f.summary)

    def test_agents_md_near_cliff_flagged_med(self):
        items = codex.scan(FIXTURES / "codex_nearcliff")
        it = items[0]
        self.assertEqual(it.loaded_portion, 1.0)  # not truncated yet
        findings = detectors.s1_load_truncation(items)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule, "S1")
        self.assertEqual(f.severity, "med")
        self.assertIn("approaching the load cliff", f.summary)

    def test_existing_over_cliff_behavior_unchanged(self):
        # the original dotclaude fixture's MEMORY.md is already past the cliff —
        # must still fire "high", not get reclassified by the new near-cliff path.
        items = claude_code.scan(FIXTURES / "dotclaude")
        findings = [f for f in detectors.s1_load_truncation(items) if f.rule == "S1"]
        self.assertTrue(any(f.severity == "high" for f in findings))

    def test_healthy_small_index_not_flagged(self):
        items = claude_code.scan(FIXTURES / "dotclaude_s7")
        findings = detectors.s1_load_truncation(items)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
