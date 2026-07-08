import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import claude_code
from memory_doctor import detectors

class TestDetectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "fixtures" / "dotclaude"
        cls.items = claude_code.scan(root)
        cls.findings = detectors.run_all(cls.items)
        cls.rules = [f.rule for f in cls.findings]

    def test_s1_truncation_detected(self):
        self.assertIn("S1", self.rules)

    def test_s2_dead_path_detected(self):
        s2 = [f for f in self.findings if f.rule == "S2"]
        self.assertTrue(any("nonexistent-dir-xyz" in f.evidence for f in s2))

    def test_s4_both_directions(self):
        s4 = " ".join(f.summary + f.evidence for f in self.findings if f.rule == "S4")
        self.assertIn("ghost.md", s4)   # linked but missing
        self.assertIn("e2.md", s4)      # exists but unlisted

    def test_s6_relative_date(self):
        s6 = [f for f in self.findings if f.rule == "S6"]
        self.assertTrue(any("relative date" in f.summary for f in s6))

    def test_s5_bloat_reported(self):
        self.assertIn("S5", self.rules)

if __name__ == "__main__":
    unittest.main()
