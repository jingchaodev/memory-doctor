import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import mem0
from memory_doctor import detectors

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXPORT_DIR = FIXTURES / "mem0_export"


class TestMem0AdapterParsing(unittest.TestCase):
    def test_directory_scan_merges_both_shapes(self):
        items = mem0.scan(EXPORT_DIR)
        ids = {it.meta["mem0_id"] for it in items}
        # array shape
        self.assertIn("dup-a", ids)
        self.assertIn("dup-b", ids)
        # results-envelope shape
        self.assertIn("deleted-but-present", ids)
        self.assertIn("assistant-voice", ids)
        for it in items:
            self.assertEqual(it.kind, "mem0_memory")
            self.assertFalse(it.always_loaded)
            self.assertEqual(it.loaded_portion, 1.0)

    def test_object_without_memory_field_is_skipped(self):
        items = mem0.scan(EXPORT_DIR)
        ids = {it.meta["mem0_id"] for it in items}
        self.assertNotIn("no-memory-field", ids)

    def test_single_file_array_shape(self):
        items = mem0.scan(EXPORT_DIR / "array_export.json")
        self.assertTrue(all(it.agent == "alice" for it in items))
        self.assertGreaterEqual(len(items), 5)

    def test_single_file_results_envelope_shape(self):
        items = mem0.scan(EXPORT_DIR / "results_export.json")
        self.assertTrue(all(it.agent == "bot-1" for it in items))
        self.assertGreaterEqual(len(items), 6)

    def test_agent_field_prefers_user_id_falls_back_agent_id(self):
        items = mem0.scan(EXPORT_DIR)
        by_id = {it.meta["mem0_id"]: it for it in items}
        self.assertEqual(by_id["dup-a"].agent, "alice")       # user_id present
        self.assertEqual(by_id["assistant-voice"].agent, "bot-1")  # agent_id fallback

    def test_mtime_parsed_from_created_at_when_no_updated_at(self):
        items = mem0.scan(EXPORT_DIR)
        by_id = {it.meta["mem0_id"]: it for it in items}
        self.assertGreater(by_id["dup-a"].meta["mtime"], 0.0)

    def test_mtime_prefers_updated_at_over_created_at(self):
        items = mem0.scan(EXPORT_DIR)
        by_id = {it.meta["mem0_id"]: it for it in items}
        it = by_id["deleted-but-present"]
        # only updated_at is set on this fixture object -- confirm it was actually used
        self.assertGreater(it.meta["mtime"], 0.0)

    def test_missing_timestamp_defaults_to_zero(self):
        items = mem0.scan(EXPORT_DIR)
        by_id = {it.meta["mem0_id"]: it for it in items}
        self.assertEqual(by_id["no-timestamp-object"].meta["mtime"], 0.0)

    def test_fractional_seconds_with_offset_parsed(self):
        items = mem0.scan(EXPORT_DIR)
        by_id = {it.meta["mem0_id"]: it for it in items}
        self.assertGreater(by_id["healthy-control-2"].meta["mtime"], 0.0)

    def test_hash_and_score_mapped_into_meta(self):
        items = mem0.scan(EXPORT_DIR)
        by_id = {it.meta["mem0_id"]: it for it in items}
        it = by_id["healthy-control-2"]
        self.assertEqual(it.meta["hash"], "abc123")
        self.assertEqual(it.meta["score"], 0.87)

    def test_metadata_dict_preserved(self):
        items = mem0.scan(EXPORT_DIR)
        by_id = {it.meta["mem0_id"]: it for it in items}
        self.assertEqual(by_id["deleted-but-present"].meta["metadata"].get("deleted"), True)

    def test_malformed_json_file_no_crash_and_no_items(self):
        items = mem0.scan(FIXTURES / "mem0_export_malformed" / "bad.json")
        self.assertEqual(items, [])

    def test_unrecognized_shape_no_crash_and_no_items(self):
        items = mem0.scan(FIXTURES / "mem0_export_malformed" / "unrecognized_shape.json")
        self.assertEqual(items, [])

    def test_malformed_directory_scan_no_crash(self):
        # directory scan must survive a bad file sitting next to others
        items = mem0.scan(FIXTURES / "mem0_export_malformed")
        self.assertEqual(items, [])

    def test_missing_path_returns_empty(self):
        items = mem0.scan(FIXTURES / "no-such-mem0-export-xyz")
        self.assertEqual(items, [])


class TestS3GeneralizedToMem0(unittest.TestCase):
    def test_duplicate_pair_detected_across_mem0_items(self):
        items = mem0.scan(EXPORT_DIR)
        findings = detectors.s3_duplicates(items)
        # duplicate detection reports the file name in its summary; both dup-a/dup-b
        # objects live in array_export.json, so a hit there confirms the pair fired.
        hit = [f for f in findings if "array_export.json" in f.summary]
        self.assertTrue(hit, findings)

    def test_old_memory_entry_tests_still_pass_kind_unaffected(self):
        # sanity: mem0 items never leak into a memory_entry-only fixture scan
        from memory_doctor.adapters import claude_code
        cc_items = claude_code.scan(FIXTURES / "dotclaude")
        findings = detectors.s3_duplicates(cc_items)
        for f in findings:
            self.assertNotIn("mem0", f.item_id)


class TestS6GeneralizedToMem0(unittest.TestCase):
    def test_relative_date_flagged(self):
        items = mem0.scan(EXPORT_DIR)
        findings = detectors.s6_date_rot(items)
        self.assertTrue(any("relative date" in f.summary and "relative-date-rot" in f.item_id
                             for f in findings))

    def test_stale_absolute_date_flagged(self):
        items = mem0.scan(EXPORT_DIR)
        findings = detectors.s6_date_rot(items)
        self.assertTrue(any("stale-absolute-date" in f.item_id for f in findings))


class TestS9GeneralizedToMem0(unittest.TestCase):
    def test_injection_shaped_text_flagged(self):
        items = mem0.scan(EXPORT_DIR)
        findings = detectors.s9_poisoning(items)
        hit = [f for f in findings if "injection-shaped" in f.item_id]
        self.assertTrue(hit)
        self.assertEqual(hit[0].severity, "med")


class TestS2DeadReferenceOnMem0(unittest.TestCase):
    def test_dead_path_in_mem0_text_flagged(self):
        items = mem0.scan(EXPORT_DIR)
        findings = detectors.s2_dead_references(items)
        self.assertTrue(any("dead-path" in f.item_id for f in findings))


if __name__ == "__main__":
    unittest.main()
