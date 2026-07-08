import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import codex
from memory_doctor import detectors

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class TestCodexAdapter(unittest.TestCase):
    def test_global_root_oversized_truncates(self):
        items = codex.scan(FIXTURES / ".codex")
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it.kind, "agents_md")
        self.assertEqual(it.agent, "codex-global")
        self.assertTrue(it.always_loaded)
        self.assertLess(it.loaded_portion, 1.0)
        self.assertIn("mtime", it.meta)
        self.assertIsInstance(it.meta["mtime"], float)

    def test_project_root_normal_size_not_truncated(self):
        items = codex.scan(FIXTURES / "codex_project")
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it.agent, "codex_project")  # dir name, not "codex-global"
        self.assertEqual(it.loaded_portion, 1.0)

    def test_missing_root_returns_empty(self):
        items = codex.scan(FIXTURES / "no-such-codex-root-xyz")
        self.assertEqual(items, [])

    def test_s1_fires_for_oversized_agents_md(self):
        items = codex.scan(FIXTURES / ".codex")
        findings = [f for f in detectors.s1_load_truncation(items) if f.rule == "S1"]
        self.assertEqual(len(findings), 1)
        self.assertIn("32KB", findings[0].summary)
        self.assertIn("codex-global", findings[0].summary)

    def test_s1_silent_for_small_agents_md(self):
        items = codex.scan(FIXTURES / "codex_project")
        findings = detectors.s1_load_truncation(items)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
