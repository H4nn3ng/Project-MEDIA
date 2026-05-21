"""
editor_story.py — render Instagram story / single-image quote posts.

Unlike carousels, each quote post stands alone — the visual world is chosen
per-quote, not as a shared thread. Gemini still uses theme-first assignment
but treats each quote independently.

Reads:
  weekly_batch/YYYY-MM-DD/intelligence/quotes.json
  pool/images/(priority|standard)/

Output:
  renders/YYYY-MM-DD_NNN/story_quotes/story_01.jpg … story_10.jpg
  renders/YYYY-MM-DD_NNN/story_quotes/manifest.json

Usage:
  python editor_story.py              # render all quote posts from latest batch
  python editor_story.py --batch 2026-05-16
  python editor_story.py --feedback   # log story performance notes → world_bible
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
    plan_image_assignments, render_slide, render_slide_minimal,
    load_world_bible, save_world_bible,
    mark_images_used,
    STORY_W, STORY_H, BATCH_ROOT, RENDERS_DIR,
)

load_dotenv()

REQUIRED_KEYS = ["GOOGLE_CLOUD_PROJECT"]


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
# Story post renderer
# ---------------------------------------------------------------------------

def render_story_posts(
    quotes: list[str],
    images: list[dict],
    render_path: Path,
    client: genai.Client,
    quote_hashtags: list[list[str]] | None = None,
) -> list[Path]:
    """
    Render each power quote as a 9:16 story image.
    Each quote is treated as its own standalone piece — Gemini assigns
    the best image per quote rather than enforcing one shared visual world.
    """
    print(f"\n[story] Rendering {len(quotes)} quote post(s)…")

    # Treat all quotes as one batch — Gemini can vary the world per quote
    # since story posts are independent (no swipe thread connecting them)
    indices   = plan_image_assignments(
        quotes,
        "standalone one-liner story posts — each quote is independent, vary images freely",
        images,
        client,
    )
    story_dir = render_path / "story_quotes"
    outputs: list[Path] = []

    for i, (quote, img_idx) in enumerate(zip(quotes, indices)):
        img = images[img_idx]
        out = story_dir / f"story_{i + 1:02d}.jpg"
        try:
            render_slide(img["_path"], quote, out, STORY_W, STORY_H)
            print(f"  story {i + 1}/{len(quotes)}")
            print(f"    quote : {quote[:55]!r}")
            print(f"    image : [{img.get('source', '?')}] {img.get('title', '')[:40]}")
            outputs.append(out)
        except Exception as exc:
            print(f"  ERROR story {i + 1}: {exc}")

    if outputs:
        manifest = [
            {
                "post":  i + 1,
                "quote": quotes[i],
                "image": images[indices[i]].get("title", ""),
                "file":  f"story_{i + 1:02d}.jpg",
            }
            for i in range(len(quotes))
        ]
        story_dir.mkdir(parents=True, exist_ok=True)
        (story_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False)
        )

        # Individual caption.txt per post — quote body + Gemini hashtags (world_bible fallback)
        _ht = quote_hashtags or []
        bible    = load_world_bible()
        keywords = bible.get("high_performing_keywords", [])
        fallback_hashtags = " ".join(f"#{kw.replace(' ', '').lower()}" for kw in keywords[:12] if kw)
        for j, entry in enumerate(manifest):
            if j < len(_ht) and _ht[j]:
                hashtags = " ".join(_ht[j])
            else:
                hashtags = fallback_hashtags
            fname   = entry["file"].replace(".jpg", "_caption.txt")
            caption = f"{entry['quote']}\n\n{hashtags}\n"
            (story_dir / fname).write_text(caption)

        mark_images_used(images, indices)
        print(f"  → {story_dir}/")
    return outputs


# ---------------------------------------------------------------------------
# Minimal renderer — cream background, no pool images required
# ---------------------------------------------------------------------------

def render_minimal_stories(
    quotes: list[str],
    render_path: Path,
    quote_hashtags: list[list[str]] | None = None,
) -> list[Path]:
    """
    Render each quote on a warm cream canvas (#F4EDE0).
    No pool images, no Gemini call — runs in seconds.
    Use for daily between-posts Stories or account warm-up.
    """
    print(f"\n[story] Rendering {len(quotes)} minimal (cream) story post(s)…")
    story_dir = render_path / "story_minimal"
    outputs: list[Path] = []

    for i, quote in enumerate(quotes):
        out = story_dir / f"story_minimal_{i + 1:02d}.jpg"
        try:
            render_slide_minimal(quote, out, STORY_W, STORY_H)
            print(f"  story {i + 1}/{len(quotes)}: {quote[:55]!r}")
            outputs.append(out)
        except Exception as exc:
            print(f"  ERROR story {i + 1}: {exc}")

    if outputs:
        manifest = [
            {"post": i + 1, "quote": quotes[i], "file": f"story_minimal_{i + 1:02d}.jpg"}
            for i in range(len(quotes))
        ]
        story_dir.mkdir(parents=True, exist_ok=True)
        (story_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False)
        )

        # Individual caption.txt per post — quote body + Gemini hashtags (world_bible fallback)
        _ht = quote_hashtags or []
        bible    = load_world_bible()
        keywords = bible.get("high_performing_keywords", [])
        fallback_hashtags = " ".join(f"#{kw.replace(' ', '').lower()}" for kw in keywords[:12] if kw)
        for j, entry in enumerate(manifest):
            if j < len(_ht) and _ht[j]:
                hashtags = " ".join(_ht[j])
            else:
                hashtags = fallback_hashtags
            fname   = entry["file"].replace(".jpg", "_caption.txt")
            caption = f"{entry['quote']}\n\n{hashtags}\n"
            (story_dir / fname).write_text(caption)

        print(f"  → {story_dir}/")
    return outputs


# ---------------------------------------------------------------------------
# Feedback / learnings
# ---------------------------------------------------------------------------

def run_feedback(render_date: str | None) -> None:
    """Log story post performance notes into world_bible['story_learnings']."""
    renders = sorted(p for p in RENDERS_DIR.iterdir() if p.is_dir()) if RENDERS_DIR.exists() else []
    candidates = [
        p for p in renders
        if (render_date is None or render_date in p.name)
        and (p / "story_quotes" / "manifest.json").exists()
    ]
    if not candidates:
        print("[story] No story render found for feedback.")
        return
    render_path   = candidates[-1]
    manifest_file = render_path / "story_quotes" / "manifest.json"
    manifest      = json.loads(manifest_file.read_text())

    print(f"\n=== Story feedback — {render_path.name} ===")
    print("For each quote post, note performance (Enter to skip).\n")

    bible     = load_world_bible()
    learnings = bible.setdefault("story_learnings", [])

    for item in manifest:
        print(f"  story_{item['post']:02d}: {item['quote']!r}")
        note = input("  Note (Enter to skip): ").strip()
        if note:
            learnings.append({
                "date":  render_path.name,
                "post":  item["post"],
                "quote": item["quote"],
                "note":  note,
            })

    save_world_bible(bible)
    print(f"[story] {len(learnings)} story learning(s) saved to world_bible.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render healing story / single-image quote posts."
    )
    parser.add_argument("--batch",    help="Batch date YYYY-MM-DD (default: most recent)")
    parser.add_argument("--minimal",  action="store_true",
                        help="Cream-background quotes — no pool images needed (daily Story format)")
    parser.add_argument("--feedback", action="store_true",
                        help="Log story performance notes into world_bible")
    parser.add_argument("--render",   help="Render folder for --feedback (default: most recent)")
    args = parser.parse_args()

    validate_env()

    if args.feedback:
        run_feedback(args.render)
        return

    batch_path = find_latest_batch()

    content        = load_content_data(batch_path)
    quotes         = content.get("quotes", [])
    quote_hashtags = content.get("quote_hashtags", [])

    if not quotes:
        print("[story] No quotes found for this batch.")
        ans = input("  Generate quotes now? Takes ~2 min (y/N): ").strip().lower()
        if ans != "y":
            print("  Run:  python agent.py --quick")
            return
        agent_path = Path(__file__).parent / "agent.py"
        result = subprocess.run([sys.executable, str(agent_path), "--quick"])
        if result.returncode != 0:
            print("[story] Quick run failed — check the output above.")
            return
        content = load_content_data(batch_path)
        quotes  = content.get("quotes", [])
        if not quotes:
            print("[story] Still no quotes after quick run — check agent.py output.")
            return

    # --minimal: cream canvas, no pool images, no Gemini call
    if args.minimal:
        render_path = make_render_path("story_minimal")
        print(f"\n=== Editor Story (minimal) — context: {batch_path.name} ===")
        print(f"  {len(quotes)} quote(s) — cream background, no pool images needed\n")
        outputs = render_minimal_stories(quotes, render_path, quote_hashtags)
        print(f"\n{'─' * 52}")
        print(f"  Rendered  : {len(outputs)} minimal story post(s)")
        print(f"  Output    : {render_path}/story_minimal/")
        print(f"{'─' * 52}")
        print("\nReady to post — no further steps needed.")
        return

    # Default: photo-backed stories (needs pool images + Gemini image assignment)
    client = _init_gemini()
    images = load_pool_images()

    print(f"\n=== Editor Story — context: {batch_path.name} ===")
    print(f"  {len(quotes)} quote(s) · {len(images)} approved image(s) in pool\n")

    if not images:
        print("[story] No images in pool — run agent.py then: python feedback.py")
        print("        Or use --minimal for cream-background posts (no images needed)")
        return

    render_path = make_render_path("story")
    outputs     = render_story_posts(quotes, images, render_path, client, quote_hashtags)

    print(f"\n{'─' * 52}")
    print(f"  Rendered  : {len(outputs)} story post(s)")
    print(f"  Output    : {render_path}/story_quotes/")
    print(f"{'─' * 52}")
    print("\nReview the posts, then run:")
    print("  python editor_story.py --feedback")
    print("to log performance notes.\n")


if __name__ == "__main__":
    main()
