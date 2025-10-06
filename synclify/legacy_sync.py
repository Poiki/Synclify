# sync_playlist.py — Add-only sync Spotify <-> YouTube Music
# - Robust handling of YouTube quotaExceeded at READ, SEARCH and INSERT stages
# - "Planning mode" when YouTube API is unavailable: resolve tracks (manual / web) and export URLs to add later
# - Efficient: no videos.list (durations); compare by normalized title + artist/channel
# - "Web auto": Google search (site:music.youtube.com), propose top results (ask if ambiguous)
# - Captcha-aware Google search: show CAPTCHA URL, let the user solve it, then retry automatically
# - All comments in English

import os
import time
import json
import random
import socket
import html
import re
import urllib.parse
import webbrowser
from typing import List, Dict, Tuple, Optional, Set
from datetime import datetime
from dotenv import load_dotenv

# ---------- Pretty console ----------
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.logging import RichHandler
import logging

console = Console()
load_dotenv()

# ---------- Spotify ----------
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

# ---------- YouTube Data API ----------
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------- Web search (Google HTML) ----------
import requests
from bs4 import BeautifulSoup

SCOPES_YT = ["https://www.googleapis.com/auth/youtube"]
TOKENS_DIR = ".tokens"
CACHE_DIR = ".cache"
os.makedirs(TOKENS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ---------- Logging ----------
logger = logging.getLogger("sync")
logger.setLevel(logging.INFO)
handler = RichHandler(
    console=console, show_time=False, show_level=True, show_path=False, markup=True
)
if not logger.handlers:
    logger.addHandler(handler)

# ---------- Config ----------
# We avoid using videos.list to save YouTube quota.
YOUTUBE_SEARCH_RATE_SLEEP = 0.10  # throttle between YouTube search API calls
YOUTUBE_INSERT_RATE_SLEEP = 0.05  # throttle between playlist insert calls
SPOTIFY_ADD_BATCH = 100
RETRY_MAX_TRIES = 5
RETRY_BASE_SLEEP = 0.5
WEB_SEARCH_THROTTLE = 0.20  # be nice to Google (HTML) when scraping results

STATE = {
    "yt_search_disabled": False,  # set True when quotaExceeded first happens
    "prompted_after_quota": False,  # ensure we only ask once
    "continue_manual_after_quota": False,  # user's choice after quota
    "continue_web_auto_after_quota": False,  # user's choice after quota
}

# Will be set in main() when needed (planning mode)
PLAN_MODE_ONLY = (
    False  # if True, we do not call YouTube API for writes; export URLs instead
)
pending_web_adds: List[str] = (
    []
)  # collected videoIds/URLs to export when in planning mode
added_video_ids: Set[str] = set()  # guard against re-adding same videoId in this run


# ---------- Small helpers ----------
def header(title: str):
    console.print(
        Panel.fit(
            f"[bold white]{title}[/bold white]", border_style="cyan", padding=(1, 2)
        )
    )


def input_choice(prompt: str, choices: List[str]) -> str:
    """Prompt the user until one of the allowed choices is entered (case-insensitive)."""
    ch_lower = [c.lower() for c in choices]
    while True:
        console.print(f"[bold]{prompt}[/bold] ({'/'.join(choices)}): ", end="")
        val = input().strip().lower()
        if val in ch_lower:
            return val
        console.print("[yellow]Invalid option.[/yellow]")


def input_yesno(prompt: str) -> bool:
    """Accept yes/no in English or Spanish."""
    while True:
        console.print(f"{prompt} [y/n]: ", end="")
        v = input().strip().lower()
        if v in ("y", "yes", "s", "si", "sí"):
            return True
        if v in ("n", "no"):
            return False
        console.print("[yellow]Please answer y/n.[/yellow]")


def input_index(
    prompt: str, min_val: int, max_val: int, allow_zero: bool = False
) -> int:
    """Ask for an index in range; optionally allow 0 as 'skip'."""
    while True:
        console.print(f"{prompt}: ", end="")
        raw = input().strip()
        if not raw.isdigit():
            console.print("[yellow]Enter a number.[/yellow]")
            continue
        n = int(raw)
        if allow_zero and n == 0:
            return 0
        if min_val <= n <= max_val:
            return n
        console.print(
            f"[yellow]Enter a number between {min_val} and {max_val}{' or 0' if allow_zero else ''}.[/yellow]"
        )


# ---------- Cache (avoid repeat API/web calls) ----------
class SearchCache:
    """Tiny JSON cache for resolved IDs across runs."""

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, str] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get(self, key: str) -> Optional[str]:
        return self.data.get(key)

    def put(self, key: str, value: str):
        self.data[key] = value


cache = SearchCache(os.path.join(CACHE_DIR, "sync_cache.json"))


def cache_key(service: str, name: str, artists: List[str]) -> str:
    """Build a stable cache key from service + normalized fields."""
    a = " ".join(sorted(artists)) if artists else ""
    return f"{service}|{name.strip().lower()}|{a.strip().lower()}"


