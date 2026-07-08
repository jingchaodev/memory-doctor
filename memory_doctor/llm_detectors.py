"""L-tier: opt-in LLM-assisted detectors (only run with --llm).

Precision doctrine: every finding here is advisory, never ground truth.
Severity is capped at med (never high), and every summary is prefixed/labeled
"LLM-assisted" so a reader can immediately tell a probabilistic finding apart
from a deterministic S-tier one. Malformed or unparseable model output is
skipped silently — a broken JSON reply must never crash the audit.

Privacy: nothing in this module makes a network call by itself. Every
function here takes a `client` object (duck-typed: `.complete(prompt,
system="") -> str`) supplied by the caller. `__main__` is the only place that
constructs a real `LLMClient`, and only when the user passes `--llm`. Tests
pass a FakeClient and never touch the network.

Tiering (judgment call, since only two of the three passes below were given
an explicit "L<n>" tag in the spec): B2 (contradiction/supersession, R4) and
B3 (junk/overgeneralization classification, R5+R8) are two prompts of the
same first-pass "read every entry and judge it" tier, so both are filed as
rule "L1". B4 (verifiable-claim extraction + LOCAL verification) is a
structurally different mechanism — verification, not judgment — and is
tagged "L2" per the spec's own "(L2)" label on that section.
"""
import json
import re
import shutil
from pathlib import Path

from .items import Finding

TRUNCATE_CHARS = 500
DEFAULT_MAX_ENTRIES = 200

SYSTEM_PROMPT = (
    "You are auditing an AI coding agent's persistent memory files for quality "
    "issues. Precision matters far more than recall: when you are not confident, "
    "return an empty result rather than guessing. Respond with JSON only -- no "
    "markdown code fences, no commentary before or after the JSON."
)


# ---------- shared helpers ----------

def _label(item):
    """Short, stable label used in prompts instead of the full path -- cheaper
    on tokens and still unique within one agent's entry set."""
    return item.path.name


L_TIER_KINDS = ("memory_entry", "mem0_memory")


def _capped_entries_by_agent(items, max_entries):
    """Flatten every agent's recallable-entry items (memory_entry, and Mem0's
    mem0_memory -- D1: this pass is kind-agnostic on purpose, since both are the
    same shape of thing: freeform text recalled on demand) in scan order, keep
    only the first `max_entries` GLOBALLY (cost control across the whole run),
    then regroup by agent. An agent whose entries all fall past the cap is
    simply not sent to the LLM at all -- no partial/misleading judgment for it."""
    all_entries = [it for it in items if it.kind in L_TIER_KINDS]
    capped = all_entries[:max_entries]
    by_agent = {}
    for it in capped:
        by_agent.setdefault(it.agent, []).append(it)
    return by_agent


def _format_entries(entries):
    lines = []
    for it in entries:
        text = it.text[:TRUNCATE_CHARS].replace("\n", " ")
        lines.append(f"{_label(it)}: {text}")
    return "\n".join(lines)


def _extract_json_array(text):
    """Defensive parse: find the first '[' ... last ']' and json.loads it.
    Returns [] on ANY failure -- malformed LLM output must never crash the
    audit (precision doctrine: skip silently, don't raise, don't guess)."""
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


# ---------- B2 (R4) : L1 contradiction / supersession detector ----------
CONTRADICTION_PROMPT = """Below are memory entries belonging to one agent, each given as "<id>: <text>".

Find pairs of entries that either:
- CONTRADICT each other (state incompatible facts about the same subject), or
- are SUPERSEDED -- an older entry whose fact a newer entry has clearly replaced on the same subject.

Return ONLY a JSON array. Each item: {{"a": "<id>", "b": "<id>", "type": "contradiction"|"superseded", "reason": "<short reason, one sentence>"}}.
If you are not confident about a pair, leave it out entirely. If there are no such pairs, return [].

Entries:
{entries}"""


