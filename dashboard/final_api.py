"""
final_api.py — render verdict logic for the dashboard.

Renders land in data/pending/YYYY-MM-DD_001/ after render.py.
  Reel output:     pending/YYYY-MM-DD_001/final/final.mp4
  Carousel output: pending/YYYY-MM-DD_001/final/slide_01.png …
  Caption sidecar: pending/YYYY-MM-DD_001/caption.txt

Verdict:
  postable     → copied to data/approved/<format>/
  not_postable → copied to data/not_approved/<format>/
"""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .paths import (
    BASE_DIR, POOL_DIR, PENDING_DIR, READY_DIR, NOT_POSTABLE_DIR,
    BIN_DIR, POST_REVIEW_PATH, WORLD_BIBLE_PATH,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac"}


# ------------------------------------------------------------------ format detect

def _detect_format(render_path: Path) -> str:
    final_dir = render_path / "final"
    if not final_dir.exists():
        return "unknown"
    if any(final_dir.glob("*.mp4")):
        return "reel"
    if any(final_dir.glob("*.png")) or any(final_dir.glob("*.jpg")):
        return "carousel"
    return "unknown"


# ------------------------------------------------------------------ review log

def load_review() -> dict:
    if POST_REVIEW_PATH.exists():
        try:
            data = json.loads(POST_REVIEW_PATH.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_review(review: dict) -> None:
    POST_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    POST_REVIEW_PATH.write_text(json.dumps(review, indent=2, sort_keys=True))


# ------------------------------------------------------------------ list renders

def list_renders() -> list[dict]:
    if not PENDING_DIR.exists():
        return []
    review = load_review()
    result = []
    for folder in sorted(PENDING_DIR.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        fmt = _detect_format(folder)
        if fmt == "unknown":
            continue

        key      = folder.name
        reviewed = 1 if key in review else 0
        total    = 1

        if fmt == "carousel":
            slides = list((folder / "final").glob("slide_*.png"))
            total  = len(slides) if slides else 1

        concept_id = _read_concept_id(folder)
        caption    = _read_caption(folder / "caption.txt")

        result.append({
            "name":       folder.name,
            "format":     fmt,
            "total":      total,
            "reviewed":   reviewed,
            "concept_id": concept_id,
            "caption":    caption[:80] if caption else "",
            "verdict":    review.get(key, {}).get("verdict", ""),
        })
    return result


# ------------------------------------------------------------------ render detail

def get_render_detail(name: str) -> dict:
    render_path = PENDING_DIR / name
    if not render_path.exists():
        return {}
    fmt    = _detect_format(render_path)
    review = load_review()
    final_dir = render_path / "final"
    caption   = _read_caption(render_path / "caption.txt")
    concept_id = _read_concept_id(render_path)

    if fmt == "reel":
        variants = []
        for f in sorted(final_dir.glob("*.mp4")):
            variants.append({
                "filename":  f.name,
                "media_url": _rel(f),
                "size_mb":   round(f.stat().st_size / 1024 / 1024, 1),
            })
            thumb = _ensure_video_thumb(f)
            if thumb:
                variants[-1]["thumb_url"] = _rel(thumb)
        entry = review.get(name, {})
        return {
            "name":       name,
            "format":     fmt,
            "variants":   variants,
            "caption":    caption,
            "concept_id": concept_id,
            "verdict":    entry.get("verdict", ""),
            "note":       entry.get("note", ""),
            "chosen":     entry.get("chosen_variant", ""),
        }

    if fmt == "carousel":
        slides = [
            {"filename": f.name, "media_url": _rel(f)}
            for f in sorted(final_dir.glob("slide_*.png"))
        ]
        entry = review.get(name, {})
        return {
            "name":       name,
            "format":     fmt,
            "slides":     slides,
            "caption":    caption,
            "concept_id": concept_id,
            "verdict":    entry.get("verdict", ""),
            "note":       entry.get("note", ""),
        }

    return {"name": name, "format": fmt}


# ------------------------------------------------------------------ submit verdict

def submit_verdict(name: str, item_key: str | None, verdict: str,
                   note: str = "", chosen_variant: str = "") -> dict:
    if verdict not in ("postable", "not_postable"):
        return {"error": "invalid verdict"}

    render_path = PENDING_DIR / name
    if not render_path.exists():
        return {"error": "render not found"}

    fmt    = _detect_format(render_path)
    review = load_review()
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    copied_to = ""

    try:
        if fmt == "reel":
            if chosen_variant:
                variant_path = render_path / "final" / chosen_variant
                dest = _copy_or_reject_reel(variant_path, name, verdict)
                copied_to = _rel(dest) if dest else ""
            review[name] = {
                "verdict":        verdict,
                "note":           note,
                "format":         "reel",
                "chosen_variant": chosen_variant,
                "reviewed_date":  today,
                "copied_to":      copied_to,
            }

        elif fmt == "carousel":
            dest = _copy_or_reject_carousel(render_path / "final", name, verdict)
            copied_to = _rel(dest) if dest else ""
            review[name] = {
                "verdict":       verdict,
                "note":          note,
                "format":        "carousel",
                "reviewed_date": today,
                "copied_to":     copied_to,
            }

        save_review(review)

        if note:
            _append_post_learning({
                "date":    today,
                "format":  fmt,
                "render":  name,
                "verdict": verdict,
                "note":    note,
            })

        return {"ok": True, "copied_to": copied_to}

    except Exception as exc:
        return {"error": str(exc)}


# ------------------------------------------------------------------ reverse verdict

def reverse_verdict(key: str) -> dict:
    review = load_review()
    if key not in review:
        return {"error": "not found"}
    entry = review.pop(key)
    copied = entry.get("copied_to", "")
    if copied:
        target = BASE_DIR / copied
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink(missing_ok=True)
    save_review(review)
    return {"ok": True}


# ------------------------------------------------------------------ delete render

def delete_render(name: str) -> dict:
    render_path = PENDING_DIR / name
    if not render_path.exists():
        return {"error": "render not found"}
    try:
        fmt      = _detect_format(render_path)
        dest_dir = BIN_DIR / fmt
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(render_path), str(dest))
        review = load_review()
        for k in [k for k in review if k == name or k.startswith(name + "/")]:
            review.pop(k)
        save_review(review)
        return {"ok": True, "deleted": name}
    except Exception as exc:
        return {"error": str(exc)}


# ------------------------------------------------------------------ stats

def get_stats() -> dict:
    review   = load_review()
    postable = sum(1 for v in review.values() if v.get("verdict") == "postable")
    not_post = sum(1 for v in review.values() if v.get("verdict") == "not_postable")

    ready_counts: dict[str, int] = {}
    if READY_DIR.exists():
        for sub in READY_DIR.iterdir():
            if sub.is_dir():
                ready_counts[sub.name] = len(list(sub.iterdir()))

    pending_count = len([f for f in PENDING_DIR.iterdir() if f.is_dir()]) if PENDING_DIR.exists() else 0
    return {
        "pending":        pending_count,
        "total_reviewed": len(review),
        "postable":       postable,
        "not_postable":   not_post,
        "ready_to_post":  ready_counts,
    }


# ------------------------------------------------------------------ intelligence

def get_intelligence() -> dict:
    bible: dict = {}
    if WORLD_BIBLE_PATH.exists():
        try:
            bible = json.loads(WORLD_BIBLE_PATH.read_text())
        except Exception:
            pass
    return {
        "high_keywords": bible.get("high_performing_keywords", []),
        "low_keywords":  bible.get("low_performing_keywords", []),
        "high_tags":     bible.get("high_performing_tags", []),
        "low_tags":      bible.get("low_performing_tags", []),
        "rejected_count": len(bible.get("rejected_ids", [])),
        "post_learnings": bible.get("post_learnings", [])[-10:],
    }


# ------------------------------------------------------------------ pool summary

def get_pool_summary() -> dict:
    footage_dir = POOL_DIR / "footage"
    audio_dir   = POOL_DIR / "audio"
    images_dir  = POOL_DIR / "images"

    clips  = _pool_items(footage_dir, VIDEO_EXTS, skip_dirs={"used"}, kind="video")
    audio  = _pool_items(audio_dir, AUDIO_EXTS, kind="audio")
    images = _pool_items(images_dir, IMAGE_EXTS, kind="image")

    music = [a for a in audio if a.get("media_kind", "music") != "bed"]
    beds  = [a for a in audio if a.get("media_kind", "") == "bed"]

    return {
        "footage": {"clips": len(clips), "items": clips},
        "music":   {"tracks": len(music), "items": music},
        "beds":    {"tracks": len(beds),  "items": beds},
        "images":  {"total": len(images), "items": images},
    }


# ------------------------------------------------------------------ copy helpers

def _copy_or_reject_reel(variant_path: Path, render_name: str, verdict: str) -> Path | None:
    if not variant_path.exists():
        return None
    base_dir = READY_DIR if verdict == "postable" else NOT_POSTABLE_DIR
    dest_dir = base_dir / "reel"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{render_name}_{variant_path.name}"
    shutil.copy2(str(variant_path), str(dest))
    return dest


def _copy_or_reject_carousel(final_dir: Path, render_name: str, verdict: str) -> Path | None:
    if not final_dir.exists():
        return None
    base_dir = READY_DIR if verdict == "postable" else NOT_POSTABLE_DIR
    dest_dir = base_dir / "carousel"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / render_name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(str(final_dir), str(dest))
    return dest


# ------------------------------------------------------------------ helpers

def _pool_items(
    directory: Path,
    extensions: set[str],
    skip_dirs: set[str] | None = None,
    kind: str = "image",
) -> list[dict]:
    if not directory.exists():
        return []
    items = []
    for f in sorted(directory.iterdir()):
        if f.is_dir():
            if skip_dirs and f.name in skip_dirs:
                continue
            # Recurse one level (e.g. priority/standard subdirs)
            items.extend(_pool_items(f, extensions, kind=kind))
            continue
        if f.suffix.lower() not in extensions:
            continue
        meta  = _read_meta(f)
        title = " ".join(str(meta.get("title", f.stem)).split())
        item: dict = {
            "filename":  f.name,
            "kind":      kind,
            "score":     meta.get("score"),
            "title":     title,
            "media_url": _rel(f),
            "media_kind": meta.get("media_kind", ""),
        }
        if kind == "video":
            thumb = _ensure_video_thumb(f)
            if thumb:
                item["thumb_url"] = _rel(thumb)
        items.append(item)
    return items


def _read_meta(media_file: Path) -> dict:
    meta_path = media_file.with_name(media_file.stem + "_metadata.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            pass
    return {}


def _read_caption(path: Path) -> str:
    if path.exists():
        try:
            return path.read_text().strip()
        except Exception:
            pass
    return ""


def _read_concept_id(render_path: Path) -> str:
    edit_log = render_path / "edit_log.json"
    if edit_log.exists():
        try:
            data = json.loads(edit_log.read_text())
            return data.get("concept_id", "") or data.get("concept", {}).get("concept_id", "")
        except Exception:
            pass
    return ""


def _ensure_video_thumb(video_path: Path) -> Path | None:
    thumbs_dir = video_path.parent / ".thumbs"
    thumbs_dir.mkdir(exist_ok=True)
    thumb = thumbs_dir / (video_path.stem + ".jpg")
    if thumb.exists():
        return thumb
    try:
        subprocess.run(
            ["ffmpeg", "-ss", "1", "-i", str(video_path),
             "-frames:v", "1", "-q:v", "4", "-y", str(thumb)],
            capture_output=True, timeout=15,
        )
        return thumb if thumb.exists() else None
    except Exception:
        return None


def _append_post_learning(entry: dict) -> None:
    try:
        if WORLD_BIBLE_PATH.exists():
            bible = json.loads(WORLD_BIBLE_PATH.read_text())
        else:
            bible = {}
        bible.setdefault("post_learnings", []).append(entry)
        WORLD_BIBLE_PATH.write_text(json.dumps(bible, indent=2))
    except Exception:
        pass


def _rel(path: Path) -> str:
    return str(path.relative_to(BASE_DIR))