# ---------- Retry / backoff ----------
def is_retriable_http_error(e: HttpError) -> bool:
    """Return True if error is likely transient and safe to retry."""
    st = getattr(e.resp, "status", None)
    if st is None:
        return False
    if 500 <= st <= 599:
        return True
    if st == 429:
        return True
    if st == 403:
        txt = str(e).lower()
        if "quota" in txt and "exceeded" in txt:
            return False
        if "rate limit" in txt or "userratelimitexceeded" in txt:
            return True
    return False


def is_quota_exceeded_http_error(e: HttpError) -> bool:
    """Detect quota exhausted errors for YouTube API."""
    try:
        st = getattr(e.resp, "status", None)
        txt = str(e).lower()
        return st == 403 and "quota" in txt and "exceeded" in txt
    except Exception:
        return False


def retry_call(func, *args, **kwargs):
    """Generic retry wrapper with exponential backoff for Spotify/YouTube/HTTP errors."""
    tries = 0
    while True:
        tries += 1
        try:
            return func(*args, **kwargs)
        except HttpError as e:
            st = getattr(e.resp, "status", None)
            text = str(e)
            if st == 403 and ("quota" in text.lower() and "exceeded" in text.lower()):
                logger.error(
                    "[red]YouTube daily quota exceeded.[/red] Automatic API search is disabled."
                )
                STATE["yt_search_disabled"] = True
                raise
            if is_retriable_http_error(e) and tries < RETRY_MAX_TRIES:
                sleep = RETRY_BASE_SLEEP * (2 ** (tries - 1)) + random.uniform(0, 0.2)
                logger.warning(f"[yellow]HTTP {st}[/yellow] retrying in {sleep:.2f}s…")
                time.sleep(sleep)
                continue
            raise
        except (socket.timeout, TimeoutError):
            if tries < RETRY_MAX_TRIES:
                sleep = RETRY_BASE_SLEEP * (2 ** (tries - 1)) + random.uniform(0, 0.2)
                logger.warning(f"[yellow]Timeout[/yellow] retrying in {sleep:.2f}s…")
                time.sleep(sleep)
                continue
            raise
        except SpotifyException as e:
            if e.http_status == 429 and tries < RETRY_MAX_TRIES:
                retry_after = 1.0
                try:
                    retry_after = float(e.headers.get("Retry-After", "1"))
                except Exception:
                    pass
                sleep = max(retry_after, RETRY_BASE_SLEEP * (2 ** (tries - 1)))
                logger.warning(
                    f"[yellow]Spotify 429][/yellow] retrying in {sleep:.2f}s…"
                )
                time.sleep(sleep)
                continue
            raise


# ---------- Normalization / keys (robust title + artist handling) ----------
import unicodedata

FEAT_PAT = re.compile(r"\b(feat\.?|ft\.?|con|with)\b", re.IGNORECASE)
REMIX_PAT = re.compile(
    r"\b(remix|radio edit|extended|edit|version|karaoke|cover|live|remastered|mono|stereo)\b",
    re.IGNORECASE,
)


