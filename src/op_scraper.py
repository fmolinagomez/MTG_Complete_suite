"""One Piece Card Game deck scraper — supports onepiece.gg, egmanevents.com, cardkaizoku.com, onepiecetopdecks.com, gumgum.gg, limitlesstcg.com."""

from __future__ import annotations

import re
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests  # noqa: F401 - retained as the scraper's test transport seam

from src.constants import ProgressCallback
from src.http_client import get as http_get
from src.http_client import post as http_post
from src.scraper_utils import (
    download_url_images,
    fetch_dotgg_deck_data,
    fetch_json,
    fetch_text,
    generate_fallback_back,
    get_global_cache,
    load_dotgg_card_db,
)
from src.scraper_utils import resources_dir as _resources_dir

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── dotgg (onepiece.gg) ────────────────────────────────────────────────────
_DOTGG_DECK_API = "https://api.dotgg.gg/cgfw/getdeck?game=onepiece&slug={slug}"
_DOTGG_CARDS_API = "https://api.dotgg.gg/cgfw/getcards?game=onepiece&mode=indexed"
_DOTGG_IMAGE_URL = "https://static.dotgg.gg/onepiece/card/{card_id}.webp"

# ── egmanevents ────────────────────────────────────────────────────────────
_EGMAN_CARDS_API = "https://deckbuilder.egmanevents.com/api/cards/optcg"
_EGMAN_IMAGE_URL = "https://deckbuilder.egmanevents.com/api/images/optcg/{card_id}.png"
_EGMAN_SUPABASE = "https://resgvirjzcpamfumrygh.supabase.co"
_EGMAN_SUPA_KEY = "sb_publishable_bdDgor6ifmOvryEuZKWniw_RBzb3vuh"

# ── cardkaizoku ────────────────────────────────────────────────────────────
_KAIZOKU_CDN = "https://cdn.cardkaizoku.com"
_KAIZOKU_REFERER = "https://deckbuilder.cardkaizoku.com/"

# ── onepiecetopdecks / gumgum / limitless ──────────────────────────────────
_OPTD_RE = re.compile(r"onepiecetopdecks\.com/deck-list/([^/?#]+)")
_GUMGUM_RE = re.compile(r"gumgum(?:tcg)?\.(?:gg|com)/decks?/([A-Za-z0-9_-]+)")
_LIMITLESS_RE = re.compile(r"(?:play\.)?limitlesstcg\.com/decks/(?:onepiece/)?([A-Za-z0-9_-]+)")
_OP_CARD_CODE_RE = re.compile(r"((?:OP|ST|EB|PRB)\d{2}-\d{3})", re.IGNORECASE)


# ── Image URL callables keyed by source ───────────────────────────────────
def _kaizoku_img(card_id: str) -> str:
    prefix = card_id.split("-")[0]
    return f"{_KAIZOKU_CDN}/cards_en/{prefix}/{card_id}.png"


_IMAGE_URL_FUNC_BY_SOURCE = {
    "dotgg": lambda cid: _DOTGG_IMAGE_URL.format(card_id=cid),
    "egman": lambda cid: _EGMAN_IMAGE_URL.format(card_id=cid),
    "kaizoku": _kaizoku_img,
}
_IMAGE_EXT_BY_SOURCE: dict[str, str] = {
    "dotgg": "webp",
    "egman": "png",
    "kaizoku": "png",
}
_IMAGE_EXTRA_HEADERS_BY_SOURCE: dict[str, dict] = {
    "kaizoku": {"Referer": _KAIZOKU_REFERER},
}

# ── Resources ─────────────────────────────────────────────────────────────
_STANDARD_BACK_BG = "#0A1628"
_STANDARD_BACK_BORDER = "#B0B8C8"


def get_op_backs() -> tuple[Path, Path]:
    """Return (default_back, leader_back) from resources/backs/op/.
    Falls back to generating simple colored images if the files are missing.
    """
    res = _resources_dir()
    op_dir = res / "backs" / "op"
    default = op_dir / "default.png"
    leader = op_dir / "lider.png"
    if default.exists() and leader.exists():
        return default, leader
    return _generate_fallback_backs(res)


