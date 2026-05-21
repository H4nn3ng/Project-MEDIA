from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True, kw_only=True)
class CanonicalSource:
    author: str
    work: str
    year: str | int | None = None
    url: str | None = None


@dataclass(slots=True, kw_only=True)
class ConceptScenario:
    text: str
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ConceptEntry:
    concept_id: str
    canonical_name: str
    alternate_names: list[str] = field(default_factory=list)
    definition: str
    canonical_source: CanonicalSource | None = None
    scenarios: list[ConceptScenario] = field(default_factory=list)
    mechanism: str | None = None
    visual_concepts: list[str] = field(default_factory=list)
    format_used: list[str] = field(default_factory=list)
    provenance: str
    provisional: bool = False
    quarantined: bool = False
    strikes: int = 0
    created_at: str
    last_used_at: str | None = None
    source_candidate_id: str | None = None


@dataclass(slots=True, kw_only=True)
class ConceptCorpus:
    version: str
    last_updated: str
    concepts: dict[str, ConceptEntry]


@dataclass(slots=True, kw_only=True)
class Candidate:
    id: str
    source: str
    media_kind: str
    title: str
    description: str = ""
    download_url: str | None = None
    licence_url: str | None = None
    licence_type: str = "unknown"
    attribution_required: bool = False
    attribution_text: str | None = None
    matched_keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    score: float | None = None
    score_breakdown: dict[str, float] = field(default_factory=dict)
    gate_pass: bool | None = None
    full_response: dict[str, Any] = field(default_factory=dict)
    provenance: str
    candidate_concept_id: str | None = None


@dataclass(slots=True, kw_only=True)
class ItemRef:
    file: str
    reasoning: str
    attribution_required: bool = False
    attribution_text: str | None = None
    position: dict[str, float] | None = None


@dataclass(slots=True, kw_only=True)
class ComponentRef:
    role: str
    item: ItemRef | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class CarouselSlide:
    role: str
    text: str
    asset_file: str | None = None
    bg_color: str | None = None
    attribution: str | None = None


@dataclass(slots=True, kw_only=True)
class CarouselLayout:
    slide_count: int
    slide_style: str
    slides: list[CarouselSlide]


@dataclass(slots=True, kw_only=True)
class BeatStructure:
    hook: str
    body: str
    reveal: str
    close: str


@dataclass(slots=True, kw_only=True)
class Claim:
    text: str
    claim_type: str
    confidence: str
    uncertainty_reason: str | None = None


@dataclass(slots=True, kw_only=True)
class EditPlan:
    concept_id: str
    format: str
    narrative: str
    beat_structure: BeatStructure | None = None
    carousel_layout: CarouselLayout | None = None
    items: list[ItemRef]
    components: dict[str, ComponentRef | None] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    caption: str
    hashtags: list[str]
    citation: str | None = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str


@dataclass(slots=True, kw_only=True)
class ClaimVerdict:
    claim_text: str
    claim_type: str
    force_flagged: bool = False
    verdict: str
    confidence: float
    sources: list[str] = field(default_factory=list)
    notes: str | None = None
    cache_hit: bool = False


@dataclass(slots=True, kw_only=True)
class AdvisoryDriftReport:
    drift_detected: bool
    flagged_phrases: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(slots=True, kw_only=True)
class FactCheckReport:
    plan_id: str | None = None
    candidate_id: str | None = None
    stage: str
    verdict: str
    claims: list[ClaimVerdict] = field(default_factory=list)
    advisory_drift_check: AdvisoryDriftReport
    timestamp: str
    cost_usd: float = 0.0


@dataclass(slots=True, kw_only=True)
class IngestionVerdict:
    decision: str
    report: FactCheckReport
    canonical_concept_id: str | None = None
    proposed_corpus_entry: dict[str, Any] | None = None


@dataclass(slots=True, kw_only=True)
class EditPlanVerdict:
    decision: str
    report: FactCheckReport
    regenerate_hints: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class MemoryStore:
    created: str = ""
    last_updated: str = ""
    voice: dict[str, Any] = field(default_factory=dict)
    high_performing_concepts: list[str] = field(default_factory=list)
    low_performing_concepts: list[str] = field(default_factory=list)
    high_performing_visual_styles: list[str] = field(default_factory=list)
    low_performing_visual_styles: list[str] = field(default_factory=list)
    high_performing_keywords: list[str] = field(default_factory=list)
    low_performing_keywords: list[str] = field(default_factory=list)
    concept_history: list[dict[str, Any]] = field(default_factory=list)
    rss_post_ids_seen: list[str] = field(default_factory=list)
    reddit_post_ids_seen: list[str] = field(default_factory=list)
    rejected_ids: list[str] = field(default_factory=list)
    external_concept_strikes: dict[str, dict[str, Any]] = field(default_factory=dict)
    factcheck_cache: dict[str, dict[str, Any]] = field(default_factory=dict)


def corpus_from_dict(data: dict[str, Any]) -> ConceptCorpus:
    """Load a ConceptCorpus from a parsed JSON dict."""
    concepts: dict[str, ConceptEntry] = {}

    for concept_id, raw_entry in data.get("concepts", {}).items():
        entry_data = dict(raw_entry)

        canonical_source = entry_data.get("canonical_source")
        if canonical_source is not None:
            entry_data["canonical_source"] = CanonicalSource(**canonical_source)

        entry_data["scenarios"] = [
            ConceptScenario(**scenario)
            for scenario in entry_data.get("scenarios", [])
        ]

        concepts[concept_id] = ConceptEntry(**entry_data)

    return ConceptCorpus(
        version=data["version"],
        last_updated=data.get("last_updated", ""),
        concepts=concepts,
    )


def edit_plan_to_dict(plan: EditPlan) -> dict[str, Any]:
    """Serialise an EditPlan to a plain dict suitable for json.dump()."""
    return asdict(plan)
