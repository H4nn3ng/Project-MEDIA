from __future__ import annotations

import json
import logging
import math
import subprocess
from functools import lru_cache
from pathlib import Path

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    VideoFileClip,
    concatenate_videoclips,
)
from moviepy.audio.fx import AudioFadeOut, AudioLoop, MultiplyVolume
from moviepy.video.fx import Resize

from config import (
    POOL_AUDIO,
    POOL_FOOTAGE,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_LOUDNESS_LUFS,
    VIDEO_WIDTH,
)
from modules.tts import generate_voiceover

logger = logging.getLogger(__name__)

_FFMPEG = "ffmpeg"

# ── Caption constants (matches healing-agent style) ────────────────────────────
_CAPTION_FONT       = "/usr/share/fonts/opentype/linux-libertine/LinLibertine_RB.otf"
_CAPTION_SIZE       = 82
_CAPTION_MIN_SIZE   = 44
_CAPTION_MARGIN     = 80    # px each side
_CAPTION_BORDER_W   = 3
_CAPTION_WIDTH_SAFETY  = 12
_CAPTION_MAX_WORDS  = 5     # per phrase — keeps lines short on mobile
_FADE_IN_DUR        = 0.18  # seconds — soft fade per word
_CAPTION_LEAD_IN_S  = 0.08  # start captions 80ms before the spoken word
# Y baseline for captions: lower quarter of screen
_CAPTION_BASELINE_Y = int(VIDEO_HEIGHT * 0.78)

_BAD_END_WORDS = {
    "a", "an", "and", "as", "at", "but", "for", "from", "if", "in",
    "into", "of", "on", "or", "so", "that", "the", "to", "when",
    "while", "with", "your", "you",
}


# ── PIL font helpers ───────────────────────────────────────────────────────────

@lru_cache(maxsize=32)
def _pil_font(size: int):
    from PIL import ImageFont
    return ImageFont.truetype(_CAPTION_FONT, size)


@lru_cache(maxsize=4096)
def _text_advance(text: str, size: int = _CAPTION_SIZE) -> float:
    try:
        return float(_pil_font(size).getlength(text))
    except Exception:
        return float(len(text) * size * 0.36)


@lru_cache(maxsize=4096)
def _text_bbox_width(text: str, size: int = _CAPTION_SIZE) -> tuple[int, int]:
    """Return (visual_width, left_bearing)."""
    try:
        font = _pil_font(size)
        left, _, right, _ = font.getbbox(text)
        return max(0, int(math.ceil(right - left))), int(math.floor(left))
    except Exception:
        return int(math.ceil(len(text) * size * 0.36)), 0


def _usable_width() -> int:
    return VIDEO_WIDTH - 2 * (_CAPTION_MARGIN + _CAPTION_BORDER_W + _CAPTION_WIDTH_SAFETY)


def _phrase_text(words: list[tuple[str, float, float]]) -> str:
    return " ".join(w for w, _, _ in words)


def _fitted_size(words: list[tuple[str, float, float]]) -> int:
    """Largest font size that fits the phrase in the usable width."""
    text = _phrase_text(words)
    visual_w, _ = _text_bbox_width(text, _CAPTION_SIZE)
    usable = _usable_width()
    if visual_w <= usable:
        return _CAPTION_SIZE
    size = int((_CAPTION_SIZE * usable) / max(1, visual_w))
    if size % 2:
        size -= 1
    return max(_CAPTION_MIN_SIZE, size)


# ── Phrase grouping ────────────────────────────────────────────────────────────

def _group_phrases(
    words: list[tuple[str, float, float]],
) -> list[tuple[list[tuple[str, float, float]], float]]:
    """
    Group timed words into display phrases.
    Each phrase is (word_list, phrase_t_out) where phrase_t_out = end time.
    """
    if not words:
        return []

    phrases: list[tuple[list[tuple[str, float, float]], float]] = []
    current: list[tuple[str, float, float]] = []
    total_end = words[-1][2]

    for i, (word, start, end) in enumerate(words):
        current.append((word, start, end))
        is_last = (i == len(words) - 1)
        word_lower = word.strip(".,!?;:").lower()

        # Flush if: max words reached, or last word, or natural break
        if len(current) >= _CAPTION_MAX_WORDS or is_last:
            # Avoid ending on weak words unless forced
            if word_lower in _BAD_END_WORDS and not is_last and len(current) < _CAPTION_MAX_WORDS:
                continue
            # phrase_t_out = start of next word or total duration
            if is_last:
                t_out = total_end + 0.3
            else:
                t_out = words[i + 1][1]
            phrases.append((list(current), t_out))
            current = []

    if current:
        phrases.append((current, total_end + 0.3))

    return phrases


