from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from config import (
    CORPUS_QUARANTINE_STRIKES,
    CORPUS_PATH,
    POOL_AUDIO,
    POOL_FOOTAGE,
)
from core.orchestration import RetryPolicy, RunContext, with_retry
from core.schemas import ConceptCorpus
from core.state import load_memory, save_memory

logger = logging.getLogger(__name__)

_NOISE_TAGS = {
    "free", "download", "creative", "commons", "video", "audio", "clip",
    "music", "sound", "footage", "stock",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _vote_tags(memory_list: list[str], tags: list[str], delta: int) -> list[str]:
    """Add or remove tags from a high/low list based on vote delta (+1 / -1)."""
    result = list(memory_list)
    for tag in tags:
        if tag in _NOISE_TAGS:
            continue
        if delta > 0 and tag not in result:
            result.append(tag)
        elif delta < 0 and tag in result:
            result.remove(tag)
    return result


def _load_metadata(meta_path: Path) -> dict:
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


# ── Mode 1 — Discrete item rating ─────────────────────────────────────────────

def review_items(
    run_dir:  Path,
    ctx:      RunContext,
) -> tuple[list[dict], list[dict]]:
    """
    Interactive CLI rating loop for footage and audio in run_dir/downloads/.
    Keys: g = good, b = bad, s = skip.
    Saves progress after each action; resumable.

    Returns (liked_items, disliked_items).
    """
    progress_path = run_dir / "review_progress.json"
    progress: dict[str, str] = {}
    if progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text())
        except Exception:
            pass

    downloads = run_dir / "downloads"
    items = sorted(downloads.glob("*_metadata.json")) if downloads.exists() else []

    liked:    list[dict] = []
    disliked: list[dict] = []

    for meta_path in items:
        cid = meta_path.stem.replace("_metadata", "")
        if cid in progress:
            verdict = progress[cid]
            meta    = _load_metadata(meta_path)
            if verdict == "g":
                liked.append(meta | {"_meta_path": str(meta_path)})
            elif verdict == "b":
                disliked.append(meta | {"_meta_path": str(meta_path)})
            continue

        meta = _load_metadata(meta_path)
        print(f"\n[review] {cid}")
        print(f"  title:  {meta.get('title', '?')[:80]}")
        print(f"  source: {meta.get('source', '?')}")
        print(f"  tags:   {', '.join(meta.get('tags', [])[:8])}")
        key = input("  g=good  b=bad  s=skip → ").strip().lower()

        if key == "g":
            liked.append(meta | {"_meta_path": str(meta_path)})
            progress[cid] = "g"
        elif key == "b":
            disliked.append(meta | {"_meta_path": str(meta_path)})
            progress[cid] = "b"
        else:
            progress[cid] = "s"

        progress_path.write_text(json.dumps(progress, indent=2))

    return liked, disliked


# ── Mode 2 — LLM free-form notes extraction ───────────────────────────────────

