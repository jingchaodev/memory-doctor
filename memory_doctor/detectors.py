"""S-tier detectors: static, deterministic, zero-dependency.

Each detector: (items) -> list[Finding]. Detectors never modify anything.
Precision discipline: prefer missing a problem over crying wolf — every rule
here should hold >80% precision on the fixture + fleet golden sets.
"""
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from .items import HarnessItem, Finding

TOKEN_PER_CHAR = 0.4  # rough estimate good enough for bloat profiling


# ---------- S1 · load-truncation ----------
# Generalized (R1): any item whose loaded_portion < 1.0 is a truncation candidate.
# Wording is driven by kind since each harness's cliff is measured differently
# (Claude Code auto-memory: 200 lines/25KB; Codex AGENTS.md: 32KB bytes).
#
# R13 static half — near-cliff warning: an item that hasn't been truncated YET
# (loaded_portion == 1.0) but is already ≥90% of either limit is a silent-failure
# waiting to happen — the next few writes may cross the cliff with no warning, or
# (per hermes#32064 / letta#3151) an at-capacity write may simply be dropped.
NEAR_CLIFF_RATIO = 0.90
MEMORY_INDEX_MAX_LINES = 200
MEMORY_INDEX_MAX_BYTES = 25 * 1024
AGENTS_MD_MAX_BYTES = 32 * 1024


def s1_load_truncation(items):
    out = []
    for it in items:
        if it.loaded_portion >= 1.0:
            if it.kind == "memory_index":
                total_lines = len(it.text.splitlines())
                total_bytes = len(it.text.encode())
                ratio = max(total_lines / MEMORY_INDEX_MAX_LINES,
                            total_bytes / MEMORY_INDEX_MAX_BYTES)
                if ratio >= NEAR_CLIFF_RATIO:
                    out.append(Finding(
                        rule="S1", severity="med", item_id=it.id,
                        summary=f"[{it.agent}] MEMORY.md is {total_lines} lines / {total_bytes:,} bytes "
                                f"({ratio:.0%} of the load cliff) — approaching the load cliff: new "
                                f"entries will soon silently stop loading, and at-capacity writes may "
                                f"be dropped",
                        evidence=f"lines={total_lines}/{MEMORY_INDEX_MAX_LINES}, "
                                 f"bytes={total_bytes:,}/{MEMORY_INDEX_MAX_BYTES:,}",
                        suggestion="Trim now, before the cliff bites — move older entries into "
                                   "per-topic files while there's still room to do it cleanly.",
                    ))
            elif it.kind == "agents_md":
                total_bytes = len(it.text.encode())
                ratio = total_bytes / AGENTS_MD_MAX_BYTES
                if ratio >= NEAR_CLIFF_RATIO:
                    out.append(Finding(
                        rule="S1", severity="med", item_id=it.id,
                        summary=f"[{it.agent}] {it.path.name} is {total_bytes:,} bytes "
                                f"({ratio:.0%} of the 32KB cliff) — approaching the load cliff: new "
                                f"entries will soon silently stop loading, and at-capacity writes may "
                                f"be dropped",
                        evidence=f"bytes={total_bytes:,}/{AGENTS_MD_MAX_BYTES:,}",
                        suggestion="Trim AGENTS.md now, before content starts silently dropping.",
                    ))
            continue
        if it.kind == "memory_index":
            total = len(it.text.splitlines())
            lost = total - int(total * it.loaded_portion)
            out.append(Finding(
                rule="S1", severity="high", item_id=it.id,
                summary=f"[{it.agent}] MEMORY.md is {total} lines; the last {lost} lines "
                        f"({(1-it.loaded_portion):.0%}) silently NEVER load (200-line/25KB cliff)",
                evidence=f"loaded_portion={it.loaded_portion:.2f}",
                suggestion="Move entries below the cliff into per-topic files and keep the index terse, "
                           "or delete stale index lines.",
            ))
        elif it.kind == "agents_md":
            total_b = len(it.text.encode())
            lost_b = total_b - int(total_b * it.loaded_portion)
            out.append(Finding(
                rule="S1", severity="high", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name} is {total_b:,} bytes; the last {lost_b:,} bytes "
                        f"({(1-it.loaded_portion):.0%}) silently NEVER load (Codex 32KB limit)",
                evidence=f"loaded_portion={it.loaded_portion:.2f}",
                suggestion="Trim AGENTS.md below 32KB, or move detail into a doc the agent reads on demand.",
            ))
        else:
            # unrecognized kind with a reported partial load — flag conservatively,
            # honest about not knowing the exact cliff semantics for this surface.
            out.append(Finding(
                rule="S1", severity="low", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: only {it.loaded_portion:.0%} of this file "
                        f"reportedly loads",
                evidence=f"loaded_portion={it.loaded_portion:.2f}",
                suggestion="Verify this surface's load limit and trim content below it.",
            ))
    return out