def _generate_fallback_backs(res_dir: Path | None = None) -> tuple[Path, Path]:
    import tempfile

    if res_dir is None:
        tmp = Path(tempfile.mkdtemp())
        return (
            generate_fallback_back(tmp / "default.png", _STANDARD_BACK_BG, _STANDARD_BACK_BORDER),
            generate_fallback_back(tmp / "lider.png", "#8B0000", "#CCCCCC"),
        )
    cache = get_global_cache()
    default_back = cache.get(f"fallback_back:op:default:{res_dir}")
    leader_back = cache.get(f"fallback_back:op:lider:{res_dir}")
    if (
        isinstance(default_back, Path)
        and default_back.exists()
        and isinstance(leader_back, Path)
        and leader_back.exists()
    ):
        return default_back, leader_back
    tmp = Path(tempfile.mkdtemp())
    default_path = generate_fallback_back(
        tmp / "default.png", _STANDARD_BACK_BG, _STANDARD_BACK_BORDER
    )
    leader_path = generate_fallback_back(tmp / "lider.png", "#8B0000", "#CCCCCC")
    cache.set(f"fallback_back:op:default:{res_dir}", default_path)
    cache.set(f"fallback_back:op:lider:{res_dir}", leader_path)
    return default_path, leader_path


# ── Data model ────────────────────────────────────────────────────────────


@dataclass
class OPCard:
    card_id: str
    name: str
    quantity: int
    is_leader: bool
    colors: list[str]


@dataclass
class OPDeck:
    name: str
    slug: str
    cards: list[OPCard]
    source: str = (
        "dotgg"  # "dotgg" | "egman" | "kaizoku" | "onepiecetopdecks" | "gumgum" | "limitless"
    )
    raw_data: dict[str, Any] | None = None

    @property
    def leader(self) -> OPCard | None:
        return next((c for c in self.cards if c.is_leader), None)

    @property
    def total_slots(self) -> int:
        return sum(c.quantity for c in self.cards)


# ── URL routing ───────────────────────────────────────────────────────────


def scrape_deck(url: str) -> OPDeck:
    """Detect the source site from the URL and dispatch to the right scraper."""
    host = urllib.parse.urlparse(url).netloc.lower()
    if "onepiece.gg" in host:
        return _scrape_dotgg(url)
    if "egmanevents.com" in host:
        return _scrape_egman(url)
    if "cardkaizoku.com" in host:
        return _scrape_kaizoku(url)
    if "onepiecetopdecks.com" in host:
        return _scrape_optopdecks(url)
    if "gumgum" in host:
        return _scrape_gumgum(url)
    if "limitless" in host:
        return _scrape_limitless(url)
    raise ValueError(
        f"URL no reconocida: {url}\n"
        "Webs soportadas: onepiece.gg, deckbuilder.egmanevents.com, "
        "deckbuilder.cardkaizoku.com, onepiecetopdecks.com, gumgum.gg, limitlesstcg.com"
    )


# ── dotgg / onepiece.gg ──────────────────────────────────────────────────


def _scrape_dotgg(url: str) -> OPDeck:
    m = re.search(r"/decks/([^/?#]+)", url)
    if not m:
        raise ValueError(f"No se pudo extraer el slug de la URL: {url}")
    slug = m.group(1).strip("/")

    deck_data = fetch_dotgg_deck_data(
        "onepiece",
        slug,
        headers=_HEADERS,
        timeout=15,
        site_label="onepiece.gg",
    )

    cards_db, _ = load_dotgg_card_db("onepiece", headers=_HEADERS, timeout=30)
    if any(cid not in cards_db for cid in deck_data.get("deck", {})):
        try:
            cards_db, _ = load_dotgg_card_db(
                "onepiece", headers=_HEADERS, timeout=30, use_cache=False
            )
        except Exception:
            pass

    cards: list[OPCard] = []
    for card_id, qty_str in deck_data["deck"].items():
        meta = cards_db.get(card_id, {})
        is_leader = meta.get("cardType", "").upper() == "LEADER"
        color_str = meta.get("Color", "")
        colors = [c.strip() for c in color_str.split("/") if c.strip()]
        cards.append(
            OPCard(
                card_id=card_id,
                name=meta.get("name", card_id),
                quantity=int(qty_str),
                is_leader=is_leader,
                colors=colors,
            )
        )

    return OPDeck(
        name=deck_data.get("humanname", slug),
        slug=slug,
        cards=cards,
        source="dotgg",
        raw_data=deck_data,
    )