def _shared_font_size(
    phrases: list[tuple[list[tuple[str, float, float]], float]],
) -> int:
    """Single font size for all phrases — smallest needed by any phrase."""
    if not phrases:
        return _CAPTION_SIZE
    sizes = [_fitted_size(words) for words, _ in phrases]
    return min(sizes)


# ── drawtext filter building ───────────────────────────────────────────────────

def _filter_escape(text: str) -> str:
    """Escape special characters for ffmpeg drawtext textfile content."""
    return text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def _build_drawtext_filters(
    phrases: list[tuple[list[tuple[str, float, float]], float]],
    caption_text_dir: Path,
    font_size: int,
) -> list[str]:
    """
    Build one drawtext filter string per word.
    Words fade in when spoken and hold until the phrase ends.
    All words in a phrase share the same y baseline for clean alignment.
    """
    filters: list[str] = []
    y_expr = f"{_CAPTION_BASELINE_Y}-ascent"

    for pi, (word_list, phrase_t_out) in enumerate(phrases):
        full_text = _phrase_text(word_list)
        visual_w, left_bearing = _text_bbox_width(full_text, font_size)
        # Left edge so the phrase is horizontally centered
        left_edge = (VIDEO_WIDTH - visual_w) / 2 - left_bearing

        # Accumulate x cursor across words
        x_cursor = left_edge
        for wi, (word, w_start, _) in enumerate(word_list):
            word_in = max(0.0, w_start - _CAPTION_LEAD_IN_S)

            alpha_expr = (
                f"if(lt(t,{word_in + _FADE_IN_DUR:.3f}),"
                f"max(0,(t-{word_in:.3f})/{_FADE_IN_DUR:.3f}),"
                "1)"
            )

            text_file = caption_text_dir / f"p{pi:03d}_w{wi:02d}.txt"
            text_file.write_text(word, encoding="utf-8")

            filters.append(
                f"drawtext=fontfile='{_CAPTION_FONT}'"
                f":textfile='{text_file}'"
                f":fontsize={font_size}"
                f":fontcolor=white:bordercolor=black:borderw={_CAPTION_BORDER_W}"
                f":x={x_cursor:.2f}:y={y_expr}"
                f":enable='between(t,{word_in:.3f},{phrase_t_out:.3f})'"
                f":alpha='{alpha_expr}'"
            )

            # Advance x by this word + space
            word_advance = _text_advance(word + " ", font_size)
            x_cursor += word_advance

    return filters


# ── Whisper word alignment ─────────────────────────────────────────────────────

def _whisper_words(vo_path: Path) -> list[tuple[str, float, float]]:
    """
    Run Whisper on a WAV to get word-level timestamps.
    Returns [(word, start_s, end_s), ...].
    Uses 'tiny' model for speed — accurate enough for caption sync.
    """
    import whisper  # type: ignore[import]
    logger.info("Running Whisper word alignment…")
    model = whisper.load_model("tiny")
    result = model.transcribe(
        str(vo_path),
        word_timestamps=True,
        language="en",
        fp16=False,
    )
    words: list[tuple[str, float, float]] = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            text = w.get("word", "").strip()
            if text:
                words.append((text, float(w["start"]), float(w["end"])))
    logger.info("Whisper aligned %d words", len(words))
    return words


# ── Asset resolution ───────────────────────────────────────────────────────────

def _resolve_asset(asset_id: str) -> Path | None:
    """Find asset file in pool/footage/ or pool/audio/ by ID."""
    for search_dir in (POOL_FOOTAGE, POOL_AUDIO):
        if not search_dir.exists():
            continue
        for f in search_dir.glob(f"{asset_id}.*"):
            if not f.name.endswith("_metadata.json"):
                return f
    return None


# ── ffprobe duration ───────────────────────────────────────────────────────────

