"""
media.py — secure local file serving.

Only files within BASE_DIR under an explicit root allowlist can be served.
Prevents path traversal and accidental exposure of secrets / config files.
"""

from pathlib import Path
from flask import send_file, abort

from .paths import BASE_DIR, MEDIA_ROOTS


def serve(relpath: str):
    try:
        target = (BASE_DIR / relpath).resolve()
    except Exception:
        abort(400)

    try:
        target.relative_to(BASE_DIR)
    except ValueError:
        abort(403)

    try:
        first = Path(relpath).parts[0]
    except (IndexError, TypeError):
        abort(403)
    if first not in MEDIA_ROOTS:
        abort(403)

    if not target.is_file():
        abort(404)

    return send_file(str(target), conditional=True)
