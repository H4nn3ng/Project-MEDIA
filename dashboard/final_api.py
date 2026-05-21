"""
final_api.py — non-interactive final review logic for the dashboard.

Mirrors the copy/verdict logic in final_review.py but returns dicts
instead of printing to a terminal.
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

IMAGE_EXTS    = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS    = {".mp4", ".mov", ".webm"}
AUDIO_EXTS    = {".mp3", ".wav", ".flac", ".m4a", ".aac"}
REEL_VARIANTS = ["draft_music.mp4", "draft_sound.mp4", "draft_voice.mp4"]

# Sources that are ambient/sound-effect tracks (not music)
_SOUND_SOURCES = {"freesound", "archive", "internet_archive"}


# ------------------------------------------------------------------ format detect

def _detect_format(render_path: Path) -> str:
    name = render_path.name
    if "_carousel" in name:
        return "carousel"
    if "_story_minimal" in name:
        return "story_minimal"
    if "_story" in name:
        return "story"
    if "_reel" in name:
        return "reel"
    if any(render_path.glob("carousel_*/")):
        return "carousel"
    if (render_path / "story_quotes").exists():
        return "story"
    if (render_path / "story_minimal").exists():
        return "story_minimal"
    if any(render_path.glob("final/draft_*.mp4")):
        return "reel"
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

        if fmt == "carousel":
            items = [d.name for d in sorted(folder.glob("carousel_*/"))]
            reviewed = sum(1 for item in items if f"{folder.name}/{item}" in review)
        elif fmt in ("story", "story_minimal"):
            subdir_name = "story_minimal" if fmt == "story_minimal" else "story_quotes"
            subdir = folder / subdir_name
            if subdir.exists():
                items = [
                    f.name for f in sorted(subdir.iterdir())
                    if f.suffix.lower() in IMAGE_EXTS and not f.name.startswith("manifest")
                ]
            else:
                items = []
            reviewed = sum(1 for item in items if f"{folder.name}/{item}" in review)
        elif fmt == "reel":
            items    = [folder.name]
            reviewed = 1 if folder.name in review else 0
        else:
            items    = []
            reviewed = 0

        result.append({
            "name":     folder.name,
            "format":   fmt,
            "total":    len(items),
            "reviewed": reviewed,
        })
    return result


# ------------------------------------------------------------------ render detail

def get_render_detail(name: str) -> dict:
    render_path = PENDING_DIR / name
    if not render_path.exists():
        return {}
    fmt    = _detect_format(render_path)
    review = load_review()

    if fmt == "carousel":
        carousels = []
        for cd in sorted(render_path.glob("carousel_*/")):
            slides = [
                {"filename": s.name, "media_url": _rel(s)}
                for s in sorted(cd.glob("slide_*.jpg"))
            ]
            theme = ""
            mfile = cd / "manifest.json"
            if mfile.exists():
                try:
                    mdata = json.loads(mfile.read_text())
                    theme = mdata[0].get("text", "")[:80] if mdata else ""
                except Exception:
                    print(f"[final_api] WARNING: manifest.json unreadable in {cd.name} — theme will be blank")
            key     = f"{name}/{cd.name}"
            caption = _read_caption(cd / "caption.txt")
            carousels.append({
                "dir":     cd.name,
                "slides":  slides,
                "theme":   theme,
                "caption": caption,
                "key":     key,
                "verdict": review.get(key, {}).get("verdict", ""),
                "note":    review.get(key, {}).get("note", ""),
            })
        return {"name": name, "format": fmt, "carousels": carousels}


    elif fmt in ("story", "story_minimal"):
        subdir_name = "story_minimal" if fmt == "story_minimal" else "story_quotes"
        subdir      = render_path / subdir_name
        images      = []
        manifest_data: dict[str, str] = {}
        if subdir.exists():
            mfile = subdir / "manifest.json"
            if mfile.exists():
                try:
                    mlist = json.loads(mfile.read_text())
                    for entry in mlist:
                        fname = entry.get("file", "")
                        quote = entry.get("quote", "")
                        if fname and quote:
                            manifest_data[fname] = quote
                except Exception:
                    print(f"[final_api] WARNING: manifest.json unreadable in {name} — quotes will be blank")
            for f in sorted(subdir.iterdir()):
                if f.suffix.lower() not in IMAGE_EXTS or f.name.startswith("manifest"):
                    continue
                key          = f"{name}/{f.name}"
                quote        = manifest_data.get(f.name, "")
                caption_file = subdir / f.name.replace(".jpg", "_caption.txt")
                caption      = _read_caption(caption_file)
                images.append({
                    "filename":  f.name,
                    "media_url": _rel(f),
                    "quote":     quote,
                    "caption":   caption,
                    "key":       key,
                    "verdict":   review.get(key, {}).get("verdict", ""),
                    "note":      review.get(key, {}).get("note", ""),
                })
        return {"name": name, "format": fmt, "images": images}

    elif fmt == "reel":
        available = []
        final_dir = render_path / "final"
        for vname in REEL_VARIANTS:
            vpath = final_dir / vname
            if vpath.exists():
                available.append({
                    "filename":  vpath.name,
                    "media_url": _rel(vpath),
                    "size_mb":   round(vpath.stat().st_size / 1024 / 1024, 1),
                })
        if final_dir.exists():
            for f in sorted(final_dir.glob("*.mp4")):
                if not any(v["filename"] == f.name for v in available):
                    available.append({
                        "filename":  f.name,
                        "media_url": _rel(f),
                        "size_mb":   round(f.stat().st_size / 1024 / 1024, 1),
                    })
        verdict_entry = review.get(name, {})
        caption       = _read_caption(render_path / "final" / "caption.txt")
        return {
            "name":     name,
            "format":   fmt,
            "variants": available,
            "caption":  caption,
            "verdict":  verdict_entry.get("verdict", ""),
            "note":     verdict_entry.get("note", ""),
            "chosen":   verdict_entry.get("chosen_variant", ""),
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

    fmt       = _detect_format(render_path)
    review    = load_review()
    key       = item_key or name
    copied_to = ""
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        if fmt == "carousel":
            carousel_name = key.split("/")[-1]
            carousel_dir  = render_path / carousel_name
            if verdict == "postable":
                dest = _copy_carousel(carousel_dir, name)
            else:
                dest = _reject_carousel(carousel_dir, name)
            copied_to = _rel(dest)
            review[key] = {
                "verdict":       verdict,
                "note":          note,
                "format":        "carousel",
                "render":        name,
                "carousel":      carousel_name,
                "reviewed_date": today,
                "copied_to":     copied_to,
            }

        elif fmt in ("story", "story_minimal"):
            subdir_name = "story_minimal" if fmt == "story_minimal" else "story_quotes"
            img_name    = key.split("/")[-1]
            img_path    = render_path / subdir_name / img_name
            if verdict == "postable":
                dest = _copy_story_image(img_path, name, fmt)
            else:
                dest = _reject_story_image(img_path, name, fmt)
            copied_to = _rel(dest)
            review[key] = {
                "verdict":       verdict,
                "note":          note,
                "format":        fmt,
                "render":        name,
                "item":          img_name,
                "reviewed_date": today,
                "copied_to":     copied_to,
            }

        elif fmt == "reel":
            if chosen_variant:
                variant_path = render_path / "final" / chosen_variant
                if verdict == "postable":
                    dest = _copy_reel_variant(variant_path, name)
                else:
                    dest = _reject_reel_variant(variant_path, name)
                copied_to = _rel(dest)
            review[key] = {
                "verdict":        verdict,
                "note":           note,
                "format":         "reel",
                "render":         name,
                "chosen_variant": chosen_variant,
                "reviewed_date":  today,
                "copied_to":      copied_to,
            }

        save_review(review)

        if note:
            _append_post_learning({
                "date":    today,
                "format":  fmt,
                "render":  name,
                "item":    key.split("/")[-1],
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
        keys_to_drop = [k for k in review if k == name or k.startswith(name + "/")]
        for k in keys_to_drop:
            review.pop(k)
        if keys_to_drop:
            save_review(review)
        return {"ok": True, "deleted": name, "backup": str(dest.relative_to(BASE_DIR))}
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

    return {
        "total_reviewed": len(review),
        "postable":       postable,
        "not_postable":   not_post,
        "ready_to_post":  ready_counts,
    }


# ------------------------------------------------------------------ copy helpers

def _copy_carousel(carousel_dir: Path, render_name: str) -> Path:
    dest_name = f"{render_name}_{carousel_dir.name}"
    dest = READY_DIR / "carousel" / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(str(carousel_dir), str(dest))
    return dest


def _reject_carousel(carousel_dir: Path, render_name: str) -> Path:
    dest_name = f"{render_name}_{carousel_dir.name}"
    dest = NOT_POSTABLE_DIR / "carousel" / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(str(carousel_dir), str(dest))
    return dest


def _copy_story_image(img_path: Path, render_name: str, fmt: str) -> Path:
    folder   = "story_minimal" if fmt == "story_minimal" else "story"
    dest_dir = READY_DIR / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{render_name}_{img_path.name}"
    shutil.copy2(str(img_path), str(dest))
    return dest


def _reject_story_image(img_path: Path, render_name: str, fmt: str) -> Path:
    folder   = "story_minimal" if fmt == "story_minimal" else "story"
    dest_dir = NOT_POSTABLE_DIR / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{render_name}_{img_path.name}"
    shutil.copy2(str(img_path), str(dest))
    return dest


def _copy_reel_variant(variant_path: Path, render_name: str) -> Path:
    dest_dir = READY_DIR / "reel"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{render_name}_{variant_path.name}"
    shutil.copy2(str(variant_path), str(dest))
    return dest


def _reject_reel_variant(variant_path: Path, render_name: str) -> Path:
    dest_dir = NOT_POSTABLE_DIR / "reel"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{render_name}_{variant_path.name}"
    shutil.copy2(str(variant_path), str(dest))
    return dest


def _rel(path: Path) -> str:
    return str(path.relative_to(BASE_DIR))


def _read_caption(path: Path) -> str:
    if path.exists():
        try:
            return path.read_text().strip()
        except Exception:
            pass
    return ""




def get_intelligence() -> dict:
    bible: dict = {}
    if WORLD_BIBLE_PATH.exists():
        try:
            bible = json.loads(WORLD_BIBLE_PATH.read_text())
        except Exception:
            pass
    themes = bible.get("universal_themes", [])
    index  = int(bible.get("universal_theme_index", 0))
    return {
        "high_keywords":  bible.get("high_performing_keywords", []),
        "low_keywords":   bible.get("low_performing_keywords",  []),
        "high_tags":      bible.get("high_performing_tags",     []),
        "low_tags":       bible.get("low_performing_tags",      []),
        "themes":         {"list": themes, "current_index": index},
        "hooks_history":  bible.get("emotional_hooks_history",  []),
        "rejected_count": len(bible.get("rejected_ids",         [])),
    }


def _append_post_learning(entry: dict) -> None:
    try:
        if WORLD_BIBLE_PATH.exists():
            bible = json.loads(WORLD_BIBLE_PATH.read_text())
        else:
            bible = {}
        bible.setdefault("post_learnings", []).append(entry)
        bible["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        WORLD_BIBLE_PATH.write_text(json.dumps(bible, indent=2))
    except Exception:
        pass


# ------------------------------------------------------------------ pool summary

def get_pool_summary() -> dict:
    footage_dir = POOL_DIR / "footage"
    audio_dir   = POOL_DIR / "audio"
    images_dir  = POOL_DIR / "images"

    clips = _pool_items(footage_dir, VIDEO_EXTS, skip_dirs={"used"}, kind="video")
    audio = _pool_items(audio_dir,   AUDIO_EXTS, kind="audio")

    sound = [a for a in audio if a.get("_source", "") in _SOUND_SOURCES]
    music = [a for a in audio if a.get("_source", "") not in _SOUND_SOURCES]

    pri_images = _pool_items(images_dir / "priority", IMAGE_EXTS, priority=True,  kind="image")
    std_images = _pool_items(images_dir / "standard", IMAGE_EXTS, priority=False, kind="image")
    all_images = pri_images + std_images

    # Strip internal helper key — not needed in the browser
    for item in clips + sound + music + all_images:
        item.pop("_source", None)

    return {
        "reels": {
            "clips": len(clips),
            "sound": len(sound),
            "music": len(music),
            "items": clips + sound + music,
        },
        "carousel": {
            "total":    len(all_images),
            "priority": len(pri_images),
            "standard": len(std_images),
            "items":    all_images,
        },
        "story": {
            "total":    len(all_images),
            "priority": len(pri_images),
            "standard": len(std_images),
            "items":    all_images,
        },
    }


def _pool_items(
    directory: Path,
    extensions: set[str],
    skip_dirs: set[str] | None = None,
    kind: str = "image",
    priority: bool | None = None,
) -> list[dict]:
    if not directory.exists():
        return []
    items = []
    for f in sorted(directory.iterdir()):
        if f.is_dir():
            continue
        if f.suffix.lower() not in extensions:
            continue

        meta   = _read_meta(f)
        score  = meta.get("score")
        title  = " ".join(str(meta.get("title", f.stem)).split())  # collapse any whitespace/newlines
        source = str(meta.get("source", ""))

        item: dict = {
            "filename": f.name,
            "kind":     kind,
            "score":    score,
            "title":    title,
            "_source":  source,
        }

        if priority is not None:
            item["priority"] = priority
        elif score is not None:
            item["priority"] = score >= 8.5

        if kind == "image":
            item["media_url"] = _rel(f)
        elif kind == "video":
            item["media_url"] = _rel(f)
            thumb = _ensure_video_thumb(f)
            if thumb:
                item["thumb_url"] = _rel(thumb)
        elif kind == "audio":
            item["media_url"] = _rel(f)

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