# ── egmanevents ──────────────────────────────────────────────────────────


def _egman_cards_db(*, use_cache: bool = True) -> dict[str, dict]:
    cache = get_global_cache()
    if use_cache and (cached := cache.get("op:egman_cards_db")) is not None:
        return cached  # type: ignore[no-any-return]
    r = http_get(_EGMAN_CARDS_API, headers=_HEADERS, timeout=20)
    r.raise_for_status()
    db = {c["card_code"]: c for c in r.json()}
    if use_cache:
        cache.set("op:egman_cards_db", db)
    return db


def _egman_build_deck(
    deck_map: dict[str, int],
    cards_db: dict[str, dict],
    slug: str,
    source: str = "egman",
) -> OPDeck:
    cards: list[OPCard] = []
    for card_id, qty in deck_map.items():
        meta = cards_db.get(card_id, {})
        is_leader = meta.get("category", "").lower() == "leader"
        raw_colors = meta.get("color", [])
        colors = (
            raw_colors if isinstance(raw_colors, list) else ([raw_colors] if raw_colors else [])
        )
        cards.append(
            OPCard(
                card_id=card_id,
                name=meta.get("name", card_id),
                quantity=qty,
                is_leader=is_leader,
                colors=colors,
            )
        )

    leader = next((c for c in cards if c.is_leader), None)
    name = f"{leader.name} Deck" if leader else slug
    return OPDeck(name=name, slug=f"{source}_{slug}", cards=cards, source=source)


def _scrape_egman(url: str) -> OPDeck:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)

    if "deck" in qs:
        # Direct format: ?deck=CARD:COUNT,CARD:COUNT,...
        deck_str = qs["deck"][0]
        deck_map: dict[str, int] = {}
        for part in deck_str.split(","):
            part = part.strip()
            if ":" in part:
                card_id, count = part.rsplit(":", 1)
                try:
                    deck_map[card_id.strip()] = int(count)
                except ValueError:
                    pass
        if not deck_map:
            raise ValueError("No se encontraron cartas en el parámetro ?deck= de la URL.")
        slug = "direct"

    elif parsed.path.startswith("/d/"):
        # Short URL: /d/CODE → Supabase RPC
        short_code = parsed.path[3:].strip("/")
        if not short_code:
            raise ValueError("Código de mazo vacío en la URL /d/...")
        deck_map, slug = _egman_load_short_code(short_code)

    else:
        raise ValueError(
            "URL de egmanevents no reconocida.\n"
            "Formatos válidos:\n"
            "  • https://deckbuilder.egmanevents.com/?deck=CARTA:X,...\n"
            "  • https://deckbuilder.egmanevents.com/d/CODIGO"
        )

    cards_db = _egman_cards_db()
    return _egman_build_deck(deck_map, cards_db, slug)


