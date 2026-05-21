from __future__ import annotations

import logging

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_BASE = "https://api.jamendo.com/v3.0/tracks/"


def search_jamendo(
    query: str,
    api_key: str,
    tags: list[str] | None = None,
    limit: int = 10,
    seen_ids: set[str] | None = None,
) -> list[Candidate]:
    """Search Jamendo for CC-licensed music tracks."""
    seen_ids = seen_ids or set()
    params: dict = {
        "client_id":   api_key,
        "format":      "json",
        "limit":       limit,
        "search":      query,
        "audioformat": "mp32",
        "order":       "popularity_total",
        "include":     "licenses",
        "licensecc":   "1",
    }
    if tags:
        params["tags"] = "+".join(tags)

    try:
        resp = requests.get(_BASE, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Jamendo search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for track in data.get("results", []):
        cid = f"jamendo_{track['id']}"
        if cid in seen_ids:
            continue

        # Skip non-commercial-friendly licences (NC = no commercial use)
        licence = track.get("license_ccurl", "") or ""
        if "nc" in licence.lower():
            continue

        attr_required = "by" in licence.lower() and "cc0" not in licence.lower()
        attr_text = (
            f"{track.get('artist_name', '')} – {track.get('name', '')} / Jamendo / {licence}"
            if attr_required else None
        )

        candidates.append(Candidate(
            id                   = cid,
            source               = "jamendo",
            media_kind           = "music",
            title                = track.get("name", query)[:200],
            description          = query,
            download_url         = track.get("audiodownload") or track.get("audio"),
            licence_url          = licence or "https://jamendo.com/legal/licensing",
            licence_type         = licence.split("/")[-2] if licence else "cc",
            attribution_required = attr_required,
            attribution_text     = attr_text,
            matched_keywords     = [query],
            tags                 = [t.lower() for t in track.get("musicinfo", {}).get("tags", {}).get("genres", [])],
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "id":          track["id"],
                "artist_name": track.get("artist_name"),
                "duration":    track.get("duration"),
                "licence":     licence,
            },
            provenance           = "jamendo",
            candidate_concept_id = None,
        ))

    return candidates
