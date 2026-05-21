from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from config import (
    CANONICAL_RESEARCHERS,
    FACTCHECK_BATCH_SIZE,
    FACTCHECK_CACHE_TTL_DAYS,
    FACTCHECK_MAX_REGENERATE,
    FORBIDDEN_TOKENS,
    GEMINI_FLASH,
    GEMINI_FLASH_LITE,
    GEMINI_PRO,
)
from core.orchestration import RetryPolicy, RunContext, with_retry
from core.schemas import (
    AdvisoryDriftReport,
    Candidate,
    Claim,
    ClaimVerdict,
    ConceptCorpus,
    EditPlan,
    EditPlanVerdict,
    FactCheckReport,
    IngestionVerdict,
)
from core.state import save_memory

logger = logging.getLogger(__name__)

# ── Force-flag patterns ───────────────────────────────────────────────────────
_SPECIFIC_NUMBER_RE = re.compile(
    r"\d+(?:\.\d+)?[xX×]\s*(?:as|more|stronger|higher|greater)"
    r"|\d+(?:\.\d+)?\s*%\s*(?:of\s+people|of\s+participants|reported|said)",
    re.IGNORECASE,
)
_SPECIFIC_YEAR_RE = re.compile(
    r"\b(19|20)\d{2}\b.{0,50}(?:study|research|experiment|paper|trial)",
    re.IGNORECASE,
)
_DOLLAR_RE = re.compile(
    r"[\$£€]\s*[\d,]+|\b\d[\d,]*\s*dollars?\b",
    re.IGNORECASE,
)
_NAME_IN_CORPUS = re.compile(
    r"\b([A-Z][a-z]+)\s+(?:and\s+[A-Z][a-z]+\s+)?\b"
    r"(?:study|research|found|showed|argued|demonstrated)",
    re.IGNORECASE,
)


def _claim_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:24]


def _is_force_flagged(text: str, corpus: ConceptCorpus) -> bool:
    if _SPECIFIC_NUMBER_RE.search(text):
        return True
    if _SPECIFIC_YEAR_RE.search(text):
        return True
    if _DOLLAR_RE.search(text):
        return True
    for m in _NAME_IN_CORPUS.finditer(text):
        name = m.group(1)
        if name not in CANONICAL_RESEARCHERS:
            corpus_names = {
                entry.canonical_source.author.split()[-1]
                for entry in corpus.concepts.values()
                if entry.canonical_source
            }
            if name not in corpus_names:
                return True
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_get(cache: dict[str, Any], claim_hash: str) -> ClaimVerdict | None:
    entry = cache.get(claim_hash)
    if not entry:
        return None
    try:
        cached_at = datetime.fromisoformat(entry["_cached_at"].rstrip("Z")).replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - cached_at).days > FACTCHECK_CACHE_TTL_DAYS:
            return None
    except Exception:
        return None
    d = entry["data"]
    return ClaimVerdict(
        claim_text    = d["claim_text"],
        claim_type    = d["claim_type"],
        force_flagged = d.get("force_flagged", False),
        verdict       = d["verdict"],
        confidence    = d["confidence"],
        sources       = d.get("sources", []),
        notes         = d.get("notes"),
        cache_hit     = True,
    )


def _cache_put(cache: dict[str, Any], claim_hash: str, verdict: ClaimVerdict) -> None:
    from dataclasses import asdict
    cache[claim_hash] = {
        "data":       asdict(verdict),
        "_cached_at": _utc_now(),
    }


def _build_grounded_model(ctx: RunContext):
    return ctx.model_flash.with_grounding()


_NETWORK_ERRORS = ("timeout", "deadline", "connection", "network", "unavailable", "503")

