#!/usr/bin/env python3
"""
Money Psychology Pipeline — main entry point.

Runs the full 8-step pipeline and produces reel + carousel artifacts.
Schedule: cron Mon/Thu (Mon=4 artifacts, Thu=3 artifacts).
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import (
    ARTIFACTS_MON,
    ARTIFACTS_THU,
    FACTCHECK_MAX_REGENERATE,
    SCORED_POOL_THRESHOLD,
)
from core.corpus import load_corpus, mark_used, next_format, pick_concept, save_corpus
from core.orchestration import bootstrap
from core.schemas import CanonicalSource, ConceptEntry, edit_plan_to_dict
from core.state import save_memory
from modules.editor_agent import generate_edit_plan
from modules.fact_check_agent import verify_edit_plan, verify_external_concept
from modules.feedback_agent import quarantine_concept
from modules.scraper_agent import run_scraper
from modules.scoring_agent import score_candidates
from renderers.carousel_renderer import render_carousel
from renderers.reel_renderer import render_reel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)



# ── Asset download ─────────────────────────────────────────────────────────────

def _download_asset(url: str, dest: Path, timeout: int = 30) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return True
    except Exception as exc:
        logger.warning("Download failed for %s: %s", url, exc)
        return False


def _acquire_assets(
    candidates_by_kind: dict,
    run_dir: Path,
) -> dict:
    """
    Download top-scoring footage, music, and bed assets into run_dir/downloads/.
    Returns dict with pools of local Candidate objects pointing to downloaded files.
    """
    dl_dir = run_dir / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)

    acquired: dict[str, list] = {"footage": [], "image": [], "music": [], "bed": []}

    for kind in ("footage", "image", "music", "bed"):
        pool = sorted(
            [c for c in candidates_by_kind.get(kind, [])
             if c.gate_pass is not False and c.download_url],
            key=lambda c: c.score or 0,
            reverse=True,
        )[:5]

        for cand in pool:
            ext = Path(cand.download_url.split("?")[0]).suffix or ".mp4"
            dest = dl_dir / f"{cand.id}{ext}"
            meta = dl_dir / f"{cand.id}_metadata.json"

            if dest.exists():
                logger.info("Already downloaded: %s", cand.id)
            else:
                if not _download_asset(cand.download_url, dest):
                    continue

            # Write metadata sidecar
            meta.write_text(
                json.dumps(asdict(cand), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            acquired[kind].append(cand)

    return acquired


# ── Artifact production ────────────────────────────────────────────────────────

def _produce_artifact(
    concept:     ConceptEntry,
    fmt:         str,
    acquired:    dict,
    corpus:      ConceptCorpus,
    ctx,
    render_root: Path,
) -> bool:
    """Plan → fact-check → render one artifact. Returns True on success."""
    render_dir = render_root / f"{concept.concept_id}_{fmt}"
    render_dir.mkdir(parents=True, exist_ok=True)

    hints: list[str] = []
    for attempt in range(FACTCHECK_MAX_REGENERATE + 1):
        # ── Edit plan ──────────────────────────────────────────────────────────
        plan = generate_edit_plan(
            concept  = concept,
            fmt      = fmt,
            footage  = acquired.get("footage", []),
            music    = acquired.get("music", []),
            beds     = acquired.get("bed", []),
            images   = acquired.get("image", []),
            ctx      = ctx,
            hints    = hints if attempt > 0 else None,
        )
        if not plan:
            logger.error("EditPlan generation failed for %s/%s", concept.concept_id, fmt)
            return False

        # Persist plan before rendering
        plan_path = render_dir / "edit_log.json"
        plan_path.write_text(
            json.dumps(edit_plan_to_dict(plan), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ── Fact-check EditPlan ────────────────────────────────────────────────
        verdict = verify_edit_plan(plan, corpus, ctx)
        report_path = render_dir / "factcheck_report.json"
        report_path.write_text(
            json.dumps(asdict(verdict.report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if verdict.decision == "pass":
            break
        elif verdict.decision == "regenerate" and attempt < FACTCHECK_MAX_REGENERATE:
            hints = verdict.regenerate_hints
            logger.warning(
                "EditPlan fact-check failed (attempt %d/%d) — regenerating with hints",
                attempt + 1, FACTCHECK_MAX_REGENERATE,
            )
            continue
        else:
            logger.error(
                "EditPlan could not pass fact-check after %d attempts — routing to human review",
                attempt + 1,
            )
            (render_dir / "HUMAN_REVIEW.txt").write_text(
                "\n".join(verdict.regenerate_hints), encoding="utf-8"
            )
            return False

    # ── Render ─────────────────────────────────────────────────────────────────
    plan_dict = json.loads(plan_path.read_text())
    voice_cfg = ctx.memory.voice or None

    try:
        if fmt == "reel":
            render_reel(plan_dict, render_dir, voice_cfg=voice_cfg)
        else:
            render_carousel(plan_dict, render_dir)
    except Exception as exc:
        logger.error("Render failed for %s/%s: %s", concept.concept_id, fmt, exc)
        return False

    logger.info("Artifact produced: %s/%s → %s", concept.concept_id, fmt, render_dir)
    return True


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ctx = bootstrap()
    logger.info("Run ID: %s", ctx.run_id)

    # Determine number of artifacts for today
    weekday = datetime.now(timezone.utc).weekday()   # 0=Mon, 3=Thu
    n_artifacts = ARTIFACTS_MON if weekday == 0 else ARTIFACTS_THU
    logger.info("Producing %d artifacts (weekday=%d)", n_artifacts, weekday)

    # ── Step 1: Scrape ─────────────────────────────────────────────────────────
    logger.info("Step 1: Scraper")
    raw_by_kind = run_scraper(ctx)

    # ── Step 2: Score concept candidates ──────────────────────────────────────
    logger.info("Step 2: Scoring")
    scored_concepts = score_candidates(raw_by_kind.get("concept", []), ctx)

    # ── Step 3: Fact-check top external concepts for corpus promotion ──────────
    logger.info("Step 3: Ingestion fact-check")
    corpus = load_corpus()
    if corpus is None:
        logger.error("Cannot continue without concept corpus — exiting")
        sys.exit(1)

    for cand in sorted(scored_concepts, key=lambda c: c.score or 0, reverse=True)[:5]:
        if (cand.score or 0) < SCORED_POOL_THRESHOLD:
            continue
        verdict = verify_external_concept(cand, corpus, ctx)
        if verdict.decision == "approved" and verdict.proposed_corpus_entry:
            logger.info("New concept approved: %s", cand.title[:60])
            entry_data = verdict.proposed_corpus_entry
            new_id = entry_data.get("canonical_name", "").lower().replace(" ", "_")[:32]
            if new_id and new_id not in corpus.concepts:
                cs_data = entry_data.get("canonical_source", {})
                corpus.concepts[new_id] = ConceptEntry(
                    concept_id      = new_id,
                    canonical_name  = entry_data["canonical_name"],
                    definition      = entry_data.get("definition", ""),
                    canonical_source = CanonicalSource(**cs_data) if cs_data else None,
                    provenance      = "external",
                    provisional     = True,
                    created_at      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                save_corpus(corpus)

    # ── Step 4: Score visual + audio candidates ─────────────────────────────────
    logger.info("Step 4: Scoring visual/audio")
    for kind in ("footage", "image", "music", "bed"):
        raw_by_kind[kind] = score_candidates(raw_by_kind.get(kind, []), ctx)

    # ── Steps 5–8: Produce artifacts ──────────────────────────────────────────
    render_root = ctx.run_dirs["renders"]
    concept_history = [h.get("concept_id") for h in ctx.memory.concept_history]

    produced = 0
    for _ in range(n_artifacts):
        concept = pick_concept(corpus, concept_history)
        if not concept:
            logger.warning("No more eligible concepts — stopping at %d artifacts", produced)
            break

        fmt = next_format(concept)
        logger.info("Step 5–8: %s / %s", concept.concept_id, fmt)

        # Acquire assets
        acquired = _acquire_assets(raw_by_kind, ctx.run_dirs["root"])

        ok = _produce_artifact(concept, fmt, acquired, corpus, ctx, render_root)
        if ok:
            mark_used(corpus, concept.concept_id, fmt)
            ctx.memory.concept_history.append({
                "concept_id": concept.concept_id,
                "format":     fmt,
                "run_id":     ctx.run_id,
            })
            concept_history.append(concept.concept_id)
            save_memory(ctx.memory)
            produced += 1
        else:
            quarantine_concept(concept.concept_id, reason="production_failure")

    logger.info("Run complete — %d/%d artifacts produced", produced, n_artifacts)


if __name__ == "__main__":
    main()
