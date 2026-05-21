from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BATCH_ROOT, MEMORY_PATH
from core.schemas import MemoryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_memory() -> MemoryStore:
    if not MEMORY_PATH.exists():
        return MemoryStore(created=_utc_now(), last_updated=_utc_now())
    with MEMORY_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return _memory_from_dict(data)


_RSS_ID_CAP     = 3000   # keep the most recent N seen IDs
_REDDIT_ID_CAP  = 3000
_HISTORY_CAP    = 100    # concept_history entries
_FC_TTL_DAYS    = 90     # factcheck_cache TTL (must match FACTCHECK_CACHE_TTL_DAYS)


def _prune_memory(store: MemoryStore) -> MemoryStore:
    """
    Trim unbounded lists so world_bible.json stays lean.

    IDs (opaque strings): keep the last N — oldest entries were seen long ago
    and re-encountering them is extremely unlikely.  We record the total count
    that was ever seen so the metric is not lost.

    factcheck_cache: drop entries older than TTL — they will be re-verified
    on next use anyway (that is the point of the TTL).

    keyword / concept lists: not pruned — they are small and every entry is
    signal we deliberately collected.
    """
    # Cap seen-ID lists — preserve total count as a metadata field
    rss     = store.rss_post_ids_seen
    reddit  = store.reddit_post_ids_seen
    store.rss_post_ids_seen    = rss[-_RSS_ID_CAP:]
    store.reddit_post_ids_seen = reddit[-_REDDIT_ID_CAP:]

    # Cap concept history
    store.concept_history = store.concept_history[-_HISTORY_CAP:]

    # Prune expired factcheck_cache entries
    now = datetime.now(timezone.utc)
    pruned_cache = {}
    for key, entry in store.factcheck_cache.items():
        try:
            cached_at = datetime.fromisoformat(
                entry["_cached_at"].rstrip("Z")
            ).replace(tzinfo=timezone.utc)
            if (now - cached_at).days <= _FC_TTL_DAYS:
                pruned_cache[key] = entry
        except Exception:
            pass   # malformed entry — drop it
    store.factcheck_cache = pruned_cache

    return store


def save_memory(store: MemoryStore) -> None:
    store = _prune_memory(store)
    data = asdict(store)
    data["last_updated"] = _utc_now()
    tmp = MEMORY_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(MEMORY_PATH)


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def init_run_dir(run_id: str) -> dict[str, Path]:
    run_root = BATCH_ROOT / run_id
    dirs = {
        "root":        run_root,
        "candidates":  run_root / "candidates",
        "downloads":   run_root / "downloads",
        "renders":     run_root / "renders",
        "intelligence": run_root / "intelligence",
        "factcheck":   run_root / "factcheck",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


class ResponseCache:
    """Filesystem JSON cache. Each entry: {"data": ..., "_cached_at": iso}."""

    def __init__(self, cache_path: Path, ttl_days: int = 1) -> None:
        self._path = cache_path
        self._ttl_days = ttl_days
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        tmp.replace(self._path)

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if not entry:
            return None
        try:
            cached_at = datetime.fromisoformat(entry["_cached_at"].rstrip("Z")).replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - cached_at).days > self._ttl_days:
                return None
        except Exception:
            return None
        return entry.get("data")

    def put(self, key: str, value: Any) -> None:
        self._data[key] = {"data": value, "_cached_at": _utc_now()}
        self._save()


def _memory_from_dict(data: dict[str, Any]) -> MemoryStore:
    now = _utc_now()
    return MemoryStore(
        created=data.get("created", now),
        last_updated=data.get("last_updated", now),
        voice=data.get("voice", {}),
        high_performing_concepts=data.get("high_performing_concepts", []),
        low_performing_concepts=data.get("low_performing_concepts", []),
        high_performing_visual_styles=data.get("high_performing_visual_styles", []),
        low_performing_visual_styles=data.get("low_performing_visual_styles", []),
        high_performing_keywords=data.get("high_performing_keywords", []),
        low_performing_keywords=data.get("low_performing_keywords", []),
        concept_history=data.get("concept_history", []),
        rss_post_ids_seen=data.get("rss_post_ids_seen", []),
        reddit_post_ids_seen=data.get("reddit_post_ids_seen", []),
        rejected_ids=data.get("rejected_ids", []),
        external_concept_strikes=data.get("external_concept_strikes", {}),
        factcheck_cache=data.get("factcheck_cache", {}),
    )