def _verify_claims_batch(
    claims: list[dict],
    concept_context: str,
    model: Any,
    ctx: RunContext,
) -> list[dict] | None:
    """
    Send a batch of claims to Gemini Flash with grounding.

    Returns:
        list[dict]  — verdicts on success
        None        — network/service failure (caller should treat as soft_fail, not pass)
        []          — Gemini responded but returned nothing verifiable
    """
    prompt = (
        f"You are verifying claims for a behavioural-finance educational artifact. "
        f"Use grounded web search. Return JSON array only — one entry per input claim.\n\n"
        f"Concept context: {concept_context}\n\n"
        f"Verify these claims:\n{json.dumps(claims, ensure_ascii=False)}\n\n"
        f"Return JSON: [\n"
        f'  {{"id": <int>, "verdict": "verified|contested|unverified|rejected", '
        f'"confidence": 0.0-1.0, "sources": [...], "notes": "..."}}\n'
        f"]"
    )
    policy = RetryPolicy(max_attempts=2, base_delay_s=4.0)
    try:
        result = with_retry(
            lambda: model.generate_content(prompt),
            policy=policy,
            label="factcheck_verify",
        )
        raw = result.text or ""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception as exc:
        msg = str(exc).lower()
        if any(e in msg for e in _NETWORK_ERRORS):
            logger.warning("Grounded verification network failure — fact-check held: %s", exc)
            return None   # signal: treat as soft_fail, not silent pass
        logger.warning("Grounded verification parse/API error: %s", exc)
        return []


