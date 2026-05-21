"""
pool_registry.py — persistent exclusion registry for the healing-agent media pool.

Tracks IDs of media items (footage / audio / image) that must never be
re-downloaded, even after the source files are deleted to free disk space:

  "rejected" — rated bad in feedback; content or quality not wanted
  "used"     — assembled into a published post; safe to delete files,
               registry prevents re-fetching deleted content

Key format: "{sanitized_source}_{sanitized_id}"  — matches pool filenames.
Stored at:  pool/registry.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_PATH = Path(__file__).parent / "data" / "sort_media" / "registry.json"


def key(source: str, item_id) -> str:
    """Canonical registry key — same sanitisation rules as pool filenames."""
    src = re.sub(r"[^\w\-]", "_", str(source))[:20]
    iid = re.sub(r"[^\w\-]", "_", str(item_id))[:60]
    return f"{src}_{iid}"


def load() -> dict[str, str]:
    if _PATH.exists():
        try:
            data = json.loads(_PATH.read_text())
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save(registry: dict[str, str]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(registry, indent=2, sort_keys=True))


def blocked_set(registry: dict[str, str]) -> set[str]:
    """Keys that must never be re-downloaded (rejected or used)."""
    return {k for k, v in registry.items() if v in ("rejected", "used")}


def mark_many(items: list[dict], status: str) -> None:
    """Write status for a batch of items in a single registry read+write."""
    if not items:
        return
    registry = load()
    for item in items:
        registry[key(item.get("source", ""), item.get("id", ""))] = status
    save(registry)
