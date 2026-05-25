"""
app.py — Flask dashboard for the healing-agent pipeline.

Entry point: run_dashboard.py
"""

import json
import queue
import sys

from flask import Flask, Response, abort, jsonify, render_template, request

from .paths  import BASE_DIR, DATA_DIR, STAGING_DIR, POOL_DIR, AGENT_SCRIPT, CAROUSEL_SCRIPT, STORY_SCRIPT, REEL_SCRIPT, WORLD_BIBLE_PATH
from .state  import state
from .runner import launch, stop
from .media  import serve as serve_media
from .       import review_api, final_api, schedule_api

app = Flask(__name__, template_folder="templates", static_folder="static")

_COMMANDS = {
    "agent":       [sys.executable, str(AGENT_SCRIPT)],
    "agent_quick": [sys.executable, str(AGENT_SCRIPT), "--quick"],
    "carousel":    [sys.executable, str(CAROUSEL_SCRIPT)],
    "story":       [sys.executable, str(STORY_SCRIPT)],
    "reel":        [sys.executable, str(REEL_SCRIPT)],
}


# ------------------------------------------------------------------ page

@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------ pipeline

@app.route("/api/status")
def api_status():
    return jsonify(state.snapshot())


@app.route("/api/run/<command>", methods=["POST"])
def api_run(command):
    if command not in _COMMANDS:
        abort(400)
    started, err = launch(command, _COMMANDS[command])
    if not started:
        return jsonify({"error": err}), 409
    return jsonify({"started": command})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    stop()
    return jsonify({"ok": True})


# ------------------------------------------------------------------ SSE

@app.route("/api/events")
def api_events():
    q = state.add_sse_client()

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'status', 'payload': state.snapshot()})}\n\n"
            for line in list(state.log_buffer):
                yield f"data: {json.dumps({'type': 'log', 'line': line})}\n\n"
            while True:
                try:
                    event_type, payload = q.get(timeout=25)
                    if event_type == "log":
                        msg = json.dumps({"type": "log", "line": payload})
                    else:
                        msg = json.dumps({"type": "status", "payload": json.loads(payload)})
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            state.remove_sse_client(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ flat media review (stage 2)

@app.route("/api/media")
def api_media_items():
    return jsonify(review_api.load_all_media_items())


@app.route("/api/media/rate", methods=["POST"])
def api_media_rate():
    data     = request.get_json(force=True)
    filename = data.get("filename", "")
    rating   = data.get("rating", "")
    if not filename or rating not in ("g", "b", ""):
        abort(400)
    return jsonify(review_api.rate_media_item("", filename, rating))


@app.route("/api/media/commit", methods=["POST"])
def api_media_commit():
    return jsonify(review_api.commit_all_media())


# ------------------------------------------------------------------ batch review (kept for backward compat)

@app.route("/api/batches")
def api_batches():
    return jsonify(review_api.list_batches())


@app.route("/api/batches/<batch_date>/items")
def api_batch_items(batch_date):
    return jsonify(review_api.load_reel_items(batch_date))


@app.route("/api/batches/<batch_date>/rate", methods=["POST"])
def api_batch_rate(batch_date):
    data     = request.get_json(force=True)
    filename = data.get("filename", "")
    rating   = data.get("rating", "")
    if not filename or rating not in ("g", "b", ""):
        abort(400)
    return jsonify(review_api.rate_reel_item(batch_date, filename, rating))


@app.route("/api/batches/<batch_date>/commit", methods=["POST"])
def api_batch_commit(batch_date):
    return jsonify(review_api.commit_reel(batch_date))


# ------------------------------------------------------------------ image review

@app.route("/api/images")
def api_images():
    return jsonify(review_api.load_image_items())


@app.route("/api/images/rate", methods=["POST"])
def api_images_rate():
    data     = request.get_json(force=True)
    filename = data.get("filename", "")
    rating   = data.get("rating", "")
    if not filename or rating not in ("g", "b", ""):
        abort(400)
    return jsonify(review_api.rate_image_item(filename, rating))


@app.route("/api/images/commit", methods=["POST"])
def api_images_commit():
    return jsonify(review_api.commit_images())


# ------------------------------------------------------------------ final review

@app.route("/api/renders")
def api_renders():
    return jsonify(final_api.list_renders())


@app.route("/api/renders/<name>")
def api_render_detail(name):
    detail = final_api.get_render_detail(name)
    if not detail:
        abort(404)
    return jsonify(detail)


@app.route("/api/renders/<name>/verdict", methods=["POST"])
def api_render_verdict(name):
    data     = request.get_json(force=True)
    item_key = data.get("item_key")
    verdict  = data.get("verdict", "")
    note     = data.get("note", "")
    chosen   = data.get("chosen_variant", "")
    result   = final_api.submit_verdict(name, item_key, verdict, note, chosen)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/renders/<name>", methods=["DELETE"])
def api_render_delete(name):
    result = final_api.delete_render(name)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/renders/<name>/verdict", methods=["DELETE"])
def api_render_verdict_delete(name):
    data   = request.get_json(force=True, silent=True) or {}
    key    = data.get("item_key") or name
    result = final_api.reverse_verdict(key)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ------------------------------------------------------------------ intelligence

@app.route("/api/intelligence")
def api_intelligence():
    return jsonify(final_api.get_intelligence())


@app.route("/api/pool-summary")
def api_pool_summary():
    return jsonify(final_api.get_pool_summary())


# ------------------------------------------------------------------ schedule

@app.route("/api/schedule")
def api_schedule():
    """Serve the posting rhythm config (read-only).

    The Rhythm Editor (tools/rhythm_editor.html) is the only writer —
    keeps Flask off the write path so the standalone tool can stay portable.
    """
    path = BASE_DIR / "config" / "posting_schedule.json"
    if path.exists():
        return Response(path.read_text(), mimetype="application/json")
    abort(404)


def _load_schedule() -> dict:
    """Load posting_schedule.json, falling back to the built-in default."""
    path = BASE_DIR / "config" / "posting_schedule.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {
        "timezone": "Europe/Berlin",
        "slots": [
            {"id": "sun_reel",     "day": "sunday",    "time_start": "19:00", "time_end": "21:00", "format": "reel",       "label": "wind-down",           "optional": False, "active": True},
            {"id": "tue_carousel", "day": "tuesday",   "time_start": "07:00", "time_end": "08:00", "format": "carousel",   "label": "save peak",           "optional": False, "active": True},
            {"id": "wed_quote",    "day": "wednesday", "time_start": "20:00", "time_end": "21:00", "format": "quote_post", "label": "midweek soft moment", "optional": True,  "active": True},
            {"id": "thu_reel",     "day": "thursday",  "time_start": "19:00", "time_end": "21:00", "format": "reel",       "label": "midweek reach",       "optional": False, "active": True},
            {"id": "sat_carousel", "day": "saturday",  "time_start": "09:00", "time_end": "10:00", "format": "carousel",   "label": "slow weekend",        "optional": False, "active": True},
        ],
    }


