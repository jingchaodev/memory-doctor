"""U-tier: usage-evidence detectors and their evidence loaders.

Answers "did this content ever actually reach a captured LLM request?" --
something no S-tier or L-tier check can see, because both only read the
memory files themselves. Everything here is opt-in (only invoked when the
CLI is given `--evidence`, `compaction-audit`, or `compliance`), read-only,
and stdlib-only.

Two local capture sources were explored (see README's U-tier section for the
full writeup):

  - prompttap body captures: a local reverse-proxy tap in front of the
    Anthropic Messages API writes one gzip JSON file per request under
    <prompttap_dir>/bodies/*.json.gz. This is the literal API request body --
    the highest-precision evidence available -- but on this host it is
    aggressively rotated (a fixed file-count cap), so the retained window is
    often only the last hour or two. "Preferred" per spec, but only when it
    exists and has something in it.

  - Claude Code session transcripts: <transcripts_dir>/<agent>/*.jsonl.
    NOT the literal API request (Claude Code doesn't log that), but a stream
    of session events -- attachments, tool results, compaction markers --
    that reliably quote file content verbatim when a memory file was read or
    injected. Retention is whatever the user kept on disk, typically far
    longer than prompttap's rotation window. Used as the fallback (or, in
    practice on a freshly-rotated host, the more useful source).

Every loader treats a corrupt/unreadable file as zero evidence, never a
crash: a tap or transcript that can't be parsed must never take down the
audit (same precision doctrine as the rest of the codebase).
"""
import datetime
import gzip
import json
import time
from collections import defaultdict
from pathlib import Path

from .detectors import S8_KINDS, _imperative_rules
from .items import Finding

DEFAULT_PROMPTTAP_DIR = Path("~/.claude/skills/_harness/prompttap")
DEFAULT_TRANSCRIPTS_DIR = Path("~/.claude/projects")

MIN_FINGERPRINT_LEN = 20
FRONTMATTER_SCAN_LINES = 60


# ---------- fingerprint extraction ----------

def fingerprint(text):
    """Return a distinctive, ≥20-char content line to search for in captured
    evidence -- skipping any leading `---`-delimited frontmatter block (the
    insight-capture convention: name/description/type keys between two `---`
    lines). Returns None when nothing qualifies -- callers must skip the item
    silently rather than guessing a fingerprint from a title or boilerplate."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), FRONTMATTER_SCAN_LINES)):
            if lines[j].strip() == "---":
                start = j + 1
                break
    for line in lines[start:start + FRONTMATTER_SCAN_LINES]:
        s = line.strip()
        if len(s) >= MIN_FINGERPRINT_LEN:
            return s
    return None


# ---------- generic JSON flattening (shared by both loaders) ----------

def _flatten_strings(obj):
    """Yield every string leaf inside a nested dict/list JSON structure. This
    is what lets fingerprint search work uniformly over prompttap bodies and
    transcript JSONL records without any source-specific field parsing --
    JSON decoding already unescapes everything, so this is more robust than
    raw-byte substring search over the on-disk text."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _flatten_strings(v)


def _parse_ts(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# ---------- evidence window ----------

class EvidenceWindow:
    """A bag of captured-request records: (timestamp|None, agent|None, searchable_text).

    `agent` is only known for transcript records (the owning project dir name,
    which is exactly the same string as HarnessItem.agent for a claude-code
    scan of the same root) -- prompttap bodies aren't organized per agent, so
    their records carry agent=None. `contains()` treats an unknown-agent
    record as compatible with any agent query, so scope mismatch can only
    ever produce a false "seen" (safe direction), never a false "never seen"."""

    def __init__(self, source, records):
        self.source = source  # "prompttap" | "transcripts"
        self.records = records

    def __len__(self):
        return len(self.records)

    @property
    def earliest(self):
        ts = [t for t, _, _ in self.records if t is not None]
        return min(ts) if ts else None

    @property
    def latest(self):
        ts = [t for t, _, _ in self.records if t is not None]
        return max(ts) if ts else None

    def window_desc(self):
        if not self.records:
            return f"0 captured requests ({self.source})"
        e = self.earliest
        if e is None:
            return f"{len(self.records)} captured requests ({self.source}, timestamps unavailable)"
        since = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e))
        return f"{len(self.records)} captured requests ({self.source}) since {since} UTC"

    def contains(self, fp, agent=None):
        if not fp:
            return False
        for _, rec_agent, text in self.records:
            if agent is not None and rec_agent is not None and rec_agent != agent:
                continue
            if fp in text:
                return True
        return False


