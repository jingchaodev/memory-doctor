# Agent Workspace

This repo uses `agentws` — a local-first, file-backed handoff protocol that makes this
repository safely resumable by multiple coding agents (Claude Code, Codex, Gemini, ...).

The workspace is the source of truth. Agents are interchangeable workers. Context should not
live primarily inside one model's private conversation — it lives here, as durable, structured,
agent-readable state.

## Files

| File | Responsibility | Source of truth? |
|---|---|---|
| `README.md` | Explains the protocol to humans and agents | Yes, for protocol usage |
| `STATE.md` | Compact current project state | Derived/curated summary |
| `TASKS.json` | Active task and done criteria | Yes, for task state |
| `DECISIONS.md` | Architecture/product decisions | Yes, for decisions |
| `RUNLOG.jsonl` | Append-only event log | Yes, for execution trace |
| `CLAIMS.jsonl` | Agent claims with verification status | Yes, for claims/evidence |
| `HANDOFF.md` | Generated handoff prompt/context | Generated artifact |
| `CHECKPOINTS/latest.*` | Last checkpoint snapshot | Generated artifact |
| `local/` | Captured command output (gitignored) | Evidence artifact |
| `templates/` | Human/agent templates | Protocol support |

## Commands

- `agentws init` — initialize the workspace in a git repo.
- `agentws enter` — print the canonical agent entry briefing.
- `agentws checkpoint --summary "..." --next "..."` — capture current handoff state.
- `agentws handoff --for codex|claude-code|gemini` — print a handoff brief for another agent.
- `agentws doctor` — score handoff readiness out of 100.

A cold agent arriving in this repo should read, in order:

1. `.agent-workspace/STATE.md`
2. `.agent-workspace/TASKS.json`
3. `.agent-workspace/DECISIONS.md`
4. `.agent-workspace/HANDOFF.md`

Or simply run `agentws enter`.