# ---------- S2 · dead references ----------
PATH_RE = re.compile(r"(?<![\w@])(/(?:root|home|Users)/[\w.\-/]+|~/[\w.\-/]+)")
SKIP_PATH_HINTS = ("/tmp/", "/dev/", "/proc/", "example", "<", ">", "{", "}")


def s2_dead_references(items):
    out = []
    for it in items:
        dead = []
        for m in PATH_RE.finditer(it.text):
            raw = m.group(1).rstrip(".,;:)]}\"'`")
            if any(h in raw for h in SKIP_PATH_HINTS):
                continue
            # globs / wildcards aren't checkable; a trailing '-' or '_' is usually a
            # truncated glob prefix like /root/restart-*.sh (precision lesson, fleet scan #1)
            if any(c in raw for c in "*?") or raw.endswith(("-", "_")):
                continue
            p = Path(raw).expanduser()
            # deliberate references to retired/old locations are not rot
            line = it.text[max(0, it.text.rfind("\n", 0, m.start())):it.text.find("\n", m.end()) if it.text.find("\n", m.end()) != -1 else len(it.text)]
            if re.search(r"deprecated|retired|old location|migrated|renamed|已迁移|已退役|不再", line, re.I):
                continue
            if not p.exists():
                dead.append(raw)
        if dead:
            uniq = sorted(set(dead))
            out.append(Finding(
                rule="S2", severity="med", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: {len(uniq)} referenced path(s) no longer exist",
                evidence="; ".join(uniq[:5]) + ("…" if len(uniq) > 5 else ""),
                suggestion="Update or delete the entry — an agent acting on a dead path wastes a turn "
                           "or invents a fallback.",
            ))
    return out


# ---------- S3 · duplicate / near-duplicate entries ----------
def _tokens(text):
    return set(re.findall(r"[a-z一-鿿]{3,}", text.lower()))


def s3_duplicates(items):
    out = []
    entries = [it for it in items if it.kind == "memory_entry"]
    seen = set()
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            if a.agent != b.agent:
                continue  # cross-agent dupes are fleet-mode (v0.3)
            ta, tb = _tokens(a.text), _tokens(b.text)
            if not ta or not tb:
                continue
            jac = len(ta & tb) / len(ta | tb)
            if jac >= 0.6 and (a.id, b.id) not in seen:
                seen.add((a.id, b.id))
                out.append(Finding(
                    rule="S3", severity="low", item_id=a.id,
                    summary=f"[{a.agent}] near-duplicate entries: {a.path.name} ≈ {b.path.name} "
                            f"(similarity {jac:.0%})",
                    suggestion="Merge into one entry; duplicates drift apart and later contradict.",
                ))
    return out


# ---------- S4 · index orphans ----------
LINK_RE = re.compile(r"\[[^\]]*\]\(([\w.\-]+\.md)\)")


def s4_index_orphans(items):
    out = []
    by_agent_idx = {it.agent: it for it in items if it.kind == "memory_index"}
    files_by_agent = defaultdict(set)
    for it in items:
        if it.kind == "memory_entry":
            files_by_agent[it.agent].add(it.path.name)
    for agent, idx in by_agent_idx.items():
        linked = set(LINK_RE.findall(idx.text))
        files = files_by_agent.get(agent, set())
        missing = sorted(linked - files)          # index points at nothing
        unlisted = sorted(files - linked)         # file exists, never indexed
        if missing:
            out.append(Finding(
                rule="S4", severity="med", item_id=idx.id,
                summary=f"[{agent}] index links {len(missing)} memory file(s) that don't exist",
                evidence="; ".join(missing[:5]) + ("…" if len(missing) > 5 else ""),
                suggestion="Remove the dead index lines (or restore the files).",
            ))
        if unlisted:
            out.append(Finding(
                rule="S4", severity="low", item_id=idx.id,
                summary=f"[{agent}] {len(unlisted)} memory file(s) exist but are NOT in the index "
                        f"— invisible unless recalled by name",
                evidence="; ".join(unlisted[:5]) + ("…" if len(unlisted) > 5 else ""),
                suggestion="Add one-line index entries or delete the orphan files.",
            ))
    return out


# ---------- S5 · bloat profile ----------
def s5_bloat(items):
    out = []
    always = [it for it in items if it.always_loaded]
    if not always:
        return out
    per_agent = defaultdict(int)
    for it in always:
        # count only the portion that actually loads
        per_agent[it.agent] += int(len(it.text) * min(it.loaded_portion, 1.0))
    total_chars = sum(per_agent.values())
    est_tokens = int(total_chars * TOKEN_PER_CHAR)
    biggest = sorted(always, key=lambda x: -len(x.text))[:3]
    out.append(Finding(
        rule="S5", severity="low" if est_tokens < 12000 else "med",
        item_id="(always-loaded set)",
        summary=f"always-loaded layer ≈ {est_tokens:,} tokens across "
                f"{len(always)} file(s) — paid EVERY session",
        evidence="largest: " + "; ".join(f"{b.path.name} ({len(b.text):,}ch)" for b in biggest),
        suggestion="Anything an agent could look up on demand shouldn't ride in the always-loaded layer.",
    ))
    return out