# ---------- prompttap loader ----------

def load_prompttap(root, max_files=None):
    """root: the prompttap dir (containing a bodies/ subdir) or the bodies/
    dir itself. Returns an EvidenceWindow, or None if nothing usable found."""
    root = Path(root).expanduser()
    bodies = root / "bodies" if (root / "bodies").is_dir() else root
    if not bodies.is_dir():
        return None
    files = sorted(bodies.glob("*.json.gz"))
    if max_files:
        files = files[-max_files:]
    records = []
    for f in files:
        try:
            ts = int(f.name.split("-", 1)[0]) / 1000
        except (ValueError, IndexError):
            ts = None
        try:
            with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
                obj = json.load(fh)
        except Exception:
            continue  # unreadable/corrupt capture file -- skip, never crash
        text = "\n".join(_flatten_strings(obj))
        records.append((ts, None, text))
    if not records:
        return None
    return EvidenceWindow("prompttap", records)


# ---------- transcript loader ----------

DEFAULT_MAX_BYTES_PER_FILE = 20_000_000  # bound time/memory on very large real transcripts


def load_transcripts(root, max_bytes_per_file=DEFAULT_MAX_BYTES_PER_FILE):
    """root: a Claude Code projects/ dir (contains <agent>/*.jsonl session
    files). Scoped to top-level session files only -- `root.glob("*/*.jsonl")`
    naturally excludes deeper <agent>/<session>/subagents/*.jsonl transcripts,
    which don't carry the user's own memory/CLAUDE.md content. Each JSONL
    line becomes one record, tagged with its owning agent (the parent dir
    name -- exactly HarnessItem.agent for a claude-code scan of this root)."""
    root = Path(root).expanduser()
    if not root.is_dir():
        return None
    files = sorted(root.glob("*/*.jsonl"))
    if not files:
        return None
    records = []
    for f in files:
        agent = f.parent.name
        try:
            read_bytes = 0
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    read_bytes += len(line)
                    if read_bytes > max_bytes_per_file:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    ts = _parse_ts(obj.get("timestamp")) if isinstance(obj, dict) else None
                    text = "\n".join(_flatten_strings(obj))
                    records.append((ts, agent, text))
        except Exception:
            continue  # unreadable file -- skip, never crash the audit
    if not records:
        return None
    return EvidenceWindow("transcripts", records)


# ---------- discovery ----------

def discover(spec="auto", prompttap_dir=None, transcripts_dir=None):
    """spec: "auto" tries the documented default locations (prompttap
    preferred, else transcripts); any other value is treated as an explicit
    directory that is tried as a prompttap dir first, then a transcripts dir.
    Returns an EvidenceWindow, or None if nothing usable was found -- callers
    must treat None as "cannot run usage-evidence checks" and say so, never
    guess. `prompttap_dir`/`transcripts_dir` let callers (and tests) override
    the real-machine defaults -- tests always pass fixture paths here."""
    if spec and spec != "auto":
        p = Path(spec).expanduser()
        return load_prompttap(p) or load_transcripts(p)
    win = load_prompttap(prompttap_dir or DEFAULT_PROMPTTAP_DIR)
    if win:
        return win
    return load_transcripts(transcripts_dir or DEFAULT_TRANSCRIPTS_DIR)


# ---------- U1 (R12): never-loaded-in-practice ----------

MIN_WINDOW_HOURS = 24  # verifier gate: a thin evidence window (e.g. prompttap's
# ~2h rotation) cannot support per-entry "never loaded" conclusions — 91 findings
# from a 2h window is noise, not signal (precision doctrine, fleet run 2026-07-08).


def window_span_hours(evidence):
    e, l = evidence.earliest, evidence.latest
    if e is None or l is None:
        return None
    return (l - e) / 3600.0


