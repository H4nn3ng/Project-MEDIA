from __future__ import annotations

import logging

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_BASE = "https://freesound.org/apiv2/search/text/"


def search_freesound(
    query: str,
    api_key: str,
    media_kind: str = "bed",
    page_size: int = 10,
    seen_ids: set[str] | None = None,
) -> list[Candidate]:
    """Search Freesound for CC0 audio (beds, SFX, ambience).

    api_key is the Freesound client_secret used as simple token auth.
    media_kind: 'bed' for ambient/background, 'sfx' for sound effects.
    """
    seen_ids = seen_ids or set()
    try:
        resp = requests.get(
            _BASE,
            params={
                "query":     query,
                "token":     api_key,
                "filter":    'license:"Creative Commons 0"',
                "fields":    "id,name,url,previews,duration,tags,license",
                "page_size": page_size,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Freesound search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for sound in data.get("results", []):
        cid = f"freesound_{sound['id']}"
        if cid in seen_ids:
            continue

        # Use the high-quality MP3 preview — full download requires OAuth2
        download_url = sound.get("previews", {}).get("preview-hq-mp3")
        if not download_url:
            continue

        candidates.append(Candidate(
            id                   = cid,
            source               = "freesound",
            media_kind           = media_kind,
            title                = sound.get("name", query)[:200],
            description          = query,
            download_url         = download_url,
            licence_url          = "https://creativecommons.org/publicdomain/zero/1.0/",
            licence_type         = "CC0",
            attribution_required = False,
            attribution_text     = None,
            matched_keywords     = [query],
            tags                 = [t.lower() for t in sound.get("tags", [])],
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "id":       sound["id"],
                "duration": sound.get("duration"),
                "url":      sound.get("url"),
            },
            provenance           = "freesound",
            candidate_concept_id = None,
        ))

    return candidates
