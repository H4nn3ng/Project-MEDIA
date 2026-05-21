#!/usr/bin/env python3
"""
scrape.py — Pipeline phase 1: scrape, score, and download assets.

Footage goes to batch/YYYY-MM-DD/footage/priority or standard (pending your review).
Audio and images are auto-promoted straight to pool/ (no review needed).

Output:
  batch/YYYY-MM-DD/footage/priority/   ← high-scored clips (score >= 7.5)
  batch/YYYY-MM-DD/footage/standard/   ← lower-scored clips (score >= 6.0)
  batch/YYYY-MM-DD/intelligence/       ← run metadata
  pool/audio/                          ← music + bed tracks (auto-approved)
  pool/images/                         ← stock images (auto-approved)

Next: python3 feedback.py  →  python3 render.py
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import (
    ARTIFACTS_MON,
    ARTIFACTS_THU,
    POOL_AUDIO,
    POOL_IMAGES,
    SCORED_POOL_THRESHOLD,
    STAGING_DIR,
)
from core.corpus import load_corpus, save_corpus
from core.orchestration import bootstrap
from core.schemas import (
    CanonicalSource,
    Candidate,
    ConceptCorpus,
    ConceptEntry,
)
from core.state import save_memory
from modules.fact_check_agent import verify_external_concept
from modules.scraper_agent import run_scraper
from modules.scoring_agent import score_candidates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)



# Magic bytes for the file types we accept — checked after download
_MAGIC: list[tuple[bytes, int, str]] = [
    (b"ftyp",            4,  "mp4"),   # mp4 / m4v — "ftyp" at offset 4
    (b"\x1a\x45\xdf\xa3", 0, "webm"), # webm / mkv
    (b"RIFF",            0,  "wav"),   # wav audio (also covers WebP container)
    (b"ID3",             0,  "mp3"),
    (b"\xff\xfb",        0,  "mp3"),   # mp3 without ID3 tag
    (b"\xff\xf3",        0,  "mp3"),
    (b"OggS",            0,  "ogg"),
    (b"\xff\xd8\xff",    0,  "jpg"),   # JPEG
    (b"\x89PNG",         0,  "png"),   # PNG
    (b"WEBP",            8,  "webp"),  # WebP (RIFF....WEBP)
]
_MAX_FILE_MB = 500


def _is_valid_media(path: Path) -> bool:
    """Return True if the file's magic bytes match a known video, audio, or image format."""
    try:
        header = path.read_bytes()[:16]
    except Exception:
        return False
    for magic, offset, _ in _MAGIC:
        if header[offset: offset + len(magic)] == magic:
            return True
    return False


_ALLOWED_HOSTS = {
    "pexels.com", "videos.pexels.com", "images.pexels.com",
    "pixabay.com", "cdn.pixabay.com",
    "coverr.co", "cdn.coverr.co", "api.coverr.co",
    "freemusicarchive.org",
    "freesound.org", "cdn.freesound.org",
    "jamendo.com", "storage.googleapis.com",
    "mp3l.jamendo.com", "mp3d.jamendo.com",
    "wikimedia.org", "upload.wikimedia.org",
    "loc.gov", "tile.loc.gov",
    "si.edu", "ids.si.edu",
    "archive.org",
}


import re as _re
_SAFE_FILENAME = _re.compile(r"[^\w\-.]")


def _safe_stem(value: str) -> str:
    """Strip any character that isn't alphanumeric, hyphen, or dot — prevents path traversal."""
    return _SAFE_FILENAME.sub("_", value)[:80]


