"""Mem0 export adapter (EXPERIMENTAL): normalize a Mem0 JSON export into HarnessItems.

EXPERIMENTAL: there is no Mem0 (mem0ai/mem0) installation on this host and this
adapter has never been run against a live Mem0 store. It is designed purely from
Mem0's DOCUMENTED shapes -- the `client.get_all()` API response and a plain
JSON-array export -- so field coverage may be incomplete against real exports
from a given Mem0 version. Every precision claim elsewhere in this project is
measured against a fixture or a live fleet; this adapter's checks are NOT --
treat findings from it as a starting point for manual review, not ground truth.
See README's "Mem0 export audit (experimental)" section for the full caveat.

Surfaces read:
  - a single export FILE, containing either:
      * a bare JSON array of memory objects: [ {...}, {...} ], or
      * the Mem0 API get_all() envelope: {"results": [ {...}, {...} ]}
  - a DIRECTORY: every *.json file inside is read the same way and merged
    (useful when memories were exported per-user/per-agent into separate files).

Per-object field mapping (every field is optional -- a real export may omit
any of these, and a missing/unrecognized field must never raise):
  id                    -> folded into the HarnessItem's stable id
  memory                -> item text (an object with no usable string here is skipped --
                           there's nothing to audit without content)
  user_id / agent_id    -> item.agent (user_id preferred; "unknown" if neither present)
  created_at/updated_at -> meta["mtime"] (ISO8601 -> unix seconds; updated_at wins if both
                           present since it's the more recent truth; missing -> 0.0)
  metadata              -> meta["metadata"] (raw dict, used by mem0_checks.py's M1/M2)
  hash                  -> meta["hash"]
  score                 -> meta["score"]

kind is always "mem0_memory". always_loaded=False (Mem0 memories are recalled on
demand via search/get_all, not injected wholesale every turn -- unlike a CLAUDE.md).
loaded_portion=1.0 (no known Mem0-side truncation cliff is documented; nothing to
model, so we don't pretend to).

Read-only. Never writes.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..items import HarnessItem

_LONG_FRAC_RE = re.compile(r"^(.*?\.\d{6})\d*([+-]\d{2}:\d{2})?$")


def _parse_iso8601(s) -> float:
    """Best-effort ISO8601 -> unix seconds. Returns 0.0 for anything that isn't
    a parseable string -- never raises, and "missing" is documented (D1) to mean 0."""
    if not isinstance(s, str) or not s.strip():
        return 0.0
    raw = s.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    for candidate in (raw,):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    # some Mem0/DB exports emit >6-digit fractional seconds (e.g. nanoseconds),
    # which fromisoformat rejects -- truncate to microseconds and retry once.
    m = _LONG_FRAC_RE.match(raw)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(1) + (m.group(2) or ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _mtime_from(obj: dict) -> float:
    # updated_at is the more recent truth when both are present; created_at as fallback.
    for key in ("updated_at", "created_at"):
        ts = _parse_iso8601(obj.get(key))
        if ts:
            return ts
    return 0.0


def _agent_from(obj: dict) -> str:
    for key in ("user_id", "agent_id"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def _load_json(p: Path):
    """Returns parsed JSON, or None on any read/parse failure -- a malformed
    export file must degrade to "no items from this file", never a crash."""
    try:
        return json.loads(p.read_text(errors="replace"))
    except Exception:
        return None


def _extract_objects(data):
    """Recognize the two documented shapes. Returns None (not []) when the
    top-level shape isn't one of them, so callers can tell "empty export" apart
    from "not a shape we understand" without guessing."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    return None


def _scan_file(p: Path) -> list[HarnessItem]:
    items: list[HarnessItem] = []
    data = _load_json(p)
    if data is None:
        return items
    objs = _extract_objects(data)
    if objs is None:
        return items
    for i, obj in enumerate(objs):
        if not isinstance(obj, dict):
            continue
        text = obj.get("memory")
        if not isinstance(text, str) or not text.strip():
            continue  # nothing to audit without memory text
        mem0_id = obj.get("id")
        suffix = mem0_id if isinstance(mem0_id, (str, int)) else i
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        items.append(HarnessItem(
            id=f"{p}#{suffix}",
            path=p,
            text=text,
            kind="mem0_memory",
            agent=_agent_from(obj),
            always_loaded=False,
            loaded_portion=1.0,
            meta={
                "mtime": _mtime_from(obj),
                "metadata": metadata,
                "hash": obj.get("hash"),
                "score": obj.get("score"),
                "mem0_id": mem0_id,
            },
        ))
    return items


def scan(path) -> list[HarnessItem]:
    path = Path(path).expanduser()
    items: list[HarnessItem] = []
    if path.is_dir():
        for f in sorted(path.glob("*.json")):
            items.extend(_scan_file(f))
    elif path.is_file():
        items.extend(_scan_file(path))
    return items
