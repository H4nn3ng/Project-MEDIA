from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent.resolve()
DATA_DIR       = BASE_DIR / "data"

CORPUS_PATH       = BASE_DIR / "corpus" / "concepts.json"
MEMORY_PATH       = BASE_DIR / "world_bible.json"

# Stage 01 — raw downloads (footage pending human review)
STAGING_DIR       = DATA_DIR / "scrape"
BATCH_ROOT        = STAGING_DIR          # backward-compat alias

# Stage 02 — approved media pool
POOL_DIR          = DATA_DIR / "sort_media"
POOL_FOOTAGE      = POOL_DIR / "footage"
POOL_FOOTAGE_USED = POOL_DIR / "footage" / "used"
POOL_AUDIO        = POOL_DIR / "audio"
POOL_IMAGES       = POOL_DIR / "images"

# Stage 03 — renders awaiting verdict
RENDERS_DIR       = DATA_DIR / "pending"

# Stage 04 — approved / rejected renders
APPROVED_DIR      = DATA_DIR / "approved"
NOT_POSTABLE_DIR  = DATA_DIR / "not_approved"
REJECTED_DIR      = DATA_DIR / "rejected"  # bad footage/audio from sort media

# ─── AI Models (Gemini) ───────────────────────────────────────────────────────
GEMINI_PRO          = "gemini-2.5-pro"
GEMINI_FLASH        = "gemini-2.5-flash"
GEMINI_FLASH_LITE   = "gemini-2.5-flash-lite"

# ─── Run Schedule ─────────────────────────────────────────────────────────────
RUN_CADENCE              = "Mon, Thu"
ARTIFACTS_MON            = 4
ARTIFACTS_THU            = 3
CANDIDATE_POOL_TARGET    = 25

# ─── Scoring Thresholds ───────────────────────────────────────────────────────
SCORED_POOL_THRESHOLD    = 7.5
FACTUAL_SAFETY_GATE      = 8.0
STANDARD_POOL_THRESHOLD  = 6.0

# ─── Reel Format ──────────────────────────────────────────────────────────────
VIDEO_WIDTH             = 1080
VIDEO_HEIGHT            = 1920
VIDEO_FPS               = 24
VIDEO_LOUDNESS_LUFS     = -14
VIDEO_LENGTH_TARGET_S   = 40
VIDEO_LENGTH_MIN_S      = 30
VIDEO_LENGTH_MAX_S      = 55
HOOK_MAX_WORDS          = 12

# ─── Carousel Format ──────────────────────────────────────────────────────────
CAROUSEL_WIDTH          = 1080
CAROUSEL_HEIGHT         = 1350
CAROUSEL_SLIDE_COUNT    = 7
CAROUSEL_SLIDE_MIN      = 6
CAROUSEL_SLIDE_MAX      = 10

# ─── Concept Lifecycle ────────────────────────────────────────────────────────
CONCEPT_RETIREMENT_DAYS     = 90
CONCEPT_FORMATS_PER_LIFE    = 2
CORPUS_QUARANTINE_STRIKES   = 2

# ─── RSS ──────────────────────────────────────────────────────────────────────
RSS_LOOKBACK_DAYS = 7
RSS_FEEDS = [
    "https://collabfund.com/blog/feed/",
    "https://ofdollarsanddata.com/feed/",
    "https://awealthofcommonsense.com/feed/",
    "https://klementoninvesting.substack.com/feed",
    "https://behavioralscientist.org/feed/",
    "https://fs.blog/feed/",
    "https://www.behavioraleconomics.com/feed/",
    "https://www.decisionsciencenews.com/feed/",
    "https://econlog.econlib.org/index.xml",
    "https://www.apa.org/rss/journals/cfp",
]

