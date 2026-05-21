"""
core/corpus.py — single source of truth for corpus read/write/selection.

Used by scrape.py, render.py, and main.py so changes only need to happen once.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from config import CONCEPT_FORMATS_PER_LIFE, CORPUS_PATH
from core.schemas import ConceptCorpus, ConceptEntry, corpus_from_dict


def load_corpus() -> ConceptCorpus | None:
    """Load corpus/concepts.json. Returns None if the file doesn't exist."""
    if not CORPUS_PATH.exists():
        return None
    with CORPUS_PATH.open(encoding="utf-8") as f:
        return corpus_from_dict(json.load(f))


def save_corpus(corpus: ConceptCorpus) -> None:
    """Atomic write — tmp file replaced on success, corpus never left half-written."""
    data = {
        "version":      corpus.version,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "concepts":     {k: asdict(v) for k, v in corpus.concepts.items()},
    }
    tmp = CORPUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(CORPUS_PATH)


def pick_concept(corpus: ConceptCorpus, recently_used: list[str]) -> ConceptEntry | None:
    """
    Select the next concept to produce.

    Rules:
    - Not quarantined
    - Has not reached CONCEPT_FORMATS_PER_LIFE uses
    - Avoids the last 5 used concepts (recency exclusion)
    - Tie-break: fewest uses first, then oldest last_used_at
    """
    eligible = [
        e for e in corpus.concepts.values()
        if not e.quarantined and len(e.format_used) < CONCEPT_FORMATS_PER_LIFE
    ]
    if not eligible:
        return None
    excluded = set(recently_used[-5:]) if recently_used else set()
    fresh = [e for e in eligible if e.concept_id not in excluded]
    pool  = fresh if fresh else eligible
    return min(pool, key=lambda e: (len(e.format_used), e.last_used_at or ""))


def next_format(concept: ConceptEntry) -> str:
    """Alternate reel → carousel → reel. First use is always reel."""
    used = concept.format_used
    if not used:
        return "reel"
    return "carousel" if used[-1] == "reel" else "reel"


def mark_used(corpus: ConceptCorpus, concept_id: str, fmt: str) -> None:
    """Stamp format_used and last_used_at, then save corpus atomically."""
    entry = corpus.concepts.get(concept_id)
    if not entry:
        return
    entry.format_used.append(fmt)
    entry.last_used_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_corpus(corpus)
