from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from config import (
    BED_SEARCH_VOCAB,
    CONCEPT_FORMATS_PER_LIFE,
    MUSIC_AUTO_PENALTY_TAGS,
    MUSIC_SEARCH_VOCAB,
    VISUAL_AUTO_PENALTY_TAGS,
    VISUAL_SEARCH_VOCAB,
)
from adapters.coverr import search_coverr
from adapters.fma import search_fma
from adapters.freesound import search_freesound
from adapters.jamendo import search_jamendo
from adapters.loc import search_loc
from adapters.pexels import search_pexels
from adapters.pixabay import search_pixabay
from adapters.praw_adapter import fetch_reddit
from adapters.rss_reader import fetch_rss
from adapters.smithsonian import search_smithsonian
from adapters.wikimedia import search_wikimedia
from core.orchestration import RunContext
from core.schemas import Candidate, ConceptCorpus
from core.state import save_memory

logger = logging.getLogger(__name__)

_SEARCH_WORKERS = 8   # concurrent HTTP requests — safe for Pexels/Pixabay rate limits


def _parallel_search(tasks: list[tuple[Callable, tuple, dict]]) -> list[Candidate]:
    """Run search callables in parallel, merge results."""
    results: list[Candidate] = []
    with ThreadPoolExecutor(max_workers=_SEARCH_WORKERS) as ex:
        futures = {ex.submit(fn, *args, **kw): fn.__name__ for fn, args, kw in tasks}
        for f in as_completed(futures):
            try:
                results.extend(f.result())
            except Exception as exc:
                logger.debug("Search task failed: %s", exc)
    return results


def _pool_ids(pool_dir: Path) -> set[str]:
    """IDs of files already downloaded into the pool (stem without extension)."""
    return {p.stem for p in pool_dir.rglob("*") if p.is_file()}


def _has_penalty_tag(candidate: Candidate, penalty_tags: set[str]) -> bool:
    return bool(penalty_tags.intersection({t.lower() for t in candidate.tags}))


def _pick_search_concepts(corpus: ConceptCorpus, recent_ids: list[str], n: int = 10) -> list:
    """Return up to n eligible concepts sorted by least-recently-used."""
    used_set = set(recent_ids[-5:]) if recent_ids else set()
    eligible = [
        e for e in corpus.concepts.values()
        if not e.quarantined and len(e.format_used) < CONCEPT_FORMATS_PER_LIFE
    ]
    fresh = [e for e in eligible if e.concept_id not in used_set]
    pool  = fresh if fresh else eligible
    return sorted(pool, key=lambda e: (len(e.format_used), e.last_used_at or ""))[:n]


