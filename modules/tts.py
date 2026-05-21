from __future__ import annotations

import json
from pathlib import Path

from config import BASE_DIR, VOICE_DEFAULT_ID, VOICE_DEFAULT_SPEED

# Kokoro model files are expected next to this project root (shared from healing-agent venv)
_ONNX  = BASE_DIR / "kokoro-v1.0.onnx"
_VOICES = BASE_DIR / "voices-v1.0.bin"

# Loaded once per process — Kokoro stays in memory across calls
_kokoro = None


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro  # type: ignore[import]
        _kokoro = Kokoro(str(_ONNX), str(_VOICES))
    return _kokoro


def generate_voiceover(
    text: str,
    output_path: str | Path,
    voice_cfg: dict | None = None,
    memory_path: str | Path | None = None,
) -> Path:
    """
    Synthesise a WAV voiceover using the Kokoro ONNX model.

    Voice config is read from world_bible (memory_path) or supplied via voice_cfg.
    The voice must have approved=True in the memory store; raises ValueError otherwise.

    Returns the resolved output Path on success, raises on failure.
    """
    import soundfile as sf  # type: ignore[import]

    if voice_cfg is None:
        # Read from world_bible
        mp = Path(memory_path) if memory_path else BASE_DIR / "world_bible.json"
        if mp.exists():
            import json as _json
            voice_cfg = _json.loads(mp.read_text()).get("voice", {})
        else:
            voice_cfg = {}

    cfg = voice_cfg or {}
    if not cfg.get("approved", False):
        raise ValueError(
            "No approved voice in memory store. Set voice.approved=true in world_bible.json."
        )

    voice_id = cfg.get("voice_id", VOICE_DEFAULT_ID)
    speed    = float(cfg.get("speed", VOICE_DEFAULT_SPEED))

    kokoro = _get_kokoro()
    samples, sample_rate = kokoro.create(text, voice=voice_id, speed=speed)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), samples, sample_rate)
    return out
