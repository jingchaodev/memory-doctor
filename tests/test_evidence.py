import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor import evidence
from memory_doctor.adapters import claude_code

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EVIDENCE_ROOT = FIXTURES / "dotclaude_evidence"
TRANSCRIPTS_FULL = FIXTURES / "transcripts_full"
TRANSCRIPTS_PARTIAL = FIXTURES / "transcripts_partial"
TRANSCRIPTS_COMPACTION = FIXTURES / "transcripts_compaction"
PROMPTTAP_ROOT = FIXTURES / "prompttap_evidence"

CLAUDE_SENTINEL = "This exact global CLAUDE dot md sentinel line should show up in evidence."
SEEN_SENTINEL = "This fixture memory entry line should be found verbatim inside evidence."
UNSEEN_SENTINEL = "This fixture memory entry line will never be found inside any evidence."
ONLY_SENTINEL = "This loneagent fixture line about scoping should never appear in evidence for testbot."


class TestFlattenStrings(unittest.TestCase):
    def test_flattens_nested_dict_list_str(self):
        obj = {"a": "x", "b": [{"c": "y"}, "z"], "d": {"e": {"f": "w"}}, "n": 5, "g": None}
        got = sorted(evidence._flatten_strings(obj))
        self.assertEqual(got, ["w", "x", "y", "z"])

    def test_bare_string(self):
        self.assertEqual(list(evidence._flatten_strings("solo")), ["solo"])

    def test_non_string_scalars_yield_nothing(self):
        self.assertEqual(list(evidence._flatten_strings(42)), [])
        self.assertEqual(list(evidence._flatten_strings(None)), [])


class TestFingerprint(unittest.TestCase):
    def test_skips_frontmatter_and_short_lines(self):
        text = "---\nname: x\ndescription: y\n---\n\nThis is a sufficiently long content line.\n"
        self.assertEqual(evidence.fingerprint(text), "This is a sufficiently long content line.")

    def test_no_frontmatter_skips_short_heading(self):
        text = "# Rules\n\n" + CLAUDE_SENTINEL + "\n"
        self.assertEqual(evidence.fingerprint(text), CLAUDE_SENTINEL)

    def test_all_lines_too_short_returns_none(self):
        self.assertIsNone(evidence.fingerprint("# hi\n\nok\nno\n"))

    def test_empty_text_returns_none(self):
        self.assertIsNone(evidence.fingerprint(""))


class TestEvidenceWindowContains(unittest.TestCase):
    def setUp(self):
        self.win = evidence.EvidenceWindow("test", [
            (1000.0, "testbot", "hello " + SEEN_SENTINEL + " world"),
            (1001.0, None, "an agent-less record containing " + ONLY_SENTINEL),
        ])

    def test_unscoped_query_matches_any_record(self):
        self.assertTrue(self.win.contains(SEEN_SENTINEL))
        self.assertTrue(self.win.contains(ONLY_SENTINEL))

    def test_scoped_query_matches_same_agent(self):
        self.assertTrue(self.win.contains(SEEN_SENTINEL, agent="testbot"))

    def test_scoped_query_excludes_different_agent(self):
        self.assertFalse(self.win.contains(SEEN_SENTINEL, agent="loneagent"))

    def test_agentless_record_matches_any_scoped_query(self):
        # ONLY_SENTINEL's record carries agent=None -- it must count as a hit
        # for ANY scoped query (the deliberate false-"seen"-biased direction).
        self.assertTrue(self.win.contains(ONLY_SENTINEL, agent="loneagent"))
        self.assertTrue(self.win.contains(ONLY_SENTINEL, agent="testbot"))

    def test_empty_fingerprint_never_matches(self):
        self.assertFalse(self.win.contains(""))
        self.assertFalse(self.win.contains(None))

    def test_len_and_window_desc(self):
        self.assertEqual(len(self.win), 2)
        desc = self.win.window_desc()
        self.assertIn("2 captured requests", desc)
        self.assertIn("test", desc)

    def test_window_desc_empty(self):
        empty = evidence.EvidenceWindow("test", [])
        self.assertIn("0 captured requests", empty.window_desc())


