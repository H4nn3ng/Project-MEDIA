from __future__ import annotations

import logging
import re
from typing import Any

import praw

from config import (
    REDDIT_FILTER_PATTERNS,
    REDDIT_HARD_EXCLUDE_BODY_MIN_CURRENCY_NUMS,
    REDDIT_HARD_EXCLUDE_FLAIR,
    REDDIT_HARD_EXCLUDE_TITLE,
    REDDIT_POSTS_PER_SUBREDDIT,
    REDDIT_SUBREDDITS,
)
from core.schemas import Candidate

logger = logging.getLogger(__name__)

_CURRENCY_RE = re.compile(r"[\$£€]\s*[\d,]+|\d+\s*dollars?", re.IGNORECASE)
_FILTER_RES  = [re.compile(p, re.IGNORECASE) for p in REDDIT_FILTER_PATTERNS]
_TITLE_RES   = [re.compile(p, re.IGNORECASE) for p in REDDIT_HARD_EXCLUDE_TITLE]


def _matches_filter(text: str) -> bool:
    return any(r.search(text) for r in _FILTER_RES)


def _should_exclude(submission: Any) -> bool:
    flair = (submission.link_flair_text or "").lower()
    if any(word in flair for word in REDDIT_HARD_EXCLUDE_FLAIR):
        return True
    title = submission.title or ""
    if any(r.search(title) for r in _TITLE_RES):
        return True
    body = submission.selftext or ""
    if len(_CURRENCY_RE.findall(body)) >= REDDIT_HARD_EXCLUDE_BODY_MIN_CURRENCY_NUMS:
        return True
    return False


def _strip_pii(text: str) -> str:
    """Remove u/ mentions and links before storing."""
    text = re.sub(r"u/\S+", "[user]", text)
    text = re.sub(r"https?://\S+", "[link]", text)
    return text.strip()


def fetch_reddit(reddit_keys: dict[str, str], seen_ids: set[str]) -> list[Candidate]:
    """Fetch top posts from configured subreddits, return unseen Candidates."""
    r = praw.Reddit(
        client_id     = reddit_keys["client_id"],
        client_secret = reddit_keys["client_secret"],
        user_agent    = reddit_keys["user_agent"],
    )

    candidates: list[Candidate] = []

    for sub_name in REDDIT_SUBREDDITS:
        try:
            subreddit = r.subreddit(sub_name)
            posts = list(subreddit.top(time_filter="week", limit=REDDIT_POSTS_PER_SUBREDDIT))
        except Exception as exc:
            logger.warning("Reddit fetch failed for r/%s: %s", sub_name, exc)
            continue

        batch = 0
        for submission in posts:
            cid = f"reddit_{submission.id}"
            if cid in seen_ids:
                continue
            if _should_exclude(submission):
                continue

            title = _strip_pii(submission.title or "")
            body  = _strip_pii(submission.selftext or "")

            # At least title or body must match the behavioural finance filter
            if not (_matches_filter(title) or _matches_filter(body)):
                continue

            candidates.append(Candidate(
                id                   = cid,
                source               = "reddit",
                media_kind           = "concept",
                title                = title[:300],
                description          = body[:1000],
                download_url         = f"https://reddit.com{submission.permalink}",
                licence_url          = None,
                licence_type         = "unknown",
                attribution_required = False,
                attribution_text     = None,
                matched_keywords     = [],
                tags                 = [sub_name],
                score                = None,
                score_breakdown      = {},
                gate_pass            = None,
                full_response        = {
                    "subreddit": sub_name,
                    "score":     submission.score,
                    "num_comments": submission.num_comments,
                },
                provenance           = "reddit",
                candidate_concept_id = None,
            ))
            batch += 1

        logger.info("Reddit r/%s → %d candidates", sub_name, batch)

    return candidates
