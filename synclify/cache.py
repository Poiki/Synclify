from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .config import CACHE_DIR


class SearchCache:
    """Tiny JSON cache for resolved IDs across runs."""

    def __init__(self, name: str = "sync_cache.json"):
        self.path = os.path.join(CACHE_DIR, name)
        self.data: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get(self, key: str) -> Optional[str]:
        return self.data.get(key)

    def put(self, key: str, value: str) -> None:
        self.data[key] = value


def cache_key(service: str, name: str, artists: List[str]) -> str:
    artists_token = " ".join(sorted(artists)) if artists else ""
    return f"{service}|{name.strip().lower()}|{artists_token.strip().lower()}"


__all__ = ["SearchCache", "cache_key"]