class TestLoadTranscripts(unittest.TestCase):
    def test_loads_records_tagged_with_agent(self):
        win = evidence.load_transcripts(TRANSCRIPTS_FULL)
        self.assertIsNotNone(win)
        self.assertEqual(win.source, "transcripts")
        self.assertTrue(len(win) >= 2)
        self.assertTrue(win.contains(SEEN_SENTINEL, agent="testbot"))
        self.assertTrue(win.contains(CLAUDE_SENTINEL))  # unscoped

    def test_missing_dir_returns_none(self):
        self.assertIsNone(evidence.load_transcripts(FIXTURES / "does_not_exist_xyz"))

    def test_empty_dir_returns_none(self):
        empty = FIXTURES / "dotclaude_evidence" / "projects"  # no *.jsonl files here
        self.assertIsNone(evidence.load_transcripts(empty))

    def test_partial_fixture_missing_claude_sentinel(self):
        win = evidence.load_transcripts(TRANSCRIPTS_PARTIAL)
        self.assertIsNotNone(win)
        self.assertFalse(win.contains(CLAUDE_SENTINEL))
        self.assertTrue(win.contains(SEEN_SENTINEL))


class TestLoadPrompttap(unittest.TestCase):
    def test_loads_gz_body_records(self):
        win = evidence.load_prompttap(PROMPTTAP_ROOT)
        self.assertIsNotNone(win)
        self.assertEqual(win.source, "prompttap")
        self.assertEqual(len(win), 1)
        self.assertTrue(win.contains(CLAUDE_SENTINEL))
        self.assertTrue(win.contains(SEEN_SENTINEL))
        # prompttap records always carry agent=None -- any scoped query still hits.
        self.assertTrue(win.contains(SEEN_SENTINEL, agent="some-other-agent"))

    def test_accepts_bodies_subdir_directly(self):
        win = evidence.load_prompttap(PROMPTTAP_ROOT / "bodies")
        self.assertIsNotNone(win)

    def test_missing_dir_returns_none(self):
        self.assertIsNone(evidence.load_prompttap(FIXTURES / "does_not_exist_xyz"))

    def test_no_gz_files_returns_none(self):
        self.assertIsNone(evidence.load_prompttap(TRANSCRIPTS_FULL))


class TestDiscover(unittest.TestCase):
    def test_prompttap_preferred_over_transcripts(self):
        win = evidence.discover("auto", prompttap_dir=PROMPTTAP_ROOT,
                                 transcripts_dir=TRANSCRIPTS_FULL)
        self.assertEqual(win.source, "prompttap")

    def test_falls_back_to_transcripts_when_no_prompttap(self):
        win = evidence.discover("auto", prompttap_dir=FIXTURES / "does_not_exist_xyz",
                                 transcripts_dir=TRANSCRIPTS_FULL)
        self.assertEqual(win.source, "transcripts")

    def test_returns_none_when_neither_present(self):
        win = evidence.discover("auto", prompttap_dir=FIXTURES / "does_not_exist_xyz",
                                 transcripts_dir=FIXTURES / "also_missing_xyz")
        self.assertIsNone(win)

    def test_explicit_path_spec_tries_prompttap_then_transcripts(self):
        win = evidence.discover(str(PROMPTTAP_ROOT))
        self.assertEqual(win.source, "prompttap")
        win2 = evidence.discover(str(TRANSCRIPTS_FULL))
        self.assertEqual(win2.source, "transcripts")


class TestU1NeverLoaded(unittest.TestCase):
    def setUp(self):
        self.items = claude_code.scan(EVIDENCE_ROOT)


    def test_thin_window_gate_emits_single_summary(self):
        # verifier gate: spans under 24h must NOT produce per-entry findings
        win = evidence.load_transcripts(TRANSCRIPTS_FULL)
        findings = evidence.u1_never_loaded(self.items, win, min_window_hours=10**6)
        self.assertEqual(len(findings), 1)
        self.assertIn("too thin", findings[0].summary)

    def test_no_evidence_yields_nothing(self):
        self.assertEqual(evidence.u1_never_loaded(self.items, None), [])
        empty_win = evidence.EvidenceWindow("test", [])
        self.assertEqual(evidence.u1_never_loaded(self.items, empty_win), [])

    def test_full_window_flags_only_unseen_entries(self):
        win = evidence.load_transcripts(TRANSCRIPTS_FULL)
        findings = evidence.u1_never_loaded(self.items, win, min_window_hours=0)
        by_file = {Path(f.item_id).name: f for f in findings}
        # CLAUDE.md and seen.md were both present in this window -- no finding.
        self.assertNotIn("CLAUDE.md", by_file)
        self.assertNotIn("seen.md", by_file)
        # unseen.md (testbot) and only.md (loneagent) never appear -- both low.
        self.assertIn("unseen.md", by_file)
        self.assertIn("only.md", by_file)
        self.assertEqual(by_file["unseen.md"].severity, "low")
        self.assertEqual(by_file["only.md"].severity, "low")
        self.assertTrue(all(f.rule == "U1" for f in findings))

    def test_partial_window_flags_always_loaded_as_med(self):
        win = evidence.load_transcripts(TRANSCRIPTS_PARTIAL)
        findings = evidence.u1_never_loaded(self.items, win, min_window_hours=0)
        by_file = {Path(f.item_id).name: f for f in findings}
        # CLAUDE.md's sentinel is absent from this window -- always-loaded, so med.
        self.assertIn("CLAUDE.md", by_file)
        self.assertEqual(by_file["CLAUDE.md"].severity, "med")
        self.assertIn("wiring bug", by_file["CLAUDE.md"].summary)

    def test_agent_scoping_prevents_cross_agent_false_seen(self):
        # transcripts_partial contains ONLY_SENTINEL's text, but mistagged
        # under agent="testbot" -- only.md's real owner is "loneagent", so a
        # correctly-scoped check must still flag it as never seen for loneagent.
        win = evidence.load_transcripts(TRANSCRIPTS_PARTIAL)
        self.assertTrue(win.contains(ONLY_SENTINEL))  # present somewhere, unscoped
        self.assertFalse(win.contains(ONLY_SENTINEL, agent="loneagent"))  # not for its owner
        findings = evidence.u1_never_loaded(self.items, win, min_window_hours=0)
        by_file = {Path(f.item_id).name: f for f in findings}
        self.assertIn("only.md", by_file)
        self.assertEqual(by_file["only.md"].severity, "low")

    def test_window_description_included_in_summary(self):
        win = evidence.load_transcripts(TRANSCRIPTS_PARTIAL)
        findings = evidence.u1_never_loaded(self.items, win, min_window_hours=0)
        self.assertTrue(any("captured requests" in f.summary for f in findings))