def _get_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            for stream in json.loads(result.stdout).get("streams", []):
                if stream.get("codec_type") in ("video", "audio"):
                    dur = stream.get("duration")
                    if dur:
                        return float(dur)
    except Exception as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
    return None


# ── Loudness normalisation ─────────────────────────────────────────────────────

def _normalize_loudness(input_path: Path, output_path: Path, target_lufs: float) -> Path:
    cmd1 = [
        _FFMPEG, "-y", "-i", str(input_path),
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    try:
        r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=120)
        raw = r1.stderr
        start = raw.rfind("{")
        end   = raw.rfind("}") + 1
        stats = json.loads(raw[start:end]) if start >= 0 and end > start else {}
    except Exception as exc:
        logger.warning("loudnorm pass 1 failed: %s", exc)
        stats = {}

    af = (
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
        f":measured_I={stats.get('input_i', '-99')}"
        f":measured_TP={stats.get('input_tp', '-99')}"
        f":measured_LRA={stats.get('input_lra', '0')}"
        f":measured_thresh={stats.get('input_thresh', '-99')}"
        f":linear=true:print_format=none"
    )
    cmd2 = [_FFMPEG, "-y", "-i", str(input_path), "-af", af, str(output_path)]
    try:
        subprocess.run(cmd2, check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        logger.error("loudnorm pass 2 failed: %s", exc.stderr)
        raise
    return output_path


# ── Caption burn via ffmpeg ────────────────────────────────────────────────────

def _burn_captions(
    assembly_path: Path,
    vo_path: Path,
    render_dir: Path,
    target_lufs: float,
) -> Path:
    """
    Whisper-align the voiceover, build per-word drawtext filters,
    burn into the assembly with loudnorm in a single ffmpeg pass.
    Returns path to final.mp4.
    """
    caption_dir = render_dir / "caption_text"
    caption_dir.mkdir(exist_ok=True)
    final_dir = render_dir / "final"
    final_dir.mkdir(exist_ok=True)
    final_path = final_dir / "final.mp4"

    words = _whisper_words(vo_path)
    phrases = _group_phrases(words)
    font_size = _shared_font_size(phrases)
    logger.info("Caption font size: %dpx, %d phrases", font_size, len(phrases))

    dt_filters = _build_drawtext_filters(phrases, caption_dir, font_size)

    # Probe total duration for audio fade
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
         str(assembly_path)],
        capture_output=True, text=True,
    )
    try:
        total_s = float(json.loads(probe.stdout)["format"]["duration"])
        last_word_end = max((w[2] for ph, _ in phrases for w in ph), default=0.0)
        has_tail = total_s - last_word_end >= 5.5
        af = f"loudnorm=I={target_lufs}:LRA=11:TP=-1.5"
        if has_tail:
            fade_start = max(0.0, total_s - 2.0)
            af += f",afade=type=out:start_time={fade_start:.3f}:duration=2.0"
    except Exception:
        af = f"loudnorm=I={target_lufs}:LRA=11:TP=-1.5"

    vf = ",".join(dt_filters) if dt_filters else "null"

    cmd = [
        _FFMPEG, "-y",
        "-i", str(assembly_path),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-r", str(VIDEO_FPS),
        str(final_path),
    ]

    if dt_filters:
        logger.info("Caption sample filter: %s", dt_filters[0][:140])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    dt_warnings = [l for l in result.stderr.splitlines()
                   if any(k in l.lower() for k in ("drawtext", "font", "alpha", "parse error"))]
    for line in dt_warnings[:4]:
        logger.warning("ffmpeg drawtext: %s", line)

    if result.returncode != 0:
        logger.error("ffmpeg caption burn failed:\n%s", result.stderr[-1500:])
        raise RuntimeError("ffmpeg caption burn failed")

    return final_path


# ── Main render entry point ────────────────────────────────────────────────────

