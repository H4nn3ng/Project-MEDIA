"""
review_api.py — staging footage review for the dashboard.

Footage lands in data/scrape/footage/priority|standard/ after scrape.py.
Approved clips → data/sort_media/footage/
Rejected clips → data/rejected/footage/
Audio and images are auto-approved (already in sort_media/).
"""

import json
import shutil
import sys
from pathlib import Path

from .paths import BASE_DIR, STAGING_DIR, POOL_DIR, BAD_DIR, WORLD_BIBLE_PATH

_AUDIO_PROGRESS_PATH = POOL_DIR / "audio" / "review_progress.json"

sys.path.insert(0, str(BASE_DIR))

VIDEO_EXT = {".mp4", ".webm", ".m4v", ".mov"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".mp4"}  # mp4 used by jamendo

_PROGRESS_PATH = STAGING_DIR / "review_progress.json"
_IMAGE_PROGRESS_PATH = POOL_DIR / "images" / "review_progress.json"


# ------------------------------------------------------------------ footage staging

def load_all_media_items() -> list[dict]:
    """Footage items in staging, unreviewed first."""
    if not STAGING_DIR.exists():
        return []
    progress = _load_progress()
    items: list[dict] = []
    for tier in ("priority", "standard"):
        src = STAGING_DIR / "footage" / tier
        if not src.exists():
            continue
        for meta_file in sorted(src.glob("*_metadata.json")):
            media = _find_file(meta_file, VIDEO_EXT)
            if not media:
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
            items.append({
                "filename":   media.name,
                "media_url":  _rel(media),
                "type":       "footage",
                "source":     meta.get("source", ""),
                "title":      meta.get("title", media.stem),
                "score":      meta.get("score", 0),
                "keyword":    meta.get("keyword", ""),
                "url":        meta.get("url", ""),
                "tier":       tier,
                "concept_id": meta.get("candidate_concept_id") or "",
                "duration":   (meta.get("full_response") or {}).get("duration"),
                "rating":     progress.get(media.name, ""),
            })
    items.sort(key=lambda x: x["rating"] != "")
    return items


def rate_media_item(_batch: str, filename: str, rating: str) -> dict:
    progress = _load_progress()
    if rating == "":
        progress.pop(filename, None)
    else:
        progress[filename] = rating
    _save_progress(progress)
    return {"ok": True}


def commit_all_media() -> dict:
    if not STAGING_DIR.exists():
        return {"moved_good": 0, "moved_bad": 0}
    progress = _load_progress()
    if not any(v in ("g", "b") for v in progress.values()):
        return {"moved_good": 0, "moved_bad": 0}

    pool_footage = POOL_DIR / "footage"
    bad_footage  = BAD_DIR / "footage"
    moved_good = moved_bad = 0
    liked: list[dict] = []
    disliked: list[dict] = []

    for tier in ("priority", "standard"):
        src = STAGING_DIR / "footage" / tier
        if not src.exists():
            continue
        for meta_file in sorted(src.glob("*_metadata.json")):
            media = _find_file(meta_file, VIDEO_EXT)
            if not media:
                continue
            rating = progress.get(media.name)
            if rating not in ("g", "b"):
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
            dest = pool_footage if rating == "g" else bad_footage
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(meta_file), str(dest / meta_file.name))
            shutil.move(str(media),     str(dest / media.name))
            if rating == "g":
                liked.append(meta)
                moved_good += 1
            else:
                disliked.append(meta)
                moved_bad += 1

    committed = {k for k, v in progress.items() if v in ("g", "b")}
    for k in committed:
        progress.pop(k, None)
    _save_progress(progress)

    _update_world_bible(liked, disliked)
    return {"moved_good": moved_good, "moved_bad": moved_bad}


# ------------------------------------------------------------------ batch compat stubs

def list_batches() -> list[dict]:
    items = load_all_media_items()
    if not items:
        return []
    reviewed = sum(1 for i in items if i["rating"])
    return [{"name": "staging", "total": len(items), "reviewed": reviewed}]


def load_reel_items(_batch: str) -> list[dict]:
    return load_all_media_items()


def rate_reel_item(_batch: str, filename: str, rating: str) -> dict:
    return rate_media_item("", filename, rating)