# ---------- S6 · date rot ----------
DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
RELATIVE_RE = re.compile(r"(next week|tomorrow|later this (week|month)|下周|明天|下个月)", re.I)
STALE_DAYS = 365


def s6_date_rot(items, today=None):
    today = today or date.today()
    out = []
    for it in items:
        if it.kind not in ("memory_entry", "claude_md", "import"):
            continue
        rel = RELATIVE_RE.findall(it.text)
        if rel:
            out.append(Finding(
                rule="S6", severity="med", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: relative date in durable memory "
                        f"({', '.join(sorted(set(r[0] if isinstance(r, tuple) else r for r in rel)))}) "
                        f"— meaningless at recall time",
                suggestion="Rewrite relative dates as absolute dates.",
            ))
        dates = [date(*map(int, d.split("-"))) for d in DATE_RE.findall(it.text)
                 if int(d[:4]) >= 2020]
        if dates and it.kind == "memory_entry":
            newest = max(dates)
            if (today - newest).days > STALE_DAYS:
                out.append(Finding(
                    rule="S6", severity="low", item_id=it.id,
                    summary=f"[{it.agent}] {it.path.name}: newest date inside is {newest} "
                            f"({(today - newest).days}d ago) — verify it still holds",
                    suggestion="Confirm, refresh, or delete.",
                ))
    return out


# ---------- S7 · staleness-by-neglect (R15) ----------
# Pure mtime check: an always-loaded instruction file (rules an agent obeys every
# session) hasn't been touched in a long time, while the memory layer around it
# keeps changing — a signal the system is in active use but nobody has revisited
# whether the old instructions still hold. Requires BOTH conditions to avoid
# flagging a healthy-but-quiet setup (precision doctrine).
STALE_INSTRUCTION_DAYS = 90
ACTIVE_MEMORY_DAYS = 7
INSTRUCTION_KINDS = ("claude_md", "import", "agents_md")


def s7_neglect(items, now=None):
    now = now if now is not None else time.time()
    out = []
    active = any(
        it.kind == "memory_entry" and it.meta.get("mtime")
        and (now - it.meta["mtime"]) <= ACTIVE_MEMORY_DAYS * 86400
        for it in items
    )
    if not active:
        return out
    for it in items:
        if it.kind not in INSTRUCTION_KINDS or not it.always_loaded:
            continue
        mtime = it.meta.get("mtime")
        if not mtime:
            continue
        age_days = (now - mtime) / 86400
        if age_days > STALE_INSTRUCTION_DAYS:
            out.append(Finding(
                rule="S7", severity="low", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name} untouched for {int(age_days)}d while memory "
                        f"keeps changing — re-evaluate whether its rules still hold",
                evidence=f"mtime age={int(age_days)}d",
                suggestion="Skim this file: confirm the rules are still accurate, or refresh it.",
            ))
    return out


# ---------- S8 · rule-density lint (R3) ----------
# Evidence: HN 48160604 / 47144537 (imperative rules beyond ~1 sentence are
# observably ignored), hermes#47349 (70+ entries, 14K chars burned per turn),
# openclaw#92451 (instruction-count regression dilutes attention).
# Scope: only the always-loaded instruction surfaces — this is about the rules
# an agent is FORCED to read every single session, not memory it recalls on demand.
S8_KINDS = ("claude_md", "import", "agents_md")
IMPERATIVE_RE = re.compile(r"\b(?:MUST|NEVER|ALWAYS)\b|DO NOT|必须|不要|禁止", re.I)
BULLET_OR_NUMBERED_RE = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)(.*)$")
SENTENCE_END_RE = re.compile(r"[.!?。！？]+(?:\s|$)")
RULE_MAX_CHARS = 300
RULE_MAX_SENTENCES = 2
FILE_MAX_RULES = 40


def _imperative_rules(text):
    """Return the body text of every bullet/numbered line that contains an
    imperative marker (MUST/NEVER/ALWAYS/DO NOT/必须/不要/禁止). Kept simple by
    design: presence of the marker is the whole test, no NLP."""
    rules = []
    for line in text.splitlines():
        m = BULLET_OR_NUMBERED_RE.match(line)
        if not m:
            continue
        body = m.group(1).strip()
        if body and IMPERATIVE_RE.search(body):
            rules.append(body)
    return rules


