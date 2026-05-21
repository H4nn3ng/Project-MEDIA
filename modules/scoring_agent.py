from __future__ import annotations

import json
import logging
from dataclasses import replace
from itertools import islice

from config import (
    FACTUAL_SAFETY_GATE,
    SCORED_POOL_THRESHOLD,
    STANDARD_POOL_THRESHOLD,
)
from core.orchestration import RetryPolicy, RunContext, with_retry
from core.schemas import Candidate

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20

# ── Scoring dimensions and weights per candidate kind ─────────────────────────
#
# concept   — text/topic candidates from RSS/Reddit; factual_safety gate applies
# footage   — stock video clips; visual quality matters, factual gate doesn't
# image     — archival/stock images; same as footage
# music/bed — audio tracks; mood fit replaces visual concerns

_CONCEPT_WEIGHTS = {
    "relevance": 0.25, "emotional_hook": 0.20, "originality": 0.20,
    "visual_fit": 0.15, "licence_safety": 0.10, "factual_safety": 0.10,
}
_VISUAL_WEIGHTS = {
    "relevance": 0.35, "emotional_hook": 0.25,
    "visual_fit": 0.25, "licence_safety": 0.15,
}
_AUDIO_WEIGHTS = {
    "relevance": 0.30, "mood_fit": 0.40, "licence_safety": 0.30,
}

# ── Prompts ───────────────────────────────────────────────────────────────────

_CONCEPT_BATCH_HEADER = """\
You are a scoring assistant for a behavioural-finance short-form video pipeline.

Score each CONCEPT candidate on six dimensions (each 0–10, one decimal).
Scores are ABSOLUTE — do not compare candidates against each other.

Dimensions:
1. relevance       – how well it fits behavioural finance / money psychology
2. emotional_hook  – emotional pull or personal resonance
3. originality     – fresh angle vs. overused trope (high = fresh)
4. visual_fit      – how well the topic maps to portrait 9:16 video footage
5. licence_safety  – 10=CC0/PD, 8=CC-BY, 4=CC-BY-SA, 2=unknown
6. factual_safety  – 10=established peer-reviewed research, 1=speculative/contested

Rules:
- factual_safety < {gate} → gate_pass must be false
- overall = relevance×0.25 + emotional_hook×0.20 + originality×0.20 + visual_fit×0.15 + licence_safety×0.10 + factual_safety×0.10
- Return ONLY a JSON array (no markdown), one object per candidate, same order as input.
  Each object: {{"idx":<int>,"scores":{{...}},"overall":<float>,"gate_pass":<bool>,"reasoning":"<one sentence>"}}

Candidates:
"""

_VISUAL_BATCH_HEADER = """\
You are a scoring assistant for a behavioural-finance short-form video pipeline.

Score each VISUAL candidate (stock footage clip or archival image) on four dimensions (each 0–10, one decimal).
Scores are ABSOLUTE — do not compare candidates against each other.

Dimensions:
1. relevance       – how well it visually represents behavioural finance / money psychology themes
2. emotional_hook  – emotional resonance for a financially curious general audience
3. visual_fit      – suitability for portrait 9:16 video: clear composition, no distracting text, good framing
4. licence_safety  – 10=CC0/PD, 8=CC-BY, 4=CC-BY-SA, 2=unknown/commercial-restricted

Rules:
- gate_pass = true when overall >= 6.0 (no factual_safety concept — this is visual media)
- overall = relevance×0.35 + emotional_hook×0.25 + visual_fit×0.25 + licence_safety×0.15
- Return ONLY a JSON array (no markdown), one object per candidate, same order as input.
  Each object: {{"idx":<int>,"scores":{{...}},"overall":<float>,"gate_pass":<bool>,"reasoning":"<one sentence>"}}

Candidates:
"""

_AUDIO_BATCH_HEADER = """\
You are a scoring assistant for a behavioural-finance short-form video pipeline.

Score each AUDIO candidate (background music or ambient bed track) on three dimensions (each 0–10, one decimal).
Scores are ABSOLUTE — do not compare candidates against each other.

Dimensions:
1. relevance       – mood fits calm, thoughtful, educational content about money and psychology (penalise upbeat/motivational/energetic tracks)
2. mood_fit        – suitable as background under narration: not too busy, not too prominent, not distracting
3. licence_safety  – 10=CC0/PD, 8=CC-BY, 4=CC-BY-SA, 2=unknown/commercial-restricted

Rules:
- gate_pass = true when overall >= 6.0
- overall = relevance×0.30 + mood_fit×0.40 + licence_safety×0.30
- Return ONLY a JSON array (no markdown), one object per candidate, same order as input.
  Each object: {{"idx":<int>,"scores":{{...}},"overall":<float>,"gate_pass":<bool>,"reasoning":"<one sentence>"}}

Candidates:
"""