class TestU2CompactionFrequency(unittest.TestCase):
    def test_heavy_session_flagged_light_session_not(self):
        findings = evidence.u2_compaction_frequency(TRANSCRIPTS_COMPACTION)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule, "U2")
        self.assertEqual(f.severity, "med")
        self.assertIn("heavysession", f.item_id)
        self.assertIn("22 compaction events", f.summary)
        self.assertIn("tokens dropped", f.summary)

    def test_custom_threshold(self):
        # lowering the threshold to 2 should now also catch lightsession
        findings = evidence.u2_compaction_frequency(TRANSCRIPTS_COMPACTION, threshold=2)
        sessions = {Path(f.item_id).parent.name for f in findings}
        self.assertIn("heavysession", sessions)
        self.assertIn("lightsession", sessions)

    def test_missing_dir_yields_nothing(self):
        findings = evidence.u2_compaction_frequency(FIXTURES / "does_not_exist_xyz")
        self.assertEqual(findings, [])

    def test_no_compactions_yields_nothing(self):
        # transcripts_full has no compact_boundary records at all
        findings = evidence.u2_compaction_frequency(TRANSCRIPTS_FULL)
        self.assertEqual(findings, [])


class TestComplianceRows(unittest.TestCase):
    def setUp(self):
        self.items = claude_code.scan(FIXTURES / "dotclaude_s8")

    def test_no_evidence_marks_unknown(self):
        rows = evidence.compliance_rows(self.items, None)
        self.assertTrue(rows)
        self.assertTrue(all(r["loaded"] == "unknown (no --evidence source)" for r in rows))
        self.assertTrue(all(r["violation_check"] == "requires --llm (future) or manual review"
                             for r in rows))

    def test_evidence_marks_matched_rule_yes_and_others_no(self):
        win = evidence.EvidenceWindow("test", [
            (1000.0, None, "some captured text ... MUST keep replies short. ... more text"),
        ])
        rows = evidence.compliance_rows(self.items, win)
        by_rule = {r["rule"]: r for r in rows}
        self.assertEqual(by_rule["MUST keep replies short."]["loaded"], "yes")
        self.assertEqual(by_rule["MUST complete filler task 0."]["loaded"], "no")

    def test_only_always_loaded_s8_kinds_are_considered(self):
        rows = evidence.compliance_rows(self.items, None)
        # every fixture rule here comes from the global always-loaded CLAUDE.md
        self.assertTrue(all(r["agent"] == "global" for r in rows))

    def test_format_compliance_table_includes_window_desc_and_placeholder(self):
        rows = evidence.compliance_rows(self.items, None)
        table = evidence.format_compliance_table(rows, window_desc="3 captured requests (test) since 2026-01-01 00:00 UTC")
        self.assertIn("evidence window: 3 captured requests", table)
        self.assertIn("requires --llm (future) or manual review", table)

    def test_format_compliance_table_empty_rows(self):
        table = evidence.format_compliance_table([], window_desc=None)
        self.assertIn("no imperative rules found", table)


if __name__ == "__main__":
    unittest.main()