def l1_contradictions(items, client, max_entries=DEFAULT_MAX_ENTRIES):
    out = []
    by_agent = _capped_entries_by_agent(items, max_entries)
    for agent, entries in by_agent.items():
        if len(entries) < 2:
            continue  # nothing to compare -- don't spend a call on it
        by_label = {_label(it): it for it in entries}
        prompt = CONTRADICTION_PROMPT.format(entries=_format_entries(entries))
        try:
            reply = client.complete(prompt, system=SYSTEM_PROMPT)
        except Exception:
            continue  # network/API failure for this agent -- skip, never crash the audit
        for row in _extract_json_array(reply):
            if not isinstance(row, dict):
                continue
            kind = row.get("type")
            if kind not in ("contradiction", "superseded"):
                continue
            a_item = by_label.get(row.get("a"))
            b_item = by_label.get(row.get("b"))
            if not a_item or not b_item:
                continue
            reason = str(row.get("reason", "")).strip()
            if kind == "contradiction":
                summary = (f"[{agent}] LLM-assisted: entries {a_item.path.name} and "
                           f"{b_item.path.name} appear to conflict")
            else:
                summary = (f"[{agent}] LLM-assisted: entry {a_item.path.name} appears "
                           f"superseded by {b_item.path.name}")
            if reason:
                summary += f" -- {reason}"
            out.append(Finding(
                rule="L1", severity="med", item_id=a_item.id,
                summary=summary,
                evidence=f"{a_item.path.name} <-> {b_item.path.name} ({kind})",
                suggestion="Review both entries; merge, delete the stale one, or resolve the conflict.",
            ))
    return out


# ---------- B3 (R5 + R8) : per-entry classification pass ----------
CLASS_PROMPT = """Below are memory entries belonging to one agent, each given as "<id>: <text>".

For EACH entry, classify it into exactly one class:
- durable_fact: a fact/preference that stays true indefinitely
- preference: a stated user preference
- project_state: current state of an ongoing project/task (may go stale, but isn't junk yet)
- transient_task: a one-off task/TODO that should have been cleared once done
- system_restatement: just restates something the system prompt/instructions already say
- noise: cron/log output, filler, or otherwise not worth keeping

Also set "overgeneralized": true ONLY when the entry states an ABSOLUTE or permanent-sounding
rule (e.g. "always", "never", "don't ever", "must never") whose OWN text shows the rule was
derived from a SINGLE dated incident or one-time example, with no stated scope or expiry --
i.e. a one-off event was turned into a blanket policy. Otherwise set it to false.

Return ONLY a JSON array, one object per entry, in the same order given:
{{"id": "<id>", "class": "<one of the classes above>", "overgeneralized": true|false}}.

Entries:
{entries}"""

JUNK_CLASSES = {"transient_task", "system_restatement", "noise"}
VALID_CLASSES = JUNK_CLASSES | {"durable_fact", "preference", "project_state"}


def l1_classification(items, client, max_entries=DEFAULT_MAX_ENTRIES):
    out = []
    by_agent = _capped_entries_by_agent(items, max_entries)
    for agent, entries in by_agent.items():
        by_label = {_label(it): it for it in entries}
        prompt = CLASS_PROMPT.format(entries=_format_entries(entries))
        try:
            reply = client.complete(prompt, system=SYSTEM_PROMPT)
        except Exception:
            continue
        for row in _extract_json_array(reply):
            if not isinstance(row, dict):
                continue
            item = by_label.get(row.get("id"))
            if not item:
                continue
            klass = row.get("class")
            if klass in JUNK_CLASSES:
                out.append(Finding(
                    rule="L1", severity="low", item_id=item.id,
                    summary=f"[{agent}] {item.path.name}: LLM-assisted: likely junk memory ({klass})",
                    evidence=klass,
                    suggestion="Confirm and delete -- this class of entry rarely earns its keep.",
                ))
            if row.get("overgeneralized") is True:
                out.append(Finding(
                    rule="L1", severity="med", item_id=item.id,
                    summary=(f"[{agent}] {item.path.name}: LLM-assisted: absolute rule "
                             f"generalized from a single incident -- add scope or expiry"),
                    suggestion="Rewrite with an explicit scope/date or an expiry, or soften "
                               "from an absolute rule to a noted preference.",
                ))
    return out


