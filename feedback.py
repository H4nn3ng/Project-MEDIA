"""
feedback.py — review downloaded files and update world_bible.json

Run after watching the footage/audio from a batch. You rate each file
good / bad / skip, and the keyword that found it gets marked accordingly.
Next run, Gemini avoids bad keywords and leans into good ones.

Usage:
    python feedback.py                     # review most recent batch
    python feedback.py --batch 2026-04-26  # review a specific batch
    python feedback.py --select            # show a list of all batches to choose from
    python feedback.py --reset             # clear saved progress and start over
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR         = Path(__file__).parent
DATA_DIR         = BASE_DIR / "data"
BATCH_ROOT       = DATA_DIR / "scrape"
WORLD_BIBLE_PATH = DATA_DIR / "world_bible.json"
POOL_DIR         = DATA_DIR / "sort_media"
BAD_DIR          = DATA_DIR / "rejected"

MEDIA_EXTENSIONS = {".mp4", ".mp3", ".webm", ".wav", ".ogg", ".m4v", ".m4a"}
IMAGE_EXTENSIONS  = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

POOL_IMAGE_DIR  = POOL_DIR / "images"
IMAGE_PROGRESS_PATH = POOL_IMAGE_DIR / "review_progress.json"

_SCREEN_SIZE: tuple[int, int] | None = None


# ---------------------------------------------------------------------------
# Media player
# ---------------------------------------------------------------------------

def _find_media_file(meta_file: Path) -> Path | None:
    base = meta_file.stem.replace("_metadata", "")
    for f in meta_file.parent.iterdir():
        if f.stem == base and f.suffix.lower() in MEDIA_EXTENSIONS:
            return f
    return None


def _get_screen_size() -> tuple[int, int]:
    global _SCREEN_SIZE
    if _SCREEN_SIZE is not None:
        return _SCREEN_SIZE
    try:
        result = subprocess.run(
            ["xrandr", "--current"], capture_output=True, text=True, timeout=3
        )
        for line in result.stdout.splitlines():
            if "current" in line:
                part = line.split("current")[1].split(",")[0]
                w, h = part.replace(" ", "").split("x")
                _SCREEN_SIZE = (int(w), int(h))
                return _SCREEN_SIZE
    except Exception:
        pass
    _SCREEN_SIZE = (1920, 1080)
    return _SCREEN_SIZE


def _clean_env() -> dict:
    """Return environment copy with VS Code snap pollution removed for subprocesses."""
    env = os.environ.copy()
    ldpath = env.get("LD_LIBRARY_PATH", "")
    if ldpath:
        system_paths = [p for p in ldpath.split(":") if "/snap/" not in p]
        env["LD_LIBRARY_PATH"] = ":".join(system_paths) if system_paths else ""
        if not system_paths:
            env.pop("LD_LIBRARY_PATH")
    env.pop("GIO_EXTRA_MODULES", None)
    return env


def _get_active_window_id() -> str | None:
    """Return the currently focused window ID. Tries xdotool, then xprop fallback."""
    xdotool = shutil.which("xdotool")
    if xdotool:
        try:
            result = subprocess.run(
                [xdotool, "getactivewindow"], capture_output=True, text=True, timeout=2
            )
            wid = result.stdout.strip()
            if wid.isdigit():
                return wid
        except Exception:
            pass

    # xprop fallback — reads _NET_ACTIVE_WINDOW from the root window property
    xprop = shutil.which("xprop")
    if xprop:
        try:
            import re as _re
            result = subprocess.run(
                [xprop, "-root", "_NET_ACTIVE_WINDOW"],
                capture_output=True, text=True, timeout=2,
            )
            m = _re.search(r"0x[0-9a-fA-F]+", result.stdout)
            if m:
                return m.group(0)   # hex ID — wmctrl accepts this format
        except Exception:
            pass

    return None


def _find_vlc_wid(xdotool: str) -> str | None:
    """Return the most recent VLC window ID, or None if not found."""
    try:
        result = subprocess.run(
            [xdotool, "search", "--name", "VLC"],
            capture_output=True, text=True, timeout=2,
        )
        wids = [w for w in result.stdout.strip().splitlines() if w.isdigit()]
        return wids[-1] if wids else None
    except Exception:
        return None


def _apply_geometry(xdotool: str, wid: str, x: int, y: int, w: int, h: int) -> None:
    subprocess.run([xdotool, "windowmove", wid, str(x), str(y)], timeout=2)
    subprocess.run([xdotool, "windowsize", wid, str(w), str(h)], timeout=2)


def _focus_terminal(xdotool: str | None, terminal_id: str) -> None:
    """Route keyboard input back to the terminal. Works with or without xdotool."""
    wmctrl = shutil.which("wmctrl")
    if wmctrl:
        subprocess.run([wmctrl, "-ia", terminal_id], timeout=2)
    if xdotool:
        subprocess.run([xdotool, "windowfocus",    terminal_id], timeout=2)
        subprocess.run([xdotool, "windowactivate", "--sync", terminal_id], timeout=2)


def _position_vlc(x: int, y: int, w: int, h: int, terminal_id: str | None) -> None:
    """Background thread: snap VLC to right half, then return focus to terminal.

    Two passes: once when the window first appears, and again 1.5 s later after
    VLC finishes loading the video (which can trigger an internal resize).
    """
    xdotool = shutil.which("xdotool")
    wmctrl  = shutil.which("wmctrl")
    if not xdotool and not wmctrl:
        return

    # --- Pass 1: position as soon as the window appears ---
    wid = None
    for _ in range(20):
        time.sleep(0.3)
        if xdotool:
            wid = _find_vlc_wid(xdotool)
            if wid:
                try:
                    _apply_geometry(xdotool, wid, x, y, w, h)
                    if terminal_id:
                        _focus_terminal(xdotool, terminal_id)
                except Exception:
                    pass
                break
        elif wmctrl:
            try:
                result = subprocess.run([wmctrl, "-l"], capture_output=True, text=True, timeout=2)
                if any("VLC" in line for line in result.stdout.splitlines()):
                    subprocess.run([wmctrl, "-r", "VLC", "-e", f"0,{x},{y},{w},{h}"], timeout=2)
                    if terminal_id:
                        _focus_terminal(None, terminal_id)
                    return
            except Exception:
                pass

    if not xdotool or not wid:
        return

    # Pass 2 + 3: re-enforce after video renderer initialises (can take up to 3 s)
    for delay in (2.5, 1.5):
        time.sleep(delay)
        try:
            current_wid = _find_vlc_wid(xdotool)
            if current_wid:
                _apply_geometry(xdotool, current_wid, x, y, w, h)
                if terminal_id:
                    _focus_terminal(xdotool, terminal_id)
        except Exception:
            pass


def _open_image(path: Path) -> None:
    """Open an image in the right half of the screen via eog or xdg-open."""
    viewers = ["eog", "feh", "xviewer", "shotwell"]
    viewer_path = next((shutil.which(v) for v in viewers if shutil.which(v)), None)
    cmd = [viewer_path or "xdg-open", str(path)]
    try:
        w, h        = _get_screen_size()
        terminal_id = _get_active_window_id()
        subprocess.Popen(
            cmd,
            env=_clean_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Reuse VLC positioner — same window-snapping logic works for image viewers
        threading.Thread(
            target=_position_vlc,
            args=(w // 2, 0, w // 2, h, terminal_id),
            daemon=True,
        ).start()
    except Exception as exc:
        print(f"         (image viewer failed: {exc})")


def _close_image_viewer() -> None:
    for proc in ["eog", "feh", "xviewer", "shotwell"]:
        try:
            subprocess.run(["pkill", proc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def _open_media(path: Path) -> None:
    """Open media in the right half of the screen via VLC."""
    vlc_path = shutil.which("vlc")
    if vlc_path:
        try:
            w, h          = _get_screen_size()
            terminal_id   = _get_active_window_id()
            subprocess.Popen(
                [vlc_path, "--no-fullscreen", "--no-qt-video-autoresize",
                 "--width", str(w // 2), "--height", str(h), str(path)],
                env=_clean_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            threading.Thread(
                target=_position_vlc,
                args=(w // 2, 0, w // 2, h, terminal_id),
                daemon=True,
            ).start()
            return
        except Exception as exc:
            print(f"         (VLC failed: {exc} — falling back to xdg-open)")
    try:
        subprocess.Popen(["xdg-open", str(path)], env=_clean_env())
    except Exception as exc:
        print(f"         (could not open media player: {exc})")


def _close_media() -> None:
    try:
        subprocess.run(["pkill", "vlc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Progress — save after every rating so you can quit and resume
# ---------------------------------------------------------------------------

def _progress_path(batch_path: Path) -> Path:
    return batch_path / "review_progress.json"


def load_progress(batch_path: Path) -> dict[str, str]:
    """Load saved ratings {filename: 'g'|'b'|'s'}. Returns empty dict if none."""
    p = _progress_path(batch_path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def save_progress(batch_path: Path, progress: dict[str, str]) -> None:
    _progress_path(batch_path).write_text(json.dumps(progress, indent=2))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_batch(date_str: str | None) -> Path:
    # date_str is ignored — staging is flat (data/scrape/), no date subdirs
    subdirs = ["footage/priority", "footage/standard", "audio/priority", "audio/standard"]
    if not BATCH_ROOT.exists() or not any((BATCH_ROOT / f).exists() for f in subdirs):
        print("[feedback] No staged files found in data/scrape/ — run agent.py first.")
        sys.exit(1)
    return BATCH_ROOT


def select_batch() -> Path:
    # Staging is flat — no date-based batch selection any more
    return find_batch(None)


def load_items(batch_path: Path) -> list[dict]:
    items: list[dict] = []
    for folder in ["footage/priority", "footage/standard", "audio/priority", "audio/standard"]:
        dir_path = batch_path / folder
        if not dir_path.exists():
            continue
        for meta_file in sorted(dir_path.glob("*_metadata.json")):
            try:
                meta = json.loads(meta_file.read_text())
                meta["_folder"] = folder
                meta["_media_path"] = _find_media_file(meta_file)
            except Exception:
                continue
            items.append(meta)
    return items


def load_world_bible() -> dict:
    if WORLD_BIBLE_PATH.exists():
        data = json.loads(WORLD_BIBLE_PATH.read_text())
        # Back-fill tag lists for bibles created before this feature
        data.setdefault("high_performing_tags", [])
        data.setdefault("low_performing_tags", [])
        data.setdefault("rejected_ids", [])
        return data
    return {
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "high_performing_keywords": [],
        "low_performing_keywords": [],
        "high_performing_tags": [],
        "low_performing_tags": [],
        "emotional_hooks_history": [],
        "era_notes": {},
        "channel_learnings": [],
    }


def save_world_bible(bible: dict) -> None:
    bible["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    WORLD_BIBLE_PATH.write_text(json.dumps(bible, indent=2))


# ---------------------------------------------------------------------------
# Interactive review
# ---------------------------------------------------------------------------

def review_items(
    items: list[dict],
    batch_path: Path,
    progress: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Walk through unreviewed items. Saves progress after every rating."""
    liked:    list[dict] = []
    disliked: list[dict] = []

    # Restore liked/disliked from previous session
    for item in items:
        mp = item.get("_media_path")
        rating = progress.get(mp.name) if mp else None
        if rating == "g":
            liked.append(item)
        elif rating == "b":
            disliked.append(item)

    remaining = [
        item for item in items
        if not (item.get("_media_path") and item["_media_path"].name in progress)
    ]

    if progress:
        print(f"  Resuming — {len(progress)} already rated, {len(remaining)} left.\n")

    current_section = None

    for i, item in enumerate(remaining):
        _close_media()

        section = "FOOTAGE" if "footage" in item["_folder"] else "AUDIO"
        if section != current_section:
            current_section = section
            print(f"\n{'─' * 52}")
            print(f"  {section}")
            print(f"{'─' * 52}")

        done_so_far = len(progress) + i + 1
        title       = item.get("title") or "untitled"
        score       = item.get("score", "?")
        keyword     = item.get("keyword", "—")
        url         = item.get("url", "")
        source      = item.get("source", "?")
        media_path  = item.get("_media_path")

        print(f"\n  {done_so_far}/{len(items)}  [{source}] {title}")
        print(f"         Score  : {score}  |  Keyword: {keyword}")
        if url:
            print(f"         Link   : {url}")

        if media_path:
            _open_media(media_path)
            print(f"         Playing: {media_path.name}")
        else:
            print(f"         Playing: (file not found — use link above)")

        while True:
            raw = input("         Rate   (g=good / b=bad / Enter=skip): ").strip().lower()
            if raw in ("g", "b", ""):
                break
            print("         Type g, b, or press Enter to skip")

        if raw == "g":
            liked.append(item)
        elif raw == "b":
            disliked.append(item)

        if media_path:
            progress[media_path.name] = raw or "s"
            save_progress(batch_path, progress)

    _close_media()
    return liked, disliked


