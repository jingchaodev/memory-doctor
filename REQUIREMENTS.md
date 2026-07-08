# Requirements — mined from the ecosystem (2026-07-08)

Sources: GitHub issues of mem0ai/mem0 (78 matched), langchain-ai/langmem, getzep/graphiti, letta-ai/letta,
anthropics/claude-code, openai/codex, plus HN (Algolia, ~150 hits reviewed). Mined by 4 parallel worker
agents; synthesized and prioritized here. **Graduation bar for building anything: observed ≥2× in the wild,
plausibly ≥80% precision, within the current surface scope.**

Recurring meta-finding: *duplicate-merge + stale-prune + contradiction-detection* is independently named the
missing core capability by builders across every source. One production audit of a 10,134-entry Mem0 store
found **97.8% junk** (52.7% system-prompt restatement, 11.5% cron noise) — mem0#4573.

## Ship next (v0.1.x — static, Claude Code + Codex)

| id | Requirement | Evidence (recurrence) |
|----|-------------|----------------------|
| R1 | **Codex/AGENTS.md adapter + 32KB silent-truncation check** (S1 sibling). Codex silently drops AGENTS.md content past ~32KB with zero warning. Cheap, expands the audience to codex users. | codex#7138, #13386 |
| R2 | **Resolution trace**: given a cwd, print the ordered list of memory files that ACTUALLY load (global → project → rules → imports), flagging files the user believes are active but aren't. Docs themselves conflict on discovery order. | claude-code#722, #16299, #16853 (paths-scoped rules leak/fail both directions) |
| R3 | **Rule-length / info-density lint**: imperative rules >~1 sentence are observably ignored; flag verbose rules and decoration-heavy entries. | HN 48160604 ("one sentence" consensus), 47144537, mem0#4573 junk categories |

## v0.2 (L-tier + U-tier — the differentiators)

| id | Requirement | Evidence |
|----|-------------|----------|
| R4 | **Contradiction / supersession detector** (L1, already designed — evidence now overwhelming): embed-cluster same-subject entries, judge-classify duplicate vs conflicting; also flag "newer same-subject fact exists but old one still loads". | mem0 ADD-only ×4 issues, graphiti#630 (over-merge drops updates), #1166 (destructive overwrite), HN ×4 |
| R5 | **Junk-content classifier**: system-prompt restatement / cron noise / transient task state / architecture dump vs durable fact. The 97.8% stat is the demo. | mem0#4573 (audited), #2736, HN "earn its keep" |
| R6 | **Compliance-gap audit (U-tier flagship)**: extract MUST/NEVER rules from memory, correlate with local session transcripts for violations → "loaded ≠ obeyed; this rule isn't memory-enforceable, make it a hook." Claude Code JSONL transcripts make this local-first feasible. | claude-code#2544/#33603/#2142/#53223, codex#23496/#4466 |
| R7 | **Compaction-boundary audit**: fingerprint the active instruction set before/after compaction events in session logs; flag rules/facts that vanish at the boundary. Compaction is the #1 memory-fidelity destroyer in both CLIs. | claude-code#9796/#13919/#24179, codex#5957/#25792, letta#3270/#3242 |
| R8 | **Over-generalization flag**: absolute rules citing a single dated incident, no scope/expiry ("don't use Stripe" said once → permanent bias). | HN 47900726; new taxonomy class |

## v0.3 (framework adapters — Mem0 first, the junk-audit demo)

| id | Requirement | Evidence |
|----|-------------|----------|
| R9 | **Cross-store ghost check**: entry deleted in one backing store but alive in another (vector vs graph vs entity table) still surfaces in search. | mem0 ×6 issues (#3695, #4869, #3245, #5577, #4863, #2165), letta#2237 |
| R10 | **Cross-file/store consistency**: same fact, different values across files/stores (SQL vs vector drift; CLAUDE.md vs AGENTS.md vs project memory duplication). | HN 46205076, claude-code#34235 family, mem0#3371 |
| R11 | **Round-trip / configured-but-dead probes**: write via each write path, confirm retrievability via each read path; flag configured-but-zero-calls stores and embedder-drift (memories stored under a different embedding model = unretrievable). | langmem#140/#138/#114, letta#3210 |
| R12 | **Never-retrieved-in-N-days + feedback-loop amplification** (recalled memories re-extracted as new duplicates → runaway growth). | mem0#5330 family ×5, #4573 |

## Recorded, not building (out of scope / not-auditable)

- Retrieval score degeneracy, DB timeouts, async update races — framework bugs, not user-data audits.
- Session-history reachability windows (codex#21128) — UI/retrieval surface.
- Scope-metadata schema gaps (graphiti#436) — design limitation; partial mitigation folded into R4 (flag scope-less facts as contradiction-ambiguous).
- Enforcement layer for rules (turn rules into hooks) — that's harness-loop's territory; memory-doctor only *diagnoses* the compliance gap (R6).

## Requirements intake (ongoing)

1. Own fleet: weekly cron scan diff (live) + selfreview flywheel false-negative wiring.
2. Issue templates: false-positive / false-negative / new-surface (live).
3. Ecosystem: re-run this mining quarterly; openclaw + NousResearch/hermes-agent trackers unmined (26K open issues — next pass).
4. Platform changelogs: Claude Code memory behavior changes (dream, load limits) = compatibility requirements.
