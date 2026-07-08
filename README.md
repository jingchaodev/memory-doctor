# memory-doctor

**Audit the QUALITY of your AI agent's memory — what's stale, contradictory, never-loaded, or never-used.**

Your coding agent remembers things. Some of what it remembers is wrong: paths that no longer exist, facts that expired months ago, entries past the load cliff that *silently never enter context*, duplicates drifting apart. Existing tools clean up disk space and tokens; **memory-doctor audits truth**.

```bash
python3 -m memory_doctor                 # audits ~/.claude — read-only, local, nothing leaves your machine
python3 -m memory_doctor --adapter codex # audits ~/.codex/AGENTS.md instead
python3 -m memory_doctor trace /path/to/project   # what would ACTUALLY load for this cwd
```

Real output from the author's own 6-month-old multi-agent setup:

```
memory-doctor · scanned 110 surfaces (96 memory entries, 13 agents) under ~/.claude
🟡 S2 · [global] CLAUDE.md: 2 referenced path(s) no longer exist
🟡 S2 · [admin-bot] reference_cron_status.md: 3 referenced path(s) no longer exist
🟡 S6 · [general] project_smh_put_spread_plan.md: relative date ("下周") in durable memory
⚪ S4 · 5 memory files exist but are NOT in the index — invisible unless recalled by name
⚪ S5 · always-loaded layer ≈ 9,133 tokens — paid EVERY session
findings by rule: S2×14, S4×1, S5×1, S6×1
```

## Detectors (v0.0 — static tier, zero dependencies)

| Rule | What it catches | Why it matters |
|---|---|---|
| **S1 load-truncation** | MEMORY.md beyond the 200-line / 25KB cliff | that memory **silently never loads** |
| **S2 dead-references** | memory citing files/paths that no longer exist | agent acts on ghosts, wastes turns |
| **S3 near-duplicates** | entries ≥60% similar within one agent | duplicates drift, then contradict |
| **S4 index-orphans** | index↔files mismatch, both directions | unindexed memory is invisible |
| **S5 bloat profile** | token cost of the always-loaded layer | you pay it every single session |
| **S6 date-rot** | relative dates ("next week") + year-old facts | meaningless or expired at recall time |
| **S7 staleness-by-neglect** | an always-loaded instruction file untouched for 90+ days while memory keeps changing | rules nobody has revisited may no longer hold |

Every rule ships with golden fixtures (`fixtures/`, `tests/`) and is precision-tested on a live 4-agent fleet before release. Roadmap: LLM-assisted contradiction detection (opt-in, your own key), and a usage tier that answers *"did this memory ever actually reach a prompt?"* via a local request tap.

### Codex support

`--adapter codex` (or `--adapter all`) reads a Codex `AGENTS.md` and applies the same S1 load-truncation check against Codex's own silent 32KB cutoff — no other flags needed.

### `trace` — what actually loads for a cwd

Claude Code's own discovery-order docs are known to be internally inconsistent (anthropics/claude-code#722). `trace` prints its best current read of the ordered load list for a directory — global CLAUDE.md, the ancestor-dir chain down to your cwd (+ `CLAUDE.local.md`), one-hop `@import`s, and the auto-memory `MEMORY.md` — plus a conservative "suspects" section for nearby files that look load-bearing but wouldn't load from that cwd:

```bash
python3 -m memory_doctor trace ~/my-project
```

## Principles

- **Local-first.** Reads your files, phones nothing home.
- **Read-only by default.** A future `--fix` will only ever propose diffs for you to approve.
- **The audited auditor.** A memory linter you can't verify is just more vibes — every detector's precision is measured and published.
- **Stdlib only.** No dependencies to trust.

## Status

v0.0 — Claude Code adapter only. Hermes / Mem0 / Zep adapters and the usage tier are next. Built from the failure taxonomy of a production agent-memory research project; issues with your own memory failure patterns are extremely welcome.

MIT license.
