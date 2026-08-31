"""Shared utilities for deck scrapers."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import requests

_log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json",
}

DEFAULT_TIMEOUT = 15
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

T = TypeVar("T")


class ScraperCache:
    """Thread-safe in-memory cache for scraper data and card databases."""

    def __init__(self, default_ttl: float | None = None) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[Any, float | None]] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._store:
                return None
            val, expiry = self._store[key]
            if expiry is not None and time.monotonic() > expiry:
                del self._store[key]
                return None
            return val

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expiry = (time.monotonic() + effective_ttl) if effective_ttl is not None else None
        with self._lock:
            self._store[key] = (value, expiry)

    def get_or_set(self, key: str, factory: Callable[[], T], ttl: float | None = None) -> T:
        existing = self.get(key)
        if existing is not None:
            return existing  # type: ignore[no-any-return]
        value = factory()
        self.set(key, value, ttl=ttl)
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Global scraper cache instance
_GLOBAL_CACHE = ScraperCache()


def get_global_cache() -> ScraperCache:
    """Return the singleton ScraperCache instance."""
    return _GLOBAL_CACHE


def fetch_url(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT,
    retries: int = 2,
    backoff_factor: float = 0.3,
    session: requests.Session | None = None,
    method: str = "GET",
    raise_for_status: bool = True,
    **kwargs: Any,
) -> requests.Response:
    """Robust HTTP request helper with retry logic on transient errors."""
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    client = session if session is not None else requests

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.request(
                method,
                url,
                headers=merged_headers,
                timeout=timeout,
                **kwargs,
            )
            if raise_for_status and resp.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
                retry_after = resp.headers.get("Retry-After")
                sleep_time = float(retry_after) if retry_after and retry_after.isdigit() else backoff_factor * (2**attempt)
                _log.warning(
                    "HTTP %d fetching %s (attempt %d/%d). Retrying in %.2fs",
                    resp.status_code,
                    url,
                    attempt + 1,
                    retries + 1,
                    sleep_time,
                )
                time.sleep(sleep_time)
                continue
            if raise_for_status:
                resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < retries:
                sleep_time = backoff_factor * (2**attempt)
                _log.warning(
                    "Connection error fetching %s (attempt %d/%d): %s. Retrying in %.2fs",
                    url,
                    attempt + 1,
                    retries + 1,
                    exc,
                    sleep_time,
                )
                time.sleep(sleep_time)
            else:
                break
        except requests.HTTPError as exc:
            last_exc = exc
            if exc.response is not None and exc.response.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
                sleep_time = backoff_factor * (2**attempt)
                time.sleep(sleep_time)
            else:
                raise

    if last_exc:
        raise last_exc
    raise requests.RequestException(f"Failed to fetch {url}")


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT,
    retries: int = 2,
    **kwargs: Any,
) -> Any:
    """Fetch URL and parse response as JSON."""
    resp = fetch_url(url, headers=headers, timeout=timeout, retries=retries, **kwargs)
    return resp.json()


def fetch_text(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT,
    retries: int = 2,
    **kwargs: Any,
) -> str:
    """Fetch URL and return response text."""
    resp = fetch_url(url, headers=headers, timeout=timeout, retries=retries, **kwargs)
    return resp.text


def sanitize_filename(name: str) -> str:
    """Sanitize a deck name to be a safe, cross-platform filename."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", name)
    sanitized = sanitized.strip(". ")
    if not any(c not in " ._" for c in sanitized):
        return "deck"
    return sanitized


def resources_dir() -> Path:
    """Return the project resources directory, handling frozen (.exe) and dev environments."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def generate_fallback_back(
    path: Path,
    bg: str,
    border: str,
    size: tuple[int, int] = (480, 670),
) -> Path:
    """Draw a plain colored card-back rectangle and save it to path. Returns path."""
    from PIL import Image, ImageDraw

    W, H = size
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    bw = max(6, W // 25)
    draw.rectangle([0, 0, W - 1, H - 1], outline=border, width=bw)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path

