from pathlib import Path

BASE_DIR         = Path(__file__).resolve().parents[1]   # healing-agent/
DATA_DIR         = BASE_DIR / "data"             # all pipeline data lives here

STAGING_DIR      = DATA_DIR / "scrape"           # Stage 01 — Scrape
POOL_DIR         = DATA_DIR / "sort_media"       # Stage 02 — Sort Media (approved pool)
PENDING_DIR      = DATA_DIR / "pending"          # Stage 03 output — pending review in Stage 04
READY_DIR        = DATA_DIR / "approved"         # Stage 04 — approved
NOT_POSTABLE_DIR = DATA_DIR / "not_approved"     # Stage 04 — not approved
BAD_DIR          = DATA_DIR / "rejected"         # Sort Media — rejected footage/audio
BIN_DIR          = DATA_DIR / "bin"              # soft-delete backup (recoverable)

WORLD_BIBLE_PATH = DATA_DIR / "world_bible.json"
POST_REVIEW_PATH = DATA_DIR / "post_review.json"

# All media lives under data/ — single allowlist entry covers all stage folders
MEDIA_ROOTS = {"data"}

AGENT_SCRIPT    = BASE_DIR / "agent.py"
CAROUSEL_SCRIPT = BASE_DIR / "editor_carousel.py"
STORY_SCRIPT    = BASE_DIR / "editor_story.py"
REEL_SCRIPT     = BASE_DIR / "editor_reels.py"
