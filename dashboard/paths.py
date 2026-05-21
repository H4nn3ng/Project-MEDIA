from pathlib import Path

BASE_DIR         = Path(__file__).resolve().parents[1]   # money-psych root
DATA_DIR         = BASE_DIR / "data"

STAGING_DIR      = DATA_DIR / "scrape"       # Stage 01 — raw footage pending review
POOL_DIR         = DATA_DIR / "sort_media"   # Stage 02 — approved media pool
PENDING_DIR      = DATA_DIR / "pending"      # Stage 03 — renders awaiting verdict
READY_DIR        = DATA_DIR / "approved"     # Stage 04 — approved to post
NOT_POSTABLE_DIR = DATA_DIR / "not_approved" # Stage 04 — rejected renders
BAD_DIR          = DATA_DIR / "rejected"     # Sort media — rejected footage/audio
BIN_DIR          = DATA_DIR / "bin"          # soft-delete backup

WORLD_BIBLE_PATH = BASE_DIR / "world_bible.json"
POST_REVIEW_PATH = DATA_DIR / "post_review.json"
CORPUS_PATH      = BASE_DIR / "corpus" / "concepts.json"

# First path component of every servable URL (prevents escaping data/)
MEDIA_ROOTS = {"data"}

SCRAPE_SCRIPT = BASE_DIR / "scrape.py"
RENDER_SCRIPT = BASE_DIR / "render.py"
