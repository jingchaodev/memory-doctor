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
| **S8 rule-density lint** | imperative rules (MUST/NEVER/ALWAYS/DO NOT/必须/不要/禁止) that run past ~1 sentence, or a single always-loaded file with 40+ of them | verbose rules are observably ignored; large rule counts dilute attention |
| **S9 poisoning/scaffold scan** | chat-template control tokens, instruction-injection-shaped text, or staging markers leaked into memory | a stray `<|im_start|>` or "ignore previous instructions" in durable memory can self-reinforce into a poisoning loop |

S1 also emits a **near-cliff warning** (med severity) once a memory index or AGENTS.md crosses 90% of its load limit but hasn't been truncated yet — the point where trimming is still easy, before writes start silently failing.

Every rule ships with golden fixtures (`fixtures/`, `tests/`) and is precision-tested on a live 4-agent fleet before release. LLM-assisted contradiction/junk/claim detection ships as the opt-in `--llm` tier below (your own key, nothing runs by default). A usage-evidence tier — *"did this memory ever actually reach a prompt?"* — now ships too, opt-in, described below.

## L-tier (opt-in) — `--llm`

```bash
python3 -m memory_doctor --llm                        # adds LLM-assisted findings
python3 -m memory_doctor --llm --llm-max-entries 50    # cap cost across the whole run
```

Everything above this line runs with zero dependencies and zero network calls. `--llm`
adds three extra, judgment-based checks that a regex can't do — but they're advisory,
not ground truth, so every finding is capped at **med/low severity** and its summary is
always prefixed **"LLM-assisted"** so you can tell a probabilistic finding apart from a
deterministic S-tier one at a glance.

| Rule | What it catches |
|---|---|
| **L1 contradiction/supersession** | one prompt per agent, batching its memory entries, asks the model to find pairs that conflict or where a newer entry has clearly replaced an older one on the same subject |
| **L1 junk/overgeneralization** | a second prompt classifies every entry (durable fact / preference / project state / transient task / system restatement / noise) and flags entries junk-classed, plus any absolute "always/never" rule whose own text shows it was generalized from a single dated incident |
| **L2 verifiable-claim probe** | a third prompt extracts claims of the shape "path X exists" or "command Y is installed"; memory-doctor then verifies each **locally** (`Path.exists()` / `shutil.which()` — nothing else, ever) and flags the ones that don't hold. This catches claims S2's regex structurally can't see: bare command names and relative/tilde-less path mentions phrased in prose. |

Privacy: this tier **only** runs when you pass `--llm`. It reads `ANTHROPIC_API_KEY`
from your own environment (never a CLI flag, never logged, never written anywhere) and
sends entry content only to your configured Anthropic endpoint — nowhere else. Missing
key + `--llm` fails clean (exit 2) before any network call. Cost is bounded by
`--llm-max-entries` (default 200) and a 500-char truncation per entry; exactly 3 LLM
calls happen per agent that has entries within the cap. Any malformed or unparseable
model response is skipped silently — it is never allowed to crash the audit.

### Codex support

`--adapter codex` (or `--adapter all`) reads a Codex `AGENTS.md` and applies the same S1 load-truncation check against Codex's own silent 32KB cutoff — no other flags needed.

### `trace` — what actually loads for a cwd

Claude Code's own discovery-order docs are known to be internally inconsistent (anthropics/claude-code#722). `trace` prints its best current read of the ordered load list for a directory — global CLAUDE.md, the ancestor-dir chain down to your cwd (+ `CLAUDE.local.md`), one-hop `@import`s, and the auto-memory `MEMORY.md` — plus a conservative "suspects" section for nearby files that look load-bearing but wouldn't load from that cwd:

```bash
python3 -m memory_doctor trace ~/my-project
```

## U-tier (usage evidence) — `--evidence`, `compaction-audit`, `compliance`

Every check above this line only reads the memory *files*. It can tell you a file is
stale, duplicated, or truncated -- it cannot tell you whether the content ever actually
reached a prompt. The U-tier answers that, by checking a local capture of real requests.
It needs one of two evidence sources on disk; without either, these commands say so and
do nothing else.

**Evidence sources** (in preference order):

- **prompttap body captures** — a local reverse-proxy tap in front of the Anthropic
  Messages API, if you run one, writes one gzip JSON file per request to
  `<prompttap_dir>/bodies/*.json.gz`. This is the literal API request body -- the
  highest-precision evidence available. But it is typically rotated aggressively (a fixed
  file-count cap), so the retained window is often just the last hour or two -- every
  U-tier finding says exactly how many requests and since when, so you can judge whether
  "never seen" means "genuinely unused" or "just outside a short window."
