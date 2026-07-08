import sys, unittest, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor import trace as tracemod

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class TestTrace(unittest.TestCase):
    def setUp(self):
        # isolate "home" so the test doesn't depend on the real machine's
        # ~/.claude/CLAUDE.md or ~/.claude/projects/* contents/mtimes.
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "CLAUDE.md").write_text("# fixture global\n")

        self.cwd = (FIXTURES / "trace_project" / "sub").resolve()
        encoded = tracemod.encode_cwd(self.cwd)
        mem_dir = self.home / ".claude" / "projects" / encoded / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "MEMORY.md").write_text("- a\n- b\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_global_and_ancestor_chain_loaded(self):
        out = tracemod.trace(self.cwd, home=self.home)
        self.assertIn(str(self.home / ".claude" / "CLAUDE.md"), out)
        self.assertIn(str(FIXTURES / "trace_project" / "CLAUDE.md"), out)

    def test_import_one_hop_loaded(self):
        out = tracemod.trace(self.cwd, home=self.home)
        self.assertIn("imported.md", out)

    def test_local_md_row_present(self):
        out = tracemod.trace(self.cwd, home=self.home)
        self.assertIn("CLAUDE.local.md", out)

    def test_auto_memory_row_with_percentage(self):
        out = tracemod.trace(self.cwd, home=self.home)
        self.assertIn("MEMORY.md", out)
        self.assertIn("loaded=100%", out)

    def test_child_claude_md_flagged_as_suspect_not_loaded(self):
        out = tracemod.trace(self.cwd, home=self.home)
        head, _, tail = out.partition("suspects")
        deeper_path = str(FIXTURES / "trace_project" / "sub" / "deeper" / "CLAUDE.md")
        self.assertNotIn(deeper_path, head)   # not in the loaded/ordered section
        self.assertIn(deeper_path, tail)      # surfaces only as a suspect

    def test_caveat_mentions_upstream_issue(self):
        out = tracemod.trace(self.cwd, home=self.home)
        self.assertIn("722", out)

    def test_encode_cwd_matches_observed_convention(self):
        self.assertEqual(tracemod.encode_cwd(Path("/root")), "-root")


if __name__ == "__main__":
    unittest.main()
