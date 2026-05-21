"""
demo_data.py — fixture data for the portfolio demo.

All media URLs point to assets/ which Flask serves under /media/.
Content is plausible healing-account material written for demo purposes —
it does not reflect the real account's content strategy.
"""

# ---------------------------------------------------------------------------
# Image assets (CC0 — Pexels / Unsplash / Pixabay)
# ---------------------------------------------------------------------------

IMAGES = [
    {
        "filename":  "pexels_photo_34741061.jpg",
        "media_url": "assets/images/pexels_photo_34741061.jpg",
        "title":     "Calm lake with misty reflections at dawn",
        "source":    "pexels_photo",
        "score":     9.6,
        "keyword":   "Soft focus misty lake dawn",
        "tier":      "priority",
        "licence":   "https://www.pexels.com/license/",
    },
    {
        "filename":  "pexels_photo_30933042.jpg",
        "media_url": "assets/images/pexels_photo_30933042.jpg",
        "title":     "Serene forest path enveloped in fog, Japan",
        "source":    "pexels_photo",
        "score":     9.6,
        "keyword":   "Misty bamboo forest path, soft light filtering",
        "tier":      "priority",
        "licence":   "https://www.pexels.com/license/",
    },
    {
        "filename":  "unsplash_BVdZKOAkbTE.jpg",
        "media_url": "assets/images/unsplash_BVdZKOAkbTE.jpg",
        "title":     "Misty mountain landscape at sunrise with soft pastel sky",
        "source":    "unsplash",
        "score":     9.5,
        "keyword":   "Aerial view of cloud-veiled mountain range at dawn",
        "tier":      "priority",
        "licence":   "https://unsplash.com/license",
    },
    {
        "filename":  "pexels_photo_10894151.jpg",
        "media_url": "assets/images/pexels_photo_10894151.jpg",
        "title":     "Aerial view of clouds over snow-covered mountains at dawn",
        "source":    "pexels_photo",
        "score":     9.5,
        "keyword":   "Aerial view of cloud-veiled mountain range at dawn",
        "tier":      "priority",
        "licence":   "https://www.pexels.com/license/",
    },
    {
        "filename":  "pexels_photo_32194380.jpg",
        "media_url": "assets/images/pexels_photo_32194380.jpg",
        "title":     "Dandelion field during golden hour — fluffy seed heads",
        "source":    "pexels_photo",
        "score":     9.3,
        "keyword":   "Soft focus dandelion field evening",
        "tier":      "priority",
        "licence":   "https://www.pexels.com/license/",
    },
    {
        "filename":  "pexels_photo_16098996.jpg",
        "media_url": "assets/images/pexels_photo_16098996.jpg",
        "title":     "Spiderweb covered in dewdrops, foggy meadow background",
        "source":    "pexels_photo",
        "score":     9.5,
        "keyword":   "Dew-kissed spiderweb in morning breeze",
        "tier":      "priority",
        "licence":   "https://www.pexels.com/license/",
    },
    {
        "filename":  "unsplash_oRidNrexMww.jpg",
        "media_url": "assets/images/unsplash_oRidNrexMww.jpg",
        "title":     "Sunlight streams through a rustic cabin window",
        "source":    "unsplash",
        "score":     9.6,
        "keyword":   "Muted light inside quiet wooden cabin",
        "tier":      "priority",
        "licence":   "https://unsplash.com/license",
    },
    {
        "filename":  "pixabay_photo_1773823.jpg",
        "media_url": "assets/images/pixabay_photo_1773823.jpg",
        "title":     "Waterdrop on leaf — dew, close up",
        "source":    "pixabay_photo",
        "score":     9.5,
        "keyword":   "Close-up slow dew drop fall from leaf",
        "tier":      "priority",
        "licence":   "https://pixabay.com/service/license/",
    },
]


# ---------------------------------------------------------------------------
# /api/status  (idle — last run was successful)
# ---------------------------------------------------------------------------

STATUS = {
    "running":        None,
    "current_step":   "",
    "step_status":    {},
    "last_exit_code": 0,
    "last_command":   "agent",
    "elapsed_s":      None,
}


# ---------------------------------------------------------------------------
# /api/media  (Stage 02 — Sort Media)
# ---------------------------------------------------------------------------

MEDIA_ITEMS = [
    {
        "filename":  img["filename"],
        "media_url": img["media_url"],
        "type":      "image",
        "source":    img["source"],
        "title":     img["title"],
        "score":     img["score"],
        "keyword":   img["keyword"],
        "url":       "",
        "tier":      img["tier"],
        "rating":    "g",
    }
    for img in IMAGES
]

