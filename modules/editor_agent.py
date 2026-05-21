from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from config import (
    CAPTION_MAX_CHARS,
    CAPTION_MIN_CHARS,
    CAROUSEL_SLIDE_COUNT,
    FORBIDDEN_TOKENS,
    HASHTAG_MAX,
    HASHTAG_MIN,
    HOOK_MAX_WORDS,
    VIDEO_LENGTH_MAX_S,
    VIDEO_LENGTH_MIN_S,
    VOICE_DEFAULT_SPEED,
)
from core.orchestration import RetryPolicy, RunContext, with_retry
from core.schemas import (
    BeatStructure,
    Candidate,
    CarouselLayout,
    CarouselSlide,
    Claim,
    ComponentRef,
    ConceptEntry,
    EditPlan,
    ItemRef,
)

logger = logging.getLogger(__name__)

_REEL_PROMPT = """\
You are the editor for a behavioural-finance educational short-form video channel.
Plan a {min_s}–{max_s} second vertical (9:16) reel for the concept below.

CHANNEL VOICE:
- Calm, observational, non-advisory
- "Here is how human brains behave" — not "you should do X"
- Grounded in peer-reviewed research; never speculative
- Forbidden tokens (never use): {forbidden}

CONCEPT:
  id:          {concept_id}
  name:        {name}
  definition:  {definition}
  mechanism:   {mechanism}
  scenarios:   {scenarios}
  citation:    {citation}

VOICE SPEED: {speed}x  (target words ≈ {min_words}–{max_words})

AVAILABLE FOOTAGE ({n_footage} clips):
{footage_json}

AVAILABLE MUSIC ({n_music} tracks):
{music_json}

AVAILABLE BEDS/SFX ({n_beds} tracks):
{beds_json}

REGENERATION HINTS (if any — address these):
{hints}

Return ONLY valid JSON — no markdown fences, no extra text:
{{
  "format": "reel",
  "narrative": "<full voiceover script — {min_words}–{max_words} words, no forbidden tokens>",
  "beat_structure": {{
    "hook":   "<≤{hook_words} words — the moment of recognition>",
    "body":   "<the concept mechanism>",
    "reveal": "<what this explains about behaviour>",
    "close":  "<quiet, non-advisory close — no CTA>"
  }},
  "footage_file": "<exact filename from list above>",
  "music_file":   "<exact filename from music list, or null>",
  "bed_file":     "<exact filename from bed list, or null>",
  "caption":      "<{min_cap}–{max_cap} chars — educational, no advice>",
  "hashtags":     ["<tag1>", ...],
  "claims": [
    {{
      "text":               "<verbatim claim from narrative>",
      "claim_type":         "definitional|attributional|quantitative|anecdotal|historical",
      "confidence":         "high|medium|low",
      "uncertainty_reason": "<why uncertain, or null>"
    }}
  ],
  "citation":   "<Author Year, Work — or null>",
  "reasoning":  "<one paragraph on the emotional arc>"
}}

Rules:
- footage_file must be an exact filename from the list (or null if list is empty)
- hashtags: {min_ht}–{max_ht} items, no #, no forbidden hashtags
- claims: extract every factual assertion from the narrative (at least 1)
"""

_CAROUSEL_PROMPT = """\
You are the editor for a behavioural-finance educational carousel channel.
Plan a {n_slides}-slide carousel (1080×1350px) for the concept below.

CHANNEL VOICE:
- Calm, observational, non-advisory
- Grounded in peer-reviewed research; never speculative
- Forbidden tokens (never use): {forbidden}

CONCEPT:
  id:          {concept_id}
  name:        {name}
  definition:  {definition}
  mechanism:   {mechanism}
  scenarios:   {scenarios}
  citation:    {citation}

AVAILABLE IMAGES ({n_images} items):
{images_json}

REGENERATION HINTS (if any):
{hints}

Return ONLY valid JSON — no markdown fences, no extra text:
{{
  "format": "carousel",
  "narrative": "<full read-through of all slide text concatenated>",
  "carousel_layout": {{
    "slide_count": {n_slides},
    "slide_style": "minimal-dark",
    "slides": [
      {{
        "role":       "hook|setup|mechanism|example|data|takeaway|cta",
        "text":       "<slide text — punchy, 1–3 lines>",
        "asset_file": "<exact filename from images list, or null>",
        "bg_color":   "<hex, or null>",
        "attribution": "<artist / licence, or null>"
      }}
    ]
  }},
  "caption":   "<{min_cap}–{max_cap} chars>",
  "hashtags":  ["<tag1>", ...],
  "claims": [
    {{
      "text":               "<verbatim claim>",
      "claim_type":         "definitional|attributional|quantitative|anecdotal|historical",
      "confidence":         "high|medium|low",
      "uncertainty_reason": null
    }}
  ],
  "citation":   "<Author Year, Work — or null>",
  "reasoning":  "<one paragraph>"
}}

Rules:
- First slide role: "hook"; last slide role: "cta" (never "follow us" — just a question to ponder)
- hashtags: {min_ht}–{max_ht} items, no # prefix
- claims: extract every factual assertion (at least 1)
"""


