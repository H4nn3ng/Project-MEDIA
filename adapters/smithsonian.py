from __future__ import annotations

import logging

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_SEARCH = "https://api.si.edu/openaccess/api/v1.0/search"


def search_smithsonian(
    query: str,
    api_key: str,
    per_page: int = 10,
    seen_ids: set[str] | None = None,
) -> list[Candidate]:
    """Search Smithsonian Open Access (CC0 images)."""
    seen_ids = seen_ids or set()
    try:
        resp = requests.get(
            _SEARCH,
            params={
                "q":        query,
                "api_key":  api_key,
                "rows":     per_page,
                "media.usage.access": "CC0",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Smithsonian search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for row in data.get("response", {}).get("rows", []):
        item_id = row.get("id", "")
        cid     = f"si_{item_id}"

        if cid in seen_ids:
            continue

        # Extract first usable image URL from nested media
        image_url: str | None = None
        for media in row.get("_source", {}).get("media", []) or []:
            content = media.get("content", "")
            if content and any(content.endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                image_url = content
                break
            # Some items expose resources list
            for res in media.get("resources", []) or []:
                url = res.get("url", "")
                if url and any(url.endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                    image_url = url
                    break
            if image_url:
                break

        title = (row.get("_source", {}).get("title") or query)[:200]
        tags  = [
            t.lower()
            for t in (row.get("_source", {}).get("topic", []) or [])
            if isinstance(t, str)
        ]

        candidates.append(Candidate(
            id                   = cid,
            source               = "smithsonian",
            media_kind           = "image",
            title                = title,
            description          = query,
            download_url         = image_url,
            licence_url          = "https://www.si.edu/openaccess",
            licence_type         = "CC0",
            attribution_required = False,
            attribution_text     = None,
            matched_keywords     = [query],
            tags                 = tags,
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "id":           item_id,
                "unit_code":    row.get("_source", {}).get("unitCode"),
                "object_type":  row.get("_source", {}).get("objectType"),
            },
            provenance           = "smithsonian",
            candidate_concept_id = None,
        ))

    return candidates
