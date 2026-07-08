import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import claude_code
from memory_doctor import detectors

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "dotclaude_s8"


class TestS8RuleDensity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = claude_code.scan(ROOT)
        cls.findings = detectors.s8_rule_density(cls.items)

    def test_long_rule_flagged_low_with_quoted_prefix(self):
        low = [f for f in self.findings if f.severity == "low"]
        self.assertTrue(low, "expected at least one low-severity long-rule finding")
        self.assertTrue(any("NEVER ship a change" in f.summary for f in low))

    def test_multi_sentence_rule_flagged(self):
        low = [f for f in self.findings if f.severity == "low"]
        self.assertTrue(any("ALWAYS read the file first" in f.summary for f in low))

    def test_short_rule_not_flagged(self):
        joined = " ".join(f.summary for f in self.findings)
        self.assertNotIn("MUST keep replies short", joined)

    def test_file_aggregate_flagged_med_over_40_rules(self):
        med = [f for f in self.findings if f.severity == "med"]
        self.assertTrue(med, "expected a med-severity aggregate finding")
        self.assertIn("imperative rules", med[0].summary)

    def test_all_findings_are_s8(self):
        self.assertTrue(all(f.rule == "S8" for f in self.findings))


if __name__ == "__main__":
    unittest.main()
