#!/usr/bin/env python3
"""Dev-only field test: run the L-tier against REAL fleet memory using the
local `claude -p` CLI (OAuth) as the model backend — no API key needed.
Not part of the shipped package (which stays stdlib+key based); this exists so
detector precision can be measured on live data. Strips ANTHROPIC_BASE_URL
(prompttap) and TELEGRAM_STATE_DIR (selfreview lesson: avoid hook recursion)."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_doctor.adapters import claude_code          # noqa: E402
from memory_doctor.llm_detectors import l_tier          # noqa: E402


class CLIClient:
    calls = 0

    def complete(self, prompt, system=""):
        CLIClient.calls += 1
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_BASE_URL", "TELEGRAM_STATE_DIR")}
        full = (system + "\n\n" + prompt) if system else prompt
        r = subprocess.run(["claude", "-p", "--model", "haiku"],
                           input=full, capture_output=True, text=True,
                           timeout=240, env=env)
        return r.stdout


def main():
    items = claude_code.scan(Path(os.environ.get("MEMDOC_ROOT", "~/.claude")))
    findings = l_tier(items, CLIClient(), int(os.environ.get("MEMDOC_MAX", "200")))
    print(f"L-tier field test · {len(items)} surfaces · {CLIClient.calls} model calls "
          f"· {len(findings)} findings\n" + "=" * 72)
    for f in findings:
        print(f"[{f.severity}] {f.rule} · {f.summary}")
        if f.evidence:
            print(f"    evidence: {f.evidence[:160]}")


if __name__ == "__main__":
    main()
