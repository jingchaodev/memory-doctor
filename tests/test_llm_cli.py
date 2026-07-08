import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor import __main__ as mm
from memory_doctor import llm

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "dotclaude_llm"


class _StubClient:
    """Stands in for LLMClient so no real network call ever happens here."""

    def __init__(self, *a, **kw):
        pass

    def complete(self, prompt, system=""):
        return "[]"


class TestLLMFlagMissingKey(unittest.TestCase):
    def test_missing_key_exits_2_with_clean_stderr(self):
        # Explicitly unset the key regardless of the host environment --
        # this path must never make a real API call.
        with mock.patch.dict("os.environ", {}, clear=True):
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                mm.audit_main(["--llm", str(ROOT)])
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("ANTHROPIC_API_KEY", err.getvalue())


class TestLLMFlagWithFakeClient(unittest.TestCase):
    def test_llm_findings_included_with_stubbed_client(self):
        with mock.patch.object(llm, "LLMClient", _StubClient):
            out = io.StringIO()
            with redirect_stdout(out):
                mm.audit_main(["--llm", str(ROOT)])
        # doesn't crash, produces normal audit output
        self.assertIn("memory-doctor", out.getvalue())

    def test_llm_max_entries_flag_accepted(self):
        with mock.patch.object(llm, "LLMClient", _StubClient):
            out = io.StringIO()
            with redirect_stdout(out):
                mm.audit_main(["--llm", "--llm-max-entries", "1", str(ROOT)])
        self.assertIn("memory-doctor", out.getvalue())


class TestNoLLMFlagUnchanged(unittest.TestCase):
    def test_default_run_has_no_l_tier_findings(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.audit_main([str(ROOT)])
        text = out.getvalue()
        self.assertNotIn("LLM-assisted", text)
        self.assertNotIn(" L1 ", text)
        self.assertNotIn(" L2 ", text)


if __name__ == "__main__":
    unittest.main()
