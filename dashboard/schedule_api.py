"""
schedule_api.py — slot assignment logic for the posting timeline.

When a post is approved in Stage 04, it auto-assigns to the next open
calendar slot of its format. Assignments persist across sessions.

Storage: data/schedule_assignments.json  (list of assignment dicts)
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .paths import DATA_DIR, PENDING_DIR, READY_DIR

ASSIGNMENTS_PATH = DATA_DIR / "schedule_assignments.json"

# Day order used for sorting slots within a week
_DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ------------------------------------------------------------------ persistence

def load_assignments() -> list[dict]:
    if ASSIGNMENTS_PATH.exists():
        try:
            data = json.loads(ASSIGNMENTS_PATH.read_text())
            if not isinstance(data, list):
                return []
            # Re-resolve folder_path on every load so stale entries (e.g. written
            # before _folder_path was corrected, or where the approved copy was
            # later removed) always reflect the current on-disk reality.
            for a in data:
                rn  = a.get("render_name", "")
                pk  = a.get("post_key", "")
                if rn and pk:
                    a["folder_path"] = _folder_path(rn, pk)
                    # Backfill thumbnail_url for assignments saved before
                    # the auto-assign code populated it. Runtime only —
                    # never written back to schedule_assignments.json.
                    fmt = a.get("format", "")
                    if not a.get("thumbnail_url") and fmt not in ("reel",):
                        resolved = _resolve_thumbnail(rn, pk, fmt)
                        if resolved:
                            a["thumbnail_url"] = resolved
                    if fmt == "reel" and not a.get("video_url"):
                        resolved_video = _resolve_reel_video(rn, a.get("chosen_variant", ""))
                        if resolved_video:
                            a["video_url"] = resolved_video
            return data
        except Exception:
            return []
    return []


def save_assignments(assignments: list[dict]) -> None:
    ASSIGNMENTS_PATH.write_text(json.dumps(assignments, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ helpers

def _monday_of_week(dt: date) -> date:
    """Monday of the ISO week containing dt."""
    return dt - timedelta(days=dt.weekday())


def _format_for_key(post_key: str) -> str:
    """Infer schedule format string from a post_key."""
    if "/carousel_" in post_key:
        return "carousel"
    if "/" in post_key:          # story_01.jpg or similar
        return "quote_post"
    return "reel"


def _resolve_thumbnail(render_name: str, post_key: str, fmt: str) -> str:
    """
    Resolve a current thumbnail URL for an assignment from the render detail.

    Older assignments were written before the auto-assign path populated
    thumbnail_url, so the calendar cell renders empty. We re-read the
    render's slides/images on load and fall back to the first slide
    (carousel) or the matching image (quote_post). Runtime only — never
    persisted back to disk.
    """
    from . import final_api
    try:
        detail = final_api.get_render_detail(render_name)
        if fmt == "carousel":
            carousel_dir = post_key.split("/", 1)[1]
            for c in detail.get("carousels", []):
                if c.get("dir") == carousel_dir:
                    slides = c.get("slides", [])
                    return slides[0]["media_url"] if slides else ""
        elif fmt == "quote_post":
            img_name = post_key.split("/", 1)[1] if "/" in post_key else ""
            for img in detail.get("images", []):
                if img.get("filename") == img_name:
                    return img.get("media_url", "")
    except Exception:
        pass
    return ""


def _resolve_reel_video(render_name: str, chosen_variant: str = "") -> str:
    """Resolve video_url for old reel assignments that predate video_url storage."""
    from . import final_api
    try:
        detail   = final_api.get_render_detail(render_name)
        variants = detail.get("variants", [])
        chosen   = chosen_variant or detail.get("chosen", "")
        if not chosen and variants:
            chosen = variants[0]["filename"]
        for v in variants:
            if v["filename"] == chosen:
                return v["media_url"]
        if variants:
            return variants[0]["media_url"]
    except Exception:
        pass
    return ""


def _folder_path(render_name: str, post_key: str) -> str:
    """
    Return the relative path (from project root) of the folder that holds
    the post's files — used by the 'open in files' feature.

    Resolves against the real READY_DIR (data/approved/) so the path stays
    in sync with what _copy_carousel / _copy_story_image / _copy_reel_variant
    actually create when a verdict is submitted. If the approved folder
    doesn't exist yet (e.g. the post was assigned but not yet copied, or
    the user is browsing before final review), fall back to the pending
    source folder so "Open in files" still lands on something real.
    """
    ready_root = READY_DIR.relative_to(DATA_DIR.parent)  # e.g. data/approved

    if "/carousel_" in post_key:
        # Approved copy is data/approved/carousel/<render>_<carousel_dir>
        carousel_dir = post_key.split("/", 1)[1]
        approved = ready_root / "carousel" / f"{render_name}_{carousel_dir}"
        if (DATA_DIR.parent / approved).exists():
            return str(approved)
        # Not yet approved — point at the pending source so the user can still browse slides
        pending = Path("data") / "pending" / render_name / carousel_dir
        return str(pending)

    if "/" in post_key:
        # Story: approved files live flat in data/approved/story[_minimal]/
        for sub in ("story_minimal", "story_quotes"):
            candidate = PENDING_DIR / render_name / sub
            if candidate.exists():
                ready_sub = "story_minimal" if sub == "story_minimal" else "story"
                approved = ready_root / ready_sub
                if (DATA_DIR.parent / approved).exists():
                    return str(approved)
                # Fall back to the pending render's story subdir
                return str(Path("data") / "pending" / render_name / sub)
        return str(ready_root / "story")

    # Reel — approved variants live in data/approved/reel/, pending source in <render>/final/
    approved = ready_root / "reel"
    if (DATA_DIR.parent / approved).exists():
        return str(approved)
    return str(Path("data") / "pending" / render_name / "final")


def _slots_for_format(schedule: dict, fmt: str) -> list[dict]:
    """Active slots matching fmt, ordered by day then time."""
    slots = [
        s for s in (schedule.get("slots") or [])
        if s.get("format") == fmt and s.get("active", True)
    ]
    return sorted(
        slots,
        key=lambda s: (
            _DAY_ORDER.index(s["day"]) if s["day"] in _DAY_ORDER else 99,
            s.get("time_start", ""),
        ),
    )


# ------------------------------------------------------------------ public API

def assign_post(
    post_key:       str,
    schedule:       dict,
    render_name:    str,
    title:          str,
    thumbnail_url:  str,
    caption:        str,
    chosen_variant: str = "",
    video_url:      str = "",
) -> dict | None:
    """
    Find the next open slot for post_key's format and create an assignment.
    Returns the new assignment dict, or None if no matching slots exist.
    """
    fmt   = _format_for_key(post_key)
    slots = _slots_for_format(schedule, fmt)
    if not slots:
        return None

    assignments = load_assignments()
    occupied    = {(a["slot_id"], a["week_start"]) for a in assignments}

    today  = date.today()
    monday = _monday_of_week(today)

    # Walk forward up to 52 weeks to find a free slot.
    # Skip slots whose date is in the past — otherwise we'd assign to
    # a day that already happened, and the timeline (a rolling 7-day
    # window starting today) would never display the slot.
    for week_offset in range(52):
        week_monday     = monday + timedelta(weeks=week_offset)
        week_start_str  = week_monday.isoformat()
        for slot in slots:
            slot_date = week_monday + timedelta(
                days=_DAY_ORDER.index(slot["day"]) if slot["day"] in _DAY_ORDER else 0
            )
            if slot_date < today:
                continue
            if (slot["id"], week_start_str) not in occupied:
                new_a = {
                    "id":             uuid.uuid4().hex[:8],
                    "slot_id":        slot["id"],
                    "slot_day":       slot["day"],
                    "slot_time":      slot.get("time_start", ""),
                    "week_start":     week_start_str,
                    "post_key":       post_key,
                    "format":         fmt,
                    "render_name":    render_name,
                    "title":          title,
                    "thumbnail_url":  thumbnail_url,
                    "video_url":      video_url,
                    "chosen_variant": chosen_variant,
                    "caption":        caption,
                    "folder_path":    _folder_path(render_name, post_key),
                    "assigned_at":    datetime.now(timezone.utc).isoformat(),
                }
                assignments.append(new_a)
                save_assignments(assignments)
                return new_a
    return None


def unassign_post(post_key: str) -> bool:
    """Remove any assignment for this post_key. Returns True if removed."""
    assignments = load_assignments()
    before      = len(assignments)
    assignments = [a for a in assignments if a.get("post_key") != post_key]
    if len(assignments) < before:
        save_assignments(assignments)
        return True
    return False


def _prune_past_assignments() -> None:
    """
    Drop assignments whose slot date is strictly before today.
    The timeline is a rolling 7-day forward view; past assignments
    cannot be displayed there, so they must be re-routed to the
    next available future slot.
    """
    assignments = load_assignments()
    today       = date.today()
    kept = []
    dropped = False
    for a in assignments:
        try:
            week_monday = date.fromisoformat(a.get("week_start", ""))
            day         = a.get("slot_day", "")
            day_offset  = _DAY_ORDER.index(day) if day in _DAY_ORDER else 0
            slot_date   = week_monday + timedelta(days=day_offset)
        except Exception:
            kept.append(a)
            continue
        if slot_date < today:
            dropped = True
            continue
        kept.append(a)
    if dropped:
        save_assignments(kept)


def auto_assign_approved(schedule: dict) -> int:
    """
    Scan all approved-but-unassigned postable verdicts and create assignments.
    Called on every Timeline load so posts approved before the auto-assign
    code was deployed still get placed in the calendar.
    Returns count of new assignments created.
    """
    from . import final_api  # local import — no circular dep

    # Drop stale assignments first so their post_keys become eligible for
    # re-assignment to a future slot in the same loop.
    _prune_past_assignments()

    assignments  = load_assignments()
    assigned_keys = {a["post_key"] for a in assignments}

    new_count = 0
    for r in final_api.list_renders():
        name   = r["name"]
        detail = final_api.get_render_detail(name)
        fmt    = detail.get("format", "")

        if fmt == "carousel":
            for c in detail.get("carousels", []):
                key = c.get("key", "")
                if not key or c.get("verdict") != "postable" or key in assigned_keys:
                    continue
                thumb   = c["slides"][0]["media_url"] if c.get("slides") else ""
                title   = c.get("theme") or c.get("dir") or key
                caption = c.get("caption", "")
                a = assign_post(key, schedule, name, title, thumb, caption)
                if a:
                    assigned_keys.add(key)
                    new_count += 1

        elif fmt in ("story", "story_minimal"):
            for img in detail.get("images", []):
                key = img.get("key", "")
                if not key or img.get("verdict") != "postable" or key in assigned_keys:
                    continue
                title   = (img.get("quote") or "")[:60] or img.get("filename", key)
                thumb   = img.get("media_url", "")
                caption = img.get("caption", "")
                a = assign_post(key, schedule, name, title, thumb, caption)
                if a:
                    assigned_keys.add(key)
                    new_count += 1

        elif fmt == "reel":
            key = name
            if detail.get("verdict") != "postable" or key in assigned_keys:
                continue
            caption  = detail.get("caption", "")
            chosen   = detail.get("chosen", "")
            variants = detail.get("variants", [])
            if not chosen and variants:
                chosen = variants[0]["filename"]
            video_url = next(
                (v["media_url"] for v in variants if v["filename"] == chosen), ""
            )
            a = assign_post(key, schedule, name, name, "", caption,
                            chosen_variant=chosen, video_url=video_url)
            if a:
                assigned_keys.add(key)
                new_count += 1

    return new_count


def weeks_ahead() -> dict:
    """
    {format: weeks_ahead_count} where weeks_ahead = number of weeks with
    assignments that are strictly beyond the current calendar week.
    """
    today          = date.today()
    current_monday = _monday_of_week(today)
    assignments    = load_assignments()

    by_fmt: dict[str, set] = {}
    for a in assignments:
        fmt = a.get("format", "")
        try:
            w = date.fromisoformat(a["week_start"])
        except Exception:
            continue
        by_fmt.setdefault(fmt, set()).add(w)

    result = {}
    for fmt in ("reel", "carousel", "quote_post"):
        weeks  = by_fmt.get(fmt, set())
        future = sum(1 for w in weeks if w > current_monday)
        result[fmt] = future
    return result