def _download_file(url: str, dest: Path) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)

    # HTTPS only
    if parsed.scheme != "https":
        logger.warning("Blocked non-HTTPS URL: %s", url)
        return False

    # Domain allowlist
    host = parsed.hostname or ""
    if not any(host == d or host.endswith("." + d) for d in _ALLOWED_HOSTS):
        logger.warning("Blocked download from untrusted host: %s", host)
        return False

    # Sanitize the destination filename — prevent path traversal via API-supplied IDs
    dest = dest.parent / _safe_stem(dest.name)
    tmp  = dest.with_suffix(".tmp")

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        session = requests.Session()
        session.max_redirects = 3
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": f"https://{parsed.hostname}/",
        })
        with session.get(url, stream=True, timeout=(5, 30), allow_redirects=True) as r:
            r.raise_for_status()
            # Reject HTML/text responses (mis-routed requests, auth pages, etc.)
            ct = r.headers.get("Content-Type", "").lower()
            if "text" in ct or "html" in ct:
                logger.warning("Server returned non-media content-type %r: %s", ct, url)
                return False
            content_len = int(r.headers.get("Content-Length", 0))
            if content_len > _MAX_FILE_MB * 1024 * 1024:
                logger.warning("Skipping oversized file (%d MB): %s", content_len // 1024 // 1024, url)
                return False
            # Write to .tmp first — if process dies mid-download, no partial file is left
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        logger.warning("Download failed for %s: %s", url, exc)
        return False

    # Validate magic bytes — reject anything that isn't actually a media file
    if not _is_valid_media(tmp):
        logger.warning("Rejecting non-media file (bad magic bytes): %s", tmp.name)
        tmp.unlink(missing_ok=True)
        return False

    tmp.replace(dest)
    return True


def _footage_subdir(score: float | None) -> Path:
    """Route footage to priority or standard subfolder based on score."""
    if (score or 0) >= SCORED_POOL_THRESHOLD:
        return STAGING_DIR / "footage" / "priority"
    return STAGING_DIR / "footage" / "standard"


def _download_candidates(
    scored_by_kind: dict[str, list[Candidate]],
) -> dict[str, int]:
    """
    Download all candidates to the right folders:
      footage → data/scrape/footage/priority|standard/
      music + bed → data/sort_media/audio/
      images → data/sort_media/images/

    Returns count dict per kind.
    """
    counts: dict[str, int] = {}

    for kind, candidates in scored_by_kind.items():
        eligible = [c for c in candidates if c.gate_pass is not False and c.download_url]
        # Take top 8 per kind
        eligible = sorted(eligible, key=lambda c: c.score or 0, reverse=True)[:8]
        logger.info("Eligible for download: %d %s candidates (top 8 of %d gate_pass)",
                    len(eligible), kind, len([c for c in candidates if c.gate_pass is not False]))
        downloaded = 0

        for cand in eligible:
            # Strip query params AND URL fragments — fragments are never sent to
            # the server but confuse Path().suffix; also normalise the download URL
            url_clean = cand.download_url.split("?")[0].split("#")[0]
            ext  = Path(url_clean).suffix or ".mp4"
            download_url = url_clean  # use fragment-free URL for the actual request
            dest_dir = (
                _footage_subdir(cand.score)  if kind == "footage"
                else POOL_AUDIO              if kind in ("music", "bed")
                else POOL_IMAGES             if kind == "image"
                else None
            )
            if dest_dir is None:
                continue

            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{cand.id}{ext}"
            meta = dest_dir / f"{cand.id}_metadata.json"

            if dest.exists():
                logger.info("Already downloaded: %s", cand.id)
            elif not _download_file(download_url, dest):
                continue

            meta.write_text(json.dumps(asdict(cand), ensure_ascii=False, indent=2))
            downloaded += 1

        counts[kind] = downloaded

    return counts


def main() -> None:
    ctx = bootstrap()
    logger.info("Run ID: %s", ctx.run_id)

    weekday     = datetime.now(timezone.utc).weekday()
    n_artifacts = ARTIFACTS_MON if weekday == 0 else ARTIFACTS_THU
    today       = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    intel_dir   = STAGING_DIR / "intelligence" / today
    intel_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    logger.info("Step 1: Scraper")
    corpus = load_corpus()
    raw_by_kind = run_scraper(ctx, corpus=corpus)
    save_memory(ctx.memory)

    # ── Step 2: Score concept candidates ─────────────────────────────────────
    logger.info("Step 2: Scoring concepts")
    scored_concepts = score_candidates(raw_by_kind.get("concept", []), ctx)

    # ── Step 3: Fact-check top external concepts ──────────────────────────────
    logger.info("Step 3: Ingestion fact-check")
    if corpus is None:
        logger.error("Cannot continue without corpus — exiting")
        sys.exit(1)

    for cand in sorted(scored_concepts, key=lambda c: c.score or 0, reverse=True)[:5]:
        if (cand.score or 0) < SCORED_POOL_THRESHOLD:
            continue
        verdict = verify_external_concept(cand, corpus, ctx)
        if verdict.decision == "approved" and verdict.proposed_corpus_entry:
            entry_data = verdict.proposed_corpus_entry
            new_id = entry_data.get("canonical_name", "").lower().replace(" ", "_")[:32]
            if new_id and new_id not in corpus.concepts:
                cs_data = entry_data.get("canonical_source", {})
                corpus.concepts[new_id] = ConceptEntry(
                    concept_id       = new_id,
                    canonical_name   = entry_data["canonical_name"],
                    definition       = entry_data.get("definition", ""),
                    canonical_source = CanonicalSource(**cs_data) if cs_data else None,
                    provenance       = "external",
                    provisional      = True,
                    created_at       = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                save_corpus(corpus)
                logger.info("New concept promoted: %s", new_id)

    # ── Step 4: Score visual/audio ────────────────────────────────────────────
    logger.info("Step 4: Scoring visual/audio")
    scored_by_kind: dict[str, list[Candidate]] = {}
    for kind in ("footage", "image", "music", "bed"):
        scored_by_kind[kind] = score_candidates(raw_by_kind.get(kind, []), ctx)

    # ── Download ──────────────────────────────────────────────────────────────
    counts = _download_candidates(scored_by_kind)

    # Save run metadata
    run_state = {
        "date": today,
        "n_artifacts": n_artifacts,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
    }
    (intel_dir / "run_state.json").write_text(json.dumps(run_state, indent=2))

    footage_count = counts.get("footage", 0)
    audio_count   = counts.get("music", 0) + counts.get("bed", 0)
    logger.info("Scrape complete — %d footage clips, %d audio tracks", footage_count, audio_count)

    print("\n" + "─" * 52)
    print(f"  Footage    : {footage_count} clips → data/scrape/footage/")
    print(f"  Audio      : {audio_count} tracks → data/sort_media/audio/  (auto-approved)")
    print(f"  Images     : {counts.get('image', 0)} → data/sort_media/images/  (auto-approved)")
    print()
    print("  Next step  : python3 feedback.py")
    print("─" * 52)


if __name__ == "__main__":
    main()
