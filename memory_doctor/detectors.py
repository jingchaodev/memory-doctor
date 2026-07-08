"""S-tier detectors: static, deterministic, zero-dependency.

Each detector: (items) -> list[Finding]. Detectors never modify anything.
Precision discipline: prefer missing a problem over crying wolf — every rule
here should hold >80% precision on the fixture + fleet golden sets.
"""
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from .items import HarnessItem, Finding

TOKEN_PER_CHAR = 0.4  # rough estimate good enough for bloat profiling


# ---------- S1 · load-truncation ----------
def s1_load_truncation(items):
    out = []
    for it in items:
        if it.kind == "memory_index" and it.loaded_portion < 1.0:
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
            p = Path(raw).expanduser()
            # globs / wildcards aren't checkable
            if any(c in raw for c in "*?"):
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


ALL = [s1_load_truncation, s2_dead_references, s3_duplicates,
       s4_index_orphans, s5_bloat, s6_date_rot]


def run_all(items):
    findings = []
    for det in ALL:
        findings.extend(det(items))
    sev_rank = {"high": 0, "med": 1, "low": 2}
    findings.sort(key=lambda f: (sev_rank[f.severity], f.rule))
    return findings