def run_scraper(ctx: RunContext, corpus: ConceptCorpus | None = None) -> dict[str, list[Candidate]]:
    """
    Run all adapters, apply penalty-tag filters, return candidates grouped by media_kind.

    Returns:
        {
            "concept":  [...],   # from RSS + Reddit
            "footage":  [...],   # from Pexels + Pixabay
            "image":    [...],   # from Wikimedia + LoC + Smithsonian
            "music":    [...],   # from Jamendo + FMA
            "bed":      [...],   # from Freesound (ambient / SFX)
        }
    """
    memory   = ctx.memory
    keys     = ctx.keys

    # ── Concept discovery ────────────────────────────────────────────────────
    seen_rss    = set(memory.rss_post_ids_seen)
    seen_reddit = set(memory.reddit_post_ids_seen)

    rss_candidates = fetch_rss(seen_rss)
    memory.rss_post_ids_seen = list(seen_rss | {c.id for c in rss_candidates})

    reddit_candidates: list[Candidate] = []
    if keys.get("reddit"):
        reddit_candidates = fetch_reddit(keys["reddit"], seen_reddit)
        memory.reddit_post_ids_seen = list(seen_reddit | {c.id for c in reddit_candidates})
    else:
        logger.info("Reddit keys not configured — skipping Reddit source")

    save_memory(memory)

    concepts: list[Candidate] = rss_candidates + reddit_candidates
    logger.info("Concept candidates: %d (rss=%d reddit=%d)",
                len(concepts), len(rss_candidates), len(reddit_candidates))

    # ── Visual discovery (footage) ────────────────────────────────────────────
    footage_pool_ids = _pool_ids(ctx.run_dirs["downloads"])
    coverr_key = keys.get("coverr", "")

    footage_tasks: list[tuple] = []

    # Concept-aware search: use visual_concepts from upcoming concepts in corpus
    if corpus:
        recent_ids = [h.get("concept_id") for h in ctx.memory.concept_history]
        search_concepts = _pick_search_concepts(corpus, recent_ids, n=10)
        for concept in search_concepts:
            visual_terms = (getattr(concept, "visual_concepts", None) or [])[:3]
            for term in visual_terms:
                footage_tasks.append((search_pexels,  (term, keys["pexels"]),  {"seen_ids": footage_pool_ids, "concept_id": concept.concept_id}))
                footage_tasks.append((search_pixabay, (term, keys["pixabay"]), {"seen_ids": footage_pool_ids, "concept_id": concept.concept_id}))
                if coverr_key:
                    footage_tasks.append((search_coverr, (term, coverr_key), {"seen_ids": footage_pool_ids, "concept_id": concept.concept_id}))
        logger.info("Queued concept-aware footage searches: %d (from %d concepts)", len(footage_tasks), len(search_concepts))

    # Generic fallback: skip if concept-aware search already queued enough tasks
    _FALLBACK_SKIP_THRESHOLD = 20
    if len(footage_tasks) < _FALLBACK_SKIP_THRESHOLD:
        for keyword in VISUAL_SEARCH_VOCAB:
            footage_tasks.append((search_pexels,  (keyword, keys["pexels"]),  {"seen_ids": footage_pool_ids}))
            footage_tasks.append((search_pixabay, (keyword, keys["pixabay"]), {"seen_ids": footage_pool_ids}))
            if coverr_key:
                footage_tasks.append((search_coverr, (keyword, coverr_key), {"seen_ids": footage_pool_ids}))
    else:
        logger.info("Skipping generic footage fallback — %d concept-aware tasks queued", len(footage_tasks))

    footage = _parallel_search(footage_tasks)
    footage = [c for c in footage if not _has_penalty_tag(c, VISUAL_AUTO_PENALTY_TAGS)]
    logger.info("Footage candidates after penalty filter: %d", len(footage))

    # ── Visual discovery (archival images) ───────────────────────────────────
    # Concept-aware: concept names work much better in historical archives than
    # modern stock keywords like "café laptop" or "shopping cart"
    image_tasks: list[tuple] = []
    if corpus:
        for concept in search_concepts:  # reuse the concepts already picked for footage
            term = concept.canonical_name
            image_tasks.append((search_wikimedia, (term,), {"seen_ids": footage_pool_ids}))
            image_tasks.append((search_loc,       (term,), {"seen_ids": footage_pool_ids}))
        logger.info("Queued concept-aware image searches: %d (from %d concepts)",
                    len(image_tasks), len(search_concepts))

    # Fallback to generic vocab only when corpus produced too few tasks
    if len(image_tasks) < 8:
        for keyword in VISUAL_SEARCH_VOCAB[:4]:
            image_tasks.append((search_wikimedia, (keyword,), {"seen_ids": footage_pool_ids}))
            image_tasks.append((search_loc,       (keyword,), {"seen_ids": footage_pool_ids}))

    images = _parallel_search(image_tasks)
    images = [c for c in images if not _has_penalty_tag(c, VISUAL_AUTO_PENALTY_TAGS)]
    logger.info("Image candidates after penalty filter: %d", len(images))

    # ── Audio discovery (music + beds) ───────────────────────────────────────
    music_pool_ids = _pool_ids(ctx.run_dirs["downloads"])

    audio_tasks: list[tuple] = []
    for keyword in MUSIC_SEARCH_VOCAB:
        audio_tasks.append((search_jamendo, (keyword, keys["jamendo"]), {"seen_ids": music_pool_ids}))
        audio_tasks.append((search_fma,     (keyword,),                 {"seen_ids": music_pool_ids}))
    for keyword in BED_SEARCH_VOCAB:
        audio_tasks.append((search_freesound, (keyword, keys["freesound"]), {"media_kind": "bed", "seen_ids": music_pool_ids}))

    audio_results = _parallel_search(audio_tasks)
    music = [c for c in audio_results if c.media_kind == "music" and not _has_penalty_tag(c, MUSIC_AUTO_PENALTY_TAGS)]
    beds  = [c for c in audio_results if c.media_kind == "bed"]
    logger.info("Music candidates: %d  Bed/SFX candidates: %d", len(music), len(beds))

    return {
        "concept": concepts,
        "footage": footage,
        "image":   images,
        "music":   music,
        "bed":     beds,
    }
