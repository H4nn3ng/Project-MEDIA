"""
tts.py — Kokoro voiceover generator for the healing-agent pipeline.

Import generate_voiceover() from editor_reels.py.
Voice config is always read from world_bible — never hardcoded here.
"""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
ONNX_MODEL    = DATA_DIR / "models" / "kokoro-v1.0.onnx"
VOICES_BIN    = DATA_DIR / "models" / "voices-v1.0.bin"
DEFAULT_BIBLE = DATA_DIR / "world_bible.json"

# Loaded once per session — Kokoro model stays in memory across calls
_kokoro = None


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(ONNX_MODEL), str(VOICES_BIN))
    return _kokoro


def generate_voiceover(
    text: str,
    output_path: str | Path,
    world_bible_path: str | Path | None = None,
) -> Path:
    """
    Generate a WAV voiceover from text using the approved Kokoro voice.
    Voice settings are always read from world_bible — never hardcoded.
    Returns the resolved output Path on success, raises on failure.
    """
    import soundfile as sf

    bible_path = Path(world_bible_path) if world_bible_path else DEFAULT_BIBLE
    with open(bible_path) as f:
        bible = json.load(f)

    voice_cfg = bible.get("voice", {})
    if not voice_cfg.get("approved"):
        raise ValueError(
            "No approved voice found in world_bible.json. "
            "Add a 'voice' block with 'approved': true before running."
        )

    voice_id = voice_cfg.get("voice_id", "af_nicole")
    speed    = voice_cfg.get("speed", 1.0)

    kokoro = _get_kokoro()
    samples, sample_rate = kokoro.create(text, voice=voice_id, speed=speed)

    out    = Path(output_path)
    parent = out.parent
    # Only mkdir when there is a real parent directory (not the current dir ".")
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)

    sf.write(str(out), samples, sample_rate)
    return out
