from __future__ import annotations

import random
import socket
import time
from typing import Any, Callable

from googleapiclient.errors import HttpError
from spotipy.exceptions import SpotifyException

from .config import RETRY_BASE_SLEEP, RETRY_MAX_TRIES
from .console import logger
from .state import STATE


def is_retriable_http_error(err: HttpError) -> bool:
    status = getattr(err.resp, "status", None)
    if status is None:
        return False
    if 500 <= status <= 599:
        return True
    if status == 429:
        return True
    if status == 403:
        text = str(err).lower()
        if "quota" in text and "exceeded" in text:
            return False
        if "rate limit" in text or "userratelimitexceeded" in text:
            return True
    return False


def is_quota_exceeded_http_error(err: HttpError) -> bool:
    try:
        status = getattr(err.resp, "status", None)
        text = str(err).lower()
        return status == 403 and "quota" in text and "exceeded" in text
    except Exception:
        return False


def retry_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Generic retry wrapper with exponential backoff for Spotify/YouTube/HTTP errors."""
    tries = 0
    while True:
        tries += 1
        try:
            return func(*args, **kwargs)
        except HttpError as err:
            status = getattr(err.resp, "status", None)
            text = str(err)
            lowered = text.lower()
            if status == 403 and "quota" in lowered and "exceeded" in lowered:
                logger.error(
                    "[red]YouTube daily quota exceeded.[/red] Automatic API search is disabled."
                )
                STATE.yt_search_disabled = True
                raise
            if is_retriable_http_error(err) and tries < RETRY_MAX_TRIES:
                sleep = RETRY_BASE_SLEEP * (2 ** (tries - 1)) + random.uniform(0, 0.2)
                logger.warning(f"[yellow]HTTP {status}[/yellow] retrying in {sleep:.2f}s")
                time.sleep(sleep)
                continue
            raise
        except (socket.timeout, TimeoutError):
            if tries < RETRY_MAX_TRIES:
                sleep = RETRY_BASE_SLEEP * (2 ** (tries - 1)) + random.uniform(0, 0.2)
                logger.warning(f"[yellow]Timeout[/yellow] retrying in {sleep:.2f}s")
                time.sleep(sleep)
                continue
            raise
        except SpotifyException as err:
            if err.http_status == 429 and tries < RETRY_MAX_TRIES:
                retry_after = 1.0
                try:
                    retry_after = float(err.headers.get("Retry-After", "1"))
                except Exception:
                    pass
                sleep = max(retry_after, RETRY_BASE_SLEEP * (2 ** (tries - 1)))
                logger.warning(
                    f"[yellow]Spotify 429[/yellow] retrying in {sleep:.2f}s"
                )
                time.sleep(sleep)
                continue
            raise


__all__ = ["retry_call", "is_retriable_http_error", "is_quota_exceeded_http_error"]