# ------------------------------------------------------------------ schedule assignments

@app.route("/api/schedule-assignments")
def api_schedule_assignments_get():
    return jsonify(schedule_api.load_assignments())


@app.route("/api/schedule-assignments", methods=["POST"])
def api_schedule_assignments_post():
    data            = request.get_json(force=True) or {}
    post_key        = data.get("post_key", "").strip()
    render_name     = data.get("render_name", "").strip()
    title           = data.get("title", "")
    thumbnail_url   = data.get("thumbnail_url", "")
    video_url       = data.get("video_url", "")
    chosen_variant  = data.get("chosen_variant", "")
    caption         = data.get("caption", "")

    if not post_key or not render_name:
        return jsonify({"error": "post_key and render_name are required"}), 400

    assignment = schedule_api.assign_post(
        post_key, _load_schedule(), render_name, title, thumbnail_url, caption,
        chosen_variant=chosen_variant, video_url=video_url,
    )
    if assignment is None:
        return jsonify({"error": "no available slots in schedule"}), 409
    return jsonify(assignment)


@app.route("/api/schedule-auto-assign", methods=["POST"])
def api_schedule_auto_assign():
    """Retroactively assign approved-but-unscheduled posts to calendar slots."""
    new_count = schedule_api.auto_assign_approved(_load_schedule())
    return jsonify({"new_assignments": new_count})


@app.route("/api/schedule-assignments/<path:post_key>", methods=["DELETE"])
def api_schedule_assignments_delete(post_key):
    removed = schedule_api.unassign_post(post_key)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/schedule-weeks-ahead")
def api_schedule_weeks_ahead():
    return jsonify(schedule_api.weeks_ahead())


