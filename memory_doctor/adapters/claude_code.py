"""Claude Code adapter: normalize ~/.claude into HarnessItems.

Surfaces read (v0.0):
  - <root>/CLAUDE.md                       global instructions (always loaded)
  - @import targets referenced from CLAUDE.md files (one hop)
  - <root>/projects/*/memory/MEMORY.md     auto-memory index (always loaded, TRUNCATED
                                           by the platform at 200 lines / 25 KB)
  - <root>/projects/*/memory/*.md          memory entry files (loaded on demand)
Read-only. Never writes.
"""
import re
from pathlib import Path

from ..items import HarnessItem

# Claude Code auto-memory load cliff (documented platform behavior)
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024

IMPORT_RE = re.compile(r"^@(/[^\s]+|~/[^\s]+)", re.M)


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except Exception:
        return ""


def _loaded_portion_index(text: str) -> float:
    """Fraction of an auto-memory index that survives the 200-line/25KB cliff."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return 1.0
    loaded_lines = lines[:INDEX_MAX_LINES]
    # byte cap applies too — whichever cuts first
    total, kept = 0, 0
    for i, ln in enumerate(loaded_lines):
        b = len(ln.encode())
        if total + b > INDEX_MAX_BYTES:
            break
        total += b
        kept = i + 1
    return kept / len(lines)


def scan(root: Path) -> list[HarnessItem]:
    root = root.expanduser()
    items: list[HarnessItem] = []

    def add_claude_md(p: Path, agent: str):
        text = _read(p)
        if not text:
            return
        items.append(HarnessItem(
            id=str(p), path=p, text=text, kind="claude_md",
            agent=agent, always_loaded=True))
        # one-hop @imports also always load
        for m in IMPORT_RE.finditer(text):
            ip = Path(m.group(1)).expanduser()
            if ip.exists() and ip.suffix == ".md":
                items.append(HarnessItem(
                    id=str(ip), path=ip, text=_read(ip), kind="import",
                    agent=agent, always_loaded=True))

    g = root / "CLAUDE.md"
    if g.exists():
        add_claude_md(g, agent="global")

    proj_root = root / "projects"
    if proj_root.is_dir():
        for proj in sorted(proj_root.iterdir()):
            mem = proj / "memory"
            if not mem.is_dir():
                continue
            agent = proj.name
            idx = mem / "MEMORY.md"
            if idx.exists():
                text = _read(idx)
                items.append(HarnessItem(
                    id=str(idx), path=idx, text=text, kind="memory_index",
                    agent=agent, always_loaded=True,
                    loaded_portion=_loaded_portion_index(text)))
            for f in sorted(mem.glob("*.md")):
                if f.name == "MEMORY.md":
                    continue
                items.append(HarnessItem(
                    id=str(f), path=f, text=_read(f), kind="memory_entry",
                    agent=agent, always_loaded=False))
    return items
