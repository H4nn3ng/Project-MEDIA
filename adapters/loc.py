from __future__ import annotations

import logging

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_SEARCH = "https://www.loc.gov/photos/"


def search_loc(
    query: str,
    per_page: int = 10,
    seen_ids: set[str] | None = None,
) -> list[Candidate]:
    """Search the Library of Congress free-to-use photos (public domain)."""
    seen_ids = seen_ids or set()
    try:
        resp = requests.get(
            _SEARCH,
            params={
                "q":          query,
                "fo":         "json",
                "c":          per_page,
                "at":         "results",
                "fa":         "access-restricted:false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("LoC search failed for %r: %s", query, exc)
        return []

    candidates: list[Candidate] = []
    for item in data.get("results", []):
        item_id = item.get("id", "")
        cid     = f"loc_{item_id.rstrip('/').split('/')[-1]}"

        if cid in seen_ids:
            continue

        # Prefer JPEG original; strip URL fragment before checking extension
        image_url: str | None = None
        for res in item.get("image_url", []):
            if isinstance(res, str) and res.split("#")[0].endswith((".jpg", ".jpeg")):
                image_url = res
                break
        if image_url is None:
            urls = item.get("image_url", [])
            image_url = urls[0] if urls else None

        title = item.get("title", query)[:200]
        tags  = [s.lower() for s in item.get("subject", []) if isinstance(s, str)]

        candidates.append(Candidate(
            id                   = cid,
            source               = "loc",
            media_kind           = "image",
            title                = title,
            description          = query,
            download_url         = image_url,
            licence_url          = "https://www.loc.gov/free-to-use/",
            licence_type         = "public domain",
            attribution_required = False,
            attribution_text     = None,
            matched_keywords     = [query],
            tags                 = tags,
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "id":       item_id,
                "date":     item.get("date"),
                "call_num": item.get("call_number"),
            },
            provenance           = "loc",
            candidate_concept_id = None,
        ))

    return candidates
