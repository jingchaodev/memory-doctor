import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import claude_code
from memory_doctor import detectors

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "dotclaude_s9"


class TestS9Poisoning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = claude_code.scan(ROOT)
        cls.findings = detectors.s9_poisoning(cls.items)

    def _by(self, severity):
        return [f for f in self.findings if f.severity == severity]

    def test_control_token_in_claude_md_flagged_high(self):
        high = self._by("high")
        self.assertTrue(any("<|im_start|>" in f.evidence for f in high))
        self.assertTrue(any(f.item_id.endswith("CLAUDE.md") for f in high))

    def test_injection_phrase_in_memory_entry_flagged_med(self):
        med = self._by("med")
        self.assertTrue(any("injected.md" in f.item_id for f in med))
        self.assertTrue(any("instruction-injection" in f.summary for f in med))

    def test_staging_marker_flagged_low(self):
        low = self._by("low")
        self.assertTrue(any("staged.md" in f.item_id for f in low))

    def test_staging_marker_in_claude_md_also_flagged(self):
        # scope for control-tokens/scaffold markers is ALL items, including claude_md
        low = self._by("low")
        self.assertTrue(any(f.item_id.endswith("CLAUDE.md") for f in low))

    def test_fenced_example_not_flagged(self):
        joined_ids = " ".join(f.item_id for f in self.findings)
        self.assertNotIn("fenced_safe.md", joined_ids)

    def test_clean_entry_not_flagged(self):
        joined_ids = " ".join(f.item_id for f in self.findings)
        self.assertNotIn("/clean.md", joined_ids)

    def test_injection_pattern_not_checked_outside_memory_entry(self):
        # CLAUDE.md itself never gets an injection-shaped (med, "instruction-injection")
        # finding — that check is scoped to memory_entry only, imperatives are legitimate
        # in claude_md.
        med = self._by("med")
        self.assertFalse(any(f.item_id.endswith("CLAUDE.md") for f in med))


if __name__ == "__main__":
    unittest.main()