def _target_words(speed: float, min_s: int, max_s: int) -> tuple[int, int]:
    wps = speed * 2.5   # approximate words/second at given speed
    return (round(min_s * wps), round(max_s * wps))


def _footage_summary(candidates: list[Candidate]) -> list[dict]:
    return [
        {"file": c.id, "title": c.title[:80], "tags": c.tags[:5]}
        for c in candidates if c.download_url
    ]


def _parse_plan_json(raw: str, candidate_id: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.rstrip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("EditPlan JSON parse failed (%s): %s", candidate_id, exc)
        return None


def _build_edit_plan(
    data: dict,
    concept: ConceptEntry,
    footage_map: dict[str, Candidate],
    music_map:   dict[str, Candidate],
    bed_map:     dict[str, Candidate],
    image_map:   dict[str, Candidate],
) -> EditPlan:
    fmt = data.get("format", "reel")

    # ── Items (selected assets) ──────────────────────────────────────────────
    items: list[ItemRef] = []
    def _add_item(file_key: str, asset_map: dict[str, Candidate]) -> None:
        fn = data.get(file_key)
        if not fn:
            return
        cand = asset_map.get(fn)
        if cand:
            items.append(ItemRef(
                file               = fn,
                reasoning          = f"selected by editor for {file_key}",
                attribution_required = cand.attribution_required,
                attribution_text    = cand.attribution_text,
            ))

    if fmt == "reel":
        _add_item("footage_file", footage_map)
        _add_item("music_file",   music_map)
        _add_item("bed_file",     bed_map)
    else:
        for slide in data.get("carousel_layout", {}).get("slides", []):
            af = slide.get("asset_file")
            if af and af in image_map:
                cand = image_map[af]
                items.append(ItemRef(
                    file               = af,
                    reasoning          = slide.get("role", "slide"),
                    attribution_required = cand.attribution_required,
                    attribution_text    = cand.attribution_text,
                ))

    # ── Claims ────────────────────────────────────────────────────────────────
    claims = [
        Claim(
            text               = c["text"],
            claim_type         = c.get("claim_type", "definitional"),
            confidence         = c.get("confidence", "medium"),
            uncertainty_reason = c.get("uncertainty_reason"),
        )
        for c in data.get("claims", [])
    ]

    # ── Beat structure / carousel layout ─────────────────────────────────────
    beat_structure: BeatStructure | None = None
    carousel_layout: CarouselLayout | None = None

    if fmt == "reel":
        bs = data.get("beat_structure", {})
        beat_structure = BeatStructure(
            hook   = bs.get("hook", ""),
            body   = bs.get("body", ""),
            reveal = bs.get("reveal", ""),
            close  = bs.get("close", ""),
        )
    else:
        cl = data.get("carousel_layout", {})
        carousel_layout = CarouselLayout(
            slide_count = cl.get("slide_count", CAROUSEL_SLIDE_COUNT),
            slide_style = cl.get("slide_style", "minimal-dark"),
            slides      = [
                CarouselSlide(
                    role       = s.get("role", "body"),
                    text       = s.get("text", ""),
                    asset_file = s.get("asset_file"),
                    bg_color   = s.get("bg_color"),
                    attribution = s.get("attribution"),
                )
                for s in cl.get("slides", [])
            ],
        )

    return EditPlan(
        concept_id      = concept.concept_id,
        format          = fmt,
        narrative       = data.get("narrative", ""),
        beat_structure  = beat_structure,
        carousel_layout = carousel_layout,
        items           = items,
        claims          = claims,
        caption         = data.get("caption", "")[:CAPTION_MAX_CHARS],
        hashtags        = data.get("hashtags", [])[:HASHTAG_MAX],
        citation        = data.get("citation"),
        reasoning       = data.get("reasoning", ""),
    )


def generate_edit_plan(
    concept:   ConceptEntry,
    fmt:       str,
    footage:   list[Candidate],
    music:     list[Candidate],
    beds:      list[Candidate],
    images:    list[Candidate],
    ctx:       RunContext,
    hints:     list[str] | None = None,
) -> EditPlan | None:
    """
    Call Gemini Pro to produce an EditPlan for the given concept + format.
    Returns EditPlan on success, None on total failure.
    """
    hints = hints or []
    voice_speed = ctx.memory.voice.get("speed", VOICE_DEFAULT_SPEED)
    min_words, max_words = _target_words(voice_speed, VIDEO_LENGTH_MIN_S, VIDEO_LENGTH_MAX_S)

    citation_str = ""
    if concept.canonical_source:
        cs = concept.canonical_source
        citation_str = f"{cs.author}, {cs.year}, {cs.work}"

    scenarios_str = "; ".join(s.text for s in concept.scenarios[:3]) or "n/a"

    footage_map = {c.id: c for c in footage}
    music_map   = {c.id: c for c in music}
    bed_map     = {c.id: c for c in beds}
    image_map   = {c.id: c for c in images}

    if fmt == "reel":
        prompt = _REEL_PROMPT.format(
            min_s       = VIDEO_LENGTH_MIN_S,
            max_s       = VIDEO_LENGTH_MAX_S,
            forbidden   = ", ".join(FORBIDDEN_TOKENS[:8]),
            concept_id  = concept.concept_id,
            name        = concept.canonical_name,
            definition  = concept.definition,
            mechanism   = concept.mechanism or "n/a",
            scenarios   = scenarios_str,
            citation    = citation_str or "n/a",
            speed       = voice_speed,
            min_words   = min_words,
            max_words   = max_words,
            hook_words  = HOOK_MAX_WORDS,
            n_footage   = len(footage),
            footage_json = json.dumps(_footage_summary(footage), ensure_ascii=False),
            n_music     = len(music),
            music_json  = json.dumps(_footage_summary(music), ensure_ascii=False),
            n_beds      = len(beds),
            beds_json   = json.dumps(_footage_summary(beds), ensure_ascii=False),
            hints       = "\n".join(hints) if hints else "(none)",
            min_cap     = CAPTION_MIN_CHARS,
            max_cap     = CAPTION_MAX_CHARS,
            min_ht      = HASHTAG_MIN,
            max_ht      = HASHTAG_MAX,
        )
    else:
        prompt = _CAROUSEL_PROMPT.format(
            n_slides    = CAROUSEL_SLIDE_COUNT,
            forbidden   = ", ".join(FORBIDDEN_TOKENS[:8]),
            concept_id  = concept.concept_id,
            name        = concept.canonical_name,
            definition  = concept.definition,
            mechanism   = concept.mechanism or "n/a",
            scenarios   = scenarios_str,
            citation    = citation_str or "n/a",
            n_images    = len(images),
            images_json = json.dumps(_footage_summary(images), ensure_ascii=False),
            hints       = "\n".join(hints) if hints else "(none)",
            min_cap     = CAPTION_MIN_CHARS,
            max_cap     = CAPTION_MAX_CHARS,
            min_ht      = HASHTAG_MIN,
            max_ht      = HASHTAG_MAX,
        )

    policy = RetryPolicy(max_attempts=3, base_delay_s=3.0)
    try:
        result = with_retry(
            lambda: ctx.model_pro.generate_content(prompt),
            policy=policy,
            label=f"editor:{concept.concept_id}:{fmt}",
        )
        raw  = result.text or ""
        data = _parse_plan_json(raw, concept.concept_id)
    except Exception as exc:
        logger.error("EditPlan generation failed for %s/%s: %s", concept.concept_id, fmt, exc)
        return None

    if not data:
        return None

    return _build_edit_plan(data, concept, footage_map, music_map, bed_map, image_map)