@app.route("/api/backfill-captions", methods=["POST"])
def api_backfill_captions():
    """Write caption.txt to approved carousel folders that are missing it.

    Older approvals copied the carousel before the verdict path ensured
    caption.txt existed, so some approved folders are missing the file.
    Pull the assignment.caption field (which the schedule stored at
    approval time) and write it into the folder so 'Open in files'
    surfaces it.
    """
    assignments = schedule_api.load_assignments()
    written = 0
    for a in assignments:
        if a.get("format") != "carousel":
            continue
        fp = a.get("folder_path", "")
        if not fp:
            continue
        folder = (BASE_DIR / fp).resolve()
        # Security: never write outside the project root, even if a
        # malformed folder_path tries to traverse upward.
        if not str(folder).startswith(str(BASE_DIR.resolve())) or not folder.exists():
            continue
        caption_file = folder / "caption.txt"
        if caption_file.exists():
            continue
        caption = (a.get("caption") or "").strip()
        if not caption:
            # Assignment stored no caption — generate from manifest.json if present.
            # This covers carousels approved before Gemini caption generation was wired up.
            manifest = folder / "manifest.json"
            if manifest.exists():
                try:
                    slides = json.loads(manifest.read_text(encoding="utf-8"))
                    texts  = [s.get("text", "").strip() for s in slides if s.get("text")]
                    if texts:
                        hook = texts[0]
                        caption = (
                            f"{hook}\n\n"
                            "Save this for when you need it 🌿\n\n"
                            "#healing #innerchild #nervoussystem #selfcare "
                            "#emotionalhealing #mindfulness #anxiety "
                            "#traumahealing #mentalhealth #selfcompassion"
                        )
                except Exception:
                    pass
        if caption:
            try:
                caption_file.write_text(caption + "\n", encoding="utf-8")
                written += 1
            except Exception:
                pass
    return jsonify({"written": written})


# ------------------------------------------------------------------ open render folder in file manager

@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    import subprocess
    data     = request.get_json(force=True) or {}
    rel_path = data.get("path", "").strip()
    if not rel_path:
        return jsonify({"error": "path required"}), 400
    target = (BASE_DIR / rel_path).resolve()
    # Security: only allow opening paths inside the project root
    if not str(target).startswith(str(BASE_DIR.resolve())):
        return jsonify({"error": "forbidden"}), 403
    if not target.exists():
        return jsonify({"error": "folder not found"}), 404
    try:
        subprocess.Popen(["xdg-open", str(target)])
        return jsonify({"ok": True, "path": str(target)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ------------------------------------------------------------------ stats

@app.route("/api/stats")
def api_stats():
    pool_footage   = _count_pool(POOL_DIR / "footage")
    pool_audio     = _count_pool(POOL_DIR / "audio")
    pool_images    = _count_pool(POOL_DIR / "images")
    staging_counts = _count_staging()
    batch_count    = len(review_api.list_batches())
    final_stats  = final_api.get_stats()

    world_bible: dict = {}
    if WORLD_BIBLE_PATH.exists():
        try:
            world_bible = json.loads(WORLD_BIBLE_PATH.read_text())
        except Exception:
            pass

    return jsonify({
        "pool": {
            "footage": pool_footage,
            "audio":   pool_audio,
            "images":  pool_images,
        },
        "staging":      staging_counts,
        "batches":      batch_count,
        "final_review": final_stats,
        "world_bible": {
            "high_tags": world_bible.get("high_performing_tags", [])[:12],
            "low_tags":  world_bible.get("low_performing_tags", [])[:12],
            "rejected":  len(world_bible.get("rejected_ids", [])),
            "learnings": len(world_bible.get("post_learnings", [])),
        },
    })


# ------------------------------------------------------------------ media

@app.route("/media/<path:relpath>")
def api_media(relpath):
    return serve_media(relpath)


# ------------------------------------------------------------------ helpers

def _count_staging() -> dict:
    if not STAGING_DIR.exists():
        return {"footage": 0, "audio": 0}
    footage = sum(
        len(list(sub.glob("*_metadata.json")))
        for sub in [STAGING_DIR / "footage" / "priority", STAGING_DIR / "footage" / "standard"]
        if sub.exists()
    )
    audio = sum(
        len(list(sub.glob("*_metadata.json")))
        for sub in [STAGING_DIR / "audio" / "priority", STAGING_DIR / "audio" / "standard"]
        if sub.exists()
    )
    return {"footage": footage, "audio": audio}


def _count_pool(path) -> dict:
    if not path.exists():
        return {"priority": 0, "standard": 0}
    pri = path / "priority"
    std = path / "standard"
    if pri.exists() or std.exists():
        # Images: organised into priority/ and standard/ subdirs
        return {
            "priority": len(list(pri.glob("*_metadata.json"))) if pri.exists() else 0,
            "standard": len(list(std.glob("*_metadata.json"))) if std.exists() else 0,
        }
    # Footage / audio: flat pool — metadata files sit directly in the folder
    total = len(list(path.glob("*_metadata.json")))
    return {"priority": total, "standard": 0}