def extract_learnings(
    notes:    str,
    edit_log: dict,
    ctx:      RunContext,
) -> dict:
    """Ask Gemini Flash to extract structured learnings from operator notes + edit log."""
    prompt = (
        "Extract structured learnings from these operator notes about a behavioural-finance "
        "short-form video. Return JSON only:\n"
        '{"positive_keywords": [...], "negative_keywords": [...], '
        '"positive_visual_styles": [...], "negative_visual_styles": [...], '
        '"positive_concepts": [...], "negative_concepts": [...], '
        '"editor_notes": "<one-sentence summary>"}\n\n'
        f"Operator notes:\n{notes[:2000]}\n\n"
        f"Concept: {edit_log.get('concept_id', 'unknown')}\n"
        f"Format: {edit_log.get('format', 'unknown')}\n"
        f"Caption: {edit_log.get('caption', '')[:200]}"
    )
    policy = RetryPolicy(max_attempts=2, base_delay_s=2.0)
    try:
        result = with_retry(
            lambda: ctx.model_flash.generate_content(prompt),
            policy=policy,
            label="feedback_extract",
        )
        raw = (result.text or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Learnings extraction failed: %s", exc)
        return {}


# ── Apply feedback to memory ──────────────────────────────────────────────────

def apply_feedback(
    ctx:      RunContext,
    liked:    list[dict],
    disliked: list[dict],
    run_dir:  Path,
) -> None:
    """
    Update memory with rating results:
    - Good items → moved to pool/<type>/
    - Bad items  → moved to run_dir/bad/, IDs added to rejected_ids
    - Tag voting on high/low keyword lists
    """
    memory   = ctx.memory
    downloads = run_dir / "downloads"

    for item in liked:
        meta_path = Path(item.get("_meta_path", ""))
        cid       = meta_path.stem.replace("_metadata", "")
        media_kind = item.get("media_kind", "footage")

        # Determine pool destination
        pool_dest = POOL_AUDIO if media_kind in ("music", "bed") else POOL_FOOTAGE
        pool_dest.mkdir(parents=True, exist_ok=True)

        # Copy media file + metadata into pool
        for suffix in (".mp4", ".mp3", ".wav", ".jpg", ".png", ".webm"):
            src = downloads / f"{cid}{suffix}"
            if src.exists():
                shutil.copy2(src, pool_dest / src.name)
                break
        if meta_path.exists():
            shutil.copy2(meta_path, pool_dest / meta_path.name)

        # Vote tags
        tags = item.get("tags", [])
        memory.high_performing_keywords = _vote_tags(memory.high_performing_keywords, tags, +1)
        memory.low_performing_keywords  = _vote_tags(memory.low_performing_keywords,  tags, -1)

    for item in disliked:
        meta_path = Path(item.get("_meta_path", ""))
        cid       = meta_path.stem.replace("_metadata", "")
        bad_dir   = run_dir / "bad"
        bad_dir.mkdir(exist_ok=True)

        # Move to bad/
        for suffix in (".mp4", ".mp3", ".wav", ".jpg", ".png", ".webm"):
            src = downloads / f"{cid}{suffix}"
            if src.exists():
                shutil.move(str(src), bad_dir / src.name)
        if meta_path.exists():
            shutil.move(str(meta_path), bad_dir / meta_path.name)

        if cid not in memory.rejected_ids:
            memory.rejected_ids.append(cid)

        tags = item.get("tags", [])
        memory.low_performing_keywords  = _vote_tags(memory.low_performing_keywords,  tags, +1)
        memory.high_performing_keywords = _vote_tags(memory.high_performing_keywords, tags, -1)

    save_memory(memory)
    logger.info("Feedback applied — %d liked, %d disliked", len(liked), len(disliked))


def apply_editor_learnings(ctx: RunContext, learnings: dict) -> None:
    """Merge LLM-extracted learnings into the memory store."""
    memory = ctx.memory
    for key in ("positive_keywords",):
        memory.high_performing_keywords = list(
            dict.fromkeys(memory.high_performing_keywords + learnings.get(key, []))
        )
    for key in ("negative_keywords",):
        memory.low_performing_keywords = list(
            dict.fromkeys(memory.low_performing_keywords + learnings.get(key, []))
        )
    for key in ("positive_visual_styles",):
        memory.high_performing_visual_styles = list(
            dict.fromkeys(memory.high_performing_visual_styles + learnings.get(key, []))
        )
    for key in ("negative_visual_styles",):
        memory.low_performing_visual_styles = list(
            dict.fromkeys(memory.low_performing_visual_styles + learnings.get(key, []))
        )
    for key in ("positive_concepts",):
        memory.high_performing_concepts = list(
            dict.fromkeys(memory.high_performing_concepts + learnings.get(key, []))
        )
    for key in ("negative_concepts",):
        memory.low_performing_concepts = list(
            dict.fromkeys(memory.low_performing_concepts + learnings.get(key, []))
        )
    save_memory(memory)


# ── Corpus quarantine ─────────────────────────────────────────────────────────

def quarantine_concept(concept_id: str, reason: str) -> None:
    """
    Add a strike to a concept. If strikes >= threshold, set quarantined=True in corpus.
    """
    if not CORPUS_PATH.exists():
        return
    import json as _json
    from core.schemas import corpus_from_dict
    from dataclasses import asdict

    data   = _json.loads(CORPUS_PATH.read_text())
    corpus = corpus_from_dict(data)
    entry  = corpus.concepts.get(concept_id)
    if not entry:
        return

    entry.strikes += 1
    if entry.strikes >= CORPUS_QUARANTINE_STRIKES:
        entry.quarantined = True
        logger.warning(
            "Concept %s quarantined after %d strikes (%s)",
            concept_id, entry.strikes, reason,
        )

    # Persist (simple re-serialise)
    data["concepts"][concept_id] = asdict(entry)
    tmp = CORPUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(CORPUS_PATH)
