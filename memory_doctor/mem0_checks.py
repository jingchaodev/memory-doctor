"""Mem0-specific static checks (EXPERIMENTAL, D2/R9/R10 fixture-level).

These only apply to items with kind == "mem0_memory" and are registered ONLY
when at least one such item is present in the scan (see __main__.py) -- they
have no meaning for Claude Code / Codex surfaces and must never run over them.

EXPERIMENTAL, same caveat as adapters/mem0.py: there is no live Mem0 store to
validate these against, so precision here is a design judgment, not a measured
number. Each rule below is written to be conservative (emit nothing rather than
guess) per the project's precision doctrine.

  M1 ghost-suspects   -- export-visible half of R9 (cross-store ghost check).
                         A full ghost check needs live access to every backing
                         store (vector/graph/entity table); from a static export
                         we can only see the half where the object's OWN metadata
                         says it was deleted yet it's still sitting in the export.
  M2 subject-conflict -- lite version of R10. Only fires when the export itself
                         carries an explicit metadata.subject/entity field --
                         if it doesn't, we have no honest way to know two entries
                         are "about the same thing" without an LLM judgment call,
                         so this stays silent rather than guessing (precision over
                         coverage, explicitly required by the spec).
  M3 attribution smell -- memory text written in assistant voice ("I recommend...",
                         "You should...") stored as if it were a durable user fact
                         (mem0#5642's class) -- usually means the wrong turn/speaker
                         got persisted.

R11 (round-trip write/read probes) is NOT here -- it requires a live store to
write through and read back from, which this project explicitly does not have.
See README roadmap for the stub note.
"""
from collections import defaultdict
import re

from .items import Finding

MEM0_KIND = "mem0_memory"


# ---------- M1 · ghost-suspects (R9 static half) ----------
_BOOL_DELETION_KEYS = ("deleted", "is_deleted", "isDeleted", "is_removed", "removed")
_STATE_KEYS = ("state", "status")
_STATE_DELETED_VALUES = {"deleted", "removed"}


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v is True
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return False


def _looks_deleted(metadata: dict) -> bool:
    if not isinstance(metadata, dict):
        return False
    for k in _BOOL_DELETION_KEYS:
        if k in metadata and _truthy(metadata.get(k)):
            return True
    for k in _STATE_KEYS:
        v = metadata.get(k)
        if isinstance(v, str) and v.strip().lower() in _STATE_DELETED_VALUES:
            return True
    return False


def m1_ghost_suspects(items):
    out = []
    for it in items:
        if it.kind != MEM0_KIND:
            continue
        metadata = it.meta.get("metadata", {})
        if _looks_deleted(metadata):
            out.append(Finding(
                rule="M1", severity="med", item_id=it.id,
                summary=f"[{it.agent}] memory's own metadata marks it deleted, but it's still "
                        f"present in this export — deleted-but-still-exported",
                evidence=f"metadata={metadata}",
                suggestion="This is only the export-visible half of a ghost check -- confirming "
                           "it's truly gone (not just flagged) needs live access to every backing "
                           "store (vector/graph/entity table). Re-export after a purge and confirm "
                           "it no longer appears, or delete it from whichever store still serves it.",
            ))
    return out


# ---------- M2 · same-subject different-value (R10 lite) ----------
_SUBJECT_KEYS = ("subject", "entity")


def _subject_of(metadata: dict):
    if not isinstance(metadata, dict):
        return None
    for k in _SUBJECT_KEYS:
        v = metadata.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def m2_subject_conflicts(items):
    out = []
    mem_items = [it for it in items if it.kind == MEM0_KIND]
    groups = defaultdict(list)
    any_subject_field = False
    for it in mem_items:
        subj = _subject_of(it.meta.get("metadata", {}))
        if subj is None:
            continue
        any_subject_field = True
        groups[(it.agent, subj)].append(it)
    if not any_subject_field:
        # No entry in this export carries a subject/entity field at all -- we have
        # no honest grouping key, so say nothing rather than guess (precision > coverage).
        return out
    for (agent, subj), group in groups.items():
        distinct_texts = {g.text.strip() for g in group}
        if len(distinct_texts) > 1:
            out.append(Finding(
                rule="M2", severity="low", item_id=group[0].id,
                summary=f"[{agent}] subject \"{subj}\": {len(group)} memories with differing "
                        f"values — possible conflicting values for one subject (verify)",
                evidence="; ".join(f"{g.id}: {g.text[:80]}" for g in group),
                suggestion="Confirm which value is current; merge or delete the stale one.",
            ))
    return out


# ---------- M3 · attribution smell ----------
# mem0#5642's class: assistant-voice text (recommendations, second-person advice,
# self-identification as the AI) persisted as if it were a durable user fact.
_ASSISTANT_VOICE_RE = re.compile(
    r"^\s*(I recommend|I suggest|I'd recommend|You should|You must|As an AI|"
    r"As your assistant|As an assistant)\b",
    re.I,
)


def m3_attribution_smell(items):
    out = []
    for it in items:
        if it.kind != MEM0_KIND:
            continue
        m = _ASSISTANT_VOICE_RE.match(it.text)
        if m:
            quoted = it.text[:100] + ("…" if len(it.text) > 100 else "")
            out.append(Finding(
                rule="M3", severity="low", item_id=it.id,
                summary=f"[{it.agent}] memory reads in assistant voice (\"{m.group(0).strip()}\") "
                        f"stored as a user fact — attribution smell",
                evidence=quoted,
                suggestion="Check who this should be attributed to -- assistant-voice text stored "
                           "as a durable user fact often means the wrong turn got persisted "
                           "(mem0#5642's class).",
            ))
    return out


ALL = [m1_ghost_suspects, m2_subject_conflicts, m3_attribution_smell]


def run_all(items):
    findings = []
    for det in ALL:
        findings.extend(det(items))
    sev_rank = {"high": 0, "med": 1, "low": 2}
    findings.sort(key=lambda f: (sev_rank[f.severity], f.rule))
    return findings
