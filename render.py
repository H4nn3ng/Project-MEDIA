#!/usr/bin/env python3
"""
render.py — Pipeline phase 2: generate edit plans and render.

Reads footage from pool/footage/ (approved by feedback.py).
Reads audio from pool/audio/ (auto-approved by scrape.py).
Outputs to renders/YYYY-MM-DD_001/, renders/YYYY-MM-DD_002/, etc.
After a successful render, moves used footage to pool/footage/used/.

Usage:
    python3 render.py
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from config import (
    FACTCHECK_MAX_REGENERATE,
    POOL_AUDIO,
    POOL_FOOTAGE,
    POOL_FOOTAGE_USED,
    POOL_IMAGES,
    RENDERS_DIR,
)
from core.corpus import load_corpus, mark_used, next_format, pick_concept, save_corpus
from core.orchestration import bootstrap
from core.schemas import Candidate, edit_plan_to_dict
from core.state import save_memory
from modules.editor_agent import generate_edit_plan
from modules.fact_check_agent import verify_edit_plan
from modules.feedback_agent import quarantine_concept
from renderers.carousel_renderer import render_carousel
from renderers.reel_renderer import render_reel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Pool scanning ──────────────────────────────────────────────────────────────

def _load_candidates_from_dir(dir_path: Path, media_kind: str) -> list[Candidate]:
    """Reconstruct Candidate objects from metadata sidecars in a pool directory."""
    if not dir_path.exists():
        return []
    candidates: list[Candidate] = []
    for meta_file in dir_path.glob("*_metadata.json"):
        try:
            d = json.loads(meta_file.read_text())
            # Ensure media file actually exists
            base_id = meta_file.stem.replace("_metadata", "")
            if not any(dir_path.glob(f"{base_id}.*")):
                continue
            cand = Candidate(**d)
            candidates.append(cand)
        except Exception as exc:
            logger.warning("Skipping bad metadata %s: %s", meta_file.name, exc)
    return candidates


def _load_pool() -> dict[str, list[Candidate]]:
    footage = _load_candidates_from_dir(POOL_FOOTAGE, "footage")
    audio   = _load_candidates_from_dir(POOL_AUDIO, "audio")
    images  = _load_candidates_from_dir(POOL_IMAGES, "image")

    music = [c for c in audio if c.media_kind in ("music",)]
    beds  = [c for c in audio if c.media_kind in ("bed", "ambient")]

    logger.info(
        "Pool: %d footage clips  |  %d music  |  %d bed  |  %d images",
        len(footage), len(music), len(beds), len(images),
    )
    return {"footage": footage, "music": music, "bed": beds, "image": images}


# ── Render directory ───────────────────────────────────────────────────────────

def _make_render_dir() -> Path:
    """Create renders/YYYY-MM-DD_001/ (auto-increments like healing-agent)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = 1
    while True:
        path = RENDERS_DIR / f"{today}_{n:03d}"
        if not path.exists():
            path.mkdir(parents=True)
            return path
        n += 1



# ── Used footage tracking ──────────────────────────────────────────────────────

def _move_footage_to_used(plan_dict: dict) -> None:
    """Move the footage clip used in this render to pool/footage/used/."""
    POOL_FOOTAGE_USED.mkdir(parents=True, exist_ok=True)
    for item in plan_dict.get("items", []):
        reasoning = (item.get("reasoning") or "").lower()
        if "footage" not in reasoning and "video" not in reasoning:
            continue
        file_id = item.get("file", "")
        if not file_id:
            continue
        for f in POOL_FOOTAGE.glob(f"{file_id}.*"):
            if not f.name.endswith("_metadata.json"):
                dest = POOL_FOOTAGE_USED / f.name
                shutil.move(str(f), dest)
                logger.info("Moved used footage: %s → pool/footage/used/", f.name)
        meta = POOL_FOOTAGE / f"{file_id}_metadata.json"
        if meta.exists():
            shutil.move(str(meta), POOL_FOOTAGE_USED / meta.name)