# ---------- B4 (L2) : verifiable-claim probe ----------
# NOTE: this is deliberately NOT a duplicate of S2 (static dead-reference scan).
# S2 only catches paths that literally match /root|/home|/Users|~/... written as
# a path string in the text. L2 catches what that regex structurally can't see:
# claims phrased in prose ("the deploy script lives at build/out.sh"), relative/
# tilde-less path mentions, and command/binary claims ("requires the `foo`
# binary") -- anything the LLM can read out of natural language that S2's regex
# was never going to match.
CLAIM_PROMPT = """Below are memory entries belonging to one agent, each given as "<id>: <text>".

Extract ONLY claims that assert either:
- a specific local filesystem path exists (kind "path_exists"), or
- a specific command-line tool/binary is installed (kind "command_exists").

Ignore everything else -- opinions, preferences, project state, unrelated facts.

Return ONLY a JSON array: {{"id": "<id>", "kind": "path_exists"|"command_exists", "target": "<the path, or the bare command name>"}}.
Omit an entry entirely if it makes no such claim, or if you are not sure.

Entries:
{entries}"""

# Verification is LOCAL ONLY -- os.path existence checks and shutil.which
# lookups, nothing else (hard constraint: never execute a user-provided
# command or arbitrary code). Any target containing whitespace or a shell
# metacharacter is rejected before we even look at its kind.
SAFE_TARGET_RE = re.compile(r"^[A-Za-z0-9_./~-]+$")


def _verify_claim(kind, target):
    """Returns True/False, or None if the target is unsafe/unverifiable (in
    which case the caller must skip it silently -- never guess, never shell out)."""
    if not isinstance(target, str) or not target or not SAFE_TARGET_RE.match(target):
        return None
    if kind == "path_exists":
        try:
            return Path(target).expanduser().exists()
        except Exception:
            return None
    if kind == "command_exists":
        try:
            return shutil.which(target) is not None
        except Exception:
            return None
    return None


def l2_claim_probe(items, client, max_entries=DEFAULT_MAX_ENTRIES):
    out = []
    by_agent = _capped_entries_by_agent(items, max_entries)
    for agent, entries in by_agent.items():
        by_label = {_label(it): it for it in entries}
        prompt = CLAIM_PROMPT.format(entries=_format_entries(entries))
        try:
            reply = client.complete(prompt, system=SYSTEM_PROMPT)
        except Exception:
            continue
        for row in _extract_json_array(reply):
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            if kind not in ("path_exists", "command_exists"):
                continue
            item = by_label.get(row.get("id"))
            if not item:
                continue
            target = row.get("target")
            result = _verify_claim(kind, target)
            if result is False:
                noun = "path" if kind == "path_exists" else "command"
                out.append(Finding(
                    rule="L2", severity="med", item_id=item.id,
                    summary=(f"[{agent}] {item.path.name}: LLM-assisted: memory claims "
                             f"{noun} \"{target}\" but it doesn't exist"),
                    evidence=f"{kind}: {target}",
                    suggestion=f"Verify and update or delete this entry -- the referenced {noun} is gone.",
                ))
    return out


# ---------- entry point ----------
def l_tier(items, client, max_entries=DEFAULT_MAX_ENTRIES):
    """Run all three LLM-assisted passes and return combined Findings.
    Exactly 3 LLM calls per agent that has >=1 entry within the max_entries
    cap (contradictions additionally require >=2 entries to be worth a call).
    Only ever invoked from __main__, and only under --llm."""
    findings = []
    findings += l1_contradictions(items, client, max_entries)
    findings += l1_classification(items, client, max_entries)
    findings += l2_claim_probe(items, client, max_entries)
    return findings
