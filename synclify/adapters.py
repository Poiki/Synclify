from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from .models import PlaylistSummary, Track


class PlaylistServiceAdapter(Protocol):
    name: str

    def list_playlists(self) -> Sequence[PlaylistSummary]:
        ...

    def create_playlist(self, name: str) -> str:
        ...

    def get_tracks(self, playlist_id: str) -> Sequence[Track]:
        ...

    def add_identifiers(self, playlist_id: str, identifiers: Sequence[str]) -> None:
        ...

    def remove_tracks(self, playlist_id: str, tracks: Sequence[Track]) -> None:
        ...

    def search_identifier(self, title: str, artists: Sequence[str]) -> Optional[str]:
        ...


@dataclass
class PlaylistSelection:
    id: str
    name: str


__all__ = ["PlaylistServiceAdapter", "PlaylistSelection"]
