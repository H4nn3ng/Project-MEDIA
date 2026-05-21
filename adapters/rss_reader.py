from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser

from config import RSS_FEEDS, RSS_LOOKBACK_DAYS
from core.schemas import Candidate

logger = logging.getLogger(__name__)


def _entry_id(feed_url: str, entry_link: str) -> str:
    raw = f"{feed_url}::{entry_link}"
    return "rss_" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def _parse_date(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                import time as _time
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_rss(seen_ids: set[str]) -> list[Candidate]:
    """Fetch all RSS feeds, return unseen Candidates from the last RSS_LOOKBACK_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RSS_LOOKBACK_DAYS)
    candidates: list[Candidate] = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", feed_url, exc)
            continue

        for entry in feed.entries:
            link = getattr(entry, "link", "") or ""
            cid  = _entry_id(feed_url, link)

            if cid in seen_ids:
                continue

            pub_date = _parse_date(entry)
            if pub_date and pub_date < cutoff:
                continue

            title   = getattr(entry, "title",   "") or ""
            summary = getattr(entry, "summary", "") or ""
            if not title:
                continue

            candidates.append(Candidate(
                id                   = cid,
                source               = "rss",
                media_kind           = "concept",
                title                = title.strip(),
                description          = summary.strip()[:1000],
                download_url         = link or None,
                licence_url          = None,
                licence_type         = "unknown",
                attribution_required = False,
                attribution_text     = None,
                matched_keywords     = [],
                tags                 = [],
                score                = None,
                score_breakdown      = {},
                gate_pass            = None,
                full_response        = {"feed_url": feed_url},
                provenance           = "rss",
                candidate_concept_id = None,
            ))

        logger.info("RSS %s → %d new entries", feed_url, sum(
            1 for c in candidates if c.full_response.get("feed_url") == feed_url
        ))

    return candidates
