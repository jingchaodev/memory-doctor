import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import claude_code
from memory_doctor import llm_detectors as ld

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "dotclaude_llm"


class FakeClient:
    """Returns canned responses in call order. No network, ever."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # (prompt, system) for every .complete() invocation

    def complete(self, prompt, system=""):
        self.calls.append((prompt, system))
        if not self._responses:
            return "[]"
        return self._responses.pop(0)


class ExplodingClient:
    """Simulates a network/API failure -- must never crash the audit."""

    def complete(self, prompt, system=""):
        raise RuntimeError("simulated network failure")


class TestL1Contradictions(unittest.TestCase):
    def setUp(self):
        self.items = claude_code.scan(ROOT)

    def test_contradiction_pair_flagged_med_and_labeled(self):
        fake = FakeClient([json.dumps([
            {"a": "pref-tea.md", "b": "pref-coffee.md", "type": "contradiction",
             "reason": "conflicting beverage preference"},
        ])])
        findings = ld.l1_contradictions(self.items, fake, max_entries=200)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule, "L1")
        self.assertEqual(f.severity, "med")
        self.assertIn("LLM-assisted", f.summary)
        self.assertIn("pref-tea.md", f.summary)
        self.assertIn("pref-coffee.md", f.summary)
        self.assertIn("conflict", f.summary)

    def test_superseded_wording(self):
        fake = FakeClient([json.dumps([
            {"a": "pref-tea.md", "b": "pref-coffee.md", "type": "superseded", "reason": ""},
        ])])
        findings = ld.l1_contradictions(self.items, fake, max_entries=200)
        self.assertEqual(len(findings), 1)
        self.assertIn("superseded", findings[0].summary)

    def test_malformed_json_skips_silently(self):
        fake = FakeClient(["this is not json at all {{{ ["])
        findings = ld.l1_contradictions(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_empty_array_yields_no_findings(self):
        fake = FakeClient(["[]"])
        findings = ld.l1_contradictions(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_unknown_ids_in_response_are_ignored(self):
        fake = FakeClient([json.dumps([
            {"a": "no-such-file.md", "b": "also-fake.md", "type": "contradiction", "reason": "x"},
        ])])
        findings = ld.l1_contradictions(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_single_entry_agent_makes_no_call(self):
        # loneagent has exactly 1 memory_entry -- not worth a contradiction call.
        fake = FakeClient([])
        ld.l1_contradictions(self.items, fake, max_entries=200)
        # testbot (5 entries) should still get exactly one call; loneagent none.
        self.assertEqual(len(fake.calls), 1)

    def test_network_failure_does_not_crash(self):
        findings = ld.l1_contradictions(self.items, ExplodingClient(), max_entries=200)
        self.assertEqual(findings, [])

    def test_max_entries_caps_prompt_content(self):
        fake = FakeClient(["[]"])
        ld.l1_contradictions(self.items, fake, max_entries=2)
        prompt = fake.calls[0][0]
        # only the first 2 (of 5) testbot entries in scan order should appear
        sent = sum(1 for name in ("pref-tea.md", "pref-coffee.md", "todo.md", "rule.md", "claim.md")
                   if f"{name}:" in prompt)
        self.assertEqual(sent, 2)


class TestL1Classification(unittest.TestCase):
    def setUp(self):
        self.items = claude_code.scan(ROOT)

    def test_junk_class_flagged_low(self):
        fake = FakeClient([json.dumps([
            {"id": "todo.md", "class": "transient_task", "overgeneralized": False},
        ])])
        findings = ld.l1_classification(self.items, fake, max_entries=200)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.rule, "L1")
        self.assertEqual(f.severity, "low")
        self.assertIn("junk memory", f.summary)
        self.assertIn("transient_task", f.summary)

    def test_overgeneralized_flagged_med(self):
        fake = FakeClient([json.dumps([
            {"id": "rule.md", "class": "durable_fact", "overgeneralized": True},
        ])])
        findings = ld.l1_classification(self.items, fake, max_entries=200)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.severity, "med")
        self.assertIn("single incident", f.summary)

    def test_non_junk_non_overgeneralized_yields_nothing(self):
        fake = FakeClient([json.dumps([
            {"id": "pref-tea.md", "class": "preference", "overgeneralized": False},
        ])])
        findings = ld.l1_classification(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_both_flags_on_one_entry_yields_two_findings(self):
        fake = FakeClient([json.dumps([
            {"id": "todo.md", "class": "noise", "overgeneralized": True},
        ])])
        findings = ld.l1_classification(self.items, fake, max_entries=200)
        self.assertEqual(len(findings), 2)

    def test_malformed_response_skips_silently(self):
        fake = FakeClient(["<html>not json</html>"])
        findings = ld.l1_classification(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_single_entry_agent_still_gets_classified(self):
        # unlike contradictions, classification is per-entry so a 1-entry agent
        # is still worth a call.
        fake = FakeClient([])
        ld.l1_classification(self.items, fake, max_entries=200)
        self.assertEqual(len(fake.calls), 2)  # testbot + loneagent


class TestL2ClaimProbe(unittest.TestCase):
    def setUp(self):
        self.items = claude_code.scan(ROOT)

    def test_dead_path_and_dead_command_both_flagged(self):
        fake = FakeClient([json.dumps([
            {"id": "claim.md", "kind": "path_exists",
             "target": "/root/definitely-not-a-real-path-xyz.sh"},
            {"id": "claim.md", "kind": "command_exists",
             "target": "definitely-not-a-real-cmd-xyz"},
        ])])
        findings = ld.l2_claim_probe(self.items, fake, max_entries=200)
        self.assertEqual(len(findings), 2)
        for f in findings:
            self.assertEqual(f.rule, "L2")
            self.assertEqual(f.severity, "med")
            self.assertIn("doesn't exist", f.summary)

    def test_existing_command_not_flagged(self):
        fake = FakeClient([json.dumps([
            {"id": "claim.md", "kind": "command_exists", "target": "python3"},
        ])])
        findings = ld.l2_claim_probe(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_existing_path_not_flagged(self):
        fake = FakeClient([json.dumps([
            {"id": "claim.md", "kind": "path_exists", "target": str(ROOT)},
        ])])
        findings = ld.l2_claim_probe(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_unsafe_target_rejected_without_executing_anything(self):
        # a target with shell metacharacters must never reach subprocess/shell
        # execution -- it should simply be skipped, not crash, not flag.
        fake = FakeClient([json.dumps([
            {"id": "claim.md", "kind": "command_exists", "target": "ls; rm -rf /"},
            {"id": "claim.md", "kind": "path_exists", "target": "$(whoami)"},
            {"id": "claim.md", "kind": "path_exists", "target": "`echo hi`"},
        ])])
        findings = ld.l2_claim_probe(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_verify_claim_helper_directly(self):
        self.assertIs(ld._verify_claim("command_exists", "python3"), True)
        self.assertIs(ld._verify_claim("command_exists", "no-such-cmd-xyz"), False)
        self.assertIs(ld._verify_claim("path_exists", str(ROOT)), True)
        self.assertIs(ld._verify_claim("path_exists", "/not/a/real/path/xyz"), False)
        self.assertIsNone(ld._verify_claim("command_exists", "ls; rm -rf /"))
        self.assertIsNone(ld._verify_claim("bogus_kind", "python3"))

    def test_malformed_response_skips_silently(self):
        fake = FakeClient(["not { valid json"])
        findings = ld.l2_claim_probe(self.items, fake, max_entries=200)
        self.assertEqual(findings, [])

    def test_network_failure_does_not_crash(self):
        findings = ld.l2_claim_probe(self.items, ExplodingClient(), max_entries=200)
        self.assertEqual(findings, [])


class TestLTier(unittest.TestCase):
    def test_three_calls_per_qualifying_agent(self):
        # testbot: 5 entries -> qualifies for all 3 passes (contradiction needs >=2)
        # loneagent: 1 entry -> classification + claim probe only (2 calls)
        items = claude_code.scan(ROOT)
        fake = FakeClient([])  # every call returns "[]" by default
        ld.l_tier(items, fake, max_entries=200)
        self.assertEqual(len(fake.calls), 5)  # 3 (testbot) + 2 (loneagent)

    def test_l_tier_never_returns_high_severity(self):
        items = claude_code.scan(ROOT)
        fake = FakeClient([
            json.dumps([{"a": "pref-tea.md", "b": "pref-coffee.md",
                        "type": "contradiction", "reason": "x"}]),
            json.dumps([{"id": "rule.md", "class": "durable_fact", "overgeneralized": True}]),
            json.dumps([{"id": "claim.md", "kind": "path_exists", "target": "/no/such/path"}]),
            "[]", "[]",
        ])
        findings = ld.l_tier(items, fake, max_entries=200)
        self.assertTrue(findings)
        self.assertTrue(all(f.severity in ("med", "low") for f in findings))
        self.assertTrue(all(f.rule in ("L1", "L2") for f in findings))


if __name__ == "__main__":
    unittest.main()
