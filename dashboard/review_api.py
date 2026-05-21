"""
review_api.py — staging media review logic for the dashboard.

Files land in staging/ after scraping.
Approved items are committed to pool/; rejected go to bad/.
"""

import json
import shutil
import sys
from pathlib import Path

from .paths import BASE_DIR, STAGING_DIR, POOL_DIR, BAD_DIR, WORLD_BIBLE_PATH

sys.path.insert(0, str(BASE_DIR))
import pool_registry

MEDIA_EXT  = {".mp4", ".mp3", ".webm", ".wav", ".ogg", ".m4v", ".m4a"}
IMAGE_EXT  = {".jpg", ".jpeg", ".png", ".webp"}

STAGING_PROGRESS_PATH = STAGING_DIR / "review_progress.json"
IMAGE_PROGRESS_PATH   = POOL_DIR / "images" / "review_progress.json"


# ------------------------------------------------------------------ flat media queue

def load_all_media_items() -> list[dict]:
    """Flat list of every footage + audio item in staging, unreviewed first."""
    if not STAGING_DIR.exists():
        return []
    progress = _load_staging_progress()
    items: list[dict] = []
    for folder in ["footage/priority", "footage/standard", "audio/priority", "audio/standard"]:
        src = STAGING_DIR / folder
        if not src.exists():
            continue
        media_type = "audio" if "audio" in folder else "footage"
        for meta_file in sorted(src.glob("*_metadata.json")):
            media = _find_media(meta_file)
            if not media:
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
            items.append({
                "filename":  media.name,
                "media_url": _rel(media),
                "type":      media_type,
                "source":    meta.get("source", ""),
                "title":     meta.get("title", media.stem),
                "score":     meta.get("score", 0),
                "keyword":   meta.get("keyword", ""),
                "url":       meta.get("url", ""),
                "tier":      folder.split("/")[1],
                "rating":    progress.get(media.name, ""),
            })
    items.sort(key=lambda x: x["rating"] != "")
    return items


def rate_media_item(batch: str, filename: str, rating: str) -> dict:
    # batch param kept for API compatibility but staging is always flat
    progress = _load_staging_progress()
    if rating == "":
        progress.pop(filename, None)
    else:
        progress[filename] = rating
    _save_staging_progress(progress)
    return {"ok": True}


def commit_all_media() -> dict:
    """Move all rated footage + audio from staging to pool/ or bad/."""
    if not STAGING_DIR.exists():
        return {"moved_good": 0, "moved_bad": 0}
    progress = _load_staging_progress()
    if not any(v in ("g", "b") for v in progress.values()):
        return {"moved_good": 0, "moved_bad": 0}

    moved_good = moved_bad = 0
    bad_metas: list[dict] = []
    liked:     list[dict] = []
    disliked:  list[dict] = []

    for folder in ["footage/priority", "footage/standard", "audio/priority", "audio/standard"]:
        src = STAGING_DIR / folder
        if not src.exists():
            continue
        parent   = folder.split("/")[0]
        pool_dir = POOL_DIR / parent
        bad_dir  = BAD_DIR  / parent

        for meta_file in sorted(src.glob("*_metadata.json")):
            media_file = _find_media(meta_file)
            if not media_file:
                continue
            rating = progress.get(media_file.name)
            if rating not in ("g", "b"):
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
            dest = pool_dir if rating == "g" else bad_dir
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(meta_file),  str(dest / meta_file.name))
            shutil.move(str(media_file), str(dest / media_file.name))
            if rating == "g":
                liked.append(meta)
                moved_good += 1
            else:
                disliked.append(meta)
                bad_metas.append(meta)
                moved_bad += 1

    committed = {k for k, v in progress.items() if v in ("g", "b")}
    for k in committed:
        progress.pop(k, None)
    _save_staging_progress(progress)

    if bad_metas:
        pool_registry.mark_many(bad_metas, "rejected")
    _update_world_bible(liked, disliked)
    return {"moved_good": moved_good, "moved_bad": moved_bad}


# ------------------------------------------------------------------ batches (backward compat stubs)

def list_batches() -> list[dict]:
    """Returns staging as a single virtual 'batch' for the stats endpoint."""
    items = load_all_media_items()
    if not items:
        return []
    reviewed = sum(1 for i in items if i["rating"])
    return [{"name": "staging", "total": len(items), "reviewed": reviewed}]