def u1_never_loaded(items, evidence, min_window_hours=MIN_WINDOW_HOURS):
    if evidence is None or len(evidence) == 0:
        return []
    span = window_span_hours(evidence)
    if span is not None and span < min_window_hours:
        from .items import Finding
        return [Finding(
            rule="U1", severity="low", item_id="(evidence window)",
            summary=f"usage-evidence window too thin for never-loaded conclusions: "
                    f"{evidence.window_desc()}, span {span:.1f}h < {min_window_hours}h — "
                    f"skipping per-entry checks",
            suggestion="Let the tap/transcripts accumulate a longer window (raise the tap's "
                       "retention cap) and re-run.",
        )]
    return _u1_never_loaded_inner(items, evidence)


def _u1_never_loaded_inner(items, evidence):
    """For each always-loaded item and each memory_entry, test whether a
    fingerprint of its content appears anywhere in the evidence window.

    - always-loaded item (claude_md/import/agents_md) never seen -> "med":
      this content SHOULD be in every single request, so its absence across
      the whole window is a likely wiring bug, not a usage question.
    - memory_entry never seen -> "low": usage-evidence that it wasn't needed
      in this window -- a candidate for archiving, NOT proof it's dead
      (hence low severity and explicit "in this window" wording).

    Items with no fingerprintable line are skipped silently -- never guess."""
    out = []
    if not evidence or not len(evidence):
        return out
    desc = evidence.window_desc()
    for it in items:
        if it.kind not in ("claude_md", "import", "agents_md", "memory_entry"):
            continue
        fp = fingerprint(it.text)
        if not fp:
            continue
        # Instruction-level always-loaded items (global CLAUDE.md, its
        # one-hop @imports, Codex AGENTS.md) apply broadly and their
        # HarnessItem.agent is a synthetic label ("global"/"codex-global"),
        # not a real transcript-project directory name -- scoping the
        # evidence check to it would just never match. Only memory_entry
        # items carry a real per-project agent name that lines up with a
        # transcript directory, so only those get agent-scoped matching.
        scope_agent = it.agent if it.kind == "memory_entry" else None
        if evidence.contains(fp, agent=scope_agent):
            continue
        quoted = fp[:60] + ("…" if len(fp) > 60 else "")
        if it.kind == "memory_entry":
            out.append(Finding(
                rule="U1", severity="low", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: usage-evidence -- never seen in any "
                        f"captured prompt in this window ({desc}) -- candidate for archive",
                evidence=f"fingerprint not found: \"{quoted}\"",
                suggestion="If it keeps not showing up across the window above, archive it out "
                           "of the always-searched memory set.",
            ))
        else:
            out.append(Finding(
                rule="U1", severity="med", item_id=it.id,
                summary=f"[{it.agent}] {it.path.name}: usage-evidence -- this always-loaded "
                        f"file's content was never seen in {desc}, but it should be in EVERY "
                        f"request -- likely a wiring bug, not a usage question",
                evidence=f"fingerprint not found: \"{quoted}\"",
                suggestion="Confirm the harness is actually reading/injecting this file -- an "
                           "always-loaded file that never appears in captured requests isn't loading.",
            ))
    return out


# ---------- U2 (R7): compaction-boundary audit ----------
# Judgment call: the harder "vanish check" (compare which always-loaded
# fingerprints were present right before a compaction boundary vs. absent
# right after) was investigated during exploration and NOT shipped. Claude
# Code transcripts log the compaction event itself with rich, exact metadata
# (timestamp, preTokens/postTokens, trigger), but the only evidence proxies
# for "what was actually in context at that moment" -- nested_memory
# attachments, tool-result file reads -- are sparse, harness-internal event
# logs, not a record of the literal system prompt on each API call. They
# don't reliably line up 1:1 with any specific boundary, so a present/absent
# comparison keyed on them would be guessing dressed up as a measurement.
# Per the precision doctrine ("uncertain -> low severity or don't ship the
# rule"), we ship the exact, certain half instead: compaction frequency.
COMPACTION_COUNT_THRESHOLD = 20