def _egman_load_short_code(code: str) -> tuple[dict[str, int], str]:
    """Resolve /d/CODE via Supabase RPC. Returns (deck_map, slug)."""
    rpc_url = f"{_EGMAN_SUPABASE}/rest/v1/rpc/get_deck_by_short_code"
    headers = {
        **_HEADERS,
        "apikey": _EGMAN_SUPA_KEY,
        "Authorization": f"Bearer {_EGMAN_SUPA_KEY}",
        "Content-Type": "application/json",
    }
    r = http_post(rpc_url, json={"p_code": code}, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    if not data:
        raise ValueError(f"No se encontró ningún mazo con el código: {code}")

    row = data[0] if isinstance(data, list) else data
    deck_data = row.get("deck_data") or row.get("deckData") or {}

    # deck_data puede ser dict {card_id: count} o lista [{card_code, count}, ...]
    deck_map: dict[str, int] = {}
    if isinstance(deck_data, dict):
        deck_map = {k: int(v) for k, v in deck_data.items()}
    elif isinstance(deck_data, list):
        for entry in deck_data:
            cid = entry.get("card_code") or entry.get("cardCode") or entry.get("id", "")
            cnt = int(entry.get("count", entry.get("quantity", 1)))
            if cid:
                deck_map[cid] = cnt

    if not deck_map:
        raise ValueError(f"El mazo con código {code} está vacío o tiene un formato desconocido.")

    slug = row.get("short_code", code)
    return deck_map, slug


# ── cardkaizoku ──────────────────────────────────────────────────────────


def _scrape_kaizoku(url: str) -> OPDeck:
    """Parse ?deck={N}x{CARD_ID}|{N}x{CARD_ID}|... from cardkaizoku.com."""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    deck_str = qs.get("deck", [""])[0]
    if not deck_str:
        raise ValueError(
            "No se encontró el parámetro ?deck= en la URL de cardkaizoku.\n"
            "Formato esperado: https://deckbuilder.cardkaizoku.com/?deck=2xOP01-001|3xOP01-002|..."
        )

    deck_map: dict[str, int] = {}
    for part in deck_str.split("|"):
        part = part.strip()
        m = re.match(r"(\d+)x(.+)", part)
        if m:
            count = int(m.group(1))
            card_id = m.group(2).strip().upper()
            deck_map[card_id] = deck_map.get(card_id, 0) + count

    if not deck_map:
        raise ValueError(
            "No se encontraron cartas en el parámetro ?deck= de la URL de cardkaizoku."
        )

    cards_db = _egman_cards_db()
    return _egman_build_deck(deck_map, cards_db, "direct", source="kaizoku")


# ── onepiecetopdecks / gumgum / limitless ────────────────────────────────


def _scrape_html_card_codes(
    url: str,
    default_name: str,
    slug: str,
    source: str,
    *,
    fetch_text_fn: Callable[..., str] | None = None,
) -> OPDeck:
    fetcher = fetch_text_fn or fetch_text
    html_text = fetcher(url, headers=_HEADERS, timeout=15)
    name = default_name
    m_name = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.IGNORECASE | re.DOTALL)
    if m_name:
        name = re.sub(r"<[^>]+>", "", m_name.group(1)).strip() or name

    cards_found = _OP_CARD_CODE_RE.findall(html_text)
    card_counts: dict[str, int] = {}
    for c in cards_found:
        c_up = c.upper()
        card_counts[c_up] = card_counts.get(c_up, 0) + 1

    cached_egman = get_global_cache().get("op:egman_cards_db") or {}
    cards: list[OPCard] = []
    for cid, cnt in card_counts.items():
        meta = cached_egman.get(cid, {}) if isinstance(cached_egman, dict) else {}
        is_leader = str(meta.get("category", "")).lower() == "leader"
        raw_colors = meta.get("color", [])
        colors = (
            raw_colors if isinstance(raw_colors, list) else ([raw_colors] if raw_colors else [])
        )
        cards.append(
            OPCard(
                card_id=cid,
                name=meta.get("name", cid),
                quantity=cnt,
                is_leader=is_leader,
                colors=colors,
            )
        )
    return OPDeck(name=name, slug=slug, cards=cards, source=source)


def _scrape_optopdecks(
    url_or_slug: str,
    *,
    fetch_text_fn: Callable[..., str] | None = None,
) -> OPDeck:
    m = _OPTD_RE.search(url_or_slug)
    slug = m.group(1) if m else url_or_slug.rstrip("/").split("/")[-1]
    url = (
        url_or_slug
        if url_or_slug.startswith("http")
        else f"https://onepiecetopdecks.com/deck-list/{url_or_slug}"
    )
    return _scrape_html_card_codes(
        url,
        "OnePieceTopDecks Deck",
        slug,
        "onepiecetopdecks",
        fetch_text_fn=fetch_text_fn,
    )


