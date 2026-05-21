from __future__ import annotations

import logging
import urllib.parse

import requests

from core.schemas import Candidate

logger = logging.getLogger(__name__)
_SEARCH  = "https://commons.wikimedia.org/w/api.php"
_COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"

# Only licences we accept — CC0 and CC-BY variants (no NC/ND)
_ALLOWED_LICENCES = {
    "cc-zero", "cc0",
    "cc-by-1.0", "cc-by-2.0", "cc-by-2.5", "cc-by-3.0", "cc-by-4.0",
    "pd", "public domain",
}


def _is_allowed_licence(licence: str) -> bool:
    return any(allowed in licence.lower() for allowed in _ALLOWED_LICENCES)


def search_wikimedia(
    query: str,
    per_page: int = 10,
    seen_ids: set[str] | None = None,
) -> list[Candidate]:
    seen_ids = seen_ids or set()

    # Step 1: search Commons for images/video matching the query
    try:
        resp = requests.get(
            _SEARCH,
            params={
                "action":      "query",
                "list":        "search",
                "srsearch":    f"{query} filetype:bitmap|jpg|png",
                "srnamespace": 6,          # File namespace
                "srlimit":     per_page,
                "format":      "json",
            },
            headers={"User-Agent": "MoneyPsychologyBot/1.0 (content pipeline; non-commercial)"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("query", {}).get("search", [])
    except Exception as exc:
        logger.warning("Wikimedia search failed for %r: %s", query, exc)
        return []

    if not results:
        return []

    # Step 2: batch-fetch extmetadata for licence + attribution
    titles = [r["title"] for r in results]
    try:
        meta_resp = requests.get(
            _SEARCH,
            params={
                "action":   "query",
                "titles":   "|".join(titles),
                "prop":     "imageinfo",
                "iiprop":   "url|extmetadata",
                "format":   "json",
            },
            headers={"User-Agent": "MoneyPsychologyBot/1.0 (content pipeline; non-commercial)"},
            timeout=15,
        )
        meta_resp.raise_for_status()
        pages = meta_resp.json().get("query", {}).get("pages", {})
    except Exception as exc:
        logger.warning("Wikimedia metadata fetch failed: %s", exc)
        return []

    candidates: list[Candidate] = []
    for page in pages.values():
        title = page.get("title", "")
        cid   = "wiki_" + urllib.parse.quote(title, safe="")[:40]

        if cid in seen_ids:
            continue

        infos = page.get("imageinfo", [])
        if not infos:
            continue
        info = infos[0]
        meta = info.get("extmetadata", {})

        licence_short = meta.get("LicenseShortName", {}).get("value", "")
        if not _is_allowed_licence(licence_short):
            continue

        licence_url = meta.get("LicenseUrl", {}).get("value", "")
        artist      = meta.get("Artist", {}).get("value", "")
        # Strip HTML tags from artist field
        import re as _re
        artist = _re.sub(r"<[^>]+>", "", artist).strip()

        attr_required = not any(x in licence_short.lower() for x in ("cc0", "cc-zero", "pd", "public domain"))
        attr_text     = f"{artist} / Wikimedia Commons / {licence_short}" if attr_required and artist else None

        file_url = info.get("url", "")

        candidates.append(Candidate(
            id                   = cid,
            source               = "wikimedia",
            media_kind           = "image",
            title                = title.replace("File:", "").replace("_", " ")[:200],
            description          = query,
            download_url         = file_url or None,
            licence_url          = licence_url or "https://commons.wikimedia.org/wiki/Commons:Licensing",
            licence_type         = licence_short or "unknown",
            attribution_required = attr_required,
            attribution_text     = attr_text,
            matched_keywords     = [query],
            tags                 = [],
            score                = None,
            score_breakdown      = {},
            gate_pass            = None,
            full_response        = {
                "title":   title,
                "licence": licence_short,
                "artist":  artist,
            },
            provenance           = "wikimedia",
            candidate_concept_id = None,
        ))

    return candidates
