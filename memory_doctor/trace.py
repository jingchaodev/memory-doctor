"""R2 · resolution trace: for a given cwd, print the ORDERED list of Claude
Code memory/instruction files that would actually load, then a conservative
"suspects" section for nearby files that look load-bearing but wouldn't load.

Discovery-order caveat: Claude Code's own docs on CLAUDE.md discovery order
are known to be internally inconsistent (anthropics/claude-code#722). This
trace follows the commonly-documented order — global -> ancestor-dir chain
(root to cwd) -> one-hop @imports -> auto-memory — but treat it as a best
current understanding, not ground truth; verify anything load-bearing.

Read-only. Never writes.
"""
from pathlib import Path

from .adapters.claude_code import IMPORT_RE, _loaded_portion_index

CAVEAT = ("discovery order follows commonly-documented behavior; Claude Code's docs "
          "are known to be internally inconsistent here (see anthropics/claude-code#722) "
          "— verify anything load-bearing")


def encode_cwd(cwd: Path) -> str:
    """Mirror Claude Code's auto-memory dir naming: cwd with '/' -> '-'.
    Verified empirically against real ~/.claude/projects/* dir names (e.g.
    /root -> -root). Undocumented, and this repo's own project list shows the
    encoding isn't fully consistent for dotted path segments — treat this as
    a best-effort match, not a guarantee."""
    return str(cwd).replace("/", "-")


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except Exception:
        return ""


def _row(order, p: Path):
    exists = p.exists()
    size = p.stat().st_size if exists else 0
    return order, p, exists, size


def trace(cwd: Path, home: Path = None) -> str:
    cwd = Path(cwd).expanduser().resolve()
    home = Path(home).expanduser() if home else Path.home()

    rows = []       # (order, path, exists, size)
    claude_texts = []

    def add(p):
        rows.append(_row(len(rows) + 1, p))
        return rows[-1][2]  # exists

    # 1. global CLAUDE.md
    g = home / ".claude" / "CLAUDE.md"
    if add(g):
        claude_texts.append(_read(g))

    # 2. ancestor-dir chain, filesystem root down to cwd, + CLAUDE.local.md beside each
    chain = list(reversed([cwd] + list(cwd.parents)))  # root first, cwd last
    for d in chain:
        cf = d / "CLAUDE.md"
        if add(cf):
            claude_texts.append(_read(cf))
        add(d / "CLAUDE.local.md")

    # 3. one-hop @imports from every CLAUDE.md that actually loaded
    seen = set()
    for text in claude_texts:
        for m in IMPORT_RE.finditer(text):
            ip = Path(m.group(1)).expanduser()
            if ip in seen:
                continue
            seen.add(ip)
            add(ip)

    # 4. auto-memory index for this cwd
    encoded = encode_cwd(cwd)
    mem = home / ".claude" / "projects" / encoded / "memory" / "MEMORY.md"
    mem_exists = add(mem)
    mem_pct = None
    if mem_exists:
        mem_pct = _loaded_portion_index(_read(mem))

    out = [f"resolution trace for cwd={cwd}", f"(caveat: {CAVEAT})", "-" * 72]
    for order, p, exists, size in rows:
        extra = ""
        if p == mem and mem_exists and mem_pct is not None:
            extra = f"  loaded={mem_pct:.0%}"
            if mem_pct < 1.0:
                extra += " (200-line/25KB cliff hit — see S1)"
        out.append(f"{order:>2}. {'yes' if exists else 'no ':<3} {size:>7}B  {p}{extra}")

    # ---- suspects: conservative, nearby-but-not-loaded ----
    suspects = []
    loaded_paths = {p for _, p, _, _ in rows}

    # a) CLAUDE.md / AGENTS.md one level below cwd — a subdirectory's own file,
    # which would only load if the agent were actually invoked from in there.
    try:
        for pattern in ("*/CLAUDE.md", "*/AGENTS.md"):
            for sub in sorted(cwd.glob(pattern)):
                if sub not in loaded_paths:
                    suspects.append((sub, "in a subdirectory below cwd — only loads if invoked from there"))
    except Exception:
        pass

    # b) other projects/<encoding>/memory dirs whose name looks related to this
    # cwd (e.g. the dir got renamed/moved) — memory that's now orphaned from cwd.
    # Only worth surfacing when cwd's OWN auto-memory dir is missing (mem_exists
    # is False) — otherwise the correct one already resolved and this is just
    # substring noise (e.g. "root" matches nearly every project under /root).
    proj_root = home / ".claude" / "projects"
    if proj_root.is_dir() and not mem_exists:
        cwd_name = cwd.name
        if len(cwd_name) >= 4:  # short/common names (src, lib, root...) are too noisy to match on
            for d in sorted(proj_root.iterdir()):
                if d.name == encoded:
                    continue
                if cwd_name in d.name:
                    cand = d / "memory" / "MEMORY.md"
                    if cand.exists():
                        suspects.append((cand, "similarly-named auto-memory dir — check for a moved/renamed cwd"))

    out.append("-" * 72)
    out.append("suspects (possible, verify manually):")
    if not suspects:
        out.append("  (none found)")
    else:
        for p, why in suspects:
            out.append(f"  - {p}  [{why}]")

    return "\n".join(out)
