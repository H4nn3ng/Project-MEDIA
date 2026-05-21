from __future__ import annotations

import logging
from typing import Any

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_BASE = "https://api.pexels.com/videos/search"


def search_pexels(
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
            headers={"Authorization": api_key},
            params={"query": query, "orientation": "portrait", "size": "medium", "per_page": per_page},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Pexels search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for video in data.get("videos", []):
        cid = f"pexels_{video['id']}"
        if cid in seen_ids:
            continue

        tags = [t["name"].lower() for t in video.get("tags", [])]

        # Best portrait video file
        files = sorted(
            [f for f in video.get("video_files", []) if f.get("quality") in ("hd", "sd")],
            key=lambda f: f.get("width", 0),
            reverse=True,
        )
        download_url = files[0]["link"] if files else None

        candidates.append(Candidate(
            id                   = cid,
            source               = "pexels",
            media_kind           = "footage",
            title                = video.get("url", "").split("/")[-2].replace("-", " "),
            description          = query,
            download_url         = download_url,
            licence_url          = "https://www.pexels.com/license/",
            licence_type         = "CC0",
            attribution_required = False,
            attribution_text     = None,
            matched_keywords     = [query],
            tags                 = tags,
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {"id": video["id"], "duration": video.get("duration"), "width": video.get("width")},
            provenance           = "pexels",
            candidate_concept_id = concept_id,
        ))

    return candidates
