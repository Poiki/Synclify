from __future__ import annotations

from typing import Optional

from .adapters import PlaylistSelection, PlaylistServiceAdapter
from .adapters_impl import SpotifyAdapter, YouTubeAdapter
from .console import console
from .manager import PlaylistManager


class SynclifyCLI:
    def __init__(self) -> None:
        self.spotify: Optional[SpotifyAdapter] = None
        self.youtube: Optional[YouTubeAdapter] = None

    def run(self) -> None:
        while True:
            console.print(
                "\n[bold cyan]Synclify[/bold cyan]\n"
                "1) Legacy sync mode\n"
                "2) Manage Spotify playlists\n"
                "3) Manage YouTube playlists\n"
                "4) Exit"
            )
            console.print("Select an option: ", end="")
            choice = input().strip()
            if choice == "1":
                self._run_legacy_sync()
            elif choice == "2":
                adapter = self._get_adapter("spotify")
                if adapter:
                    self._manage(adapter)
            elif choice == "3":
                adapter = self._get_adapter("youtube")
                if adapter:
                    self._manage(adapter)
            elif choice == "4":
                return
            else:
                console.print("[yellow]Invalid option.[/yellow]")

    def _get_adapter(self, name: str) -> Optional[PlaylistServiceAdapter]:
        try:
            if name == "spotify":
                if self.spotify is None:
                    self.spotify = SpotifyAdapter()
                return self.spotify
            if name == "youtube":
                if self.youtube is None:
                    self.youtube = YouTubeAdapter()
                return self.youtube
        except Exception as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            console.print(f"[red]Could not initialize {name}: {exc}[/red]")
        return None

    def _run_legacy_sync(self) -> None:
        from . import legacy_sync

        legacy_sync.main()

    def _manage(self, adapter: PlaylistServiceAdapter) -> None:
        manager = PlaylistManager(adapter)
        try:
            selection = manager.choose_playlist("management")
        except Exception as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            console.print(f"[red]Failed to fetch playlist list: {exc}[/red]")
            return
        if not selection:
            return
        self._playlist_menu(manager, selection)

    def _playlist_menu(self, manager: PlaylistManager, selection: PlaylistSelection) -> None:
        while True:
            console.print(
                f"\n[bold]Managing:[/bold] {selection.name} ({manager.adapter.name})\n"
                "1) Artist summary\n"
                "2) Add tracks\n"
                "3) Remove tracks by artist\n"
                "4) Remove similar duplicates\n"
                "5) Back"
            )
            console.print("Select an option: ", end="")
            choice = input().strip()
            try:
                if choice == "1":
                    manager.show_artist_summary(selection.id)
                elif choice == "2":
                    manager.add_tracks_interactive(selection.id)
                elif choice == "3":
                    console.print("Enter artists separated by commas: ", end="")
                    artists = [part.strip() for part in input().split(",") if part.strip()]
                    manager.remove_by_artists(selection.id, artists)
                elif choice == "4":
                    manager.remove_duplicates(selection.id)
                elif choice == "5":
                    return
                else:
                    console.print("[yellow]Invalid option.[/yellow]")
            except Exception as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                console.print(f"[red]Error managing playlist: {exc}[/red]")


def main() -> None:
    SynclifyCLI().run()


__all__ = ["SynclifyCLI", "main"]