# Single-candidate fallback prompts (used when a batch parse fails)
_CONCEPT_SINGLE = """\
You are a scoring assistant for a behavioural-finance short-form video pipeline.

Score this CONCEPT candidate on six dimensions (each 0–10, one decimal):
1. relevance       – how well it fits behavioural finance / money psychology
2. emotional_hook  – emotional pull or personal resonance
3. originality     – fresh angle vs. overused trope (high = fresh)
4. visual_fit      – how well the topic maps to portrait 9:16 video footage
5. licence_safety  – 10=CC0/PD, 8=CC-BY, 4=CC-BY-SA, 2=unknown
6. factual_safety  – 10=established peer-reviewed research, 1=speculative/contested

Rules:
- factual_safety < {gate} → gate_pass must be false
- Return ONLY valid JSON, no markdown: {{"scores":{{...}},"overall":<float>,"gate_pass":<bool>,"reasoning":"<one sentence>"}}

Candidate:
  title: {title}
  description: {description}
  source: {source}
  tags: {tags}
  licence_type: {licence_type}
"""

_VISUAL_SINGLE = """\
You are a scoring assistant for a behavioural-finance short-form video pipeline.

Score this VISUAL candidate (stock footage or image) on four dimensions (each 0–10, one decimal):
1. relevance       – visually represents behavioural finance / money psychology themes
2. emotional_hook  – emotional resonance for a financially curious audience
3. visual_fit      – suitability for portrait 9:16 video: clear composition, no distracting text
4. licence_safety  – 10=CC0/PD, 8=CC-BY, 4=CC-BY-SA, 2=unknown

Rules:
- gate_pass = true when overall >= 6.0
- Return ONLY valid JSON, no markdown: {{"scores":{{...}},"overall":<float>,"gate_pass":<bool>,"reasoning":"<one sentence>"}}

Candidate:
  title: {title}
  description: {description}
  source: {source}
  tags: {tags}
  licence_type: {licence_type}
"""

_AUDIO_SINGLE = """\
You are a scoring assistant for a behavioural-finance short-form video pipeline.

Score this AUDIO candidate on three dimensions (each 0–10, one decimal):
1. relevance       – mood fits calm, educational money psychology content
2. mood_fit        – suitable as background under narration, not distracting
3. licence_safety  – 10=CC0/PD, 8=CC-BY, 4=CC-BY-SA, 2=unknown

Rules:
- gate_pass = true when overall >= 6.0
- Return ONLY valid JSON, no markdown: {{"scores":{{...}},"overall":<float>,"gate_pass":<bool>,"reasoning":"<one sentence>"}}

Candidate:
  title: {title}
  description: {description}
  source: {source}
  tags: {tags}
  licence_type: {licence_type}
"""


def _kind_config(media_kind: str) -> tuple[str, str, dict]:
    """Return (batch_header, single_prompt, weights) for a given media_kind."""
    if media_kind in ("music", "bed"):
        return _AUDIO_BATCH_HEADER, _AUDIO_SINGLE, _AUDIO_WEIGHTS
    if media_kind in ("footage", "image"):
        return _VISUAL_BATCH_HEADER, _VISUAL_SINGLE, _VISUAL_WEIGHTS
    return _CONCEPT_BATCH_HEADER.format(gate=FACTUAL_SAFETY_GATE), _CONCEPT_SINGLE, _CONCEPT_WEIGHTS


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_batch_prompt(batch: list[Candidate], batch_header: str) -> str:
    lines = [batch_header]
    for i, c in enumerate(batch):
        lines.append(
            f"{i}. title={c.title[:200]!r}  source={c.source}"
            f"  tags={', '.join(c.tags[:10])}"
            f"  licence={c.licence_type}"
            f"  description={c.description[:300]!r}"
        )
    return "\n".join(lines)


def _build_single_prompt(c: Candidate, single_template: str) -> str:
    return single_template.format(
        gate         = FACTUAL_SAFETY_GATE,
        title        = c.title[:200],
        description  = c.description[:400],
        source       = c.source,
        tags         = ", ".join(c.tags[:20]),
        licence_type = c.licence_type,
    )


# ── Parsers ───────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip()
    return text