def load_reel_items(batch_date: str) -> list[dict]:
    return load_all_media_items()


def rate_reel_item(batch_date: str, filename: str, rating: str) -> dict:
    return rate_media_item("", filename, rating)


def commit_reel(batch_date: str) -> dict:
    return commit_all_media()


# ------------------------------------------------------------------ image items

def load_image_items() -> list[dict]:
    progress = _load_image_progress()
    items    = []
    for subdir in ["priority", "standard"]:
        src = POOL_DIR / "images" / subdir
        if not src.exists():
            continue
        for meta_file in sorted(src.glob("*_metadata.json")):
            base = meta_file.stem.replace("_metadata", "")
            img  = next(
                (f for f in src.iterdir()
                 if f.stem == base and f.suffix.lower() in IMAGE_EXT),
                None
            )
            if not img:
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
            items.append({
                "filename":  img.name,
                "media_url": _rel(img),
                "source":    meta.get("source", ""),
                "title":     meta.get("title", img.stem),
                "score":     meta.get("score", 0),
                "keyword":   meta.get("keyword", ""),
                "tier":      subdir,
                "rating":    progress.get(img.name, ""),
            })
    return items


def rate_image_item(filename: str, rating: str) -> dict:
    progress = _load_image_progress()
    progress[filename] = rating
    _save_image_progress(progress)
    return {"ok": True}


def commit_images() -> dict:
    progress = _load_image_progress()
    bad_dir  = BAD_DIR / "images"
    moved_bad = 0
    bad_metas: list[dict] = []
    disliked:  list[dict] = []

    for subdir in ["priority", "standard"]:
        src = POOL_DIR / "images" / subdir
        if not src.exists():
            continue
        for meta_file in sorted(src.glob("*_metadata.json")):
            base = meta_file.stem.replace("_metadata", "")
            img  = next(
                (f for f in src.iterdir()
                 if f.stem == base and f.suffix.lower() in IMAGE_EXT),
                None
            )
            if not img:
                continue
            if progress.get(img.name) != "b":
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
            bad_metas.append(meta)
            disliked.append(meta)
            bad_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(meta_file), str(bad_dir / meta_file.name))
            shutil.move(str(img),       str(bad_dir / img.name))
            moved_bad += 1

    if bad_metas:
        pool_registry.mark_many(bad_metas, "rejected")
    _update_world_bible([], disliked)
    return {"moved_bad": moved_bad}


# ------------------------------------------------------------------ internals

def _find_media(meta_file: Path) -> Path | None:
    base = meta_file.stem.replace("_metadata", "")
    return next(
        (f for f in meta_file.parent.iterdir()
         if f.stem == base and f.suffix.lower() in MEDIA_EXT),
        None
    )


def _load_staging_progress() -> dict:
    if STAGING_PROGRESS_PATH.exists():
        try:
            return json.loads(STAGING_PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_staging_progress(progress: dict):
    STAGING_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STAGING_PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


def _load_image_progress() -> dict:
    if IMAGE_PROGRESS_PATH.exists():
        try:
            return json.loads(IMAGE_PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_image_progress(progress: dict):
    IMAGE_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMAGE_PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


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

    high_tags = set(bible.get("high_performing_tags", []))
    low_tags  = set(bible.get("low_performing_tags", []))
    rejected  = set(bible.get("rejected_ids", []))

    for item in liked:
        for tag in item.get("platform_tags", []):
            high_tags.add(tag)
            low_tags.discard(tag)

    for item in disliked:
        for tag in item.get("platform_tags", []):
            low_tags.add(tag)
            high_tags.discard(tag)
        src = item.get("source", "")
        iid = str(item.get("id", ""))
        if src and iid:
            rejected.add(f"{src}_{iid}")

    bible["high_performing_tags"] = sorted(high_tags)
    bible["low_performing_tags"]  = sorted(low_tags)
    bible["rejected_ids"]         = sorted(rejected)
    WORLD_BIBLE_PATH.write_text(json.dumps(bible, indent=2))


def _rel(path: Path) -> str:
    return str(path.relative_to(BASE_DIR))
