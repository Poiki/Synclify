from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Track:
    service: str
    id: Optional[str]
    uri: Optional[str]
    name: str
    artists: List[str] = field(default_factory=list)
    raw: Optional[dict] = None
    playlist_item_id: Optional[str] = None


@dataclass
class PlaylistSummary:
    id: str
    name: str
    track_count: int


__all__ = ["Track", "PlaylistSummary"]