def commit_reel(_batch: str) -> dict:
    return commit_all_media()


# ------------------------------------------------------------------ pool audio (rateable — reject removes from pool)

def load_audio_items() -> list[dict]:
    audio_dir = POOL_DIR / "audio"
    if not audio_dir.exists():
        return []
    progress = _load_audio_progress()
    items = []
    for f in sorted(audio_dir.iterdir()):
        if f.is_dir() or f.suffix.lower() not in AUDIO_EXT:
            continue
        meta = _read_meta(f)
        items.append({
            "filename":   f.name,
            "media_url":  _rel(f),
            "source":     meta.get("source", ""),
            "title":      meta.get("title", f.stem),
            "score":      meta.get("score", 0),
            "keyword":    meta.get("keyword", ""),
            "media_kind": meta.get("media_kind", "music"),
            "rating":     progress.get(f.name, ""),
        })
    return items


def rate_audio_item(filename: str, rating: str) -> dict:
    progress = _load_audio_progress()
    if rating == "":
        progress.pop(filename, None)
    else:
        progress[filename] = rating
    _save_audio_progress(progress)
    return {"ok": True}


def commit_audio() -> dict:
    """Move tracks rated 'b' to rejected/audio/. Good tracks stay in pool."""
    progress  = _load_audio_progress()
    audio_dir = POOL_DIR / "audio"
    bad_dir   = BAD_DIR / "audio"
    moved_bad = 0
    for f in sorted(audio_dir.iterdir()):
        if f.is_dir() or f.suffix.lower() not in AUDIO_EXT:
            continue
        if progress.get(f.name) != "b":
            continue
        bad_dir.mkdir(parents=True, exist_ok=True)
        meta_f = f.with_name(f.stem + "_metadata.json")
        shutil.move(str(f), str(bad_dir / f.name))
        if meta_f.exists():
            shutil.move(str(meta_f), str(bad_dir / meta_f.name))
        moved_bad += 1
    _save_audio_progress({})
    return {"moved_bad": moved_bad}


# ------------------------------------------------------------------ pool images (rateable for rejection)

def load_image_items() -> list[dict]:
    images_dir = POOL_DIR / "images"
    if not images_dir.exists():
        return []
    progress = _load_image_progress()
    items = []
    for f in sorted(images_dir.iterdir()):
        if f.is_dir() or f.suffix.lower() not in IMAGE_EXT:
            continue
        meta = _read_meta(f)
        items.append({
            "filename":  f.name,
            "media_url": _rel(f),
            "source":    meta.get("source", ""),
            "title":     meta.get("title", f.stem),
            "score":     meta.get("score", 0),
            "tier":      "standard",
            "rating":    progress.get(f.name, ""),
        })
    return items


def rate_image_item(filename: str, rating: str) -> dict:
    progress = _load_image_progress()
    if rating == "":
        progress.pop(filename, None)
    else:
        progress[filename] = rating
    _save_image_progress(progress)
    return {"ok": True}


def commit_images() -> dict:
    progress = _load_image_progress()
    bad_dir   = BAD_DIR / "images"
    moved_bad = 0
    images_dir = POOL_DIR / "images"
    for f in sorted(images_dir.iterdir()):
        if f.is_dir() or f.suffix.lower() not in IMAGE_EXT:
            continue
        if progress.get(f.name) != "b":
            continue
        bad_dir.mkdir(parents=True, exist_ok=True)
        meta_f = f.with_name(f.stem + "_metadata.json")
        shutil.move(str(f), str(bad_dir / f.name))
        if meta_f.exists():
            shutil.move(str(meta_f), str(bad_dir / meta_f.name))
        moved_bad += 1
    return {"moved_bad": moved_bad}


# ------------------------------------------------------------------ rejected media restore

def load_rejected_items(kind: str) -> list[dict]:
    """List items currently in data/rejected/{kind}/."""
    if kind not in ("images", "audio", "footage"):
        return []
    bad_dir = BAD_DIR / kind
    if not bad_dir.exists():
        return []
    ext_set = IMAGE_EXT if kind == "images" else (AUDIO_EXT if kind == "audio" else VIDEO_EXT)
    items = []
    for f in sorted(bad_dir.iterdir()):
        if f.is_dir() or f.suffix.lower() not in ext_set:
            continue
        meta = _read_meta(f)
        items.append({
            "filename":  f.name,
            "media_url": _rel(f),
            "source":    meta.get("source", ""),
            "title":     meta.get("title", f.stem),
            "score":     meta.get("score", 0),
        })
    return items


