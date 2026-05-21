from __future__ import annotations

import logging

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_BASE = "https://api.coverr.co/videos"


def search_coverr(
    query: str,
    api_key: str,
    per_page: int = 10,
    seen_ids: set[str] | None = None,
    concept_id: str | None = None,
) -> list[Candidate]:
    if not api_key:
        return []
    seen_ids = seen_ids or set()

    try:
        resp = requests.get(
            _BASE,
            params={"keywords": query, "page_size": per_page},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Coverr-Token":  api_key,          # kept for older API compat
                "User-Agent":    "MoneyPsychologyBot/1.0 (content pipeline; non-commercial)",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Coverr search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for video in data.get("hits", []):
        cid = f"coverr_{video.get('id', '')}"
        if cid in seen_ids:
            continue

        # Coverr provides mp4 URLs at different qualities
        urls = video.get("urls", {})
        download_url = (
            urls.get("mp4_download")
            or urls.get("mp4")
            or urls.get("preview")
        )
        if not download_url:
            continue

        tags = [t.lower() for t in video.get("tags", [])]

        candidates.append(Candidate(
            id                   = cid,
            source               = "coverr",
            media_kind           = "footage",
            title                = video.get("title", query)[:200],
            description          = query,
            download_url         = download_url,
            licence_url          = "https://coverr.co/license",
            licence_type         = "coverr",
            attribution_required = False,
            attribution_text     = None,
            matched_keywords     = [query],
            tags                 = tags,
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "id":       video.get("id"),
                "duration": video.get("duration"),
            },
            provenance           = "coverr",
            candidate_concept_id = concept_id,
        ))

    return candidates