def clean_title(s: str) -> str:
    """Normalize a track title aggressively to reduce false mismatches."""
    s2 = (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    # Drop noise inside () or [] if it contains typical non-identity words
    s2 = re.sub(
        r"[\(\[][^)\]]*(official|video|audio|lyric|lyrics|mv|hd|4k|remaster(ed)?|live|cover)[^)\]]*[\)\]]",
        " ",
        s2,
    )
    # Remove suffix like " - radio edit / remix ..."
    s2 = re.sub(r"\s*[-–—]\s*" + REMIX_PAT.pattern + r".*$", " ", s2)
    s2 = REMIX_PAT.sub(" ", s2)
    # Remove separators and extra spaces
    s2 = re.sub(r"[\|\-–—_]+", " ", s2)
    s2 = re.sub(r"\s+", " ", s2)
    return s2.strip()


def clean_artists(artists: List[str]) -> List[str]:
    """Return up to two most informative tokens for artist signature."""
    out = []
    for a in artists or []:
        a2 = (
            unicodedata.normalize("NFKD", a)
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower()
        )
        a2 = FEAT_PAT.split(a2)[0]  # keep left part before 'feat/ft/con/with'
        a2 = re.sub(r"[,/&;+]+", " ", a2)
        a2 = re.sub(r"\s+", " ", a2).strip()
        if a2:
            out.extend([p for p in a2.split(" ") if p])
    # pick top-2 longest distinct tokens as signature
    out = sorted(set(out), key=lambda x: (-len(x), x))
    return out[:2]


def key_loose(name: str, artists: List[str]) -> str:
    """Loose key = normalized title + compact artist signature."""
    t = clean_title(name)
    a = " ".join(clean_artists(artists))
    return f"{t}::{a}"


def key_title_only(name: str) -> str:
    """Fallback key = normalized title only (to catch 'Radio Edit' / 'Remix' differences)."""
    return clean_title(name)


def deduplicate_tracks(tracks: List[Dict]) -> List[Dict]:
    """Remove duplicates within a list by (title+artist) and then by title only, keeping the first."""
    seen: Set[str] = set()
    seen_title: Set[str] = set()
    out = []
    for t in tracks:
        k = key_loose(t.get("name", ""), t.get("artists", []))
        kt = key_title_only(t.get("name", ""))
        if k in seen or (kt in seen_title):
            continue
        seen.add(k)
        seen_title.add(kt)
        out.append(t)
    return out


# ---------- URL parsers (manual fallback) ----------
from urllib.parse import urlparse, parse_qs


def parse_spotify_track_uri(url_or_uri: str) -> Optional[str]:
    """Parse Spotify track URL/URI and return canonical 'spotify:track:ID' or None."""
    s = url_or_uri.strip()
    if not s:
        return None
    if s.startswith("spotify:track:"):
        return s
    try:
        parts = urlparse(s)
        if parts.netloc.endswith("open.spotify.com") and parts.path.startswith(
            "/track/"
        ):
            tid = parts.path.split("/")[2]
            if tid:
                return f"spotify:track:{tid}"
    except Exception:
        return None
    return None


def parse_youtube_video_id(url: str) -> Optional[str]:
    """Extract the YouTube videoId from a (music.)youtube.com or youtu.be URL."""
    s = url.strip()
    if not s:
        return None
    try:
        u = urlparse(s)
        if u.netloc in ("youtu.be",):
            vid = u.path.lstrip("/")
            return vid or None
        if "youtube.com" in u.netloc or "music.youtube.com" in u.netloc:
            qs = parse_qs(u.query)
            vid = (qs.get("v") or [None])[0]
            if vid:
                return vid
    except Exception:
        return None
    return None


# ---------- Spotify helpers ----------
def spotify_preflight(sp_auth: SpotifyOAuth, sp_client: spotipy.Spotify):
    """Print who is authenticated and validate basic access; helpful for 403 diagnostics."""
    try:
        me = sp_client.me()
        logger.info(f"[cyan]Spotify user:[/cyan] {me.get('id')} ({me.get('email')})")
    except Exception:
        logger.warning("[yellow]Could not fetch Spotify profile (sp.me()).[/yellow]")

    tok = None
    try:
        tok = sp_auth.get_cached_token()
    except Exception:
        pass
    scopes = (tok or {}).get("scope", "")
    logger.info(f"[cyan]Token scopes:[/cyan] {scopes or '(unknown)'}")

    # Minimal call to detect 403 early
    try:
        _ = sp_client.current_user_playlists(limit=1)
        logger.info("[green]Spotify API OK (playlists accessible).[/green]")
    except SpotifyException as e:
        if e.http_status == 403:
            logger.error(
                "[red]Spotify 403.[/red] Fix in Dashboard:\n"
                "  • Add your user under 'Users and Access' (Development mode)\n"
                "  • Ensure Redirect URI matches exactly (e.g., http://127.0.0.1:8000/callback)\n"
                "  • Delete ./.tokens/spotify_token.json and re-auth"
            )
            raise
        else:
            raise


def get_spotify_client() -> Tuple[spotipy.Spotify, SpotifyOAuth]:
    """Create an authenticated Spotify client (Spotipy) and return (client, auth_manager)."""
    client_id = os.getenv("SPOTIFY_CLIENT_ID") or input("SPOTIFY_CLIENT_ID: ").strip()
    client_secret = (
        os.getenv("SPOTIFY_CLIENT_SECRET") or input("SPOTIFY_CLIENT_SECRET: ").strip()
    )
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback")
    scope = "playlist-read-private playlist-modify-private playlist-modify-public user-library-read"
    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        cache_path=os.path.join(TOKENS_DIR, "spotify_token.json"),
        show_dialog=True,
    )
    sp_client = spotipy.Spotify(auth_manager=auth)
    # Preflight check to surface 403 early with clear guidance
    spotify_preflight(auth, sp_client)
    return sp_client, auth


def list_spotify_playlists(sp: spotipy.Spotify) -> List[Dict]:
    """List the user's Spotify playlists."""
    playlists = []
    results = retry_call(sp.current_user_playlists, limit=50)
    while results:
        playlists.extend(results["items"])
        results = retry_call(sp.next, results) if results.get("next") else None
    return playlists


def pick_spotify_playlist(sp: spotipy.Spotify, purpose: str) -> Tuple[str, str]:
    """Prompt user to pick a Spotify playlist; return (playlist_id, name)."""
    pls = list_spotify_playlists(sp)
    if not pls:
        logger.warning("No Spotify playlists found.")
        return ("", "")
    table = Table(
        title=f"Spotify Playlists ({purpose})",
        show_lines=False,
        title_style="bold cyan",
    )
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Tracks", justify="right")
    for i, p in enumerate(pls, 1):
        table.add_row(str(i), p["name"], str(p["tracks"]["total"]))
    console.print(table)
    idx = int(input("Pick playlist number: "))
    chosen = pls[idx - 1]
    return chosen["id"], chosen["name"]


def create_spotify_playlist(
    sp: spotipy.Spotify, name: str, public=False, description="Sync created by script"
):
    """Create a Spotify playlist and return its ID."""
    me = retry_call(sp.me)
    pl = retry_call(
        sp.user_playlist_create, me["id"], name, public=public, description=description
    )
    return pl["id"]


