from __future__ import annotations

import logging

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_BASE = "https://pixabay.com/api/videos/"


def search_pixabay(
    query: str,
    api_key: str,
    per_page: int = 15,
    seen_ids: set[str] | None = None,
    concept_id: str | None = None,
) -> list[Candidate]:
    seen_ids = seen_ids or set()
    try:
        resp = requests.get(
            _BASE,
            params={
                "key":         api_key,
                "q":           query,
                "video_type":  "film",
                "orientation": "vertical",
                "per_page":    per_page,
                "safesearch":  "true",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Pixabay search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for hit in data.get("hits", []):
        cid = f"pixabay_{hit['id']}"
        if cid in seen_ids:
            continue

        tags = [t.strip().lower() for t in hit.get("tags", "").split(",") if t.strip()]

        # Prefer large > medium > small quality
        videos = hit.get("videos", {})
        file_url: str | None = None
        for quality in ("large", "medium", "small"):
            url = videos.get(quality, {}).get("url")
            if url:
                file_url = url
                break

        candidates.append(Candidate(
            id                   = cid,
            source               = "pixabay",
            media_kind           = "footage",
            title                = hit.get("tags", query)[:120],
            description          = query,
            download_url         = file_url,
            licence_url          = "https://pixabay.com/service/license-summary/",
            licence_type         = "pixabay",
            attribution_required = False,
            attribution_text     = None,
            matched_keywords     = [query],
            tags                 = tags,
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "id":       hit["id"],
                "duration": hit.get("duration"),
                "views":    hit.get("views"),
            },
            provenance           = "pixabay",
            candidate_concept_id = concept_id,
        ))

    return candidates