IMAGE_ITEMS = MEDIA_ITEMS[:]


# ---------------------------------------------------------------------------
# /api/pool-summary  (Stage 03 — pool inventory widget)
# ---------------------------------------------------------------------------

POOL_SUMMARY = {
    "reels": {
        "clips":  0,
        "sound":  0,
        "music":  0,
        "items":  [],
    },
    "carousel": {
        "total":    8,
        "priority": 8,
        "standard": 0,
        "items": [
            {
                "filename":  img["filename"],
                "media_url": img["media_url"],
                "title":     img["title"],
                "source":    img["source"],
                "score":     img["score"],
                "kind":      "image",
                "priority":  True,
            }
            for img in IMAGES
        ],
    },
    "story": {
        "total":    8,
        "priority": 8,
        "standard": 0,
        "items": [
            {
                "filename":  img["filename"],
                "media_url": img["media_url"],
                "title":     img["title"],
                "source":    img["source"],
                "score":     img["score"],
                "kind":      "image",
                "priority":  True,
            }
            for img in IMAGES
        ],
    },
}


# ---------------------------------------------------------------------------
# /api/renders  list
# ---------------------------------------------------------------------------

RENDERS_LIST = [
    {"name": "2026-05-16_001", "format": "carousel", "date": "2026-05-16"},
    {"name": "2026-05-16_002", "format": "story",    "date": "2026-05-16"},
]


# ---------------------------------------------------------------------------
# /api/renders/<name>  detail
# ---------------------------------------------------------------------------

_CAROUSEL_CAPTION = """\
Some weight you have been carrying for a long time was never yours to hold.
This one is for the days you keep going while quietly falling apart.

Save this for when rest feels like something you have to earn 🌿

#healingjourney #nervoussystemhealing #innerpeace #selfcompassion #mentalhealth \
#anxietyrelief #emotionalhealing #softlife #slowliving #restisproductive \
#burnoutrecovery #quiethealing"""

_CAROUSEL_2_CAPTION = """\
There are days you feel like too much — too loud, too sensitive, too needy.
That is not a flaw. That is what happens when you have been told to be smaller.

Drop a 🌿 if this one reached you.

#highlysensitiveperson #toomuch #innerchild #selfworth #emotionalhealing \
#hsplife #deepfeeling #sensitivesouls #healingspace #gentlereminder \
#youareenough #selfacceptance"""

