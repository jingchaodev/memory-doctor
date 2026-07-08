"""memory-doctor — audit the QUALITY of your AI agent's memory.

Usage:
  python3 -m memory_doctor                    # audit ~/.claude (Claude Code)
  python3 -m memory_doctor /path/to/.claude   # audit another root
  python3 -m memory_doctor --adapter codex    # audit ~/.codex/AGENTS.md instead
  python3 -m memory_doctor --adapter all      # audit both harnesses
  python3 -m memory_doctor --md report.md     # also write a markdown report
  python3 -m memory_doctor trace [cwd]        # print what would actually load for cwd

Read-only. Nothing leaves your machine.
"""
import argparse
import sys
from pathlib import Path

from .adapters import claude_code, codex
from .detectors import run_all
from .trace import trace as run_trace

SEV_ICON = {"high": "🔴", "med": "🟡", "low": "⚪"}


def audit_main(argv):
    ap = argparse.ArgumentParser(prog="memory-doctor", description=__doc__)
    ap.add_argument("root", nargs="?", default=None,
                     help="root to scan (default: ~/.claude, or ~/.codex for --adapter codex)")
    ap.add_argument("--adapter", choices=["claude-code", "codex", "all"], default="claude-code",
                     help="which harness to scan (default: claude-code)")
    ap.add_argument("--md", metavar="FILE", help="write a markdown report")
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


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "trace":
        trace_main(argv[1:])
    else:
        audit_main(argv)


if __name__ == "__main__":
    main()
