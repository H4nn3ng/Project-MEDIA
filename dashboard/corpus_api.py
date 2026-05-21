"""
corpus_api.py — corpus browser data for the dashboard.

Reads corpus/concepts.json and returns categorised concept lists:
  upcoming   — eligible for next render (not quarantined, uses < limit)
  used       — exhausted their format_used limit
  quarantined — blocked from production
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .paths import CORPUS_PATH, BASE_DIR

# Import from the pipeline config so this stays in sync if the value changes
sys.path.insert(0, str(BASE_DIR))
try:
    from config import CONCEPT_FORMATS_PER_LIFE as _FORMATS_PER_LIFE
except ImportError:
    _FORMATS_PER_LIFE = 2


def get_corpus_summary() -> dict:
    if not CORPUS_PATH.exists():
        return {"upcoming": [], "used": [], "quarantined": [], "total": 0}

    try:
        raw = json.loads(CORPUS_PATH.read_text())
    except Exception:
        return {"upcoming": [], "used": [], "quarantined": [], "total": 0, "error": "parse_error"}

    concepts = raw.get("concepts", {})
    upcoming:    list[dict] = []
    used:        list[dict] = []
    quarantined: list[dict] = []

    for cid, entry in concepts.items():
        if isinstance(entry, dict):
            pass
        else:
            continue  # malformed

        formats_used = entry.get("format_used", [])
        is_quarantined = bool(entry.get("quarantined"))
        is_exhausted   = len(formats_used) >= _FORMATS_PER_LIFE

        row = {
            "concept_id":    entry.get("concept_id", cid),
            "name":          entry.get("canonical_name", cid),
            "formats_used":  formats_used,
            "next_format":   _next_format(formats_used),
            "last_used":     entry.get("last_used_at", ""),
            "provenance":    entry.get("provenance", "seed"),
            "provisional":   entry.get("provisional", False),
            "quarantine_reason": entry.get("quarantine_reason", ""),
        }

        if is_quarantined:
            quarantined.append(row)
        elif is_exhausted:
            used.append(row)
        else:
            upcoming.append(row)

    # Sort upcoming: fewest uses first, then by last_used (oldest first = highest priority)
    upcoming.sort(key=lambda r: (len(r["formats_used"]), r["last_used"] or ""))
    used.sort(key=lambda r: r["last_used"] or "", reverse=True)
    quarantined.sort(key=lambda r: r["name"])

    return {
        "upcoming":    upcoming,
        "used":        used,
        "quarantined": quarantined,
        "total":       len(concepts),
        "version":     raw.get("version", ""),
        "last_updated": raw.get("last_updated", ""),
    }


def _next_format(formats_used: list[str]) -> str:
    if not formats_used:
        return "reel"
    return "carousel" if formats_used[-1] == "reel" else "reel"


def add_concept(data: dict) -> dict:
    """
    Add a new concept to the corpus from dashboard input.
    Returns {"ok": True, "concept_id": ...} or {"error": "..."}.
    """
    name = (data.get("canonical_name") or "").strip()
    definition = (data.get("definition") or "").strip()
    if not name or not definition:
        return {"error": "canonical_name and definition are required"}

    # Auto-generate a stable slug from the name
    concept_id = re.sub(r"[^\w]+", "_", name.lower()).strip("_")[:40]

    # Load corpus
    if not CORPUS_PATH.exists():
        return {"error": "corpus file not found"}
    try:
        raw = json.loads(CORPUS_PATH.read_text())
    except Exception as e:
        return {"error": f"corpus parse error: {e}"}

    if concept_id in raw.get("concepts", {}):
        return {"error": f"concept_id '{concept_id}' already exists"}

    # Build visual_concepts list from comma-separated string
    vis_raw = data.get("visual_concepts", "")
    visual_concepts = [v.strip() for v in vis_raw.split(",") if v.strip()]

    # Build scenarios list from up to 3 non-empty scenario strings
    scenarios = [
        {"text": s.strip(), "tags": []}
        for s in [
            data.get("scenario_1", ""),
            data.get("scenario_2", ""),
            data.get("scenario_3", ""),
        ]
        if s.strip()
    ]

    # Optional source
    source_author = (data.get("source_author") or "").strip()
    source_work   = (data.get("source_work") or "").strip()
    source_year   = data.get("source_year")
    canonical_source = None
    if source_author and source_work:
        canonical_source = {
            "author": source_author,
            "work":   source_work,
            "year":   int(source_year) if source_year else None,
            "url":    (data.get("source_url") or "").strip() or None,
        }

    entry = {
        "concept_id":       concept_id,
        "canonical_name":   name,
        "alternate_names":  [],
        "definition":       definition,
        "canonical_source": canonical_source,
        "scenarios":        scenarios,
        "mechanism":        (data.get("mechanism") or "").strip() or None,
        "visual_concepts":  visual_concepts,
        "format_used":      [],
        "provenance":       "manual",
        "provisional":      False,
        "quarantined":      False,
        "strikes":          0,
        "created_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_used_at":     None,
        "source_candidate_id": None,
    }

    raw.setdefault("concepts", {})[concept_id] = entry
    raw["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tmp = CORPUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    tmp.replace(CORPUS_PATH)

    return {"ok": True, "concept_id": concept_id}
