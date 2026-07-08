"""Codex adapter: normalize a Codex AGENTS.md surface into HarnessItems.

Surfaces read (v0.1):
  - <root>/AGENTS.md   the single bootstrap file Codex loads for a given root.
                       root is either the global config dir (~/.codex, agent
                       labeled "codex-global") or a project directory (agent
                       labeled after the dir name). Codex silently truncates
                       AGENTS.md past ~32KB with no warning to the user.
Read-only. Never writes.
"""
from pathlib import Path

from ..items import HarnessItem

# Codex silent-truncation cliff (documented failure mode: codex#7138, #13386)
AGENTS_MAX_BYTES = 32 * 1024


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except Exception:
        return ""


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


def _loaded_portion_bytes(text: str) -> float:
    """Fraction of AGENTS.md that survives Codex's 32KB byte-based truncation."""
    total = len(text.encode())
    if total == 0:
        return 1.0
    return min(1.0, AGENTS_MAX_BYTES / total)


def scan(root: Path) -> list[HarnessItem]:
    root = root.expanduser()
    items: list[HarnessItem] = []
    is_global = root.name == ".codex"
    p = root / "AGENTS.md"
    if p.exists():
        text = _read(p)
        if text:
            items.append(HarnessItem(
                id=str(p), path=p, text=text, kind="agents_md",
                agent="codex-global" if is_global else root.name,
                always_loaded=True,
                loaded_portion=_loaded_portion_bytes(text),
                meta={"mtime": _mtime(p)}))
    return items