RENDER_DETAIL = {
    "2026-05-16_001": {
        "name":     "2026-05-16_001",
        "format":   "carousel",
        "carousels": [
            {
                "dir":     "carousel_01",
                "theme":   "When rest feels unearned",
                "caption": _CAROUSEL_CAPTION,
                "key":     "2026-05-16_001/carousel_01",
                "verdict": "approved",
                "note":    "",
                "slides": [
                    {
                        "slide":     1,
                        "text":      "You have been carrying something heavy for a long time.",
                        "image":     "Calm lake with misty reflections at dawn",
                        "file":      "slide_01.jpg",
                        "media_url": "assets/images/pexels_photo_34741061.jpg",
                    },
                    {
                        "slide":     2,
                        "text":      "Rest is not a reward for productivity. It is a basic need.",
                        "image":     "Serene forest path in fog",
                        "file":      "slide_02.jpg",
                        "media_url": "assets/images/pexels_photo_30933042.jpg",
                    },
                    {
                        "slide":     3,
                        "text":      "Your nervous system does not understand 'earning it'. It only knows safe or unsafe.",
                        "image":     "Misty mountain landscape",
                        "file":      "slide_03.jpg",
                        "media_url": "assets/images/unsplash_BVdZKOAkbTE.jpg",
                    },
                    {
                        "slide":     4,
                        "text":      "You are allowed to stop. Not when you finish everything. Now.",
                        "image":     "Dandelion field at golden hour",
                        "file":      "slide_04.jpg",
                        "media_url": "assets/images/pexels_photo_32194380.jpg",
                    },
                    {
                        "slide":     5,
                        "text":      "Slowness is not failure. It is a form of listening to yourself.",
                        "image":     "Sunlight through cabin window",
                        "file":      "slide_05.jpg",
                        "media_url": "assets/images/unsplash_oRidNrexMww.jpg",
                    },
                ],
            },
            {
                "dir":     "carousel_02",
                "theme":   "On the days you feel like too much",
                "caption": _CAROUSEL_2_CAPTION,
                "key":     "2026-05-16_001/carousel_02",
                "verdict": "",
                "note":    "",
                "slides": [
                    {
                        "slide":     1,
                        "text":      "There are days you feel like too much.",
                        "image":     "Spiderweb with dewdrops",
                        "file":      "slide_01.jpg",
                        "media_url": "assets/images/pexels_photo_16098996.jpg",
                    },
                    {
                        "slide":     2,
                        "text":      "That is not a flaw. That is what happens when you have been told to be smaller.",
                        "image":     "Clouds over mountains",
                        "file":      "slide_02.jpg",
                        "media_url": "assets/images/pexels_photo_10894151.jpg",
                    },
                    {
                        "slide":     3,
                        "text":      "The people who called you too much were not the right size for you.",
                        "image":     "Waterdrop on leaf",
                        "file":      "slide_03.jpg",
                        "media_url": "assets/images/pixabay_photo_1773823.jpg",
                    },
                    {
                        "slide":     4,
                        "text":      "Your depth is not a burden. It is a gift not everyone knows how to hold.",
                        "image":     "Misty lake at dawn",
                        "file":      "slide_04.jpg",
                        "media_url": "assets/images/pexels_photo_34741061.jpg",
                    },
                    {
                        "slide":     5,
                        "text":      "You are not too much. You are just enough for the right people.",
                        "image":     "Forest path in fog",
                        "file":      "slide_05.jpg",
                        "media_url": "assets/images/pexels_photo_30933042.jpg",
                    },
                ],
            },
        ],
    },
    "2026-05-16_002": {
        "name":   "2026-05-16_002",
        "format": "story",
        "images": [
            {
                "filename":  "story_01.jpg",
                "media_url": "assets/images/unsplash_oRidNrexMww.jpg",
                "quote":     "You are not behind. You are on a path no one else has walked before.",
                "caption":   "You are not behind. You are on a path no one else has walked before.\n\n#healingjourney #selfcompassion #ownpace #gentlereminder #innerpeace #slowliving #youareenough #mentalhealth #softlife #emotionalhealing #quiethealing #healingspace",
                "key":       "2026-05-16_002/story_01.jpg",
                "verdict":   "approved",
                "note":      "",
            },
            {
                "filename":  "story_02.jpg",
                "media_url": "assets/images/pexels_photo_32194380.jpg",
                "quote":     "Some days healing looks like crying in the car and ordering takeout. That still counts.",
                "caption":   "Some days healing looks like crying in the car and ordering takeout. That still counts.\n\n#healingjourney #realistichealing #selfcompassion #emotionalhealth #gentlereminder #mentalhealth #burnoutrecovery #softlife #anxietyrelief #innerpeace #quiethealing #youareenough",
                "key":       "2026-05-16_002/story_02.jpg",
                "verdict":   "",
                "note":      "",
            },
            {
                "filename":  "story_03.jpg",
                "media_url": "assets/images/pexels_photo_16098996.jpg",
                "quote":     "Your softness is not weakness. It is the bravest thing you carry.",
                "caption":   "Your softness is not weakness. It is the bravest thing you carry.\n\n#softness #highlysensitiveperson #emotionalhealing #innerstrength #selfacceptance #gentlereminder #healingspace #sensitivesouls #selfcompassion #mentalhealth #quiethealing #innerpeace",
                "key":       "2026-05-16_002/story_03.jpg",
                "verdict":   "",
                "note":      "",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# /api/intelligence
# ---------------------------------------------------------------------------

INTELLIGENCE = {
    "keywords":        ["misty forest morning", "slow rain window", "nervous system reset",
                        "quiet cabin light", "fog rolling over valley", "golden hour field",
                        "dew on spider web", "mountain sunrise silence"],
    "audio_keywords":  ["lo-fi ambient", "slow piano", "rain background", "nature white noise"],
    "emotional_hooks": ["the weight you keep carrying isn't yours to hold",
                        "rest before you've 'earned' it",
                        "you were never too much — just surrounded by too little"],
    "carousel_count":  2,
    "quote_count":     10,
    "run_date":        "2026-05-16",
}


# ---------------------------------------------------------------------------
# /api/stats
# ---------------------------------------------------------------------------

STATS = {
    "pool": {
        "footage": {"priority": 0,  "standard": 0},
        "audio":   {"priority": 0,  "standard": 0},
        "images":  {"priority": 8,  "standard": 0},
    },
    "staging":      {"footage": 0, "audio": 0},
    "batches":      1,
    "final_review": {"total": 2, "approved": 1, "pending": 1},
    "world_bible": {
        "high_tags": ["misty forest", "nervous system", "inner child", "soft light",
                      "slow living", "quiet healing", "golden hour", "morning fog"],
        "low_tags":  [],
        "rejected":  3,
        "learnings": 5,
    },
}
