from __future__ import annotations

import logging

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
# FMA content is archived on the Internet Archive
_BASE = "https://archive.org/advancedsearch.php"


def search_fma(
    query: str,
    limit: int = 10,
    seen_ids: set[str] | None = None,
) -> list[Candidate]:
    """Search Free Music Archive content via Internet Archive (CC0/CC-BY only)."""
    seen_ids = seen_ids or set()
    try:
        resp = requests.get(
            _BASE,
            params={
                "q":       f'subject:"fma" mediatype:audio ({query}) licenseurl:*creativecommons*',
                "output":  "json",
                "rows":    limit,
                "fl[]":    ["identifier", "title", "licenseurl", "creator"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
    except Exception as exc:
        logger.warning("FMA/IA search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for doc in docs:
        identifier = doc.get("identifier", "")
        if not identifier:
            continue

        cid = f"fma_{identifier}"
        if cid in seen_ids:
            continue

        licence_url = doc.get("licenseurl", "") or ""
        # Skip non-commercial licences
        if "nc" in licence_url.lower():
            continue

        attr_required = "by" in licence_url.lower() and "cc0" not in licence_url.lower()
        creator = doc.get("creator", "")
        attr_text = (
            f"{creator} / Internet Archive (FMA) / {licence_url}"
            if attr_required and creator else None
        )

        # Standard IA MP3 download pattern
        download_url = f"https://archive.org/download/{identifier}/{identifier}.mp3"

        candidates.append(Candidate(
            id                   = cid,
            source               = "fma",
            media_kind           = "music",
            title                = (doc.get("title") or query)[:200],
            description          = query,
            download_url         = download_url,
            licence_url          = licence_url or "https://creativecommons.org/",
            licence_type         = "cc-by" if attr_required else "CC0",
            attribution_required = attr_required,
            attribution_text     = attr_text,
            matched_keywords     = [query],
            tags                 = [],
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "identifier": identifier,
                "creator":    creator,
                "licenseurl": licence_url,
            },
            provenance           = "fma",
            candidate_concept_id = None,
        ))

    return candidates