def s8_rule_density(items):
    out = []
    for it in items:
        if it.kind not in S8_KINDS or not it.always_loaded:
            continue
        rules = _imperative_rules(it.text)
        for r in rules:
            n_sentences = len(SENTENCE_END_RE.findall(r))
            if len(r) > RULE_MAX_CHARS or n_sentences > RULE_MAX_SENTENCES:
                quoted = r[:60] + ("…" if len(r) > 60 else "")
                out.append(Finding(
                    rule="S8", severity="low", item_id=it.id,
                    summary=f"[{it.agent}] {it.path.name}: imperative rule runs long "
                            f"({len(r)} chars, ~{n_sentences} sentences) — \"{quoted}\"",
                    evidence=r[:200] + ("…" if len(r) > 200 else ""),
                    suggestion="Rules beyond ~1 sentence are observably ignored in the field — "
                               "trim to a single imperative clause.",
                ))
        if len(rules) > FILE_MAX_RULES:
            out.append(Finding(
                rule="S8", severity="med", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: {len(rules)} imperative rules in one "
                        f"always-loaded file — attention dilution territory",
                evidence=f"imperative rule count={len(rules)} (threshold {FILE_MAX_RULES})",
                suggestion="Consolidate or move lower-priority rules out of the always-loaded layer; "
                           "large rule counts are observed to regress instruction-following.",
            ))
    return out


# ---------- S9 · poisoning / scaffold scan (L3 static subset) ----------
# Evidence: openclaw#69943 (unsanitized chat-template control tokens written into
# durable memory create a self-reinforcing poisoning loop), openclaw#80613
# (staging-scaffold markers promoted into MEMORY.md).
# NOTE (openclaw#80613 lesson): dedup/near-duplicate logic elsewhere (S3 above)
# must stay locale-aware — ASCII-only matching misses CJK near-dupes. S9 doesn't
# do similarity matching, but any future edit to S3/S9 must keep that in mind.
CONTROL_TOKENS = ["<|im_start|>", "<|im_end|>", "<|endoftext|>", "[INST]", "<<SYS>>", "</s>"]
INJECTION_RE = re.compile(
    r"ignore (?:all |the )?(?:previous|prior|above) instructions"
    r"|disregard .{0,20}instructions"
    r"|you must now"
    r"|new system prompt",
    re.I,
)
FENCE_RE = re.compile(r"^\s*```")
STAGING_RE = re.compile(r"^\s*-\s*Candidate:|status:\s*staged", re.I)


def _unfenced_lines(text):
    """Yield each line outside ``` fenced code blocks. Memory files legitimately
    quote injection/control-token examples for reference (e.g. this very file's
    fixtures) — a naive scan would flag its own documentation."""
    fenced = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        yield line


def s9_poisoning(items):
    out = []
    for it in items:
        tokens_found = set()
        injection_lines = []
        staging_lines = []
        for line in _unfenced_lines(it.text):
            for tok in CONTROL_TOKENS:
                if tok in line:
                    tokens_found.add(tok)
            if it.kind == "memory_entry" and INJECTION_RE.search(line):
                injection_lines.append(line.strip())
            if STAGING_RE.search(line):
                staging_lines.append(line.strip())
        if tokens_found:
            out.append(Finding(
                rule="S9", severity="high", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: chat-template control token(s) found in "
                        f"durable memory — {', '.join(sorted(tokens_found))}",
                evidence="; ".join(sorted(tokens_found)),
                suggestion="Strip these immediately — control tokens surviving in memory can "
                           "trigger a self-reinforcing poisoning loop.",
            ))
        if injection_lines:
            quoted = injection_lines[0][:60] + ("…" if len(injection_lines[0]) > 60 else "")
            out.append(Finding(
                rule="S9", severity="med", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: instruction-injection-shaped text inside a "
                        f"memory entry — \"{quoted}\"",
                evidence="; ".join(injection_lines[:5]),
                suggestion="Confirm this is a legitimate quoted example and not injected content; "
                           "delete it or move it into a fenced example if it must be kept.",
            ))
        if staging_lines:
            out.append(Finding(
                rule="S9", severity="low", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: staging/scaffold marker leaked into memory",
                evidence="; ".join(staging_lines[:5]),
                suggestion="Candidate:/status: staged markers belong in a working doc, not durable "
                           "memory — clean up or promote properly.",
            ))
    return out


ALL = [s1_load_truncation, s2_dead_references, s3_duplicates,
       s4_index_orphans, s5_bloat, s6_date_rot, s7_neglect,
       s8_rule_density, s9_poisoning]


def run_all(items):
    findings = []
    for det in ALL:
        findings.extend(det(items))
    sev_rank = {"high": 0, "med": 1, "low": 2}
    findings.sort(key=lambda f: (sev_rank[f.severity], f.rule))
    return findings
