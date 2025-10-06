from __future__ import annotations

import html
import time
import urllib.parse
import webbrowser
from typing import List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup

from .console import console, logger
from .config import WEB_SEARCH_THROTTLE
from .utils import clean_artists, parse_youtube_video_id


def strip_tracking_params(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        keep = {}
        if "v" in query:
            keep["v"] = query["v"]
        if "list" in query:
            keep["list"] = query["list"]
        encoded = urllib.parse.urlencode(keep, doseq=True)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", encoded, ""))
    except Exception:
        return url


def token_set(text: str) -> set[str]:
    tokens = (
        text.lower()
        .replace("(official)", " ")
        .replace("[official]", " ")
        .replace("video", " ")
        .replace("audio", " ")
    )
    tokens = "".join(ch if ch.isalnum() else " " for ch in tokens)
    stop_words = {"the", "a", "an", "feat", "featuring", "official", "video", "audio", "ft", "con", "with"}
    return {word for word in tokens.split() if word and word not in stop_words}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def google_music_search(query_title: str, query_artists: Sequence[str], max_results: int = 8) -> List[Tuple[str, str]]:
    title_query = f'"{query_title}"'
    artist_terms = " ".join(query_artists[:2])
    query = f"site:music.youtube.com {title_query} {artist_terms}".strip()

    url = "https://www.google.com/search"
    params = {"q": query, "hl": "en", "gl": "US", "num": "10", "safe": "off", "pws": "0"}
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }

    attempts = 0
    while attempts < 3:
        attempts += 1
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=12,
                allow_redirects=True,
            )
            if response.status_code == 429 or "sorry" in response.url.lower():
                captcha_url = response.url
                console.print(
                    "[yellow]Google rate-limited the search (429).[/yellow]\n"
                    "Solve the CAPTCHA in your browser, then press ENTER to retry."
                )
                console.print(f"[blue]CAPTCHA URL:[/blue] {captcha_url}")
                try:
                    webbrowser.open(captcha_url)
                except Exception:
                    pass
                input("Press ENTER after solving the CAPTCHA to retry...")
                time.sleep(1.0 + attempts * 0.5)
                continue
            response.raise_for_status()
            body = response.text
        except Exception as err:
            logger.error(f"[red]Google search failed:[/red] {err}")
            time.sleep(0.8 * attempts)
            if attempts < 3:
                continue
            return []

        soup = BeautifulSoup(body, "html.parser")
        results: List[Tuple[str, str]] = []
        for anchor in soup.select("a"):
            href = anchor.get("href") or ""
            text = anchor.get_text(" ", strip=True)
            if not href or not text:
                continue
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

        seen: set[str] = set()
        unique: List[Tuple[str, str]] = []
        for url_value, title in results:
            if url_value in seen:
                continue
            seen.add(url_value)
            unique.append((url_value, title))
        time.sleep(WEB_SEARCH_THROTTLE)
        return unique
    return []


def pick_best_web_result(results: Sequence[Tuple[str, str]], track_title: str, artists: Sequence[str]) -> Optional[str]:
    if not results:
        return None
    desired_tokens = token_set(track_title) | set(clean_artists(artists))
    scored: List[Tuple[float, str, str]] = []
    for url_value, title in results:
        got_tokens = token_set(title)
        score = jaccard(desired_tokens, got_tokens)
        title_tokens = token_set(track_title)
        if title_tokens and title_tokens.issubset(got_tokens):
            score += 0.15
        if "/playlist" in url_value:
            score -= 0.08
        scored.append((score, url_value, title))
    scored.sort(reverse=True)
    best = scored[0]
    if best[0] >= 0.35:
        return parse_youtube_video_id(best[1])
    return None


def pick_from_web_results(results: Sequence[Tuple[str, str]], track_display: str) -> Optional[str]:
    if not results:
        return None
    from rich.table import Table

    top = list(results[:5])
    table = Table(title=f"Web results for {track_display}", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("URL")
    for idx, (url_value, title) in enumerate(top, start=1):
        table.add_row(str(idx), title, url_value)
    console.print(table)
    console.print("Pick a result (0 to skip): ", end="")
    try:
        selection = int(input().strip())
    except Exception:
        return None
    if selection == 0:
        return None
    if 1 <= selection <= len(top):
        return parse_youtube_video_id(top[selection - 1][0])
    return None


__all__ = [
    "google_music_search",
    "pick_best_web_result",
    "pick_from_web_results",
    "strip_tracking_params",
    "token_set",
    "jaccard",
]