# ─── Reddit ───────────────────────────────────────────────────────────────────
REDDIT_POSTS_PER_SUBREDDIT = 50
REDDIT_SUBREDDITS = [
    "personalfinance",
    "Frugal",
    "povertyfinance",
    "financialindependence",
    "MiddleClassFinance",
]
REDDIT_FILTER_PATTERNS = [
    r"I just realized",
    r"Why do I always",
    r"I can't stop",
    r"embarrassed to admit",
    r"ashamed that",
    r"didn't realize until",
    r"wish I.?d known",
]
REDDIT_HARD_EXCLUDE_FLAIR                   = {"advice", "help"}
REDDIT_HARD_EXCLUDE_TITLE                   = {r"should I", r"is it ok", r"how do I"}
REDDIT_HARD_EXCLUDE_BODY_MIN_CURRENCY_NUMS  = 3

# ─── Visual Asset Vocabulary ──────────────────────────────────────────────────
VISUAL_SEARCH_VOCAB = [
    "shopping cart", "checkout phone", "café laptop",
    "subway commute", "delivery package", "credit card",
    "ATM", "sticky note", "morning coffee desk",
    "receipt", "wallet", "grocery aisle",
]
VISUAL_AUTO_PENALTY_TAGS = {
    "yacht", "luxury car", "money-rain", "suit-handshake",
    "fanned cash", "Lambo", "mansion", "private jet",
    "champagne", "Rolex", "gold-bars",
}

# ─── Audio Vocabulary ─────────────────────────────────────────────────────────
MUSIC_SEARCH_VOCAB = [
    "cinematic", "documentary", "neo-classical",
    "piano", "thoughtful", "contemplative",
    "underscore", "film score", "minimal piano",
]
MUSIC_AUTO_PENALTY_TAGS = {
    "uplifting", "corporate", "motivational", "epic",
    "trailer", "powerful", "inspiring", "energetic",
}
BED_SEARCH_VOCAB = [
    "café ambience", "light rain", "subway",
    "distant traffic", "office room tone", "kitchen",
    "library hum", "city window",
]

# ─── Forbidden Tokens ─────────────────────────────────────────────────────────
FORBIDDEN_TOKENS = [
    "you should", "best way to", "guaranteed", "secret", "trick", "hack",
    "the rich", "the 1%", "millionaire mindset", "passive income",
    "financial freedom", "this is why you're broke",
]
FORBIDDEN_HASHTAGS = {
    "wealth", "money", "rich", "investing", "millionaire", "passiveincome",
}

# ─── Caption ──────────────────────────────────────────────────────────────────
CAPTION_MIN_CHARS = 150
CAPTION_MAX_CHARS = 220
HASHTAG_MIN       = 8
HASHTAG_MAX       = 12

# ─── Fact-Check ───────────────────────────────────────────────────────────────
FACTCHECK_BATCH_SIZE       = 5
FACTCHECK_CACHE_TTL_DAYS   = 90
FACTCHECK_MAX_REGENERATE   = 2

CANONICAL_RESEARCHERS = {
    "Kahneman", "Tversky", "Thaler", "Ariely", "Housel",
    "Cialdini", "Mullainathan", "Shafir", "Dunn", "Norton",
    "Stanley", "Danko", "Perkins", "Robin", "Dominguez",
    "Clear", "Gilbert", "Laibson",
}

# ─── Voice Profile Defaults ───────────────────────────────────────────────────
VOICE_DEFAULT_ID     = "bm_george"
VOICE_DEFAULT_SPEED  = 0.9
VOICE_AUDITION_ORDER = ["bm_george", "am_michael", "bf_emma", "am_adam", "bm_fable"]

# ─── Secrets ──────────────────────────────────────────────────────────────────
REQUIRED_KEYS = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "FREESOUND_CLIENT_SECRET",
    "JAMENDO_API_KEY",
]

# Optional — pipeline skips these sources when keys are absent
OPTIONAL_KEYS = [
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USER_AGENT",
]

def get_secret(name: str) -> str:
    val = os.getenv(name, "")
    if not val:
        raise EnvironmentError(f"Missing required env var: {name}")
    return val

def get_optional(name: str) -> str:
    return os.getenv(name, "")

def validate_env() -> None:
    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {missing}")