# ---------------------------------------------------------------------------
# World bible update
# ---------------------------------------------------------------------------

# Generic platform tags that carry no useful signal for our niche
_NOISE_TAGS = {
    "video", "audio", "music", "sound", "nature", "background", "stock",
    "free", "hd", "4k", "footage", "clip", "media", "film", "movie",
    "alpha channel", "greenscreen", "green screen", "loop", "seamless",
    "effect", "overlay effect", "transition", "template",
}


def apply_feedback(bible: dict, liked: list[dict], disliked: list[dict]) -> None:
    # --- Keywords ---
    # Use matched_keywords (all run keywords Gemini says describe this item) when
    # available. Fall back to the single keyword that found it if matched is empty.
    # This means a good rating can credit multiple relevant keywords at once.
    high_kw = set(bible.get("high_performing_keywords", []))
    low_kw  = set(bible.get("low_performing_keywords", []))

    def _keywords_for(item: dict) -> list[str]:
        matched = item.get("matched_keywords") or []
        if matched:
            return matched
        kw = item.get("keyword", "")
        return [kw] if kw else []

    kw_votes: dict[str, int] = {}
    for item in liked:
        for kw in _keywords_for(item):
            kw_votes[kw] = kw_votes.get(kw, 0) + 1
    for item in disliked:
        for kw in _keywords_for(item):
            kw_votes[kw] = kw_votes.get(kw, 0) - 1

    for kw, net in kw_votes.items():
        if net > 0:
            high_kw.add(kw)
            low_kw.discard(kw)
        elif net < 0:
            low_kw.add(kw)
            high_kw.discard(kw)

    bible["high_performing_keywords"] = sorted(high_kw)
    bible["low_performing_keywords"]  = sorted(low_kw)

    # --- Platform tags (Pixabay + Freesound only; Pexels has none) ---
    high_tags = set(bible.get("high_performing_tags", []))
    low_tags  = set(bible.get("low_performing_tags", []))

    tag_votes: dict[str, int] = {}
    for item in liked:
        for tag in item.get("platform_tags", []):
            tag = tag.lower().strip()
            if tag and tag not in _NOISE_TAGS:
                tag_votes[tag] = tag_votes.get(tag, 0) + 1
    for item in disliked:
        for tag in item.get("platform_tags", []):
            tag = tag.lower().strip()
            if tag and tag not in _NOISE_TAGS:
                tag_votes[tag] = tag_votes.get(tag, 0) - 1

    for tag, net in tag_votes.items():
        if net > 0:
            high_tags.add(tag)
            low_tags.discard(tag)
        elif net < 0:
            low_tags.add(tag)
            high_tags.discard(tag)

    bible["high_performing_tags"] = sorted(high_tags)
    bible["low_performing_tags"]  = sorted(low_tags)

    # --- Rejected source IDs — prevents re-downloading bad clips in future runs ---
    rejected = set(bible.get("rejected_ids", []))
    for item in disliked:
        source = item.get("source", "")
        item_id = str(item.get("id", ""))
        if source and item_id:
            rejected.add(f"{source}_{item_id}")
    bible["rejected_ids"] = sorted(rejected)


