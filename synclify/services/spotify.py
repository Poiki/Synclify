from __future__ import annotations

import os
from typing import List, Optional, Sequence, Tuple

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

from ..config import SPOTIFY_ADD_BATCH, TOKENS_DIR
from ..console import logger
from ..models import PlaylistSummary, Track
from ..retry import retry_call


def spotify_preflight(sp_auth: SpotifyOAuth, sp_client: spotipy.Spotify) -> None:
    try:
        me = sp_client.me()
        logger.info(f"[cyan]Spotify user:[/cyan] {me.get('id')} ({me.get('email')})")
    except Exception:
        logger.warning("[yellow]Could not fetch Spotify profile (sp.me()).[/yellow]")

    token = None
    try:
        token = sp_auth.get_cached_token()
    except Exception:
        pass
    scopes = (token or {}).get("scope", "")
    logger.info(f"[cyan]Token scopes:[/cyan] {scopes or '(unknown)'}")

    try:
        _ = sp_client.current_user_playlists(limit=1)
        logger.info("[green]Spotify API OK (playlists accessible).[/green]")
    except SpotifyException as err:
        if err.http_status == 403:
            logger.error(
                "[red]Spotify 403.[/red] Fix in Dashboard:\n"
                "  - Add your user under 'Users and Access' (Development mode)\n"
                "  - Ensure Redirect URI matches exactly (e.g., http://127.0.0.1:8000/callback)\n"
                "  - Delete ./.tokens/spotify_token.json and re-auth"
            )
        raise


def build_client(scope: Optional[str] = None) -> Tuple[spotipy.Spotify, SpotifyOAuth]:
    client_id = os.getenv("SPOTIFY_CLIENT_ID") or input("SPOTIFY_CLIENT_ID: ").strip()
    client_secret = (
        os.getenv("SPOTIFY_CLIENT_SECRET") or input("SPOTIFY_CLIENT_SECRET: ").strip()
    )
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback")
    scope = scope or (
        "playlist-read-private playlist-modify-private playlist-modify-public user-library-read"
    )
    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        cache_path=os.path.join(TOKENS_DIR, "spotify_token.json"),
        show_dialog=True,
    )
    client = spotipy.Spotify(auth_manager=auth)
    spotify_preflight(auth, client)
    return client, auth


class SpotifyService:
    def __init__(self, client: Optional[spotipy.Spotify] = None):
        self.client, self.auth = build_client() if client is None else (client, None)
        if client is not None:
            self.auth = None

    # Playlist management -------------------------------------------------
    def list_playlists(self) -> List[PlaylistSummary]:
        playlists: List[PlaylistSummary] = []
        results = retry_call(self.client.current_user_playlists, limit=50)
        while results:
            for item in results.get("items", []):
                playlists.append(
                    PlaylistSummary(
                        id=item["id"],
                        name=item["name"],
                        track_count=item["tracks"].get("total", 0),
                    )
                )
            results = retry_call(self.client.next, results) if results.get("next") else None
        return playlists

    def create_playlist(
        self,
        name: str,
        public: bool = False,
        description: str = "Sync created by Synclify",
    ) -> str:
        me = retry_call(self.client.me)
        playlist = retry_call(
            self.client.user_playlist_create,
            me["id"],
            name,
            public=public,
            description=description,
        )
        return playlist["id"]

    # Track handling ------------------------------------------------------
    def get_tracks(self, playlist_id: str) -> List[Track]:
        tracks: List[Track] = []
        results = retry_call(
            self.client.playlist_items,
            playlist_id,
            additional_types=("track",),
            limit=100,
        )
        while True:
            for item in results.get("items", []):
                data = item.get("track")
                if not data or data.get("is_local"):
                    continue
                tracks.append(
                    Track(
                        service="spotify",
                        id=data.get("id"),
                        uri=data.get("uri"),
                        name=data.get("name", ""),
                        artists=[artist.get("name", "") for artist in data.get("artists", [])],
                        raw=data,
                    )
                )
            if results.get("next"):
                results = retry_call(self.client.next, results)
            else:
                break
        return tracks

    def add_tracks(self, playlist_id: str, uris: Sequence[str]) -> None:
        for start in range(0, len(uris), SPOTIFY_ADD_BATCH):
            chunk = list(uris[start : start + SPOTIFY_ADD_BATCH])
            if chunk:
                retry_call(self.client.playlist_add_items, playlist_id, chunk)

    def remove_tracks_by_uri(self, playlist_id: str, uris: Sequence[str]) -> None:
        items = [uri for uri in uris if uri]
        if not items:
            logger.info('[yellow]No valid URIs to remove on Spotify.[/yellow]')
            return
        logger.info(f'[red]Requesting deletion of {len(items)} tracks on Spotify.[/red]')
        retry_call(self.client.playlist_remove_all_occurrences_of_items, playlist_id, items)

    def search_track(self, name: str, artists: Sequence[str]) -> Optional[str]:
        query = f"{name} {' '.join(artists)}".strip()
        try:
            results = retry_call(self.client.search, q=query, type="track", limit=1)
        except SpotifyException as err:
            logger.error(f"[red]Spotify search error:[/red] {err}")
            return None
        items = results.get("tracks", {}).get("items", [])
        if not items:
            return None
        return items[0].get("uri")


__all__ = ["SpotifyService", "build_client", "spotify_preflight"]