# ── Artifact production ────────────────────────────────────────────────────────

def _produce_artifact(
    concept:  ConceptEntry,
    fmt:      str,
    pool:     dict[str, list[Candidate]],
    corpus:   ConceptCorpus,
    ctx,
    render_dir: Path,
) -> bool:
    hints: list[str] = []
    plan = None

    for attempt in range(FACTCHECK_MAX_REGENERATE + 1):
        plan = generate_edit_plan(
            concept = concept,
            fmt     = fmt,
            footage = pool.get("footage", []),
            music   = pool.get("music", []),
            beds    = pool.get("bed", []),
            images  = pool.get("image", []),
            ctx     = ctx,
            hints   = hints if attempt > 0 else None,
        )
        if not plan:
            logger.error("EditPlan generation failed for %s/%s", concept.concept_id, fmt)
            return False

        plan_path = render_dir / "edit_log.json"
        plan_path.write_text(
            json.dumps(edit_plan_to_dict(plan), ensure_ascii=False, indent=2),
        )

        verdict = verify_edit_plan(plan, corpus, ctx)
        (render_dir / "factcheck_report.json").write_text(
            json.dumps(asdict(verdict.report), ensure_ascii=False, indent=2),
        )

        if verdict.decision == "pass":
            break
        elif verdict.decision == "regenerate" and attempt < FACTCHECK_MAX_REGENERATE:
            hints = verdict.regenerate_hints
            logger.warning("Fact-check failed (attempt %d/%d) — regenerating", attempt + 1, FACTCHECK_MAX_REGENERATE)
            continue
        else:
            logger.error("Could not pass fact-check after %d attempts", attempt + 1)
            (render_dir / "HUMAN_REVIEW.txt").write_text("\n".join(verdict.regenerate_hints))
            return False

    plan_dict = json.loads((render_dir / "edit_log.json").read_text())
    voice_cfg = ctx.memory.voice or None

    try:
        if fmt == "reel":
            render_reel(plan_dict, render_dir, voice_cfg=voice_cfg)
            _move_footage_to_used(plan_dict)
        else:
            render_carousel(plan_dict, render_dir)
    except Exception as exc:
        logger.error("Render failed for %s/%s: %s", concept.concept_id, fmt, exc)
        return False

    logger.info("Artifact produced: %s/%s → %s", concept.concept_id, fmt, render_dir)
    return True


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    pool = _load_pool()
    if not pool["footage"]:
        logger.error("Footage pool is empty — approve clips in Sort Media first")
        sys.exit(1)

    ctx    = bootstrap()
    corpus = load_corpus()
    if corpus is None:
        logger.error("Corpus not found — run seed_corpus.py first")
        sys.exit(1)

    weekday     = datetime.now(timezone.utc).weekday()
    n_artifacts = 4 if weekday == 0 else 3
    logger.info("Producing %d artifacts", n_artifacts)

    concept_history = [h.get("concept_id") for h in ctx.memory.concept_history]
    produced = 0

    for _ in range(n_artifacts):
        concept = pick_concept(corpus, concept_history)
        if not concept:
            logger.warning("No more eligible concepts — stopping at %d", produced)
            break

        fmt        = next_format(concept)
        render_dir = _make_render_dir()
        logger.info("Producing: %s / %s → %s", concept.concept_id, fmt, render_dir.name)

        ok = _produce_artifact(concept, fmt, pool, corpus, ctx, render_dir)
        if ok:
            mark_used(corpus, concept.concept_id, fmt)
            ctx.memory.concept_history.append({
                "concept_id": concept.concept_id,
                "format":     fmt,
                "render_dir": render_dir.name,
            })
            concept_history.append(concept.concept_id)
            save_memory(ctx.memory)
            produced += 1
        else:
            quarantine_concept(concept.concept_id, reason="production_failure")

    print("\n" + "─" * 52)
    print(f"  Done: {produced}/{n_artifacts} artifacts")
    print(f"  Output: renders/")
    print("─" * 52)


if __name__ == "__main__":
    main()
