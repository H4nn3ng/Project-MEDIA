"""
app.py — portfolio demo server for the healing-agent dashboard.

All API routes return fixture data from demo_data.py.
POST / DELETE endpoints are no-ops (return success, write nothing to disk).
Run: gunicorn -b 0.0.0.0:$PORT app:app
"""

import json
import os
import threading
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

import demo_data

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder="templates", static_folder="static")


# ------------------------------------------------------------------ page

@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------ pipeline status

@app.route("/api/status")
def api_status():
    return jsonify(demo_data.STATUS)


@app.route("/api/run/<command>", methods=["POST"])
def api_run(command):
    return jsonify({"started": command, "demo": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    return jsonify({"ok": True})


# ------------------------------------------------------------------ SSE (idle stream)

@app.route("/api/events")
def api_events():
    def generate():
        yield f"data: {json.dumps({'type': 'status', 'payload': demo_data.STATUS})}\n\n"
        stop = threading.Event()
        try:
            while not stop.wait(25):
                yield ": keepalive\n\n"
        except GeneratorExit:
            pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ Stage 02 — Sort Media

@app.route("/api/media")
def api_media_items():
    return jsonify(demo_data.MEDIA_ITEMS)


@app.route("/api/media/rate", methods=["POST"])
def api_media_rate():
    return jsonify({"ok": True})


@app.route("/api/media/commit", methods=["POST"])
def api_media_commit():
    return jsonify({"ok": True, "moved": 0})


# ------------------------------------------------------------------ legacy batch compat

@app.route("/api/batches")
def api_batches():
    return jsonify([])


@app.route("/api/batches/<batch_date>/items")
def api_batch_items(batch_date):
    return jsonify([])


@app.route("/api/batches/<batch_date>/rate", methods=["POST"])
def api_batch_rate(batch_date):
    return jsonify({"ok": True})


@app.route("/api/batches/<batch_date>/commit", methods=["POST"])
def api_batch_commit(batch_date):
    return jsonify({"ok": True})


# ------------------------------------------------------------------ image review

@app.route("/api/images")
def api_images():
    return jsonify(demo_data.IMAGE_ITEMS)


@app.route("/api/images/rate", methods=["POST"])
def api_images_rate():
    return jsonify({"ok": True})


@app.route("/api/images/commit", methods=["POST"])
def api_images_commit():
    return jsonify({"ok": True, "moved": 0})


# ------------------------------------------------------------------ Stage 03/04 — Renders

@app.route("/api/renders")
def api_renders():
    return jsonify(demo_data.RENDERS_LIST)


@app.route("/api/renders/<name>")
def api_render_detail(name):
    detail = demo_data.RENDER_DETAIL.get(name)
    if not detail:
        abort(404)
    return jsonify(detail)


@app.route("/api/renders/<name>/verdict", methods=["POST"])
def api_render_verdict(name):
    return jsonify({"ok": True})


@app.route("/api/renders/<name>/verdict", methods=["DELETE"])
def api_render_verdict_delete(name):
    return jsonify({"ok": True})


@app.route("/api/renders/<name>", methods=["DELETE"])
def api_render_delete(name):
    return jsonify({"ok": True})


# ------------------------------------------------------------------ pool inventory

@app.route("/api/pool-summary")
def api_pool_summary():
    return jsonify(demo_data.POOL_SUMMARY)


# ------------------------------------------------------------------ intelligence

@app.route("/api/intelligence")
def api_intelligence():
    return jsonify(demo_data.INTELLIGENCE)


# ------------------------------------------------------------------ stats

@app.route("/api/stats")
def api_stats():
    return jsonify(demo_data.STATS)


# ------------------------------------------------------------------ schedule stub

@app.route("/api/schedule")
def api_schedule():
    return jsonify({"weeks": []})


# ------------------------------------------------------------------ media files

@app.route("/media/<path:relpath>")
def api_media(relpath):
    # Only serve from assets/ — block any path traversal
    parts = Path(relpath).parts
    if not parts or parts[0] != "assets":
        abort(404)
    file_path = BASE_DIR.joinpath(*parts)
    if not file_path.exists() or not file_path.is_file():
        abort(404)
    return send_from_directory(BASE_DIR / "assets", "/".join(parts[1:]))


# ------------------------------------------------------------------ entry point

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5500))
    app.run(host="0.0.0.0", port=port, debug=False)