# ---------------------------------------------------------------------------
# File sorting
# ---------------------------------------------------------------------------

def _sort_rated_files(batch_path: Path, progress: dict[str, str]) -> None:
    """
    Good files → global pool (pool/footage/ or pool/audio/) — available across all batches.
    Bad files  → bad/footage/ or bad/audio/ — consolidated, no batch subdivision.
    Rejected IDs are written to pool_registry so agent.py never re-downloads them.
    """
    import pool_registry
    moved      = {"g": 0, "b": 0}
    bad_metas: list[dict] = []

    for folder in ["footage/priority", "footage/standard", "audio/priority", "audio/standard"]:
        src_dir = batch_path / folder
        if not src_dir.exists():
            continue
        parent   = folder.split("/")[0]   # "footage" or "audio"
        pool_dir = POOL_DIR / parent
        bad_dir  = BAD_DIR / parent        # bad/footage/ or bad/audio/
        for meta_file in sorted(src_dir.glob("*_metadata.json")):
            media_file = _find_media_file(meta_file)
            if not media_file:
                continue
            rating = progress.get(media_file.name)
            if rating not in ("g", "b"):
                continue
            # Read metadata before moving — needed for registry if rating == "b"
            if rating == "b":
                try:
                    bad_metas.append(json.loads(meta_file.read_text()))
                except Exception:
                    pass
            dest = pool_dir if rating == "g" else bad_dir
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(meta_file), dest / meta_file.name)
            shutil.move(str(media_file), dest / media_file.name)
            moved[rating] += 1

    if bad_metas:
        pool_registry.mark_many(bad_metas, "rejected")

    if moved["g"] or moved["b"]:
        print(f"  Sorted   : {moved['g']} → data/sort_media/  ·  {moved['b']} → data/rejected/")