def get_spotify_tracks(sp: spotipy.Spotify, playlist_id: str) -> List[Dict]:
    """Read Spotify tracks from a playlist as simple dicts (name + artists + IDs)."""
    tracks = []
    results = retry_call(
        sp.playlist_items, playlist_id, additional_types=("track",), limit=100
    )
    while True:
        for it in results["items"]:
            t = it.get("track")
            if not t or t.get("is_local"):
                continue
            tracks.append(
                {
                    "service": "spotify",
                    "id": t["id"],
                    "uri": t["uri"],
                    "name": t["name"],
                    "artists": [a["name"] for a in t.get("artists", [])],
                }
            )
        if results.get("next"):
            results = retry_call(sp.next, results)
        else:
            break
    return tracks


def add_spotify_tracks(sp: spotipy.Spotify, playlist_id: str, uris: List[str]):
    """Add tracks to a Spotify playlist in batches."""
    for i in range(0, len(uris), SPOTIFY_ADD_BATCH):
        retry_call(sp.playlist_add_items, playlist_id, uris[i : i + SPOTIFY_ADD_BATCH])


# ---------- YouTube helpers (avoid videos.list to save quota) ----------
def get_youtube_client():
    """Create an authenticated YouTube Data API v3 client."""
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
            with open("client_secret.json", "r") as f:
                data = json.load(f)
            info = data.get("installed") or {}
            project_id = info.get("project_id", "unknown")
            project_number = (info.get("client_id", "").split("-")[0]) or "unknown"
            logger.info(
                f"[cyan]Google OAuth:[/cyan] project_id={project_id} project_number={project_number}"
            )
        except Exception:
            pass
        flow = InstalledAppFlow.from_client_secrets_file(
            "client_secret.json", SCOPES_YT
        )
        creds = flow.run_local_server(port=8081, prompt="consent")
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def list_youtube_playlists(youtube):
    """List the user's YouTube playlists (title + itemCount)."""
    pls = []
    req = youtube.playlists().list(
        part="snippet,contentDetails", mine=True, maxResults=50
    )
    while req:
        try:
            res = retry_call(req.execute)
        except HttpError as e:
            msg = str(e)
            if getattr(e.resp, "status", None) == 403 and (
                "accessNotConfigured" in msg or "youtube.googleapis.com" in msg
            ):
                logger.error(
                    "[red]YouTube Data API v3 is not enabled in your project.[/red]\n"
                    "Fix:\n  1) APIs & Services → Library → YouTube Data API v3 → Enable\n"
                    "  2) OAuth consent screen: External + add your email to Test users\n"
                    "  3) Delete ./.tokens/youtube_token.json and try again"
                )
                raise SystemExit(1)
            raise
        pls.extend(res.get("items", []))
        req = youtube.playlists().list_next(req, res)
    return pls


def get_youtube_playlist_tracks(youtube, playlist_id: str) -> List[Dict]:
    """Read playlist items (title + owner channel). Avoid videos.list to save quota."""
    items = []
    req = youtube.playlistItems().list(
        part="snippet,contentDetails", playlistId=playlist_id, maxResults=50
    )
    while req:
        res = retry_call(req.execute)
        for it in res.get("items", []):
            sn = it["snippet"]
            title = sn["title"]
            channel = sn.get("videoOwnerChannelTitle") or sn.get("channelTitle") or ""
            items.append(
                {
                    "service": "youtube",
                    "video_id": sn["resourceId"]["videoId"],
                    "name": title,
                    "artists": [channel] if channel else [],
                }
            )
        req = youtube.playlistItems().list_next(req, res)
    return items


def add_youtube_videos(youtube, playlist_id: str, video_ids: List[str]):
    """Insert videos into a YouTube playlist one by one (simplest, reliable)."""
    for vid in video_ids:
        # Avoid adding the same id twice in this run:
        if vid in added_video_ids:
            continue
        retry_call(
            youtube.playlistItems()
            .insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": vid},
                    }
                },
            )
            .execute
        )
        added_video_ids.add(vid)
        time.sleep(YOUTUBE_INSERT_RATE_SLEEP)


# ---------- Destination search (minimal YouTube API search) ----------
def yt_search_one(youtube, query: str) -> Optional[str]:
    """Minimal-cost API search: ask for 1 result (no videos.list)."""
    if STATE["yt_search_disabled"]:
        return None

    def do_search():
        return (
            youtube.search()
            .list(part="id", q=query, type="video", maxResults=1)
            .execute()
        )

    res = retry_call(do_search)
    time.sleep(YOUTUBE_SEARCH_RATE_SLEEP)
    items = res.get("items", [])
    if not items:
        return None
    return items[0]["id"]["videoId"]


def sp_search_one(sp: spotipy.Spotify, name: str, artists: List[str]) -> Optional[str]:
    """Simple Spotify search for one best match."""
    try:
        q = f"{name} {' '.join(artists)}"
        res = retry_call(sp.search, q=q, type="track", limit=1)
        items = res.get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]
    except SpotifyException as e:
        logger.error(f"[red]Spotify search error:[/red] {e}")
    return None


