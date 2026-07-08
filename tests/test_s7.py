import os, sys, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import claude_code
from memory_doctor import detectors

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "dotclaude_s7"
CLAUDE_MD = ROOT / "CLAUDE.md"
NOTE_MD = ROOT / "projects" / "agentx" / "memory" / "note.md"

DAY = 86400


def _touch(p: Path, days_ago: float):
    t = time.time() - days_ago * DAY
    os.utime(p, (t, t))


class TestS7Neglect(unittest.TestCase):
    def test_stale_instruction_with_active_memory_fires(self):
        _touch(CLAUDE_MD, 100)   # instruction untouched for 100d (> 90d threshold)
        _touch(NOTE_MD, 1)       # memory touched 1d ago -> system actively used
        items = claude_code.scan(ROOT)
        findings = detectors.s7_neglect(items)
        self.assertTrue(any(f.rule == "S7" and f.item_id == str(CLAUDE_MD) for f in findings))

    def test_stale_instruction_with_inactive_memory_does_not_fire(self):
        _touch(CLAUDE_MD, 100)   # instruction stale
        _touch(NOTE_MD, 100)     # but memory ALSO not touched recently -> not "active"
        items = claude_code.scan(ROOT)
        findings = detectors.s7_neglect(items)
        self.assertEqual(findings, [])

    def test_fresh_instruction_does_not_fire(self):
        _touch(CLAUDE_MD, 1)    # instruction touched recently -> not neglected
        _touch(NOTE_MD, 1)
        items = claude_code.scan(ROOT)
        findings = detectors.s7_neglect(items)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