def _iter_compactions(transcripts_root):
    """Yield (agent, session_file, timestamp, compactMetadata dict) for every
    compact_boundary system record under transcripts_root. Malformed lines
    or files are skipped silently."""
    root = Path(transcripts_root).expanduser()
    if not root.is_dir():
        return
    for f in sorted(root.glob("*/*.jsonl")):
        agent = f.parent.name
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"compact_boundary"' not in line:
                        continue  # cheap prefilter before paying for json.loads
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "system" \
                            and obj.get("subtype") == "compact_boundary":
                        yield agent, f, obj.get("timestamp"), obj.get("compactMetadata") or {}
        except Exception:
            continue


def u2_compaction_frequency(transcripts_root, threshold=COMPACTION_COUNT_THRESHOLD):
    """Informational finding when a single session shows >= threshold
    compaction events -- the runaway-compaction failure class (openclaw#24179:
    211 compactions in one session). Count and token deltas are exact figures
    read straight off the transcript, nothing inferred."""
    by_session = defaultdict(list)
    for agent, f, ts, meta in _iter_compactions(transcripts_root):
        by_session[(agent, f)].append((ts, meta))
    out = []
    for (agent, f), events in sorted(by_session.items(), key=lambda kv: str(kv[0][1])):
        if len(events) < threshold:
            continue
        pre = [m.get("preTokens") for _, m in events if isinstance(m, dict) and isinstance(m.get("preTokens"), (int, float))]
        post = [m.get("postTokens") for _, m in events if isinstance(m, dict) and isinstance(m.get("postTokens"), (int, float))]
        dropped_note = ""
        if pre and post and len(pre) == len(post):
            total_dropped = sum(a - b for a, b in zip(pre, post))
            dropped_note = f", ~{total_dropped:,.0f} tokens dropped cumulatively"
        first_ts, last_ts = events[0][0], events[-1][0]
        out.append(Finding(
            rule="U2", severity="med", item_id=str(f),
            summary=f"[{agent}] {f.name}: {len(events)} compaction events in this session "
                    f"(>= {threshold}) -- usage-evidence: runaway-compaction pattern"
                    f"{dropped_note}",
            evidence=f"first={first_ts}, last={last_ts}",
            suggestion="A session compacting this often is thrashing its own context -- start a "
                       "fresh session, or trim the always-loaded layer (see S5/S8) so less has to "
                       "be rebuilt after every compaction.",
        ))
    return out


# ---------- compliance (R6): evidence-collection half only ----------
# NOT violation detection. REQUIREMENTS.md is explicit that memory-doctor only
# diagnoses the compliance gap (R6) -- turning a rule into an enforced hook is
# harness-loop's territory. This reuses S8's imperative-rule extractor and
# answers exactly one question per rule: was this rule's text ever present in
# a captured prompt? Whether the agent actually OBEYED it is a semantic
# judgment this module deliberately does not attempt.

def compliance_rows(items, evidence):
    rows = []
    for it in items:
        if it.kind not in S8_KINDS or not it.always_loaded:
            continue
        for rule_text in _imperative_rules(it.text):
            if evidence and len(evidence):
                # S8_KINDS are all always-loaded instruction-level surfaces with
                # synthetic agent labels ("global"/"codex-global") -- see the
                # matching comment in u1_never_loaded -- so check unscoped.
                loaded = "yes" if evidence.contains(rule_text[:80], agent=None) else "no"
            else:
                loaded = "unknown (no --evidence source)"
            rows.append({
                "agent": it.agent,
                "file": it.path.name,
                "rule": rule_text,
                "loaded": loaded,
                "violation_check": "requires --llm (future) or manual review",
            })
    return rows


def format_compliance_table(rows, window_desc=None):
    lines = []
    if window_desc:
        lines.append(f"evidence window: {window_desc}")
    if not rows:
        lines.append("no imperative rules found in any always-loaded file")
        return "\n".join(lines)
    lines.append(f"{'agent':<14} {'file':<22} {'loaded?':<31} rule")
    lines.append("-" * 100)
    for r in rows:
        rule_short = r["rule"][:70] + ("…" if len(r["rule"]) > 70 else "")
        lines.append(f"{r['agent']:<14} {r['file']:<22} {r['loaded']:<31} {rule_short}")
        lines.append(f"{'':<14} {'':<22} {'':<31} violation check: {r['violation_check']}")
    return "\n".join(lines)