def _check_advisory_drift(narrative: str, ctx: RunContext) -> AdvisoryDriftReport:
    prompt = (
        "You are checking a short-form video script for 'advisory drift' — "
        "any phrase that sounds like financial advice rather than education.\n"
        "Look for patterns like 'you should', 'the best way', 'guaranteed', 'hack', 'trick'.\n\n"
        f"Script:\n{narrative[:2000]}\n\n"
        "Return JSON only:\n"
        '{"drift_detected": <bool>, "flagged_phrases": [<str>,...], "notes": "<str|null>"}'
    )
    try:
        result = with_retry(
            lambda: ctx.model_flash.generate_content(prompt),
            label="advisory_drift",
        )
        raw = (result.text or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        return AdvisoryDriftReport(
            drift_detected  = data.get("drift_detected", False),
            flagged_phrases = data.get("flagged_phrases", []),
            notes           = data.get("notes"),
        )
    except Exception as exc:
        logger.warning("Advisory drift check failed: %s", exc)
        return AdvisoryDriftReport(drift_detected=False, notes=f"check_error: {exc}")


# ── Stage 1 — Ingestion ───────────────────────────────────────────────────────

def verify_external_concept(
    candidate: Candidate,
    corpus: ConceptCorpus,
    ctx: RunContext,
) -> IngestionVerdict:
    """Check a scored external candidate before promoting to corpus."""
    cache    = ctx.memory.factcheck_cache
    ts       = _utc_now()

    # Forbidden-token check
    full_text = f"{candidate.title} {candidate.description}".lower()
    for token in FORBIDDEN_TOKENS:
        if token.lower() in full_text:
            report = FactCheckReport(
                candidate_id         = candidate.id,
                stage                = "ingestion",
                verdict              = "fail",
                claims               = [],
                advisory_drift_check = AdvisoryDriftReport(
                    drift_detected  = True,
                    flagged_phrases = [token],
                    notes           = "forbidden token in candidate text",
                ),
                timestamp = ts,
            )
            return IngestionVerdict(decision="quarantined", report=report)

    # Step 1: identify the concept
    id_prompt = (
        "Identify the behavioural finance concept described below. "
        "Return JSON only: {\"canonical_name\": \"...\", \"definition\": \"<1 sentence>\"}\n\n"
        f"Title: {candidate.title}\nBody: {candidate.description[:600]}"
    )
    try:
        id_result = with_retry(
            lambda: ctx.model_flash.generate_content(id_prompt),
            label="fc_identify",
        )
        id_raw = (id_result.text or "").strip()
        if id_raw.startswith("```"):
            id_raw = id_raw.split("```")[1].lstrip("json").strip()
        id_data = json.loads(id_raw)
    except Exception as exc:
        logger.warning("Concept identification failed: %s", exc)
        id_data = {"canonical_name": candidate.title[:60], "definition": ""}

    canonical_name = id_data.get("canonical_name", candidate.title[:60])

    # Step 2: check for duplicates in corpus
    corpus_names_json = json.dumps(
        [{"id": k, "name": v.canonical_name, "aliases": v.alternate_names}
         for k, v in corpus.concepts.items()],
        ensure_ascii=False,
    )[:3000]

    dup_prompt = (
        f"Does '{canonical_name}' already exist in this concept corpus (under any name or alias)? "
        f"Return JSON: {{\"duplicate\": <bool>, \"canonical_concept_id\": \"<str|null>\"}}\n\n"
        f"Corpus:\n{corpus_names_json}"
    )
    try:
        dup_result = with_retry(
            lambda: ctx.model_flash.generate_content(dup_prompt),
            label="fc_dup_check",
        )
        dup_raw = (dup_result.text or "").strip()
        if dup_raw.startswith("```"):
            dup_raw = dup_raw.split("```")[1].lstrip("json").strip()
        dup_data = json.loads(dup_raw)
    except Exception as exc:
        logger.warning("Duplicate check failed: %s", exc)
        dup_data = {"duplicate": False, "canonical_concept_id": None}

    if dup_data.get("duplicate"):
        report = FactCheckReport(
            candidate_id         = candidate.id,
            stage                = "ingestion",
            verdict              = "pass",
            claims               = [],
            advisory_drift_check = AdvisoryDriftReport(drift_detected=False),
            timestamp            = ts,
        )
        return IngestionVerdict(
            decision             = "quarantined",
            report               = report,
            canonical_concept_id = dup_data.get("canonical_concept_id"),
        )

    # Step 3: grounded verification
    grounded_model = _build_grounded_model(ctx)
    verify_prompt = (
        f"Is '{canonical_name}' an established concept in behavioural economics or "
        f"money psychology literature? Provide a canonical citation if yes.\n"
        f"Return JSON: {{\"established\": <bool>, \"citation\": \"<researcher + year + work | null>\", "
        f"\"confidence\": <0.0-1.0>}}"
    )
    try:
        ver_result = with_retry(
            lambda: grounded_model.generate_content(verify_prompt),
            label="fc_ingest_verify",
        )
        ver_raw = (ver_result.text or "").strip()
        if ver_raw.startswith("```"):
            ver_raw = ver_raw.split("```")[1].lstrip("json").strip()
        ver_data = json.loads(ver_raw)
    except Exception as exc:
        logger.warning("Grounded ingestion verify failed: %s", exc)
        ver_data = {"established": False, "citation": None, "confidence": 0.0}

    established = ver_data.get("established", False)
    citation    = ver_data.get("citation")
    confidence  = float(ver_data.get("confidence", 0.5))

    drift = _check_advisory_drift(f"{candidate.title} {candidate.description}", ctx)
    if drift.drift_detected:
        established = False

    if established and confidence >= 0.7:
        decision = "approved"
        verdict  = "pass"
        proposed = {
            "canonical_name": canonical_name,
            "definition":     id_data.get("definition", ""),
            "canonical_source": {"author": citation or "", "work": "", "year": None},
            "provenance":     "external",
            "provisional":    True,
        }
    elif established and confidence >= 0.4:
        decision = "soft_fail_review"
        verdict  = "soft_fail"
        proposed = None
    else:
        decision = "quarantined"
        verdict  = "fail"
        proposed = None

    report = FactCheckReport(
        candidate_id         = candidate.id,
        stage                = "ingestion",
        verdict              = verdict,
        claims               = [],
        advisory_drift_check = drift,
        timestamp            = ts,
    )
    # Persist cache changes
    ctx.memory.factcheck_cache = cache
    save_memory(ctx.memory)

    return IngestionVerdict(decision=decision, report=report, proposed_corpus_entry=proposed)


# ── Stage 2 — EditPlan ────────────────────────────────────────────────────────

def verify_edit_plan(
    plan: EditPlan,
    corpus: ConceptCorpus,
    ctx: RunContext,
) -> EditPlanVerdict:
    """Verify all claims in an EditPlan before rendering."""
    cache = ctx.memory.factcheck_cache
    ts    = _utc_now()

    concept_entry = corpus.concepts.get(plan.concept_id)
    concept_context = (
        f"{plan.concept_id} — {concept_entry.definition}"
        if concept_entry else plan.concept_id
    )

    # ── Classify + force-flag claims ────────────────────────────────────────
    claim_objects: list[dict] = []
    for i, claim in enumerate(plan.claims):
        ff = _is_force_flagged(claim.text, corpus)
        claim_objects.append({
            "id":           i,
            "claim":        claim.text,
            "claim_type":   claim.claim_type,
            "force_flagged": ff,
        })

    # ── Cache lookup ─────────────────────────────────────────────────────────
    verdicts: dict[int, ClaimVerdict] = {}
    uncached: list[dict] = []
    for co in claim_objects:
        h = _claim_hash(co["claim"])
        cached = _cache_get(cache, h)
        if cached:
            verdicts[co["id"]] = cached
        else:
            uncached.append(co)

    # ── Batch grounded verification for uncached claims ──────────────────────
    network_failed = False
    if uncached:
        grounded_model = _build_grounded_model(ctx)
        for batch_start in range(0, len(uncached), FACTCHECK_BATCH_SIZE):
            batch   = uncached[batch_start: batch_start + FACTCHECK_BATCH_SIZE]
            results = _verify_claims_batch(batch, concept_context, grounded_model, ctx)

            if results is None:
                # Network failure — don't silently pass; flag for soft_fail
                network_failed = True
                for co in batch:
                    verdicts[co["id"]] = ClaimVerdict(
                        claim_text    = co["claim"],
                        claim_type    = co["claim_type"],
                        force_flagged = co["force_flagged"],
                        verdict       = "unverified",
                        confidence    = 0.0,
                        notes         = "network_failure: grounded check could not run",
                        cache_hit     = False,
                    )
                continue

            result_map = {r["id"]: r for r in results if isinstance(r, dict)}
            for co in batch:
                r  = result_map.get(co["id"], {})
                h  = _claim_hash(co["claim"])
                cv = ClaimVerdict(
                    claim_text    = co["claim"],
                    claim_type    = co["claim_type"],
                    force_flagged = co["force_flagged"],
                    verdict       = r.get("verdict", "unverified"),
                    confidence    = float(r.get("confidence", 0.5)),
                    sources       = r.get("sources", []),
                    notes         = r.get("notes"),
                    cache_hit     = False,
                )
                verdicts[co["id"]] = cv
                _cache_put(cache, h, cv)

    # ── Advisory drift ────────────────────────────────────────────────────────
    narrative = plan.narrative
    if plan.beat_structure:
        bs = plan.beat_structure
        narrative = f"{bs.hook} {bs.body} {bs.reveal} {bs.close}"
    drift = _check_advisory_drift(narrative, ctx)

    # ── Aggregate result ──────────────────────────────────────────────────────
    verdict_list = list(verdicts.values())
    hints: list[str] = []
    final_verdict = "pass"

    for cv in verdict_list:
        if cv.verdict == "rejected":
            final_verdict = "fail"
            hints.append(f"Rejected claim: '{cv.claim_text}' — {cv.notes or 'no detail'}")
        elif cv.verdict == "contested" and cv.force_flagged:
            final_verdict = "fail"
            hints.append(f"Force-flagged contested claim: '{cv.claim_text}'")
        elif cv.verdict == "contested" and final_verdict == "pass":
            final_verdict = "soft_fail"

    if drift.drift_detected:
        final_verdict = "fail"
        hints += [f"Advisory drift: {p}" for p in drift.flagged_phrases]

    # Network failure during grounded check → soft_fail, not silent pass
    if network_failed and final_verdict == "pass":
        final_verdict = "soft_fail"
        hints.append("Grounded fact-check unavailable (network failure) — proceed with caution")

    if final_verdict == "fail":
        decision = "regenerate"
    elif final_verdict == "soft_fail":
        decision = "pass"   # proceed but log the warning
    else:
        decision = "pass"

    report = FactCheckReport(
        plan_id              = plan.concept_id,
        stage                = "editplan",
        verdict              = final_verdict,
        claims               = verdict_list,
        advisory_drift_check = drift,
        timestamp            = ts,
    )

    ctx.memory.factcheck_cache = cache
    save_memory(ctx.memory)

    return EditPlanVerdict(decision=decision, report=report, regenerate_hints=hints)