def _apply_score(cand: Candidate, data: dict, weights: dict, is_concept: bool) -> Candidate:
    breakdown = data.get("scores", {})
    overall   = data.get("overall")
    gate      = data.get("gate_pass", False)

    if overall is None:
        overall = sum(breakdown.get(k, 0) * w for k, w in weights.items())

    if is_concept:
        # Hard-enforce factual safety gate regardless of model output
        fs   = breakdown.get("factual_safety", 0)
        gate = bool(gate) and fs >= FACTUAL_SAFETY_GATE
    else:
        # Visual/audio: gate is purely score-based
        gate = float(overall) >= STANDARD_POOL_THRESHOLD

    return replace(cand, score=round(overall, 2), score_breakdown=breakdown, gate_pass=gate)


def _parse_batch(raw: str, batch: list[Candidate], weights: dict, is_concept: bool) -> list[Candidate] | None:
    try:
        items = json.loads(_strip_fences(raw))
        if not isinstance(items, list) or len(items) != len(batch):
            return None
        return [_apply_score(cand, item, weights, is_concept) for item, cand in zip(items, batch)]
    except Exception as exc:
        logger.warning("Batch score parse failed: %s — raw: %.300s", exc, raw)
        return None


def _parse_single(raw: str, cand: Candidate, weights: dict, is_concept: bool) -> Candidate:
    try:
        data = json.loads(_strip_fences(raw))
        return _apply_score(cand, data, weights, is_concept)
    except Exception as exc:
        logger.warning("Single score parse failed for %s: %s", cand.id, exc)
        return cand


# ── Main entry point ──────────────────────────────────────────────────────────

def score_candidates(
    candidates: list[Candidate],
    ctx: RunContext,
) -> list[Candidate]:
    """
    Score candidates in batches of _BATCH_SIZE using Flash-Lite.
    Uses different prompts/gates for concept vs visual/audio candidates.
    Falls back to per-candidate calls for any batch that fails to parse.
    """
    if not candidates:
        return candidates

    media_kind   = candidates[0].media_kind or "concept"
    batch_header, single_template, weights = _kind_config(media_kind)
    is_concept   = media_kind not in ("footage", "image", "music", "bed")

    model  = ctx.model_lite
    policy = RetryPolicy(max_attempts=3, base_delay_s=2.0)
    scored: list[Candidate] = []

    batches = list(_chunks(candidates, _BATCH_SIZE))
    logger.info(
        "Scoring %d %s candidates in %d batches of ≤%d",
        len(candidates), media_kind, len(batches), _BATCH_SIZE,
    )

    for batch_idx, batch in enumerate(batches):
        prompt = _build_batch_prompt(batch, batch_header)
        try:
            result = with_retry(
                lambda p=prompt: model.generate_content(p),
                policy=policy,
                label=f"score_batch:{media_kind}:{batch_idx}",
            )
            parsed = _parse_batch(result.text or "", batch, weights, is_concept)
        except Exception as exc:
            logger.warning("Batch %d API call failed: %s — falling back", batch_idx, exc)
            parsed = None

        if parsed is not None:
            scored.extend(parsed)
            continue

        logger.warning("Batch %d parse failed — falling back to %d individual calls", batch_idx, len(batch))
        for cand in batch:
            prompt = _build_single_prompt(cand, single_template)
            try:
                result = with_retry(
                    lambda p=prompt: model.generate_content(p),
                    policy=policy,
                    label=f"score:{cand.id}",
                )
                scored.append(_parse_single(result.text or "", cand, weights, is_concept))
            except Exception as exc:
                logger.warning("Scoring failed for %s: %s", cand.id, exc)
                scored.append(cand)

    _log_summary(scored, media_kind)
    return scored


# ── Helpers ───────────────────────────────────────────────────────────────────

def _chunks(lst: list, n: int):
    it = iter(lst)
    while chunk := list(islice(it, n)):
        yield chunk


def _log_summary(candidates: list[Candidate], kind: str) -> None:
    passed   = [c for c in candidates if c.gate_pass is True]
    priority = [c for c in passed if (c.score or 0) >= SCORED_POOL_THRESHOLD]
    standard = [c for c in passed if STANDARD_POOL_THRESHOLD <= (c.score or 0) < SCORED_POOL_THRESHOLD]
    rejected = [c for c in candidates if c.gate_pass is False]

    logger.info(
        "Scoring [%s] — total=%d  priority=%d  standard=%d  gate_fail=%d  unscored=%d",
        kind, len(candidates), len(priority), len(standard), len(rejected),
        len([c for c in candidates if c.score is None]),
    )
