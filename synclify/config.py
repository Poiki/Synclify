from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

SCOPES_YT = ["https://www.googleapis.com/auth/youtube"]
TOKENS_DIR = ".tokens"
CACHE_DIR = ".cache"

os.makedirs(TOKENS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# Rate limiting and retry behaviour
YOUTUBE_SEARCH_RATE_SLEEP = 0.10
YOUTUBE_INSERT_RATE_SLEEP = 0.05
SPOTIFY_ADD_BATCH = 100
RETRY_MAX_TRIES = 5
RETRY_BASE_SLEEP = 0.5
WEB_SEARCH_THROTTLE = 0.20

__all__ = [
    "SCOPES_YT",
    "TOKENS_DIR",
    "CACHE_DIR",
    "YOUTUBE_SEARCH_RATE_SLEEP",
    "YOUTUBE_INSERT_RATE_SLEEP",
    "SPOTIFY_ADD_BATCH",
    "RETRY_MAX_TRIES",
    "RETRY_BASE_SLEEP",
    "WEB_SEARCH_THROTTLE",
]
