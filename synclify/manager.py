from __future__ import annotations

from difflib import SequenceMatcher
from typing import List, Optional, Sequence

from rich.table import Table

from .adapters import PlaylistSelection, PlaylistServiceAdapter
from .console import console, logger
from .models import Track
from .utils import clean_artists, clean_title, summarize_by_artist
from .websearch import google_music_search, pick_best_web_result, pick_from_web_results


class PlaylistManager:
    def __init__(self, adapter: PlaylistServiceAdapter):
        self.adapter = adapter

    # Selection -----------------------------------------------------------
    def choose_playlist(self, purpose: str) -> Optional[PlaylistSelection]:
        playlists = list(self.adapter.list_playlists())
        if not playlists:
            console.print("[yellow]No playlists found.[/yellow]")
            return None
        table = Table(title=f"Playlists on {self.adapter.name} ({purpose})", show_lines=False)
        table.add_column("#", justify="right")
        table.add_column("Name")
        table.add_column("Tracks", justify="right")
        for idx, playlist in enumerate(playlists, start=1):
            table.add_row(str(idx), playlist.name, str(playlist.track_count))
        console.print(table)
        console.print("Choose a playlist: ", end="")
        try:
            raw = int(input().strip())
        except Exception:
            return None
        if not (1 <= raw <= len(playlists)):
            return None
        target = playlists[raw - 1]
        logger.info(f'[cyan]Playlist selected:[/cyan] {target.name} ({target.track_count} tracks)')
        return PlaylistSelection(id=target.id, name=target.name)

    # Reporting -----------------------------------------------------------
    def show_artist_summary(self, playlist_id: str) -> None:
        tracks = list(self.adapter.get_tracks(playlist_id))
        logger.info(f'[cyan]Tracks loaded:[/cyan] {len(tracks)}')
        summary = summarize_by_artist([track.__dict__ for track in tracks])
        if not summary:
            console.print("[yellow]No tracks available.[/yellow]")
            return
        table = Table(title="Artists", show_lines=False)
        table.add_column("Artist")
        table.add_column("Count", justify="right")
        for artist, count in summary:
            table.add_row(artist, str(count))
        console.print(table)

    # Additions -----------------------------------------------------------
    def add_tracks_interactive(self, playlist_id: str) -> None:
        console.print(
            "Enter tracks to add. Suggested format: Title - Artist1,Artist2."
            " Leave blank line to finish."
        )
        payload: List[str] = []
        while True:
            line = input().strip()
            if not line:
                break
            title, artists = self._split_title_artists(line)
            logger.info(f"[cyan]Searching:[/cyan] {title} :: {', '.join(artists) if artists else 'no artist'}")
            identifier = self.adapter.search_identifier(title, artists)
            if not identifier and self.adapter.name == "youtube":
                identifier = self._resolve_youtube_via_web(title, artists)
            if identifier:
                payload.append(identifier)
                logger.info(f"[green]ID found:[/green] {identifier}")
                console.print(f"[green]Queued for addition:[/green] {title}")
            else:
                logger.warning(f"[yellow]No identifier for:[/yellow] {title}")
                console.print(f"[yellow]No identifier found for:[/yellow] {title}")
        if payload:
            logger.info(f"[cyan]Adding {len(payload)} tracks into the playlist[/cyan]")
            self.adapter.add_identifiers(playlist_id, payload)
            console.print(f"[green]Added {len(payload)} tracks.[/green]")
        else:
            console.print("[yellow]No tracks were added.[/yellow]")

    def _resolve_youtube_via_web(self, title: str, artists: Sequence[str]) -> Optional[str]:
        results = google_music_search(title, artists)
        logger.info(f"[cyan]Web results:[/cyan] {len(results)}")
        if not results:
            return None
        auto = pick_best_web_result(results, title, list(artists))
        if auto:
            logger.info('[green]Auto web match accepted.[/green]')
            return auto
        display = f"{title} - {', '.join(artists)}" if artists else title
        return pick_from_web_results(results, display)

    # Removals ------------------------------------------------------------
    def remove_by_artists(self, playlist_id: str, artists: Sequence[str]) -> None:
        targets = {artist.strip().lower() for artist in artists if artist.strip()}
        if not targets:
            console.print("[yellow]No valid artists provided.[/yellow]")
            return
        tracks = list(self.adapter.get_tracks(playlist_id))
        logger.info(f"[cyan]Tracks loaded for filtering:[/cyan] {len(tracks)}")
        matched = [track for track in tracks if self._matches_artist(track, targets)]
        if not matched:
            console.print("[yellow]No matches found.[/yellow]")
            return
        logger.info(f"[red]Removing {len(matched)} matching tracks[/red]")
        self.adapter.remove_tracks(playlist_id, matched)
        console.print(f"[green]Removed {len(matched)} tracks from {', '.join(artists)}.[/green]")

    def _matches_artist(self, track: Track, targets: set[str]) -> bool:
        for artist in track.artists:
            if artist.lower() in targets:
                return True
        return False

    # Duplicates ----------------------------------------------------------
    def remove_duplicates(self, playlist_id: str, threshold: float = 0.9) -> None:
        tracks = list(self.adapter.get_tracks(playlist_id))
        total = len(tracks)
        if not tracks:
            console.print("[yellow]The playlist is empty.[/yellow]")
            return
        logger.info(f"[cyan]Scanning for duplicates across {total} tracks (threshold {threshold:.2f}).[/cyan]")
        duplicates = self._detect_duplicates(tracks, threshold)
        if not duplicates:
            console.print("[green]No close duplicates found.[/green]")
            logger.info('[green]No duplicates detected.[/green]')
            return
        logger.info(f"[red]Duplicates detected:[/red] {len(duplicates)}")
        self.adapter.remove_tracks(playlist_id, duplicates)
        console.print(f"[green]Removed {len(duplicates)} duplicates.[/green]")

    def _detect_duplicates(self, tracks: Sequence[Track], threshold: float) -> List[Track]:
        kept: List[Track] = []
        duplicates: List[Track] = []
        for position, track in enumerate(tracks, start=1):
            try:
                normalized_title = clean_title(track.name or "")
            except Exception as exc:
                logger.warning(f"[yellow]Could not normalize title #{position}: {exc}[/yellow]")
                continue
            try:
                artist_signature = set(clean_artists(track.artists))
            except Exception as exc:
                logger.warning(f"[yellow]Could not normalize artists #{position}: {exc}[/yellow]")
                continue
            found = False
            for keeper in kept:
                if self._is_similar(track, keeper, normalized_title, artist_signature, threshold):
                    logger.info(f"[magenta]Duplicate detected:[/magenta] '{track.name}' ~ '{keeper.name}'")
                    duplicates.append(track)
                    found = True
                    break
            if not found:
                kept.append(track)
        return duplicates

    def _is_similar(
        self,
        candidate: Track,
        keeper: Track,
        candidate_title: str,
        candidate_artists: set[str],
        threshold: float,
    ) -> bool:
        try:
            keeper_title = clean_title(keeper.name or "")
        except Exception as exc:
            logger.warning(f"[yellow]Could not normalize reference title: {exc}[/yellow]")
            return False
        try:
            keeper_artists = set(clean_artists(keeper.artists))
        except Exception as exc:
            logger.warning(f"[yellow]Could not normalize reference artists: {exc}[/yellow]")
            return False
        ratio = SequenceMatcher(None, candidate_title, keeper_title).ratio()
        artist_overlap = bool(candidate_artists & keeper_artists) or not keeper_artists or not candidate_artists
        if ratio >= threshold and artist_overlap:
            logger.debug(f"[cyan]Similarity {ratio:.2f} with artist overlap {candidate_artists & keeper_artists}")
            return True
        return False

    # Helpers -------------------------------------------------------------
    def _split_title_artists(self, entry: str) -> tuple[str, List[str]]:
        if "-" in entry:
            title, rest = entry.split("-", 1)
            artists = [artist.strip() for artist in rest.split(",") if artist.strip()]
            return title.strip(), artists
        return entry.strip(), []


__all__ = ["PlaylistManager"]