- **Claude Code session transcripts** (`~/.claude/projects/<agent>/*.jsonl`) — not the
  literal API request (Claude Code doesn't log that), but a stream of session events
  (attachments, tool results, compaction markers) that reliably quotes file content
  verbatim when a memory file was read or injected. Retention is whatever you kept on
  disk -- usually much longer than prompttap's window, and in practice the more useful
  source on a host where the tap has already rotated.

```bash
python3 -m memory_doctor --evidence auto        # add U1 findings using whichever source exists
python3 -m memory_doctor --evidence /path/to/dir  # use a specific capture dir instead
python3 -m memory_doctor compaction-audit         # R7: sessions with a runaway compaction count
python3 -m memory_doctor compliance --evidence auto  # R6: rule -> loaded-in-evidence? table
```

| Rule / command | What it checks | What it can conclude | What it cannot conclude |
|---|---|---|---|
| **U1** (`--evidence`, R12) | for every always-loaded file and every memory entry, whether a distinctive content fingerprint ever shows up in the evidence window | an always-loaded file's content missing from *every single request in the window* is a likely wiring bug (med); a memory entry missing from the window is usage-evidence it wasn't needed there (low, "candidate for archive") | it is never proof of permanent non-use -- only of non-use *within the captured window*, which is why every finding states the window size and start date |
| **U2** (`compaction-audit`, R7) | compaction-event count per session, from Claude Code's own `compact_boundary` markers | exact compaction count and token deltas per session; flags sessions with an unusually high count (>=20 by default, calibrated against the "211 compactions" openclaw#24179 runaway class) | the harder version -- "which specific always-loaded item vanished at *this* compaction boundary" -- was investigated and **not shipped**; see the judgment-call note below |
| **compliance** (R6) | reuses S8's imperative-rule extractor (MUST/NEVER/ALWAYS/DO NOT/必须/不要/禁止) over always-loaded files; for each rule, whether its text appears in the evidence window | whether a rule's text was *present* in a captured prompt | whether the agent actually **obeyed** it -- that's a semantic judgment this module deliberately does not attempt. Every row's violation-check column reads `requires --llm (future) or manual review` on purpose; turning a rule into an enforced check is out of scope for a read-only auditor |

**Judgment call: why the compaction "vanish check" wasn't shipped.** The R7 spec asks
for something stronger than a frequency count: for each compaction, compare which
always-loaded fingerprints were present right before it against right after, and flag
anything that vanished. Real transcripts do log the compaction event itself with exact
metadata (`trigger`, `preTokens`, `postTokens`, `durationMs`) -- but the only evidence
proxies for "what was actually in the system prompt at that exact moment" are
`nested_memory` attachments and tool-result file reads, which are sparse, harness-internal
event logs, not a record of the literal request on every single API call. They don't
reliably line up 1:1 with any one specific boundary. A present/after-vanish comparison
built on them would be guessing dressed up as a measurement -- exactly what the precision
doctrine says not to ship. The exact, certain half (compaction frequency) shipped instead;
the vanish-check remains a documented gap, not a silent one.

**Privacy / cost**: same as the rest of memory-doctor -- read-only, stdlib-only, no
network calls. The U-tier reads capture files that already exist on your disk (written by
a tap or by Claude Code itself); it does not create, modify, or transmit anything.

### Codex and the U-tier

`compliance` accepts `--adapter codex` the same way the main audit does. `--evidence` and
`compaction-audit`, however, are Claude Code-specific: Codex has no equivalent local
transcript or tap capture on this host, so there's nothing to check against for a Codex
`AGENTS.md` today.

## Principles

- **Local-first.** Reads your files, phones nothing home — except the opt-in `--llm` tier, which only ever talks to your own configured Anthropic endpoint, and only when you pass the flag.
- **Read-only by default.** A future `--fix` will only ever propose diffs for you to approve.
- **The audited auditor.** A memory linter you can't verify is just more vibes — every detector's precision is measured and published.
- **Stdlib only.** No dependencies to trust — including the LLM tier, which talks to the Anthropic API via `urllib`, no SDK.

## Status

v0.2 — Claude Code + Codex adapters, static S-tier, the opt-in LLM-assisted L-tier, and the opt-in usage-evidence U-tier (`--evidence`, `compaction-audit`, `compliance`). Hermes / Mem0 / Zep adapters are next. Built from the failure taxonomy of a production agent-memory research project; issues with your own memory failure patterns are extremely welcome.

MIT license.
