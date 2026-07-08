# memory-doctor

**Audit the QUALITY of your AI agent's memory — what's stale, contradictory, never-loaded, or never-used.**

Your coding agent remembers things. Some of what it remembers is wrong: paths that no longer exist, facts that expired months ago, entries past the load cliff that *silently never enter context*, duplicates drifting apart. Existing tools clean up disk space and tokens; **memory-doctor audits truth**.

```bash
python3 -m memory_doctor                 # audits ~/.claude — read-only, local, nothing leaves your machine
python3 -m memory_doctor --adapter codex # audits ~/.codex/AGENTS.md instead
python3 -m memory_doctor --adapter mem0 PATH  # audits a Mem0 JSON export (EXPERIMENTAL, see below)
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

## Mem0 export audit (EXPERIMENTAL) — `--adapter mem0`

**EXPERIMENTAL.** Everything in this section is designed purely against Mem0's
*documented* export/API shapes — there is no Mem0 (mem0ai/mem0) installation on this
host, and none of it has ever run against a live store or a real user's export. Every
other precision claim in this README is measured (fixture or live fleet); this one is
not. Treat its findings as a starting point for manual review, not ground truth.

Motivation: an independent audit of a 10,134-entry production Mem0 store found **97.8%
was junk** (mem0#4573) — restated system prompts, transient chatter, and other noise
persisted as if it were durable fact. `--adapter mem0` lets you point the existing
static detectors, plus three new Mem0-specific ones, at your own export to get a first
read on the same failure class.

```bash
# export your memories first (Mem0 SDK, run wherever your Mem0 client lives):
#   import json
#   from mem0 import MemoryClient
#   data = MemoryClient().get_all()   # or .get_all(user_id=...) etc.
#   json.dump(data, open("export.json", "w"))

python3 -m memory_doctor --adapter mem0 export.json        # a single export file
python3 -m memory_doctor --adapter mem0 ./exports/          # or a directory of them
```

The adapter accepts either documented shape — a bare JSON array of memory objects, or
the `get_all()` envelope `{"results": [...]}` — and merges every `*.json` file when
given a directory. Per-object fields (`id`, `memory`, `user_id`/`agent_id`,
`created_at`/`updated_at`, `metadata`, `hash`, `score`) are mapped defensively: every
field is optional, and a malformed file or an unrecognized top-level shape degrades to
"no items from that file," never a crash.

**What runs on mem0 items:**

| Rule | Applies to mem0? | Notes |
|---|---|---|
| S2 dead-references | yes, unchanged | path/kind-agnostic already |
| S3 near-duplicates | yes, generalized | scope widened from `memory_entry` only to `("memory_entry", "mem0_memory")` — both are the same shape of thing (freeform text recalled on demand) |
| S6 date-rot | yes, generalized | same widening as S3 |
| S9 poisoning/scaffold scan | yes, generalized | same widening as S3 |
| S1, S4, S5, S7, S8 | **no, not forced** | these assume an always-loaded/indexed-file model (load cliffs, index files, imperative-rule files) that doesn't map onto Mem0's recalled-on-demand memory objects |
| `--llm` L-tier | yes | `_capped_entries_by_agent` was widened the same way as S3/S6/S9 for this batch — before, the cap/grouping pass only looked at `kind == "memory_entry"`, so mem0 items were silently excluded from every LLM prompt even with `--llm` on. That's fixed now: L1/L2 run over mem0 items exactly like Claude Code memory entries. |

**New Mem0-specific checks (registered only when the scan contains ≥1 `mem0_memory` item):**

| Rule | What it catches | What it can conclude | What it CANNOT conclude |
|---|---|---|---|
| **M1 ghost-suspects** | an object whose own `metadata` marks it deleted/removed, yet it's still sitting in the export | this one, specific, self-contradictory record | this is only the export-visible half of R9's cross-store ghost check — Mem0 can back a single logical memory with a vector store, a graph store, and an entity table, and a leftover ghost that isn't flagged in its own metadata is invisible from a static export. A full check needs live access to every backing store. |
| **M2 subject-conflict** (R10, lite) | two-or-more objects for the same agent sharing an explicit `metadata.subject` or `metadata.entity` value, but with different memory text | a same-subject group with textually different content, worth a human look | which value is current — this rule doesn't rank or timestamp-compare; and if the export carries **no** `subject`/`entity` field at all, this check emits nothing rather than guess at what "same subject" means from free text (precision over coverage, required by spec) |
| **M3 attribution smell** | memory text opening in assistant voice ("I recommend...", "You should...", "As an AI...") stored as if it were a durable user fact — mem0#5642's class | that the text reads as assistant-voice | who it *should* be attributed to, or whether it's actually wrong — a human still has to look |

**Explicitly not shipped — R11 (round-trip probes).** The spec's round-trip check (write
a canary memory, read it back through the Mem0 API, confirm fidelity) requires a live,
writable Mem0 store to run against. This project is read-only by design and has no such
store available, so R11 is **not implemented** here — faking it against static fixtures
would just be theater. If you have a live store, the honest way to get this signal today
is to write a canary through your own Mem0 client and diff the read-back yourself.

**Fixtures**: `fixtures/mem0_export/` (both documented shapes, one planted hit per rule
above) and `fixtures/mem0_export_malformed/` / `fixtures/mem0_export_no_subject/` for the
defensive-parsing and "no subject field → M2 stays silent" cases. `tests/test_mem0.py`
and `tests/test_mem0_checks.py` cover all of it.

## Measured precision (field test, 2026-07-08, live 13-agent fleet)

| Tier | Result | Notes |
|---|---|---|
| L1 supersession | **3/3 correct** | all three flagged pairs were genuine stale-vs-newer conflicts, human-verified |
| L1 junk classification | ~5/7 useful | all low severity; "restates instruction files" hits were accurate, two debatable |
| L2 claim probe | 1/3 on first run → **two FP classes fixed** | relative-path claims now skipped (no known base); command misses demoted to low + hedged ("fail2ban" missed while `fail2ban-client` existed) |

This table is the product promise: every rule's precision gets measured on real data and the
failure modes get fixed or the rule gets demoted — findings you can't trust are worse than no findings.

## Principles

- **Local-first.** Reads your files, phones nothing home — except the opt-in `--llm` tier, which only ever talks to your own configured Anthropic endpoint, and only when you pass the flag.
- **Read-only by default.** A future `--fix` will only ever propose diffs for you to approve.
- **The audited auditor.** A memory linter you can't verify is just more vibes — every detector's precision is measured and published.
- **Stdlib only.** No dependencies to trust — including the LLM tier, which talks to the Anthropic API via `urllib`, no SDK.

## Status

v0.3 — Claude Code + Codex adapters, static S-tier, the opt-in LLM-assisted L-tier, the opt-in usage-evidence U-tier (`--evidence`, `compaction-audit`, `compliance`), and an experimental Mem0 export adapter (`--adapter mem0`, M1-M3 checks — unmeasured against a live store, see the Mem0 section above). Hermes / Zep adapters are next. Built from the failure taxonomy of a production agent-memory research project; issues with your own memory failure patterns are extremely welcome.

MIT license.