def load_image_progress() -> dict[str, str]:
    if IMAGE_PROGRESS_PATH.exists():
        try:
            return json.loads(IMAGE_PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {}


def save_image_progress(progress: dict[str, str]) -> None:
    POOL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


def load_image_items() -> list[dict]:
    """Load all unrejected images from pool/images/(priority|standard)."""
    items: list[dict] = []
    for subdir in ["priority", "standard"]:
        dir_path = POOL_IMAGE_DIR / subdir
        if not dir_path.exists():
            continue
        for meta_file in sorted(dir_path.glob("*_metadata.json")):
            base = meta_file.stem.replace("_metadata", "")
            img_file = next(
                (f for f in dir_path.iterdir()
                 if f.stem == base and f.suffix.lower() in IMAGE_EXTENSIONS),
                None,
            )
            if not img_file:
                continue
            try:
                meta = json.loads(meta_file.read_text())
                meta["_folder"]     = f"images/{subdir}"
                meta["_image_path"] = img_file
                meta["_meta_path"]  = meta_file
            except Exception:
                continue
            items.append(meta)
    return items


def review_images(items: list[dict], progress: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """
    Walk through unreviewed pool images.
    Good (g) → stays in pool/images/ as-is.
    Bad  (b) → moved to pool/images/bad/.
    Saves progress after every rating so you can quit and resume.
    """
    liked:    list[dict] = []
    disliked: list[dict] = []

    for item in items:
        ip = item.get("_image_path")
        rating = progress.get(ip.name) if ip else None
        if rating == "g":
            liked.append(item)
        elif rating == "b":
            disliked.append(item)

    remaining = [
        item for item in items
        if not (item.get("_image_path") and item["_image_path"].name in progress)
    ]

    if progress:
        print(f"  Resuming — {len(progress)} already rated, {len(remaining)} left.\n")

    for i, item in enumerate(remaining):
        _close_image_viewer()

        done_so_far = len(progress) + i + 1
        title       = item.get("title") or "untitled"
        score       = item.get("score", "?")
        keyword     = item.get("keyword", "—")
        url         = item.get("url", "")
        source      = item.get("source", "?")
        img_path    = item.get("_image_path")
        subdir      = "PRIORITY" if "priority" in item.get("_folder", "") else "STANDARD"

        print(f"\n  {done_so_far}/{len(items)}  [{source}] {title}  [{subdir}]")
        print(f"         Score  : {score}  |  Keyword: {keyword}")
        if url:
            print(f"         Link   : {url}")

        if img_path:
            _open_image(img_path)
            print(f"         Viewing: {img_path.name}")
        else:
            print(f"         Viewing: (file not found — use link above)")

        while True:
            raw = input("         Rate   (g=keep / b=bad / Enter=skip): ").strip().lower()
            if raw in ("g", "b", ""):
                break
            print("         Type g, b, or press Enter to skip")

        if raw == "g":
            liked.append(item)
        elif raw == "b":
            disliked.append(item)

        if img_path:
            progress[img_path.name] = raw or "s"
            save_image_progress(progress)

    _close_image_viewer()
    return liked, disliked


def _sort_rated_images(progress: dict[str, str]) -> None:
    """
    Bad images → bad/images/ — consolidated alongside bad footage and audio.
    Good images → already in pool/images/priority or standard — no move needed.
    Rejected IDs are written to pool_registry so agent.py never re-downloads them.
    """
    import pool_registry
    bad_dir   = BAD_DIR / "images"
    moved_bad = 0
    bad_metas: list[dict] = []

    for subdir in ["priority", "standard"]:
        src_dir = POOL_IMAGE_DIR / subdir
        if not src_dir.exists():
            continue
        for meta_file in sorted(src_dir.glob("*_metadata.json")):
            base = meta_file.stem.replace("_metadata", "")
            img_file = next(
                (f for f in src_dir.iterdir()
                 if f.stem == base and f.suffix.lower() in IMAGE_EXTENSIONS),
                None,
            )
            if not img_file:
                continue
            rating = progress.get(img_file.name)
            if rating != "b":
                continue
            # Read metadata before moving — needed for registry
            try:
                bad_metas.append(json.loads(meta_file.read_text()))
            except Exception:
                pass
            bad_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(meta_file), bad_dir / meta_file.name)
            shutil.move(str(img_file), bad_dir / img_file.name)
            moved_bad += 1

    if bad_metas:
        pool_registry.mark_many(bad_metas, "rejected")

    if moved_bad:
        print(f"  Moved    : {moved_bad} image(s) → data/rejected/images/")



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _section_header(title: str) -> None:
    width = 52
    print(f"\n{'#' * width}")
    print(f"  ### {title}")
    print(f"{'#' * width}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rate batch output and update world_bible.json")
    parser.add_argument("--batch",   help="Batch date YYYY-MM-DD (default: most recent)")
    parser.add_argument("--select",  action="store_true", help="Show a list of all batches")
    parser.add_argument("--reset",   action="store_true", help="Clear saved progress and start over")
    parser.add_argument("--images",  action="store_true",
                        help="Review image pool only (skip reel footage/audio)")
    args = parser.parse_args()

    bible = load_world_bible()
    all_liked:    list[dict] = []
    all_disliked: list[dict] = []

    # ── SECTION 1: REEL (footage + audio) ────────────────────────────────────
    if not args.images:
        if args.select:
            batch_path = select_batch()
        else:
            batch_path = find_batch(args.batch)

        reel_items = load_items(batch_path)

        _section_header("REEL")
        print(f"Batch: {batch_path.name}  ·  {len(reel_items)} footage/audio file(s)")
        print("Rate each file: g = good · b = bad · Enter = skip")
        print("Progress saves after every rating — quit any time and resume later.\n")

        reel_progress: dict[str, str] = {}
        if args.reset:
            p = _progress_path(batch_path)
            if p.exists():
                p.unlink()
            print("  Progress cleared.\n")
        else:
            reel_progress = load_progress(batch_path)

        if reel_items:
            liked, disliked = review_items(reel_items, batch_path, reel_progress)
            all_liked.extend(liked)
            all_disliked.extend(disliked)
            skipped = len(reel_items) - len(liked) - len(disliked)
            print(f"\n  Reel results: {len(liked)} good · {len(disliked)} bad · {skipped} skipped")
            _sort_rated_files(batch_path, reel_progress)

            # Persist emotional hooks so step3 avoids repeating them
            hooks_file = batch_path / "intelligence" / "emotional_hooks.txt"
            if hooks_file.exists():
                hooks = [h.strip() for h in hooks_file.read_text().splitlines() if h.strip()]
                if hooks:
                    history = set(bible.get("emotional_hooks_history", []))
                    history.update(hooks)
                    bible["emotional_hooks_history"] = sorted(history)
        else:
            print("  No reel files found in this batch — skipping to images.\n")

    # ── SECTION 2: CAROUSEL (priority images — high score, best for multi-slide) ──
    _section_header("CAROUSEL")

    img_progress: dict[str, str] = {}
    if args.reset and IMAGE_PROGRESS_PATH.exists():
        IMAGE_PROGRESS_PATH.unlink()
        print("  Image progress cleared.\n")
    else:
        img_progress = load_image_progress()

    carousel_items = [
        item for item in load_image_items()
        if "priority" in item.get("_folder", "")
    ]
    print(f"{len(carousel_items)} priority image(s) in pool/images/priority/")
    print("These are your highest-scored images — best for carousel slides.")
    print("Rate: g = keep · b = reject (moves to pool/images/bad/) · Enter = skip\n")

    if carousel_items:
        liked, disliked = review_images(carousel_items, img_progress)
        all_liked.extend(liked)
        all_disliked.extend(disliked)
        skipped = len(carousel_items) - len(liked) - len(disliked)
        print(f"\n  Carousel results: {len(liked)} kept · {len(disliked)} rejected · {skipped} skipped")
    else:
        print("  No priority images yet — run agent.py to populate pool/images/priority/")

    # ── SECTION 3: STORY / SINGLE POST (standard images) ─────────────────────
    _section_header("STORY / SINGLE POST")

    story_items = [
        item for item in load_image_items()
        if "standard" in item.get("_folder", "")
        and item.get("_image_path") and item["_image_path"].name not in img_progress
    ]
    print(f"{len(story_items)} standard image(s) in pool/images/standard/")
    print("These are your solid-scoring images — great for single story or post covers.")
    print("Rate: g = keep · b = reject · Enter = skip\n")

    if story_items:
        liked, disliked = review_images(story_items, img_progress)
        all_liked.extend(liked)
        all_disliked.extend(disliked)
        skipped = len(story_items) - len(liked) - len(disliked)
        print(f"\n  Story results: {len(liked)} kept · {len(disliked)} rejected · {skipped} skipped")
    else:
        print("  No standard images yet — run agent.py to populate pool/images/standard/")

    # Sort bad images after both image sections are done
    _sort_rated_images(img_progress)

    # ── Apply all feedback to world_bible in one pass ─────────────────────────
    apply_feedback(bible, all_liked, all_disliked)
    save_world_bible(bible)

    print(f"\n{'─' * 52}")
    print(f"  High-performing keywords : {len(bible['high_performing_keywords'])}")
    print(f"  Low-performing keywords  : {len(bible['low_performing_keywords'])}")
    print(f"  High-performing tags     : {len(bible['high_performing_tags'])}")
    print(f"  Low-performing tags      : {len(bible['low_performing_tags'])}")
    print(f"{'─' * 52}")
    if not args.images:
        print("\nNext: python editor_reels.py    # reel")
        print("      python editor_carousel.py  # carousel posts")
        print("      python editor_story.py     # quote posts")


if __name__ == "__main__":
    main()
