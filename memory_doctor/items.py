"""Core data model. Surface-agnostic by design (scope decision 2026-07-07):
memory is the first surface; skills/hooks can become HarnessItems later
without changing detector interfaces."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HarnessItem:
    """One auditable unit of persistent agent state."""
    id: str                    # stable id, e.g. "general/memory/feedback_language.md"
    path: Path                 # source file
    text: str                  # raw content
    kind: str                  # "memory_index" | "memory_entry" | "claude_md" | "import"
    agent: str                 # which agent/project this belongs to
    always_loaded: bool = False  # enters context every session
    loaded_portion: float = 1.0  # fraction of the file that actually loads (S1)
    meta: dict = field(default_factory=dict)


@dataclass
class Finding:
    rule: str                  # "S1" ...
    severity: str              # "high" | "med" | "low"
    item_id: str
    summary: str               # one line, human-readable
    evidence: str = ""         # the exact lines/paths that prove it
    suggestion: str = ""       # what to do about it