# ---------- Google HTML search (site:music.youtube.com) ----------
def strip_tracking_params(u: str) -> str:
    """Keep only relevant query params (v, list); drop tracking like &si, &pp etc."""
    try:
        p = urllib.parse.urlparse(u)
        q = urllib.parse.parse_qs(p.query)
        keep = {}
        if "v" in q:
            keep["v"] = q["v"]
        if "list" in q:
            keep["list"] = q["list"]
        uq = urllib.parse.urlencode(keep, doseq=True)
        return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", uq, ""))
    except Exception:
        return u


def token_set(s: str) -> Set[str]:
    """Basic tokenization to compare strings in a language-agnostic way."""
    s = (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    stop = {
        "the",
        "a",
        "an",
        "official",
        "video",
        "audio",
        "lyrics",
        "lyric",
        "remix",
        "edit",
        "radio",
        "version",
        "feat",
        "ft",
        "con",
        "with",
    }
    return {w for w in s.split() if w and w not in stop}


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    i = len(a & b)
    u = len(a | b)
    return i / u if u else 0.0


def google_music_search(
    query_title: str, query_artists: List[str], max_results: int = 8
) -> List[Tuple[str, str]]:
    """
    Google search restricted to music.youtube.com.
    Captcha-aware: if Google returns 429/Sorry page, show it to the user to solve and retry.
    Returns list of (clean_url, google_result_title).
    """
    # Build a precise query: quoted title + top artist tokens
    title_q = f'"{query_title}"'
    artist_terms = " ".join(query_artists[:2])  # two most informative tokens
    q = f"site:music.youtube.com {title_q} {artist_terms}".strip()

    url = "https://www.google.com/search"
    params = {
        "q": q,
        "hl": "es",
        "gl": "ES",
        "num": "10",
        "safe": "off",
        "pws": "0",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }

    # retry loop with captcha handling
    attempts = 0
    while attempts < 3:
        attempts += 1
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=12, allow_redirects=True
            )
            # Detect explicit rate-limit / captcha flows
            if resp.status_code == 429 or "sorry" in resp.url:
                cap_url = resp.url
                console.print(
                    Panel.fit(
                        "[yellow]Google rate-limited the search (429).[/yellow]\n"
                        "I will open the CAPTCHA page. Solve it in your browser, then return here and press ENTER to retry.",
                        border_style="yellow",
                    )
                )
                console.print(f"[blue]CAPTCHA URL:[/blue] {cap_url}")
                try:
                    webbrowser.open(cap_url)
                except Exception:
                    pass
                input("Press ENTER after solving the CAPTCHA to retry… ")
                time.sleep(1.0 + attempts * 0.5)
                continue

            resp.raise_for_status()
            html_text = resp.text
        except Exception as e:
            logger.error(f"[red]Google search failed:[/red] {e}")
            time.sleep(0.8 * attempts)
            if attempts < 3:
                continue
            return []

        soup = BeautifulSoup(html_text, "html.parser")
        results: List[Tuple[str, str]] = []

        # Broadly select links; filter strictly by domain and path type afterward
        for a in soup.select("a"):
            href = a.get("href") or ""
            text = a.get_text(" ", strip=True)
            if not href or not text:
                continue
            # unwrap '/url?q=' style redirects
            if href.startswith("/url?"):
                parsed = urllib.parse.urlparse(href)
                real = urllib.parse.parse_qs(parsed.query).get("q", [None])[0]
                if real:
                    href = real

            if "music.youtube.com" not in href:
                continue
            if not ("/watch" in href or "/playlist" in href):
                continue

            clean = strip_tracking_params(href)
            results.append((clean, html.unescape(text)))
            if len(results) >= max_results:
                break

        # Deduplicate by cleaned URL
        seen = set()
        uniq = []
        for u, t in results:
            if u in seen:
                continue
            seen.add(u)
            uniq.append((u, t))

        time.sleep(WEB_SEARCH_THROTTLE)
        return uniq

    # If all attempts exhausted
    return []


def pick_best_web_result(
    results: List[Tuple[str, str]], track_title: str, artists: List[str]
) -> Optional[str]:
    """
    Auto-pick the best candidate by simple similarity.
    If ambiguous, return None so the user can choose.
    """
    if not results:
        return None
    want = token_set(track_title) | set(clean_artists(artists))
    scored = []
    for u, t in results:
        got = token_set(t)
        score = jaccard(want, got)
        # Bonus if all title tokens are present
        title_tokens = token_set(track_title)
        if title_tokens and title_tokens.issubset(got):
            score += 0.15
        # Small penalty for playlists (prefer single videos)
        if "/playlist" in u:
            score -= 0.08
        scored.append((score, u, t))
    scored.sort(reverse=True)
    best = scored[0]
    if best[0] >= 0.35:
        return parse_youtube_video_id(best[1])
    return None


def pick_from_web_results(
    results: List[Tuple[str, str]], track_display: str
) -> Optional[str]:
    """
    If multiple plausible results, ask user to choose among top 5 (0 to skip).
    Returns a videoId or None.
    """
    if not results:
        return None
    if len(results) == 1:
        return parse_youtube_video_id(results[0][0])
    table = Table(
        title=f"Google results for: {track_display}",
        show_lines=False,
        title_style="bold green",
    )
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("URL")
    for i, (u, t) in enumerate(results[:5], 1):
        table.add_row(str(i), t, u)
    console.print(table)
    idx = input_index(
        "Pick a result (0 = skip)", 1, min(5, len(results)), allow_zero=True
    )
    if idx == 0:
        return None
    return parse_youtube_video_id(results[idx - 1][0])


