"""memory-doctor — audit the QUALITY of your AI agent's memory.

Usage:
  python3 -m memory_doctor                    # audit ~/.claude (Claude Code)
  python3 -m memory_doctor /path/to/.claude   # audit another root
  python3 -m memory_doctor --adapter codex    # audit ~/.codex/AGENTS.md instead
  python3 -m memory_doctor --adapter all      # audit both harnesses
  python3 -m memory_doctor --md report.md     # also write a markdown report
  python3 -m memory_doctor --evidence auto    # add U1 usage-evidence findings (opt-in)
  python3 -m memory_doctor trace [cwd]        # print what would actually load for cwd
  python3 -m memory_doctor compaction-audit [projects_dir]  # compaction-frequency report
  python3 -m memory_doctor compliance [root]  # rule -> loaded-in-evidence? table (not violation detection)

Read-only. Nothing leaves your machine.
"""
import argparse
import sys
from pathlib import Path

from . import evidence as evidence_mod
from . import llm
from .adapters import claude_code, codex
from .detectors import run_all
from .llm_detectors import l_tier
from .trace import trace as run_trace

SEV_ICON = {"high": "🔴", "med": "🟡", "low": "⚪"}
SEV_RANK = {"high": 0, "med": 1, "low": 2}


def audit_main(argv):
    ap = argparse.ArgumentParser(prog="memory-doctor", description=__doc__)
    ap.add_argument("root", nargs="?", default=None,
                     help="root to scan (default: ~/.claude, or ~/.codex for --adapter codex)")
    ap.add_argument("--adapter", choices=["claude-code", "codex", "all"], default="claude-code",
                     help="which harness to scan (default: claude-code)")
    ap.add_argument("--md", metavar="FILE", help="write a markdown report")
    ap.add_argument("--llm", action="store_true",
                     help="enable opt-in LLM-assisted detectors (contradiction/junk/claim "
                          "checks). Requires ANTHROPIC_API_KEY in the environment. Nothing "
                          "leaves your machine unless this flag is passed.")
    ap.add_argument("--llm-max-entries", type=int, default=200, metavar="N",
                     help="cap how many memory entries are sent to the LLM across the whole "
                          "run (default: 200)")
    ap.add_argument("--evidence", nargs="?", const="auto", default=None, metavar="DIR_OR_AUTO",
                     help="opt-in U-tier: add U1 usage-evidence findings by checking whether "
                          "memory content ever appears in a local capture source. 'auto' (the "
                          "default when the bare flag is given) tries prompttap bodies then "
                          "Claude Code transcripts at their documented default locations; a "
                          "path uses that directory instead. Without this flag: zero behavior "
                          "change.")
    a = ap.parse_args(argv)

    items = []
    scanned = []  # (label, root) for the header line

    if a.adapter in ("claude-code", "all"):
        cc_root = Path(a.root) if a.root else Path("~/.claude")
        items += claude_code.scan(cc_root)
        scanned.append(f"claude-code:{cc_root}")

    if a.adapter in ("codex", "all"):
        # explicit --adapter codex honors a positional root override; in --adapter
        # all mode the positional root belongs to claude-code, so codex always
        # uses its own default and is skipped silently if that dir is absent.
        if a.adapter == "codex":
            cx_root = Path(a.root) if a.root else Path("~/.codex")
        else:
            cx_root = Path("~/.codex")
        if cx_root.expanduser().exists():
            items += codex.scan(cx_root)
            scanned.append(f"codex:{cx_root}")
        elif a.adapter == "codex":
            print(f"no codex root found at {cx_root}", file=sys.stderr)

    if not items:
        print(f"no memory surfaces found ({', '.join(scanned) or a.adapter})", file=sys.stderr)
        sys.exit(1)
    findings = run_all(items)

    if a.llm:
        try:
            client = llm.LLMClient()
        except llm.LLMError as e:
            print(f"--llm error: {e}", file=sys.stderr)
            sys.exit(2)
        findings += l_tier(items, client, a.llm_max_entries)
        findings.sort(key=lambda f: (SEV_RANK[f.severity], f.rule))

    if a.evidence is not None:
        win = evidence_mod.discover(a.evidence)
        if win is None:
            print(f"--evidence: no usable capture source found for '{a.evidence}' "
                  f"(looked for prompttap bodies/ and Claude Code transcripts) -- "
                  f"skipping U1", file=sys.stderr)
        else:
            findings += evidence_mod.u1_never_loaded(items, win)
            findings.sort(key=lambda f: (SEV_RANK[f.severity], f.rule))

    n_entries = sum(1 for i in items if i.kind == "memory_entry")
    n_agents = len({i.agent for i in items if i.kind not in ("claude_md", "agents_md")})
    print(f"memory-doctor · scanned {len(items)} surfaces "
          f"({n_entries} memory entries, {n_agents} agents) — {', '.join(scanned)}")
    print(f"{'='*72}")
    if not findings:
        print("✅ no findings — unusually healthy memory (or send us your failure modes)")
    counts = {}
    for f in findings:
        counts[f.rule] = counts.get(f.rule, 0) + 1
        print(f"{SEV_ICON[f.severity]} {f.rule} · {f.summary}")
        if f.evidence:
            print(f"     evidence: {f.evidence}")
        if f.suggestion:
            print(f"     fix: {f.suggestion}")
    print(f"{'='*72}")
    print("findings by rule: " + ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())))

    if a.md:
        lines = ["# memory-doctor report", ""]
        for f in findings:
            lines += [f"## {SEV_ICON[f.severity]} {f.rule} — {f.summary}",
                      f"- evidence: {f.evidence}" if f.evidence else "",
                      f"- fix: {f.suggestion}" if f.suggestion else "", ""]
        Path(a.md).write_text("\n".join(l for l in lines if l is not None))
        print(f"markdown report -> {a.md}")