def restore_item(kind: str, filename: str) -> dict:
    """Move one item from data/rejected/{kind}/ back to its pool directory."""
    if kind not in ("images", "audio", "footage"):
        return {"error": "Unknown kind"}
    bad_dir  = BAD_DIR / kind
    src      = bad_dir / Path(filename).name  # .name strips any path traversal
    if not src.exists():
        return {"error": "Not found"}
    pool_map = {
        "images":  POOL_DIR / "images",
        "audio":   POOL_DIR / "audio",
        "footage": POOL_DIR / "footage",
    }
    dest_dir = pool_map[kind]
    dest_dir.mkdir(parents=True, exist_ok=True)
    meta_src = src.with_name(src.stem + "_metadata.json")
    shutil.move(str(src), str(dest_dir / src.name))
    if meta_src.exists():
        shutil.move(str(meta_src), str(dest_dir / meta_src.name))
    return {"ok": True}


# ------------------------------------------------------------------ internals

def _find_file(meta_file: Path, exts: set[str]) -> Path | None:
    base = meta_file.stem.replace("_metadata", "")
    return next(
        (f for f in meta_file.parent.iterdir()
         if f.stem == base and f.suffix.lower() in exts),
        None,
    )


def _load_progress() -> dict:
    if _PROGRESS_PATH.exists():
        try:
            return json.loads(_PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_progress(progress: dict):
    _PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


def _load_image_progress() -> dict:
    if _IMAGE_PROGRESS_PATH.exists():
        try:
            return json.loads(_IMAGE_PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_image_progress(progress: dict):
    _IMAGE_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IMAGE_PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


def _load_audio_progress() -> dict:
    if _AUDIO_PROGRESS_PATH.exists():
        try:
            return json.loads(_AUDIO_PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_audio_progress(progress: dict):
    _AUDIO_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _AUDIO_PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


def _read_meta(media_file: Path) -> dict:
    meta_path = media_file.with_name(media_file.stem + "_metadata.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            pass
    return {}


def _update_world_bible(liked: list[dict], disliked: list[dict]):
    if not liked and not disliked:
        return
    if not WORLD_BIBLE_PATH.exists():
        bible: dict = {}
    else:
        try:
            bible = json.loads(WORLD_BIBLE_PATH.read_text())
        except Exception:
            bible = {}

    high_kw  = set(bible.get("high_performing_keywords", []))
    low_kw   = set(bible.get("low_performing_keywords", []))
    high_tags = set(bible.get("high_performing_tags", []))
    low_tags  = set(bible.get("low_performing_tags", []))
    rejected  = set(bible.get("rejected_ids", []))

    for item in liked:
        for kw in (item.get("matched_keywords") or ([item["keyword"]] if item.get("keyword") else [])):
            high_kw.add(kw); low_kw.discard(kw)
        for tag in item.get("platform_tags", []):
            high_tags.add(tag); low_tags.discard(tag)

    for item in disliked:
        for kw in (item.get("matched_keywords") or ([item["keyword"]] if item.get("keyword") else [])):
            low_kw.add(kw); high_kw.discard(kw)
        for tag in item.get("platform_tags", []):
            low_tags.add(tag); high_tags.discard(tag)
        src = item.get("source", ""); iid = str(item.get("id", ""))
        if src and iid:
            rejected.add(f"{src}_{iid}")

    bible["high_performing_keywords"] = sorted(high_kw)
    bible["low_performing_keywords"]  = sorted(low_kw)
    bible["high_performing_tags"]     = sorted(high_tags)
    bible["low_performing_tags"]      = sorted(low_tags)
    bible["rejected_ids"]             = sorted(rejected)
    WORLD_BIBLE_PATH.write_text(json.dumps(bible, indent=2))


def _rel(path: Path) -> str:
    return str(path.relative_to(BASE_DIR))