# ---------- After-quota prompt (consistent UX) ----------
def prompt_after_quota() -> None:
    """
    Ask once how to proceed for remaining tracks after YouTube daily quota is exhausted.
    Options:
      1) Manual: user pastes YouTube URLs.
      2) Web auto: use Google (site:music.youtube.com) and propose candidates.
      3) Stop.
    """
    if STATE["prompted_after_quota"]:
        return
    STATE["prompted_after_quota"] = True

    console.print(
        Panel.fit(
            "[red]YouTube daily quota exhausted.[/red]\n"
            "Choose how to continue for the remaining tracks:\n"
            "  [bold]1[/bold]) Manual: paste YouTube Music URLs yourself.\n"
            "  [bold]2[/bold]) Web auto: I will search Google (site:music.youtube.com) and propose candidates.\n"
            "  [bold]3[/bold]) Stop.",
            border_style="red",
        )
    )
    while True:
        console.print("Option [1/2/3]: ", end="")
        opt = (input().strip() or "").lower()
        if opt in {"1", "2", "3"}:
            break
        console.print("[yellow]Invalid option.[/yellow]")

    STATE["continue_manual_after_quota"] = opt == "1"
    STATE["continue_web_auto_after_quota"] = opt == "2"
    # If opt == "3", both flags remain False => we will stop later.


