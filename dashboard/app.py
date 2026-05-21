"""
app.py — Flask dashboard for the Money Psychology pipeline.

Entry point: run_dashboard.py
"""

import json
import queue
import sys

from flask import Flask, Response, abort, jsonify, render_template, request

from .paths  import BASE_DIR, DATA_DIR, STAGING_DIR, POOL_DIR, SCRAPE_SCRIPT, RENDER_SCRIPT, WORLD_BIBLE_PATH
from .state  import state
from .runner import launch, stop, _venv_python
from .media  import serve as serve_media
from .       import review_api, final_api, corpus_api

app = Flask(__name__, template_folder="templates", static_folder="static")

_COMMANDS = {
    "scrape": [_venv_python(), str(SCRAPE_SCRIPT)],
    "render": [_venv_python(), str(RENDER_SCRIPT)],
}


# ------------------------------------------------------------------ page

@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------ pipeline control

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


# ------------------------------------------------------------------ footage staging review

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


# ------------------------------------------------------------------ pool audio

@app.route("/api/audio")
def api_audio():
    return jsonify(review_api.load_audio_items())


@app.route("/api/audio/rate", methods=["POST"])
def api_audio_rate():
    data     = request.get_json(force=True)
    filename = data.get("filename", "")
    rating   = data.get("rating", "")
    if not filename or rating not in ("b", ""):
        abort(400)
    return jsonify(review_api.rate_audio_item(filename, rating))


@app.route("/api/audio/commit", methods=["POST"])
def api_audio_commit():
    return jsonify(review_api.commit_audio())


# ------------------------------------------------------------------ pool images

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


# ------------------------------------------------------------------ rejected media restore

@app.route("/api/rejected/<kind>")
def api_rejected(kind):
    if kind not in ("images", "audio", "footage"):
        abort(404)
    return jsonify(review_api.load_rejected_items(kind))


@app.route("/api/rejected/<kind>/restore", methods=["POST"])
def api_restore(kind):
    if kind not in ("images", "audio", "footage"):
        abort(404)
    data     = request.get_json(force=True)
    filename = data.get("filename", "")
    if not filename:
        abort(400)
    result = review_api.restore_item(kind, filename)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ------------------------------------------------------------------ render verdict

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
    data    = request.get_json(force=True)
    verdict = data.get("verdict", "")
    note    = data.get("note", "")
    chosen  = data.get("chosen_variant", "")
    result  = final_api.submit_verdict(name, name, verdict, note, chosen)
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
    result = final_api.reverse_verdict(name)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ------------------------------------------------------------------ corpus browser

@app.route("/api/corpus")
def api_corpus():
    return jsonify(corpus_api.get_corpus_summary())


@app.route("/api/corpus/add", methods=["POST"])
def api_corpus_add():
    data = request.get_json(force=True)
    result = corpus_api.add_concept(data)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


# ------------------------------------------------------------------ intelligence

@app.route("/api/intelligence")
def api_intelligence():
    return jsonify(final_api.get_intelligence())


# ------------------------------------------------------------------ pool summary

@app.route("/api/pool-summary")
def api_pool_summary():
    return jsonify(final_api.get_pool_summary())


# ------------------------------------------------------------------ stats

@app.route("/api/stats")
def api_stats():
    footage_staging = _count_staging_footage()
    pool_footage    = _count_dir(POOL_DIR / "footage", skip={"used"})
    pool_audio      = _count_dir(POOL_DIR / "audio")
    pool_images     = _count_dir(POOL_DIR / "images")
    final_stats     = final_api.get_stats()

    world_bible: dict = {}
    if WORLD_BIBLE_PATH.exists():
        try:
            world_bible = json.loads(WORLD_BIBLE_PATH.read_text())
        except Exception:
            pass

    return jsonify({
        "staging":      {"footage": footage_staging},
        "pool": {
            "footage": pool_footage,
            "audio":   pool_audio,
            "images":  pool_images,
        },
        "final_review": final_stats,
        "world_bible": {
            "high_keywords": world_bible.get("high_performing_keywords", [])[:12],
            "low_keywords":  world_bible.get("low_performing_keywords", [])[:12],
            "high_tags":     world_bible.get("high_performing_tags", [])[:12],
            "low_tags":      world_bible.get("low_performing_tags", [])[:12],
            "rejected":      len(world_bible.get("rejected_ids", [])),
        },
    })


# ------------------------------------------------------------------ media serving

@app.route("/media/<path:relpath>")
def api_media(relpath):
    return serve_media(relpath)


# ------------------------------------------------------------------ helpers

def _count_staging_footage() -> int:
    total = 0
    for tier in ("priority", "standard"):
        d = STAGING_DIR / "footage" / tier
        if d.exists():
            total += len(list(d.glob("*_metadata.json")))
    return total


def _count_dir(path, skip: set | None = None) -> int:
    if not path.exists():
        return 0
    total = len(list(path.glob("*_metadata.json")))
    for sub in path.iterdir():
        if sub.is_dir() and (not skip or sub.name not in skip):
            total += len(list(sub.glob("*_metadata.json")))
    return total
