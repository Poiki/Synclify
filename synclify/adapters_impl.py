from __future__ import annotations

from typing import Optional, Sequence

from .adapters import PlaylistServiceAdapter
from .models import PlaylistSummary, Track
from .services.spotify import SpotifyService
from .services.youtube import YouTubeService


class SpotifyAdapter(PlaylistServiceAdapter):
    name = "spotify"

    def __init__(self, service: Optional[SpotifyService] = None):
        self._service: Optional[SpotifyService] = service

    def _ensure_service(self) -> SpotifyService:
        if self._service is None:
            self._service = SpotifyService()
        return self._service

    def list_playlists(self) -> Sequence[PlaylistSummary]:
        return self._ensure_service().list_playlists()

    def create_playlist(self, name: str) -> str:
        return self._ensure_service().create_playlist(name)

    def get_tracks(self, playlist_id: str) -> Sequence[Track]:
        return self._ensure_service().get_tracks(playlist_id)

    def add_identifiers(self, playlist_id: str, identifiers: Sequence[str]) -> None:
        uris = [
            identifier if identifier.startswith("spotify:track:") else f"spotify:track:{identifier}"
            for identifier in identifiers
        ]
        self._ensure_service().add_tracks(playlist_id, uris)

    def remove_tracks(self, playlist_id: str, tracks: Sequence[Track]) -> None:
        uris = [track.uri for track in tracks if track.uri]
        self._ensure_service().remove_tracks_by_uri(playlist_id, uris)

    def search_identifier(self, title: str, artists: Sequence[str]) -> Optional[str]:
        return self._ensure_service().search_track(title, artists)


class YouTubeAdapter(PlaylistServiceAdapter):
    name = "youtube"

    def __init__(self, service: Optional[YouTubeService] = None):
        self._service: Optional[YouTubeService] = service

    def _ensure_service(self) -> YouTubeService:
        if self._service is None:
            self._service = YouTubeService()
        return self._service

    def list_playlists(self) -> Sequence[PlaylistSummary]:
        return self._ensure_service().list_playlists()

    def create_playlist(self, name: str) -> str:
        return self._ensure_service().create_playlist(name)

    def get_tracks(self, playlist_id: str) -> Sequence[Track]:
        return self._ensure_service().get_tracks(playlist_id)

    def add_identifiers(self, playlist_id: str, identifiers: Sequence[str]) -> None:
        self._ensure_service().add_videos(playlist_id, identifiers)

    def remove_tracks(self, playlist_id: str, tracks: Sequence[Track]) -> None:
        playlist_items = [track.playlist_item_id for track in tracks if track.playlist_item_id]
        self._ensure_service().remove_videos(playlist_items)

    def search_identifier(self, title: str, artists: Sequence[str]) -> Optional[str]:
        query = f"{title} {' '.join(artists)}".strip()
        return self._ensure_service().search_video(query)


__all__ = ["SpotifyAdapter", "YouTubeAdapter"]