# ---------- Main ----------
def main():
    global PLAN_MODE_ONLY, pending_web_adds
    header("🎵 Sync Spotify ↔︎ YouTube Music")
    console.print(f"[dim]Start:[/dim] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    sp = None
    yt = None
    src = input_choice("Source service", ["spotify", "youtube"])
    dst = input_choice("Destination service", ["spotify", "youtube"])
    if src == dst:
        logger.warning(
            "[yellow]Source and destination are the same.[/yellow] Only duplicates would be cleaned (not performed in this mode)."
        )

    # Authenticate
    sp_auth = None
    if "spotify" in (src, dst):
        logger.info("[cyan]Authenticating with Spotify…[/cyan]")
        sp, sp_auth = get_spotify_client()
    if "youtube" in (src, dst):
        logger.info("[red]Authenticating with YouTube…[/red]")
        yt = get_youtube_client()

    # Pick source playlist
    if src == "spotify":
        src_id, src_name = pick_spotify_playlist(sp, "source")
    else:
        pls = list_youtube_playlists(yt)
        if not pls:
            logger.error("No YouTube playlists found.")
            return
        table = Table(
            title="YouTube Playlists (source)", show_lines=False, title_style="bold red"
        )
        table.add_column("#", justify="right")
        table.add_column("Name")
        table.add_column("Items", justify="right")
        for i, p in enumerate(pls, 1):
            table.add_row(
                str(i),
                p["snippet"]["title"],
                str(p["contentDetails"].get("itemCount", 0)),
            )
        console.print(table)
        idx = int(input("Pick playlist number: "))
        chosen = pls[idx - 1]
        src_id, src_name = chosen["id"], chosen["snippet"]["title"]
    if not src_id:
        logger.error("No source playlist selected.")
        return

    # Pick or create destination playlist
    console.print()
    console.print(
        Panel.fit(
            "Use an existing destination playlist or create a new one?",
            border_style="magenta",
        )
    )
    use_existing = input_yesno("Use existing? (No = create new)")
    if use_existing:
        if dst == "spotify":
            dst_id, dst_name = pick_spotify_playlist(sp, "destination")
        else:
            pls = list_youtube_playlists(yt)
            if not pls:
                logger.error("No YouTube playlists found.")
                return
            table = Table(
                title="YouTube Playlists (destination)",
                show_lines=False,
                title_style="bold red",
            )
            table.add_column("#", justify="right")
            table.add_column("Name")
            table.add_column("Items", justify="right")
            for i, p in enumerate(pls, 1):
                table.add_row(
                    str(i),
                    p["snippet"]["title"],
                    str(p["contentDetails"].get("itemCount", 0)),
                )
            console.print(table)
            idx = int(input("Pick playlist number: "))
            chosen = pls[idx - 1]
            dst_id, dst_name = chosen["id"], chosen["snippet"]["title"]
        if not dst_id:
            logger.error("No destination playlist selected.")
            return
    else:
        new_name = input("Name for the new destination playlist: ").strip()
        if dst == "spotify":
            dst_id = create_spotify_playlist(sp, new_name)
        else:
            dst_id = retry_call(
                yt.playlists()
                .insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": new_name,
                            "description": "Sync created by script",
                        },
                        "status": {"privacyStatus": "private"},
                    },
                )
                .execute
            )["id"]
        dst_name = new_name

    console.print()
    header("Selection summary")
    table = Table(show_lines=False)
    table.add_column("Role", style="bold")
    table.add_column("Service")
    table.add_column("Playlist")
    table.add_row("Source", src, src_name)
    table.add_row("Destination", dst, dst_name)
    console.print(table)

    # Load tracks (source)
    logger.info("Loading [bold]source[/bold] tracks…")
    if src == "spotify":
        src_tracks = get_spotify_tracks(sp, src_id)
    else:
        src_tracks = get_youtube_playlist_tracks(yt, src_id)
    # Deduplicate early to simplify comparisons
    src_tracks = deduplicate_tracks(src_tracks)
    logger.info(f"Source has [bold]{len(src_tracks)}[/bold] items.")

    # Load tracks (destination)
    logger.info("Loading [bold]destination[/bold] tracks…")
    if dst == "spotify":
        dst_tracks = get_spotify_tracks(sp, dst_id)
        dst_tracks = deduplicate_tracks(dst_tracks)
    else:
        try:
            dst_tracks = get_youtube_playlist_tracks(yt, dst_id)
            dst_tracks = deduplicate_tracks(dst_tracks)
        except HttpError as e:
            if is_quota_exceeded_http_error(e):
                STATE["yt_search_disabled"] = True
                console.print(
                    Panel.fit(
                        "[red]YouTube daily quota has been exhausted while reading the destination playlist.[/red]\n"
                        "You can:\n"
                        "  • Continue in [bold]planning mode[/bold]: no further YouTube API calls; we will resolve via Manual/Web and export URLs to add later.\n"
                        "  • Stop now.",
                        border_style="red",
                    )
                )
                cont = input_yesno(
                    "Continue in planning mode (no API calls to YouTube)?"
                )
                if cont:
                    globals()["PLAN_MODE_ONLY"] = True
                    # Prefer Web Auto in planning mode unless user later chooses manual
                    STATE["continue_web_auto_after_quota"] = True
                    dst_tracks = (
                        []
                    )  # treat destination as empty (add-only; we won't delete anyway)
                    logger.warning(
                        "[yellow]Destination will be treated as empty for comparison.[/yellow]"
                    )
                else:
                    logger.info(
                        "Stopped by user after quota exhaustion while reading destination."
                    )
                    return
            else:
                raise
    logger.info(
        f"Destination currently has [bold]{len(dst_tracks)}[/bold] items (add-only; extras will be kept)."
    )

    # Build loose keys and compute differences
    src_map = {key_loose(t["name"], t.get("artists", [])): t for t in src_tracks}
    dst_keyset = {key_loose(t["name"], t.get("artists", [])) for t in dst_tracks}
    dst_titles = {key_title_only(t["name"]) for t in dst_tracks}

    already = len(src_map.keys() & dst_keyset)
    to_add = []
    for k, t in src_map.items():
        if k in dst_keyset or key_title_only(t["name"]) in dst_titles:
            continue
        to_add.append(t)

    console.print()
    header("Pre-check")
    pre = Table(show_lines=False)
    pre.add_column("Metric")
    pre.add_column("Value", justify="right")
    pre.add_row("Source items", str(len(src_tracks)))
    pre.add_row("Destination items (before)", str(len(dst_tracks)))
    pre.add_row("Already matched (loose key)", str(already))
    pre.add_row("Missing (to add)", str(len(to_add)))
    console.print(pre)

    # Resolve & add
    to_add_sp_uris: List[str] = []

    if to_add:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task(
                "[cyan]Resolving & adding…[/cyan]", total=len(to_add)
            )

            for t in to_add:
                display = f"{t['name']} — {', '.join(t.get('artists', []))}".strip(" —")

                # -------- Destination: Spotify --------
                if dst == "spotify":
                    ck = cache_key("spotify", t["name"], t.get("artists", []))
                    uri = cache.get(ck)
                    if not uri:
                        uri = sp_search_one(sp, t["name"], t.get("artists", []))
                        if uri:
                            cache.put(ck, uri)
                            cache.save()
                    if not uri:
                        progress.stop()
                        console.print(
                            f"[yellow]Not found on Spotify:[/yellow] {display}. Paste URL (or ENTER to skip): ",
                            end="",
                        )
                        manual = parse_spotify_track_uri(input().strip())
                        progress.start()
                        if manual:
                            uri = manual
                    if uri:
                        to_add_sp_uris.append(uri)

                # -------- Destination: YouTube --------
                else:
                    vid = cache.get(
                        cache_key("youtube", t["name"], t.get("artists", []))
                    )

                    # 1) Try API search (until quota)
                    if (
                        not vid
                        and not STATE["yt_search_disabled"]
                        and not PLAN_MODE_ONLY
                    ):
                        try:
                            q = f"{t['name']} {' '.join(t.get('artists', []))}".strip()
                            vid = yt_search_one(yt, q)
                        except HttpError:
                            # Quota exceeded here -> stop progress, ask, set flags, resume
                            try:
                                progress.stop()
                            except Exception:
                                pass
                            prompt_after_quota()
                            if (
                                not STATE["continue_manual_after_quota"]
                                and not STATE["continue_web_auto_after_quota"]
                            ):
                                logger.info(
                                    "Stopped after quota exhaustion (no continuation selected)."
                                )
                                return
                            STATE["yt_search_disabled"] = True
                            vid = None
                            try:
                                progress.start()
                            except Exception:
                                pass

                    # 2) If API not available / no result -> manual or web auto
                    if not vid:
                        q_art = clean_artists(t.get("artists", []))
                        # Prefer web auto (also in planning mode) unless user explicitly chose manual
                        if not STATE["continue_manual_after_quota"]:
                            web_results = google_music_search(
                                t["name"], q_art, max_results=8
                            )
                            auto_vid = (
                                pick_best_web_result(
                                    web_results, t["name"], t.get("artists", [])
                                )
                                if web_results
                                else None
                            )
                            if auto_vid:
                                vid = auto_vid
                            else:
                                progress.stop()
                                if web_results:
                                    vid = pick_from_web_results(web_results, display)
                                if not vid:
                                    console.print(
                                        f"[yellow]Not found automatically:[/yellow] {display}. Paste URL (or ENTER to skip): ",
                                        end="",
                                    )
                                    vid = parse_youtube_video_id(input().strip())
                                progress.start()
                        else:
                            # Explicit manual choice by user
                            progress.stop()
                            console.print(
                                f"[yellow]Missing on YouTube:[/yellow] {display}. Paste URL (or ENTER to skip): ",
                                end="",
                            )
                            vid = parse_youtube_video_id(input().strip())
                            progress.start()

                    # 3) Insert (or collect in planning mode) and cache
                    if vid:
                        if PLAN_MODE_ONLY:
                            if vid not in added_video_ids:
                                pending_web_adds.append(vid)
                                added_video_ids.add(vid)
                            cache.put(
                                cache_key("youtube", t["name"], t.get("artists", [])),
                                vid,
                            )
                            cache.save()
                        else:
                            try:
                                add_youtube_videos(yt, dst_id, [vid])
                                cache.put(
                                    cache_key(
                                        "youtube", t["name"], t.get("artists", [])
                                    ),
                                    vid,
                                )
                                cache.save()
                            except HttpError as e:
                                if is_quota_exceeded_http_error(e):
                                    # Quota during insert → offer switching to planning mode
                                    try:
                                        progress.stop()
                                    except Exception:
                                        pass
                                    console.print(
                                        Panel.fit(
                                            "[red]YouTube quota exhausted during insert.[/red]\n"
                                            "Switch to [bold]planning mode[/bold] and export URLs to add later?",
                                            border_style="red",
                                        )
                                    )
                                    if input_yesno("Switch to planning mode?"):
                                        globals()["PLAN_MODE_ONLY"] = True
                                        if vid not in added_video_ids:
                                            pending_web_adds.append(vid)
                                            added_video_ids.add(vid)
                                    else:
                                        logger.info(
                                            "Stopped by user after quota exhaustion during insert."
                                        )
                                        return
                                    try:
                                        progress.start()
                                    except Exception:
                                        pass
                                else:
                                    logger.error(
                                        f"[red]Insert failed on YouTube:[/red] {e}"
                                    )

                progress.update(task, advance=1)

    # Batch add on Spotify
    if dst == "spotify" and to_add_sp_uris:
        console.print()
        logger.info(f"Adding on Spotify: [bold]{len(to_add_sp_uris)}[/bold] tracks…")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            task2 = progress.add_task(
                "[magenta]Adding on Spotify…[/magenta]", total=len(to_add_sp_uris)
            )
            for i in range(0, len(to_add_sp_uris), SPOTIFY_ADD_BATCH):
                add_spotify_tracks(
                    sp, dst_id, to_add_sp_uris[i : i + SPOTIFY_ADD_BATCH]
                )
                progress.update(
                    task2, advance=len(to_add_sp_uris[i : i + SPOTIFY_ADD_BATCH])
                )

    # Export pending URLs if planning-only mode (YouTube quota exhausted)
    if dst == "youtube" and PLAN_MODE_ONLY and pending_web_adds:
        os.makedirs("out", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join("out", f"youtube_pending_add_{ts}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            for vid in pending_web_adds:
                if vid.startswith("http"):
                    f.write(vid + "\n")
                else:
                    f.write(f"https://music.youtube.com/watch?v={vid}\n")
        console.print(
            Panel.fit(
                f"[green]Planning file written:[/green] {out_path}\n"
                "Open it later and add those URLs when your YouTube quota resets.",
                border_style="green",
            )
        )

    # Final summary (no extra reads)
    console.print()
    header("✅ Final summary (add-only)")
    end_table = Table(show_lines=False)
    end_table.add_column("Metric")
    end_table.add_column("Value", justify="right")
    end_table.add_row("Source items", str(len(src_tracks)))
    end_table.add_row("Destination items (before)", str(len(dst_tracks)))
    if dst == "spotify":
        end_table.add_row("Added now", str(len(to_add_sp_uris)))
    else:
        end_table.add_row("Added now", "— (YouTube added per item or planning file)")
    console.print(end_table)
    console.print(
        "[dim]Add-only mode: compare by normalized title+artist; extras in destination are kept. "
        "If YouTube quota is exhausted, choose Manual or Web auto; if API is unavailable even to read, use Planning mode.[/dim]"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled by user.[/dim]")