def trace_main(argv):
    ap = argparse.ArgumentParser(prog="memory-doctor trace",
                                  description="print the ordered list of files that would "
                                              "actually load for a given cwd (Claude Code)")
    ap.add_argument("cwd", nargs="?", default=".", help="directory to resolve (default: cwd)")
    a = ap.parse_args(argv)
    print(run_trace(Path(a.cwd)))


def compaction_audit_main(argv):
    ap = argparse.ArgumentParser(
        prog="memory-doctor compaction-audit",
        description="R7 (informational half): report sessions whose Claude Code transcript "
                     "shows an unusually high number of compaction events. NOT a vanish-check "
                     "(see README's U-tier section for why that variant wasn't shipped).")
    ap.add_argument("root", nargs="?", default=None,
                     help="Claude Code projects/ dir to scan (default: ~/.claude/projects)")
    ap.add_argument("--threshold", type=int, default=evidence_mod.COMPACTION_COUNT_THRESHOLD,
                     metavar="N", help="minimum compaction count in one session to report "
                                        f"(default: {evidence_mod.COMPACTION_COUNT_THRESHOLD})")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else evidence_mod.DEFAULT_TRANSCRIPTS_DIR
    findings = evidence_mod.u2_compaction_frequency(root, threshold=a.threshold)
    print(f"memory-doctor compaction-audit · {root}")
    print("=" * 72)
    if not findings:
        print(f"no session found with >= {a.threshold} compaction events")
    for f in findings:
        print(f"{SEV_ICON[f.severity]} {f.rule} · {f.summary}")
        if f.evidence:
            print(f"     evidence: {f.evidence}")
        if f.suggestion:
            print(f"     fix: {f.suggestion}")


def compliance_main(argv):
    ap = argparse.ArgumentParser(
        prog="memory-doctor compliance",
        description="R6 (evidence-collection half): list every MUST/NEVER/ALWAYS rule found "
                     "in always-loaded files and whether its text appears in a captured "
                     "prompt. This is NOT violation detection -- whether a rule was actually "
                     "obeyed needs --llm (future) or manual review.")
    ap.add_argument("root", nargs="?", default=None,
                     help="root to scan (default: ~/.claude, or ~/.codex for --adapter codex)")
    ap.add_argument("--adapter", choices=["claude-code", "codex", "all"], default="claude-code")
    ap.add_argument("--evidence", nargs="?", const="auto", default=None, metavar="DIR_OR_AUTO",
                     help="capture source to check rule-loading against (same semantics as "
                          "the main audit's --evidence). Without it, 'loaded?' is reported as "
                          "unknown for every rule -- this subcommand never guesses.")
    a = ap.parse_args(argv)

    items = []
    if a.adapter in ("claude-code", "all"):
        cc_root = Path(a.root) if a.root else Path("~/.claude")
        items += claude_code.scan(cc_root)
    if a.adapter in ("codex", "all"):
        cx_root = Path(a.root) if a.root and a.adapter == "codex" else Path("~/.codex")
        if cx_root.expanduser().exists():
            items += codex.scan(cx_root)

    win = None
    if a.evidence is not None:
        win = evidence_mod.discover(a.evidence)
        if win is None:
            print(f"--evidence: no usable capture source found for '{a.evidence}' -- "
                  f"every rule will report loaded=unknown", file=sys.stderr)

    rows = evidence_mod.compliance_rows(items, win)
    print(f"memory-doctor compliance · {len(rows)} imperative rule(s) in always-loaded files")
    print("=" * 72)
    print(evidence_mod.format_compliance_table(rows, win.window_desc() if win else None))


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "trace":
        trace_main(argv[1:])
    elif argv and argv[0] == "compaction-audit":
        compaction_audit_main(argv[1:])
    elif argv and argv[0] == "compliance":
        compliance_main(argv[1:])
    else:
        audit_main(argv)


if __name__ == "__main__":
    main()