def render_reel(
    plan_dict:  dict,
    render_dir: Path,
    voice_cfg:  dict | None = None,
) -> Path:
    """
    Full reel render pipeline:
      1. Kokoro TTS → voiceover.wav
      2. Whisper word alignment
      3. MoviePy: footage loop + music mix → assembly.mp4
      4. ffmpeg: burn word-by-word captions + loudnorm → final/final.mp4

    Returns path to final.mp4.
    """
    render_dir.mkdir(parents=True, exist_ok=True)
    vo_dir = render_dir / "voiceovers"
    vo_dir.mkdir(exist_ok=True)

    narrative = plan_dict.get("narrative", "")
    caption   = plan_dict.get("caption", "")
    hashtags  = plan_dict.get("hashtags", [])

    # Resolve footage and music from items list
    footage_path: Path | None = None
    music_path:   Path | None = None
    for item in plan_dict.get("items", []):
        reasoning = (item.get("reasoning") or "").lower()
        file_id   = item.get("file", "")
        if not file_id:
            continue
        resolved = _resolve_asset(file_id)
        if resolved is None:
            logger.warning("Asset not found in downloads: %s", file_id)
            continue
        if "footage" in reasoning or "video" in reasoning:
            footage_path = resolved
        elif "music" in reasoning or "audio" in reasoning or "bed" in reasoning:
            music_path = resolved

    # ── 1. Voiceover ─────────────────────────────────────────────────────────
    vo_path = vo_dir / "voiceover.wav"
    logger.info("Generating voiceover (%d chars)…", len(narrative))
    generate_voiceover(narrative, vo_path, voice_cfg=voice_cfg)

    vo_clip = AudioFileClip(str(vo_path))
    vo_dur  = vo_clip.duration
    logger.info("Voiceover duration: %.2fs", vo_dur)

    # ── 2. Footage ────────────────────────────────────────────────────────────
    if footage_path and footage_path.exists():
        video_clip = VideoFileClip(str(footage_path)).without_audio()
        video_clip = video_clip.with_effects([Resize((VIDEO_WIDTH, VIDEO_HEIGHT))])
        if video_clip.duration < vo_dur:
            n_loops = int(vo_dur / video_clip.duration) + 1
            video_clip = concatenate_videoclips([video_clip] * n_loops)
        video_clip = video_clip.subclipped(0, vo_dur)
    else:
        video_clip = ColorClip((VIDEO_WIDTH, VIDEO_HEIGHT), color=(0, 0, 0), duration=vo_dur)
        logger.warning("No footage found — using black background")

    video_clip = video_clip.with_fps(VIDEO_FPS)

    # ── 3. Audio mix ──────────────────────────────────────────────────────────
    audio_tracks = [vo_clip.with_effects([MultiplyVolume(1.0)])]

    if music_path and music_path.exists():
        music_clip = AudioFileClip(str(music_path))
        music_clip = music_clip.with_effects([MultiplyVolume(0.18)])
        if music_clip.duration < vo_dur:
            music_clip = music_clip.with_effects([AudioLoop(duration=vo_dur)])
        music_clip = music_clip.subclipped(0, vo_dur)
        music_clip = music_clip.with_effects([AudioFadeOut(2.0)])
        audio_tracks.append(music_clip)

    composite_audio = CompositeAudioClip(audio_tracks)
    final_clip = video_clip.with_audio(composite_audio)

    # ── 4. Export assembly ────────────────────────────────────────────────────
    assembly_path = render_dir / "assembly.mp4"
    final_clip.write_videofile(
        str(assembly_path),
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(render_dir / "temp_audio.m4a"),
        remove_temp=True,
        logger=None,
    )
    final_clip.close()

    # ── 5. Burn captions + loudnorm ───────────────────────────────────────────
    final_path = _burn_captions(assembly_path, vo_path, render_dir, VIDEO_LOUDNESS_LUFS)
    assembly_path.unlink(missing_ok=True)

    # ── 6. Test still ─────────────────────────────────────────────────────────
    try:
        subprocess.run(
            [_FFMPEG, "-y", "-i", str(final_path), "-vframes", "1",
             "-q:v", "2", str(render_dir / "test_frame.png")],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass

    # ── 7. Sidecars ───────────────────────────────────────────────────────────
    hashtag_str = " ".join(f"#{t}" for t in hashtags)
    (render_dir / "caption.txt").write_text(f"{caption}\n\n{hashtag_str}\n", encoding="utf-8")
    (render_dir / "script.txt").write_text(narrative, encoding="utf-8")

    logger.info("Reel rendered → %s", final_path)
    return final_path
