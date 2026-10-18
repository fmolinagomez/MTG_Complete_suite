"""Shared utilities for deck scrapers."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event
from typing import Any, TypeVar

import requests

from src.cancellation import Cancelled
from src.constants import ProgressCallback
from src.http_client import get as http_get

_log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
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
            if (
                raise_for_status
                and resp.status_code in RETRYABLE_STATUS_CODES
                and attempt < retries
            ):
                retry_after = resp.headers.get("Retry-After")
                sleep_time = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else backoff_factor * (2**attempt)
                )
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
            if (
                exc.response is not None
                and exc.response.status_code in RETRYABLE_STATUS_CODES
                and attempt < retries
            ):
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


def bundled_back(game: str, stem: str = "back") -> Path | None:
    """Find a bundled card back without coupling a scraper to one image format."""
    back_dir = resources_dir() / "backs" / game
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        if (path := back_dir / f"{stem}{suffix}").exists():
            return path
    return None


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


def resolve_card_back(
    game: str,
    stem: str = "back",
    *,
    bg: str = "#111111",
    border: str = "#888888",
    size: tuple[int, int] = (480, 670),
    res_dir: Path | None = None,
    cache: ScraperCache | None = None,
) -> Path:
    """Return a bundled card back if present, or a cached generated fallback PNG."""
    base_dir = res_dir if res_dir is not None else resources_dir()
    back_dir = base_dir / "backs" / game
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        if (candidate := back_dir / f"{stem}{suffix}").exists():
            return candidate

    target_cache = cache if cache is not None else _GLOBAL_CACHE
    cache_key = f"fallback_back:{game}:{stem}:{bg}:{border}:{size[0]}x{size[1]}"
    cached_path = target_cache.get(cache_key)
    if isinstance(cached_path, Path) and cached_path.exists():
        return cached_path

    import tempfile

    tmp_dir = Path(tempfile.mkdtemp())
    generated = generate_fallback_back(tmp_dir / f"{stem}.png", bg, border, size=size)
    target_cache.set(cache_key, generated)
    return generated


DOTGG_DECK_API = "https://api.dotgg.gg/cgfw/getdeck?game={game}&slug={slug}"
DOTGG_CARDS_API = "https://api.dotgg.gg/cgfw/getcards?game={game}&mode=indexed"


def fetch_dotgg_deck_data(
    game: str,
    slug: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | float = 20,
    site_label: str | None = None,
) -> dict[str, Any]:
    """Fetch and validate a deck payload from the shared dotgg API."""
    label = site_label or f"{game}.gg"
    url = DOTGG_DECK_API.format(game=game, slug=slug)
    resp = http_get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    if isinstance(getattr(resp, "text", None), str) and not resp.text.strip():
        raise ValueError(
            f"El mazo '{slug}' no está disponible en {label}.\n"
            "Puede ser privado, haber sido eliminado, o no existir.\n"
            "Asegúrate de que la URL sea correcta y el mazo sea público."
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise ValueError(
            f"La respuesta de {label} para '{slug}' no es JSON válido.\n"
            "La API puede haber cambiado temporalmente. Vuelve a intentarlo."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"La respuesta de {label} para '{slug}' no tiene el formato esperado.")
    return data


def index_dotgg_cards(raw: Any) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Index a dotgg getcards payload into ({id: card_dict}, {name_lower: id})."""
    try:
        if not isinstance(raw, dict):
            raise TypeError("Expected JSON object")
        names = raw["names"]
        rows = raw["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"La base de datos de cartas de dotgg.gg devolvió un formato inesperado: {exc}\n"
            "Puede ser un problema temporal. Vuelve a intentarlo."
        ) from exc

    id_to_card: dict[str, dict[str, Any]] = {}
    name_to_id: dict[str, str] = {}
    for row in rows:
        card = dict(zip(names, row))
        cid = str(card.get("id") or (row[0] if row else ""))
        if cid:
            id_to_card[cid] = card
            card_name = str(card.get("name") or "").lower()
            if card_name:
                name_to_id[card_name] = cid
    return id_to_card, name_to_id


def load_dotgg_card_db(
    game: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | float = 30,
    cache: ScraperCache | None = None,
    use_cache: bool = True,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Fetch and index the dotgg card database for *game*, caching in ScraperCache."""
    target_cache = cache if cache is not None else _GLOBAL_CACHE
    cache_key = f"dotgg:cards:{game}"
    if use_cache:
        cached = target_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

    url = DOTGG_CARDS_API.format(game=game)
    resp = http_get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    try:
        raw = resp.json()
    except ValueError as exc:
        raise ValueError(
            f"La base de datos de cartas de dotgg.gg devolvió un formato inesperado: {exc}\n"
            "Puede ser un problema temporal. Vuelve a intentarlo."
        ) from exc

    indexed = index_dotgg_cards(raw)
    if use_cache:
        target_cache.set(cache_key, indexed)
    return indexed


def download_url_images(
    urls: dict[str, str],
    dest_dir: Path,
    path_for_key: Callable[[str, str], Path],
    headers: dict[str, str],
    cancel_event: Event | None = None,
    progress_cb: ProgressCallback = None,
    fallback_for_key: Callable[[str], tuple[str, Path, dict[str, str]] | None] | None = None,
) -> dict[str, Path]:
    """Cache and download keyed image URLs with consistent cancellation/progress."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    def _fetch(key: str, url: str) -> tuple[str, Path]:
        if cancel_event and cancel_event.is_set():
            raise Cancelled()
        path = path_for_key(key, url)
        if path.exists():
            return key, path
        try:
            if cancel_event and cancel_event.is_set():
                raise Cancelled()
            response = http_get(url, headers=headers)
            path.write_bytes(response.content)
            return key, path
        except requests.exceptions.HTTPError as exc:
            if fallback_for_key is None or exc.response is None or exc.response.status_code != 404:
                raise
        fb = fallback_for_key(key)
        if fb is None:
            raise
        fb_url, fb_path, fb_headers = fb
        if fb_path.exists():
            return key, fb_path
        if cancel_event and cancel_event.is_set():
            raise Cancelled()
        fb_response = http_get(fb_url, headers=fb_headers)
        fb_path.write_bytes(fb_response.content)
        return key, fb_path

    result: dict[str, Path] = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch, key, url): key for key, url in urls.items()}
        for done, future in enumerate(as_completed(futures), start=1):
            if cancel_event and cancel_event.is_set():
                for pending in futures:
                    pending.cancel()
                raise Cancelled()
            key, path = future.result()
            result[key] = path
            if progress_cb:
                progress_cb(done, len(urls))
    return result
