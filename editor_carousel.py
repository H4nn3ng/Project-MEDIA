"""
editor_carousel.py — render Instagram carousel posts from agent.py's carousel scripts.

Reads:
  weekly_batch/YYYY-MM-DD/intelligence/carousel_scripts.json
  pool/images/(priority|standard)/

Output:
  renders/YYYY-MM-DD_NNN/carousel_01/slide_01.jpg … slide_07.jpg + slide_08.jpg (closing)
  renders/YYYY-MM-DD_NNN/carousel_01/manifest.json

Usage:
  python editor_carousel.py                    # render all carousels from latest batch
  python editor_carousel.py --batch 2026-05-16 # render from a specific batch
  python editor_carousel.py --feedback         # log carousel notes → world_bible
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from google import genai
from dotenv import load_dotenv

from editor_shared import (
    find_latest_batch, make_render_path,
    load_pool_images, load_content_data,
    plan_image_assignments, render_slide, render_closing_slide,
    load_world_bible, save_world_bible,
    mark_images_used,
    CAROUSEL_W, CAROUSEL_H, BATCH_ROOT, RENDERS_DIR,
)

load_dotenv()

REQUIRED_KEYS  = ["GOOGLE_CLOUD_PROJECT"]
MAX_SLIDES     = 7
CHANNEL_HANDLE = "@healingwithin.ig"  # update to your actual handle


def get_secret(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(f"Required secret '{key}' is missing")
    return value


def validate_env() -> None:
    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k)]
    if missing:
        for k in missing:
            print(f"[env] Missing: {k}")
        sys.exit(1)


def _init_gemini() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=get_secret("GOOGLE_CLOUD_PROJECT"),
        location="us-central1",
    )


# ---------------------------------------------------------------------------
# Carousel renderer
# ---------------------------------------------------------------------------

def render_carousel(
    carousel: dict,
    images: list[dict],
    render_path: Path,
    carousel_idx: int,
    client: genai.Client,
) -> list[Path]:
    """
    Render one carousel (up to MAX_SLIDES) as numbered JPEG files.
    Trims to MAX_SLIDES keeping hook (slide 1) and resolution (last slide).
    Gemini assigns a visual world + image per slide; PIL renders text on image.
    """
    slides = carousel.get("slides", [])
    theme  = carousel.get("theme", f"carousel_{carousel_idx}")

    if len(slides) > MAX_SLIDES:
        slides = slides[:MAX_SLIDES - 1] + [slides[-1]]

    print(f"\n[carousel] #{carousel_idx}: {len(slides)} slides — {theme[:55]!r}")

    if not images:
        print("[carousel] No images in pool — run feedback.py first.")
        return []
    if len(images) == 1:
        print(f"  NOTE: only 1 image in pool — it repeats across all {len(slides)} slides")

    indices      = plan_image_assignments(slides, theme, images, client)
    carousel_dir = render_path / f"carousel_{carousel_idx:02d}"
    outputs: list[Path] = []

    for i, (slide_text, img_idx) in enumerate(zip(slides, indices)):
        img = images[img_idx]
        out = carousel_dir / f"slide_{i + 1:02d}.jpg"
        try:
            render_slide(img["_path"], slide_text, out, CAROUSEL_W, CAROUSEL_H)
            print(f"  slide {i + 1}/{len(slides)}: [{img.get('source', '?')}] {img.get('title', '')[:40]}")
            outputs.append(out)
        except Exception as exc:
            print(f"  ERROR slide {i + 1}: {exc}")

    if outputs:
        # Closing slide sits outside MAX_SLIDES — Instagram allows up to 10
        closing_idx  = len(slides) + 1
        closing_path = carousel_dir / f"slide_{closing_idx:02d}.jpg"
        try:
            render_closing_slide(CHANNEL_HANDLE, closing_path, CAROUSEL_W, CAROUSEL_H)
            outputs.append(closing_path)
            print(f"  slide {closing_idx}/{closing_idx} (closing): 'Save this…' + {CHANNEL_HANDLE}")
        except Exception as exc:
            print(f"  WARNING: closing slide failed: {exc}")

        manifest = [
            {
                "slide": i + 1,
                "text":  slides[i],
                "image": images[indices[i]].get("title", ""),
                "file":  f"slide_{i + 1:02d}.jpg",
            }
            for i in range(len(slides))
        ]
        if closing_path.exists():
            manifest.append({
                "slide": closing_idx,
                "text":  "Save this for when you need it.",
                "image": "",
                "file":  closing_path.name,
            })
        (carousel_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False)
        )

        # caption.txt — Gemini-written caption preferred; world_bible keywords as last resort
        gemini_caption = carousel.get("instagram_caption", "")
        if gemini_caption:
            caption = gemini_caption + "\n"
        else:
            bible    = load_world_bible()
            keywords = bible.get("high_performing_keywords", [])
            hashtags = " ".join(f"#{kw.replace(' ', '').lower()}" for kw in keywords[:12] if kw)
            caption  = f"{theme}\n\nSave this for when you need it 🌿\n\n{hashtags}\n"
        (carousel_dir / "caption.txt").write_text(caption)

        mark_images_used(images, indices)
        print(f"  → {carousel_dir}/")
    return outputs


# ---------------------------------------------------------------------------
# Feedback / learnings
# ---------------------------------------------------------------------------

def run_feedback(render_date: str | None) -> None:
    """Log carousel performance notes into world_bible['carousel_learnings']."""
    renders = sorted(p for p in RENDERS_DIR.iterdir() if p.is_dir()) if RENDERS_DIR.exists() else []
    candidates = [
        p for p in renders
        if (render_date is None or render_date in p.name)
        and any(p.glob("carousel_*/manifest.json"))
    ]
    if not candidates:
        print("[carousel] No carousel render found for feedback.")
        return
    render_path = candidates[-1]

    print(f"\n=== Carousel feedback — {render_path.name} ===")
    print("For each carousel, note what worked or didn't (Enter to skip).\n")

    bible     = load_world_bible()
    learnings = bible.setdefault("carousel_learnings", [])

    for manifest_file in sorted(render_path.glob("carousel_*/manifest.json")):
        c_name   = manifest_file.parent.name
        manifest = json.loads(manifest_file.read_text())
        theme    = manifest[0].get("text", "")[:60] if manifest else c_name
        print(f"  {c_name}: {theme!r}")
        note = input("  Note (Enter to skip): ").strip()
        if note:
            learnings.append({
                "date":     render_path.name,
                "carousel": c_name,
                "note":     note,
            })

    save_world_bible(bible)
    print(f"[carousel] {len(learnings)} carousel learning(s) saved to world_bible.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Render healing carousel posts.")
    parser.add_argument("--batch",    help="Batch date YYYY-MM-DD (default: most recent)")
    parser.add_argument("--feedback", action="store_true",
                        help="Log carousel performance notes into world_bible")
    parser.add_argument("--render",   help="Render folder for --feedback (default: most recent)")
    args = parser.parse_args()

    validate_env()

    if args.feedback:
        run_feedback(args.render)
        return

    client = _init_gemini()

    batch_path = find_latest_batch()

    content   = load_content_data(batch_path)
    images    = load_pool_images()
    carousels = content.get("carousels", [])

    print(f"\n=== Editor Carousel — context: {batch_path.name} ===")
    print(f"  {len(carousels)} carousel script(s) · {len(images)} approved image(s) in pool\n")

    if not carousels:
        print("[carousel] No carousel scripts found for this batch.")
        ans = input("  Generate scripts now? Takes ~2 min (y/N): ").strip().lower()
        if ans != "y":
            print("  Run:  python agent.py --quick")
            return
        agent_path = Path(__file__).parent / "agent.py"
        result = subprocess.run([sys.executable, str(agent_path), "--quick"])
        if result.returncode != 0:
            print("[carousel] Quick run failed — check the output above.")
            return
        content   = load_content_data(batch_path)
        carousels = content.get("carousels", [])
        if not carousels:
            print("[carousel] Still no scripts after quick run — check agent.py output.")
            return
    if not images:
        print("[carousel] No images in pool — run agent.py then: python feedback.py")
        return

    render_path  = make_render_path("carousel")
    all_outputs: list[Path] = []

    for i, carousel in enumerate(carousels, 1):
        outputs = render_carousel(carousel, images, render_path, i, client)
        all_outputs.extend(outputs)

    print(f"\n{'─' * 52}")
    print(f"  Rendered  : {len(all_outputs)} slide(s) across {len(carousels)} carousel(s)")
    print(f"  Output    : {render_path}/")
    print(f"{'─' * 52}")
    print("\nReview the slides, then run:")
    print("  python editor_carousel.py --feedback")
    print("to log notes and improve future carousels.\n")


if __name__ == "__main__":
    main()
