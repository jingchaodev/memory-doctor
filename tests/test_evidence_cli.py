import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor import __main__ as mm
from memory_doctor import evidence as evidence_mod

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EVIDENCE_ROOT = FIXTURES / "dotclaude_evidence"
TRANSCRIPTS_FULL = FIXTURES / "transcripts_full"
TRANSCRIPTS_PARTIAL = FIXTURES / "transcripts_partial"
TRANSCRIPTS_COMPACTION = FIXTURES / "transcripts_compaction"
PROMPTTAP_ROOT = FIXTURES / "prompttap_evidence"
S8_ROOT = FIXTURES / "dotclaude_s8"


class TestEvidenceFlagCLI(unittest.TestCase):
    def test_evidence_flag_with_explicit_dir_adds_u1_findings(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.audit_main(["--evidence", str(TRANSCRIPTS_PARTIAL), str(EVIDENCE_ROOT)])
        text = out.getvalue()
        self.assertIn("U1", text)
        self.assertIn("usage-evidence", text)

    def test_evidence_flag_missing_source_warns_and_continues(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            mm.audit_main(["--evidence", str(FIXTURES / "does_not_exist_xyz"), str(EVIDENCE_ROOT)])
        self.assertIn("no usable capture source", err.getvalue())
        # the normal audit output must still print -- U1 is skipped, not fatal.
        self.assertIn("memory-doctor", out.getvalue())

    def test_bare_evidence_flag_defaults_to_auto_with_patched_defaults(self):
        # argparse's nargs="?" on --evidence means a value placed right after
        # it is consumed as that value (see the explicit-dir test above), so
        # exercising the bare/"auto" branch requires the root positional to
        # come FIRST and --evidence to have no following token.
        argv = [str(EVIDENCE_ROOT), "--evidence"]
        out = io.StringIO()
        with mock.patch.object(evidence_mod, "DEFAULT_PROMPTTAP_DIR", PROMPTTAP_ROOT), \
             mock.patch.object(evidence_mod, "DEFAULT_TRANSCRIPTS_DIR", TRANSCRIPTS_FULL):
            with redirect_stdout(out):
                mm.audit_main(argv)
        text = out.getvalue()
        self.assertIn("memory-doctor", text)


class TestNoEvidenceFlagUnchanged(unittest.TestCase):
    def test_default_run_has_no_u_tier_findings(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.audit_main([str(EVIDENCE_ROOT)])
        text = out.getvalue()
        self.assertNotIn("usage-evidence", text)
        self.assertNotIn(" U1 ", text)

    def test_default_run_output_identical_with_and_without_evidence_module_import(self):
        # importing memory_doctor.evidence must not change the default audit's
        # behavior at all -- it's dormant until --evidence is passed.
        out1 = io.StringIO()
        with redirect_stdout(out1):
            mm.audit_main([str(EVIDENCE_ROOT)])
        out2 = io.StringIO()
        with redirect_stdout(out2):
            mm.audit_main([str(EVIDENCE_ROOT)])
        self.assertEqual(out1.getvalue(), out2.getvalue())


class TestCompactionAuditSubcommand(unittest.TestCase):
    def test_reports_heavy_session_only(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.compaction_audit_main([str(TRANSCRIPTS_COMPACTION)])
        text = out.getvalue()
        self.assertIn("heavysession", text)
        self.assertNotIn("lightsession", text)

    def test_custom_threshold_flag(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.compaction_audit_main([str(TRANSCRIPTS_COMPACTION), "--threshold", "2"])
        text = out.getvalue()
        self.assertIn("heavysession", text)
        self.assertIn("lightsession", text)

    def test_no_sessions_found_message(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.compaction_audit_main([str(TRANSCRIPTS_FULL)])
        self.assertIn("no session found", out.getvalue())

    def test_dispatch_from_main(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["memory-doctor", "compaction-audit", str(TRANSCRIPTS_COMPACTION)]):
            with redirect_stdout(out):
                mm.main()
        self.assertIn("heavysession", out.getvalue())


class TestComplianceSubcommand(unittest.TestCase):
    def test_reports_rules_with_unknown_loaded_status_without_evidence(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.compliance_main([str(S8_ROOT)])
        text = out.getvalue()
        self.assertIn("requires --llm (future) or manual review", text)
        self.assertIn("unknown", text)

    def test_reports_loaded_status_with_evidence(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mm.compliance_main([str(S8_ROOT), "--evidence", str(TRANSCRIPTS_PARTIAL)])
        text = out.getvalue()
        self.assertIn("requires --llm (future) or manual review", text)

    def test_dispatch_from_main(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["memory-doctor", "compliance", str(S8_ROOT)]):
            with redirect_stdout(out):
                mm.main()
        self.assertIn("imperative rule", out.getvalue())

    def test_missing_evidence_source_warns_reports_unknown(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            mm.compliance_main([str(S8_ROOT), "--evidence", str(FIXTURES / "does_not_exist_xyz")])
        self.assertIn("no usable capture source", err.getvalue())
        self.assertIn("unknown", out.getvalue())


if __name__ == "__main__":
    unittest.main()
