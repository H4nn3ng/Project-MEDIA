from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path
from typing import Any

from config import CAROUSEL_HEIGHT, CAROUSEL_WIDTH, POOL_DIR

logger = logging.getLogger(__name__)

# ── Style constants ────────────────────────────────────────────────────────────
_BG_DEFAULT    = (18, 18, 20)      # near-black
_TEXT_COLOR    = (240, 235, 225)   # warm white
_ACCENT_COLOR  = (180, 160, 120)   # muted gold
_FONT_LARGE    = 72
_FONT_MEDIUM   = 56
_FONT_SMALL    = 42
_PADDING       = 80
_SLIDE_ROLES   = {"hook", "setup", "mechanism", "example", "data", "takeaway", "cta"}


def _load_pil():
    from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]
    return Image, ImageDraw, ImageFont


def _load_font(ImageFont, size: int) -> Any:
    for path in [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, font: Any, max_width: int, ImageDraw_ref: Any) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        try:
            bbox = ImageDraw_ref.textbbox((0, 0), test, font=font)
            w = bbox[2] - bbox[0]
        except AttributeError:
            w = len(test) * (font.size // 2)
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _role_to_size(role: str) -> int:
    if role == "hook":
        return _FONT_LARGE
    if role in ("takeaway", "cta"):
        return _FONT_MEDIUM
    return _FONT_SMALL


def _draw_slide(
    slide_data: dict,
    slide_num:  int,
    total:      int,
    image_dir:  Path,
) -> Any:
    Image, ImageDraw, ImageFont = _load_pil()

    bg_hex   = slide_data.get("bg_color") or ""
    role     = slide_data.get("role", "body")
    text     = slide_data.get("text", "")
    asset_fn = slide_data.get("asset_file")
    attrib   = slide_data.get("attribution") or ""

    # Background
    if bg_hex and bg_hex.startswith("#"):
        try:
            r = int(bg_hex[1:3], 16)
            g = int(bg_hex[3:5], 16)
            b = int(bg_hex[5:7], 16)
            bg = (r, g, b)
        except ValueError:
            bg = _BG_DEFAULT
    else:
        bg = _BG_DEFAULT

    img  = Image.new("RGB", (CAROUSEL_WIDTH, CAROUSEL_HEIGHT), color=bg)
    draw = ImageDraw.Draw(img)

    # Optional background image (desaturated, blended)
    if asset_fn:
        asset_path = image_dir / asset_fn
        if asset_path.exists():
            try:
                bg_img = Image.open(asset_path).convert("RGB")
                bg_img = bg_img.resize((CAROUSEL_WIDTH, CAROUSEL_HEIGHT), Image.LANCZOS)
                # Darken overlay for readability
                from PIL import ImageEnhance  # type: ignore[import]
                bg_img = ImageEnhance.Brightness(bg_img).enhance(0.35)
                img.paste(bg_img, (0, 0))
                draw = ImageDraw.Draw(img)
            except Exception as exc:
                logger.warning("Could not load background image %s: %s", asset_fn, exc)

    # Slide counter pill
    font_small = _load_font(ImageFont, 32)
    counter = f"{slide_num}/{total}"
    draw.text(
        (_PADDING, _PADDING // 2),
        counter,
        font=font_small,
        fill=_ACCENT_COLOR,
    )

    # Main text
    font_main = _load_font(ImageFont, _role_to_size(role))
    max_text_w = CAROUSEL_WIDTH - 2 * _PADDING
    lines = _wrap_text(text, font_main, max_text_w, draw)

    line_h = _role_to_size(role) + 16
    total_h = len(lines) * line_h
    y_start = (CAROUSEL_HEIGHT - total_h) // 2

    # Accent bar for hook slide
    if role == "hook":
        bar_y = y_start - 32
        draw.rectangle(
            [_PADDING, bar_y, _PADDING + 60, bar_y + 6],
            fill=_ACCENT_COLOR,
        )

    for i, line in enumerate(lines):
        draw.text(
            (_PADDING, y_start + i * line_h),
            line,
            font=font_main,
            fill=_TEXT_COLOR,
        )

    # Attribution (small, bottom)
    if attrib:
        font_attrib = _load_font(ImageFont, 28)
        draw.text(
            (_PADDING, CAROUSEL_HEIGHT - _PADDING),
            attrib[:80],
            font=font_attrib,
            fill=_ACCENT_COLOR,
        )

    return img


def render_carousel(
    plan_dict: dict,
    render_dir: Path,
) -> list[Path]:
    """
    Render a carousel from a plan dict (edit_log.json format).
    Saves slide_01.png … slide_NN.png into render_dir/final/.
    Returns list of output paths.
    """
    layout = plan_dict.get("carousel_layout", {})
    slides = layout.get("slides", [])
    if not slides:
        logger.error("No slides in carousel plan")
        return []

    out_dir = render_dir / "final"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Where downloaded images live
    image_dir = POOL_DIR / "footage"

    Image, _, _ = _load_pil()
    paths: list[Path] = []

    for i, slide in enumerate(slides, start=1):
        img = _draw_slide(slide, i, len(slides), image_dir)
        out_path = out_dir / f"slide_{i:02d}.png"
        img.save(str(out_path), format="PNG")
        paths.append(out_path)
        logger.info("Carousel slide %d/%d → %s", i, len(slides), out_path.name)

    # Write caption sidecar
    caption = plan_dict.get("caption", "")
    hashtags = " ".join(f"#{t}" for t in plan_dict.get("hashtags", []))
    (render_dir / "caption.txt").write_text(
        f"{caption}\n\n{hashtags}\n",
        encoding="utf-8",
    )

    logger.info("Carousel rendered: %d slides → %s", len(paths), out_dir)
    return paths
