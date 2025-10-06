from __future__ import annotations

import json
import os
import time
from typing import List, Optional, Sequence

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..cache import SearchCache
from ..config import SCOPES_YT, TOKENS_DIR, YOUTUBE_INSERT_RATE_SLEEP, YOUTUBE_SEARCH_RATE_SLEEP
from ..console import logger
from ..models import PlaylistSummary, Track
from ..retry import retry_call
from ..state import STATE


def build_client():
    token_path = os.path.join(TOKENS_DIR, "youtube_token.json")
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES_YT)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not os.path.exists("client_secret.json"):
            logger.error("Missing [bold]client_secret.json[/bold] (Google OAuth).")
            raise SystemExit(1)
        try:
            with open("client_secret.json", "r", encoding="utf-8") as handle:
                data = json.load(handle)
            info = data.get("installed") or {}
            project_id = info.get("project_id", "unknown")
            project_number = (info.get("client_id", "").split("-")[0]) or "unknown"
            logger.info(
                f"[cyan]Google OAuth:[/cyan] project_id={project_id} project_number={project_number}"
            )
        except Exception:
            pass
        flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES_YT)
        creds = flow.run_local_server(port=8081, prompt="consent")
        with open(token_path, "w", encoding="utf-8") as handle:
            handle.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


class YouTubeService:
    def __init__(self, client=None, cache: Optional[SearchCache] = None):
        self.client = client or build_client()
        self.cache = cache or SearchCache()

    # Playlist management -------------------------------------------------
    def list_playlists(self) -> List[PlaylistSummary]:
        playlists: List[PlaylistSummary] = []
        request = self.client.playlists().list(
            part="snippet,contentDetails", mine=True, maxResults=50
        )
        while request:
            try:
                response = retry_call(request.execute)
            except HttpError as err:
                message = str(err)
                if getattr(err.resp, "status", None) == 403 and (
                    "accessNotConfigured" in message or "youtube.googleapis.com" in message
                ):
                    logger.error(
                        "[red]YouTube Data API v3 is not enabled in your project.[/red]\n"
                        "Fix:\n  1) APIs & Services -> Library -> YouTube Data API v3 -> Enable\n"
                        "  2) OAuth consent screen: External + add your email to Test users\n"
                        "  3) Delete ./.tokens/youtube_token.json and try again"
                    )
                    raise SystemExit(1)
                raise
            for item in response.get("items", []):
                playlists.append(
                    PlaylistSummary(
                        id=item["id"],
                        name=item["snippet"].get("title", ""),
                        track_count=item["contentDetails"].get("itemCount", 0),
                    )
                )
            request = self.client.playlists().list_next(request, response)
        return playlists

    def create_playlist(self, name: str, description: str = "Sync created by Synclify") -> str:
        response = retry_call(
            self.client.playlists()
            .insert(
                part="snippet,status",
                body={
                    "snippet": {"title": name, "description": description},
                    "status": {"privacyStatus": "private"},
                },
            )
            .execute
        )
        return response["id"]

    # Track handling ------------------------------------------------------
    def get_tracks(self, playlist_id: str) -> List[Track]:
        items: List[Track] = []
        request = self.client.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
        )
        while request:
            response = retry_call(request.execute)
            for element in response.get("items", []):
                snippet = element["snippet"]
                title = snippet.get("title", "")
                channel = (
                    snippet.get("videoOwnerChannelTitle")
                    or snippet.get("channelTitle")
                    or ""
                )
                items.append(
                    Track(
                        service="youtube",
                        id=snippet.get("resourceId", {}).get("videoId"),
                        uri=None,
                        name=title,
                        artists=[channel] if channel else [],
                        raw=element,
                        playlist_item_id=element.get("id"),
                    )
                )
            request = self.client.playlistItems().list_next(request, response)
        return items

    def add_videos(self, playlist_id: str, video_ids: Sequence[str]) -> None:
        for video_id in video_ids:
            if not video_id:
                continue
            if video_id in STATE.added_video_ids:
                continue
            retry_call(
                self.client.playlistItems()
                .insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }
                    },
                )
                .execute
            )
            STATE.added_video_ids.add(video_id)
            time.sleep(YOUTUBE_INSERT_RATE_SLEEP)

    def remove_videos(self, playlist_item_ids: Sequence[str]) -> None:
        for item_id in playlist_item_ids:
            if not item_id:
                continue
            retry_call(self.client.playlistItems().delete(id=item_id).execute)

    # Search --------------------------------------------------------------
    def search_video(self, query: str) -> Optional[str]:
        if STATE.yt_search_disabled:
            return None

        def do_search():
            return (
                self.client.search()
                .list(part="id", q=query, type="video", maxResults=1)
                .execute()
            )

        response = retry_call(do_search)
        time.sleep(YOUTUBE_SEARCH_RATE_SLEEP)
        items = response.get("items", [])
        if not items:
            return None
        return items[0]["id"].get("videoId")


__all__ = ["YouTubeService", "build_client"]
