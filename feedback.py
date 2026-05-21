#!/usr/bin/env python3
"""
feedback.py — Review downloaded footage and move approved clips to pool.

Run between scrape.py and render.py.
Approved footage → data/sort_media/footage/   (render.py reads from here)
Rejected footage → data/rejected/footage/

Usage:
    python3 feedback.py          # review all footage in data/scrape/footage/
    python3 feedback.py --reset  # clear saved progress and restart
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

from config import MEMORY_PATH as WORLD_BIBLE_PATH, POOL_FOOTAGE, REJECTED_DIR, STAGING_DIR

VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov"}

_SCREEN_SIZE: tuple[int, int] | None = None


# ── Screen / VLC helpers ───────────────────────────────────────────────────────

def _get_screen_size() -> tuple[int, int]:
    global _SCREEN_SIZE
    if _SCREEN_SIZE is not None:
        return _SCREEN_SIZE
    try:
        result = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=3)
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
    env = os.environ.copy()
    ldpath = env.get("LD_LIBRARY_PATH", "")
    if ldpath:
        paths = [p for p in ldpath.split(":") if "/snap/" not in p]
        env["LD_LIBRARY_PATH"] = ":".join(paths) if paths else ""
        if not paths:
            env.pop("LD_LIBRARY_PATH")
    env.pop("GIO_EXTRA_MODULES", None)
    return env


def _get_active_window_id() -> str | None:
    """Return the currently focused window ID. Tries xdotool, then xprop fallback."""
    xdotool = shutil.which("xdotool")
    if xdotool:
        try:
            result = subprocess.run([xdotool, "getactivewindow"], capture_output=True, text=True, timeout=2)
            wid = result.stdout.strip()
            if wid.isdigit():
                return wid
        except Exception:
            pass

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
                return m.group(0)
        except Exception:
            pass

    return None


def _find_vlc_wid(xdotool: str) -> str | None:
    try:
        result = subprocess.run(
            [xdotool, "search", "--name", "VLC"], capture_output=True, text=True, timeout=2,
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
    xdotool = shutil.which("xdotool")
    wmctrl  = shutil.which("wmctrl")
    if not xdotool and not wmctrl:
        return

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
    for delay in (2.5, 1.5):
        time.sleep(delay)
        try:
            current = _find_vlc_wid(xdotool)
            if current:
                _apply_geometry(xdotool, current, x, y, w, h)
                if terminal_id:
                    _focus_terminal(xdotool, terminal_id)
        except Exception:
            pass


def _play_video(path: Path) -> None:
    vlc = shutil.which("vlc")
    if vlc:
        try:
            sw, sh      = _get_screen_size()
            terminal_id = _get_active_window_id()
            subprocess.Popen(
                [vlc, "--no-fullscreen", "--no-qt-video-autoresize",
                 "--width", str(sw // 2), "--height", str(sh), str(path)],
                env=_clean_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            threading.Thread(
                target=_position_vlc,
                args=(sw // 2, 0, sw // 2, sh, terminal_id),
                daemon=True,
            ).start()
            return
        except Exception as exc:
            print(f"         (VLC failed: {exc} — trying xdg-open)")
    try:
        subprocess.Popen(["xdg-open", str(path)], env=_clean_env())
    except Exception as exc:
        print(f"         (could not open media player: {exc})")


def _close_vlc() -> None:
    try:
        subprocess.run(["pkill", "vlc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _load_footage_items() -> list[dict]:
    """Load all footage files from staging priority + standard, with metadata."""
    items: list[dict] = []
    for subfolder in ("footage/priority", "footage/standard"):
        dir_path = STAGING_DIR / subfolder
        if not dir_path.exists():
            continue
        for meta_file in sorted(dir_path.glob("*_metadata.json")):
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                continue
            base_id = meta_file.stem.replace("_metadata", "")
            media_path: Path | None = None
            for f in dir_path.iterdir():
                if f.stem == base_id and f.suffix.lower() in VIDEO_EXTS:
                    media_path = f
                    break
            if media_path is None:
                continue
            meta["_meta_path"]  = meta_file
            meta["_media_path"] = media_path
            meta["_subfolder"]  = subfolder
            items.append(meta)
    return items


# ── Progress ───────────────────────────────────────────────────────────────────

_PROGRESS_PATH = STAGING_DIR / "review_progress.json"


def _load_progress() -> dict[str, str]:
    if _PROGRESS_PATH.exists():
        try:
            return json.loads(_PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_progress(progress: dict[str, str]) -> None:
    _PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


# ── File sorting ───────────────────────────────────────────────────────────────

def _move_file(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dest_dir / src.name)


def _sort_rated_files(progress: dict[str, str], items: list[dict]) -> dict[str, int]:
    """
    Approved (g) → data/sort_media/footage/
    Rejected (b) → data/rejected/footage/
    """
    moved = {"g": 0, "b": 0}

    item_by_id = {
        item["_media_path"].stem: item
        for item in items
    }

    for asset_id, rating in progress.items():
        if rating not in ("g", "b"):
            continue
        item = item_by_id.get(asset_id)
        if not item:
            continue
        dest = POOL_FOOTAGE if rating == "g" else REJECTED_DIR / "footage"
        _move_file(item["_media_path"], dest)
        _move_file(item["_meta_path"],  dest)
        moved[rating] += 1

    if moved["g"] or moved["b"]:
        print(f"  Sorted   : {moved['g']} → sort_media/footage/  ·  {moved['b']} → rejected/footage/")
    return moved


# ── World bible keyword feedback ───────────────────────────────────────────────

def _update_world_bible(liked: list[dict], disliked: list[dict]) -> None:
    if not WORLD_BIBLE_PATH.exists():
        return
    bible = json.loads(WORLD_BIBLE_PATH.read_text())

    high_kw  = set(bible.get("high_performing_keywords", []))
    low_kw   = set(bible.get("low_performing_keywords", []))
    rejected = set(bible.get("rejected_ids", []))

    votes: dict[str, int] = {}
    for item in liked:
        for kw in (item.get("matched_keywords") or ([item["keyword"]] if item.get("keyword") else [])):
            votes[kw] = votes.get(kw, 0) + 1
    for item in disliked:
        for kw in (item.get("matched_keywords") or ([item["keyword"]] if item.get("keyword") else [])):
            votes[kw] = votes.get(kw, 0) - 1

    for kw, net in votes.items():
        if net > 0:
            high_kw.add(kw); low_kw.discard(kw)
        elif net < 0:
            low_kw.add(kw); high_kw.discard(kw)

    for item in disliked:
        src = item.get("source", ""); iid = str(item.get("id", ""))
        if src and iid:
            rejected.add(f"{src}_{iid}")

    bible["high_performing_keywords"] = sorted(high_kw)
    bible["low_performing_keywords"]  = sorted(low_kw)
    bible["rejected_ids"]             = sorted(rejected)
    bible["last_updated"]             = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    WORLD_BIBLE_PATH.write_text(json.dumps(bible, ensure_ascii=False, indent=2))


# ── Interactive review ─────────────────────────────────────────────────────────

def _review(
    items: list[dict],
    progress: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    liked: list[dict] = []
    disliked: list[dict] = []

    for item in items:
        rating = progress.get(item["_media_path"].stem)
        if rating == "g":
            liked.append(item)
        elif rating == "b":
            disliked.append(item)

    remaining = [item for item in items if item["_media_path"].stem not in progress]

    if progress:
        print(f"  Resuming — {len(progress)} already rated, {len(remaining)} left.\n")

    current_section = None
    for i, item in enumerate(remaining):
        _close_vlc()

        section = item["_subfolder"].split("/")[1].upper()  # PRIORITY or STANDARD
        if section != current_section:
            current_section = section
            print(f"\n{'─' * 52}")
            print(f"  {section}")
            print(f"{'─' * 52}")

        done   = len(progress) + i + 1
        title  = item.get("title") or item["_media_path"].stem
        score  = item.get("score", "?")
        kws    = ", ".join(item.get("matched_keywords") or ([item.get("keyword", "")] if item.get("keyword") else []))
        source = item.get("source", "?")
        concept_id = item.get("candidate_concept_id") or "—"

        # Duration from full_response (Pexels/Pixabay store it there)
        fr       = item.get("full_response") or {}
        duration = fr.get("duration")
        dur_str  = f"{duration}s" if duration else "?"

        # Score breakdown — show the 3 most useful dimensions
        bd = item.get("score_breakdown") or {}
        bd_str = "  ".join(
            f"{k[:3]}={v}" for k, v in bd.items()
            if k in ("relevance", "emotional_hook", "visual_fit")
        ) if bd else "—"

        # Platform tags (Pixabay has rich tags, Pexels usually empty)
        tags = [t for t in (item.get("tags") or []) if t.lower() not in (
            "video","footage","clip","hd","4k","free","background","stock","media"
        )]
        tags_str = ", ".join(tags[:6]) if tags else "—"

        print(f"\n  {done}/{len(items)}  [{source}] {title[:70]}")
        print(f"         Keywords : {kws or '—'}  |  {dur_str}")
        print(f"         Score    : {score}  ({bd_str})")
        if tags_str != "—":
            print(f"         Tags     : {tags_str}")
        if concept_id != "—":
            print(f"         Concept  : {concept_id}")
        _play_video(item["_media_path"])
        print(f"         Playing  : {item['_media_path'].name}")

        while True:
            raw = input("         Rate   (g=approve / b=reject / Enter=skip): ").strip().lower()
            if raw in ("g", "b", ""):
                break
            print("         Type g, b, or press Enter to skip")

        if raw == "g":
            liked.append(item)
        elif raw == "b":
            disliked.append(item)

        progress[item["_media_path"].stem] = raw or "s"
        _save_progress(progress)

    _close_vlc()
    return liked, disliked


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Review footage and approve clips for rendering")
    parser.add_argument("--reset", action="store_true", help="Clear saved progress and restart")
    args = parser.parse_args()

    print("\n=== Footage Feedback ===\n")

    items = _load_footage_items()
    if not items:
        print("No footage found in data/scrape/footage/. Run scrape.py first.")
        sys.exit(0)

    progress: dict[str, str] = {}
    if args.reset:
        if _PROGRESS_PATH.exists():
            _PROGRESS_PATH.unlink()
        print("  Progress cleared.\n")
    else:
        progress = _load_progress()

    pool_count = sum(1 for f in POOL_FOOTAGE.glob("*.mp4")) if POOL_FOOTAGE.exists() else 0
    print(f"Found {len(items)} footage clips  |  {pool_count} already in pool")
    print("g = approve → sort_media/footage/   |  b = reject → rejected/footage/   |  Enter = skip\n")

    liked, disliked = _review(items, progress)
    skipped = len(items) - len(liked) - len(disliked)

    print(f"\n{'─' * 52}")
    print(f"  Results  : {len(liked)} approved · {len(disliked)} rejected · {skipped} skipped")

    _sort_rated_files(progress, items)
    _update_world_bible(liked, disliked)

    new_pool = sum(1 for f in POOL_FOOTAGE.glob("*.mp4")) if POOL_FOOTAGE.exists() else 0
    print(f"  Pool now : {new_pool} clips in sort_media/footage/")
    print(f"{'─' * 52}")

    if new_pool > 0:
        print("\nNext step: python3 render.py")
    else:
        print("\nNo footage approved. Run scrape.py again or re-run feedback.py.")


if __name__ == "__main__":
    main()
