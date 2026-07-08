"""memory-doctor — audit the QUALITY of your AI agent's memory.

Usage:
  python3 -m memory_doctor                    # audit ~/.claude
  python3 -m memory_doctor /path/to/.claude   # audit another root
  python3 -m memory_doctor --md report.md     # also write a markdown report

Read-only. Nothing leaves your machine.
"""
import argparse
import sys
from pathlib import Path

from .adapters import claude_code
from .detectors import run_all

SEV_ICON = {"high": "🔴", "med": "🟡", "low": "⚪"}


def main():
    ap = argparse.ArgumentParser(prog="memory-doctor", description=__doc__)
    ap.add_argument("root", nargs="?", default="~/.claude")
    ap.add_argument("--md", metavar="FILE", help="write a markdown report")
    a = ap.parse_args()

    items = claude_code.scan(Path(a.root))
    if not items:
        print(f"no memory surfaces found under {a.root}", file=sys.stderr)
        sys.exit(1)
    findings = run_all(items)

    n_entries = sum(1 for i in items if i.kind == "memory_entry")
    n_agents = len({i.agent for i in items if i.kind != "claude_md"})
    print(f"memory-doctor · scanned {len(items)} surfaces "
          f"({n_entries} memory entries, {n_agents} agents) under {a.root}")
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


if __name__ == "__main__":
    main()