def _scrape_gumgum(
    url_or_id: str,
    *,
    fetch_json_fn: Callable[..., Any] | None = None,
) -> OPDeck:
    m = _GUMGUM_RE.search(url_or_id)
    deck_id = m.group(1) if m else url_or_id.rstrip("/").split("/")[-1]
    url = url_or_id if url_or_id.startswith("http") else f"https://gumgum.gg/decks/{url_or_id}"
    fetcher = fetch_json_fn or fetch_json
    data = fetcher(url, headers=_HEADERS, timeout=15)
    name = data.get("name", "GumGum Deck")
    cards: list[OPCard] = []
    for c in data.get("cards", []):
        cid = c.get("card_id", c.get("id", ""))
        is_leader = bool(c.get("is_leader")) or str(c.get("category", "")).lower() == "leader"
        raw_colors = c.get("colors", c.get("color", []))
        colors = (
            raw_colors if isinstance(raw_colors, list) else ([raw_colors] if raw_colors else [])
        )
        cards.append(
            OPCard(
                card_id=cid,
                name=c.get("name", cid),
                quantity=int(c.get("quantity", c.get("count", 1))),
                is_leader=is_leader,
                colors=colors,
            )
        )
    return OPDeck(
        name=name,
        slug=deck_id,
        cards=cards,
        source="gumgum",
        raw_data=data,
    )


def _scrape_limitless(
    url_or_id: str,
    *,
    fetch_text_fn: Callable[..., str] | None = None,
) -> OPDeck:
    m = _LIMITLESS_RE.search(url_or_id)
    deck_id = m.group(1) if m else url_or_id.rstrip("/").split("/")[-1]
    url = (
        url_or_id
        if url_or_id.startswith("http")
        else f"https://play.limitlesstcg.com/decks/{url_or_id}"
    )
    return _scrape_html_card_codes(
        url,
        "Limitless Deck",
        deck_id,
        "limitless",
        fetch_text_fn=fetch_text_fn,
    )


# ── Image download ─────────────────────────────────────────────────────────


def download_images(
    deck: OPDeck,
    dest_dir: Path,
    cancel_event: threading.Event | None = None,
    progress_cb: ProgressCallback = None,
) -> dict[str, Path]:
    """Download one image per unique card. Returns {card_id: local_path}."""
    url_func = _IMAGE_URL_FUNC_BY_SOURCE.get(
        deck.source,
        lambda cid: _DOTGG_IMAGE_URL.format(card_id=cid),
    )
    ext = _IMAGE_EXT_BY_SOURCE.get(deck.source, "webp")
    extra_headers = _IMAGE_EXTRA_HEADERS_BY_SOURCE.get(deck.source, {})
    req_headers = {**_HEADERS, **extra_headers}
    urls = {card.card_id: url_func(card.card_id) for card in deck.cards}

    def _path_for_key(card_id: str, _url: str) -> Path:
        return dest_dir / f"{card_id}.{ext}"

    def _fallback_for_key(card_id: str) -> tuple[str, Path, dict[str, str]]:
        return (
            _kaizoku_img(card_id),
            dest_dir / f"{card_id}.png",
            {**_HEADERS, "Referer": _KAIZOKU_REFERER},
        )

    return download_url_images(
        urls,
        dest_dir,
        _path_for_key,
        req_headers,
        cancel_event=cancel_event,
        progress_cb=progress_cb,
        fallback_for_key=_fallback_for_key,
    )


# ── Deck expansion ────────────────────────────────────────────────────────


def expand_deck(
    deck: OPDeck,
    image_map: dict[str, Path],
    leader_back: Path | None,
    standard_back: Path,
) -> tuple[list[Path], list[Path | None]]:
    """Expand each card by its quantity.
    Returns (fronts, per_slot_backs) where None means use the standard back.
    """
    fronts: list[Path] = []
    backs: list[Path | None] = []
    for card in sorted(deck.cards, key=lambda c: c.name.casefold()):
        img_path = image_map.get(card.card_id)
        if img_path is None:
            continue
        slot_back: Path | None = leader_back if (card.is_leader and leader_back) else None
        for _ in range(card.quantity):
            fronts.append(img_path)
            backs.append(slot_back)
    return fronts, backs
