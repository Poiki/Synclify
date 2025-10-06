from __future__ import annotations

import re
import unicodedata
import urllib.parse
from collections import Counter
from typing import Dict, List, Optional, Sequence, Set, Tuple

FEAT_PAT = re.compile(r"\b(feat\.?|ft\.?|con|with)\b", re.IGNORECASE)
REMIX_PAT = re.compile(
    r"\b(remix|radio edit|extended|edit|version|karaoke|cover|live|remastered|mono|stereo)\b",
    re.IGNORECASE,
)


def clean_title(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    normalized = re.sub(
        r"[\(\[][^)\]]*(official|video|audio|lyric|lyrics|mv|hd|4k|remaster(ed)?|live|cover)[^)\]]*[\)\]]",
        " ",
        normalized,
    )
    normalized = re.sub(r"\s*[-_]+\s*" + REMIX_PAT.pattern + r".*$", " ", normalized)
    normalized = REMIX_PAT.sub(" ", normalized)
    normalized = re.sub(r"[\|\-_/]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def clean_artists(artists: Sequence[str]) -> List[str]:
    tokens: List[str] = []
    for artist in artists or []:
        if isinstance(artist, dict):
            artist = artist.get("name") or artist.get("artist") or ""
        elif not isinstance(artist, str):
            artist = str(artist) if artist is not None else ""
        if not artist:
            continue
        normalized = (
            unicodedata.normalize("NFKD", artist)
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower()
        )
        normalized = FEAT_PAT.split(normalized)[0]
        normalized = re.sub(r"[,/&;+]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized:
            tokens.extend(token for token in normalized.split(" ") if token)
    tokens = sorted(set(tokens), key=lambda item: (-len(item), item))
    return tokens[:2]


def key_loose(name: str, artists: Sequence[str]) -> str:
    artist_key = " ".join(clean_artists(artists))
    return f"{clean_title(name)}::{artist_key}"


def key_title_only(name: str) -> str:
    return clean_title(name)


def deduplicate_tracks(tracks: Sequence[Dict]) -> List[Dict]:
    seen: Set[str] = set()
    seen_title: Set[str] = set()
    result: List[Dict] = []
    for track in tracks:
        key = key_loose(track.get("name", ""), track.get("artists", []))
        title_key = key_title_only(track.get("name", ""))
        if key in seen or title_key in seen_title:
            continue
        seen.add(key)
        seen_title.add(title_key)
        result.append(track)
    return result


def parse_spotify_track_uri(value: str) -> Optional[str]:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.startswith("spotify:track:"):
        return candidate
    try:
        parts = urllib.parse.urlparse(candidate)
        if parts.netloc.endswith("open.spotify.com") and parts.path.startswith("/track/"):
            track_id = parts.path.split("/")[2]
            if track_id:
                return f"spotify:track:{track_id}"
    except Exception:
        return None
    return None


def parse_youtube_video_id(value: str) -> Optional[str]:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urllib.parse.urlparse(candidate)
        if parsed.netloc in ("youtu.be",):
            return parsed.path.lstrip("/") or None
        if "youtube.com" in parsed.netloc or "music.youtube.com" in parsed.netloc:
            query = urllib.parse.parse_qs(parsed.query)
            video_id = (query.get("v") or [None])[0]
            if video_id:
                return video_id
    except Exception:
        return None
    return None


def summarize_by_artist(tracks: Sequence[Dict]) -> List[Tuple[str, int]]:
    counter: Counter[str] = Counter()
    for track in tracks:
        artists = track.get("artists") or ["Unknown"]
        for artist in artists:
            counter[artist] += 1
    return counter.most_common()


__all__ = [
    "clean_title",
    "clean_artists",
    "key_loose",
    "key_title_only",
    "deduplicate_tracks",
    "parse_spotify_track_uri",
    "parse_youtube_video_id",
    "summarize_by_artist",
]
