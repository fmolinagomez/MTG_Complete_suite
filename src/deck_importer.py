"""Game deck ingestion engine and adapter registry.

Supports:
- MTG: Moxfield, Archidekt, Scryfall, Deckstats, TappedOut, Manabox
- Lorcana: Dreamborn, Inktable, LorcanaGG, Inkdecks
- One Piece: OnePieceTopDecks, GumGum, Limitless, OnepieceGG, Egman, Kaizoku
- Riftbound: PiltoverArchive, Riftmana, Riftbinder, Riftdex, RiftboundGG
"""

from __future__ import annotations

import abc
import html as _html
import json as _json
import re
import urllib.parse
from dataclasses import dataclass, field

import requests

from src.scraper_utils import (
    fetch_json,
    fetch_text,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 15

# MTG Regexes
_MOXFIELD_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)")
_ARCHIDEKT_RE = re.compile(r"archidekt\.com/decks/(\d+)")
_DECKSTATS_RE = re.compile(r"deckstats\.net/decks/(\d+)/(\d+)")
_TAPPEDOUT_RE = re.compile(r"tappedout\.net/mtg-decks/([^/?#]+)")
_MANABOX_RE = re.compile(r"manabox\.app/decks/([A-Za-z0-9_-]+)")
_SCRYFALL_RE = re.compile(r"scryfall\.com/(?:@[^/]+/decks/|decks/)([0-9a-fA-F-]{36})")

_MOXFIELD_API = "https://api2.moxfield.com/v3/decks/all/{deck_id}"
_ARCHIDEKT_API = "https://archidekt.com/api/decks/{deck_id}/"
_DECKSTATS_API = (
    "https://deckstats.net/api.php"
    "?action=get_deck&id_type=saved&owner_id={owner_id}&id={deck_id}&response_type=json"
)
_TAPPEDOUT_API = "https://tappedout.net/mtg-decks/{slug}/?fmt=txt"
_SCRYFALL_API = "https://api.scryfall.com/decks/{deck_id}"

_ARCHIDEKT_SIDE_CATEGORIES = frozenset({"Sideboard"})
_ARCHIDEKT_SKIP_CATEGORIES = frozenset({"Maybeboard"})
_MANABOX_SIDE_CATS = frozenset({1})  # boardCategory 1 = sideboard

_TAPPEDOUT_MAIN_RE = re.compile(r"^(\d+)\s+(.+)$")
_TAPPEDOUT_SIDE_RE = re.compile(r"^SB:\s*(\d+)\s+(.+)$")

# Lorcana Regexes
_LORCANA_GG_RE = re.compile(r"lorcana\.gg/decks/([^/?#]+)")
_INKDECKS_RE = re.compile(r"inkdecks\.com/.*-(\d+)(?:[/?#]|$)")
_DREAMBORN_RE = re.compile(r"dreamborn\.ink/decks/([A-Za-z0-9_-]+)")
_INKTABLE_RE = re.compile(r"inktable\.net/(?:lorcana/)?decks?/([A-Za-z0-9_-]+)")

# One Piece Regexes
_ONEPIECE_GG_RE = re.compile(r"onepiece\.gg/decks/([^/?#]+)")
_EGMAN_RE = re.compile(r"deckbuilder\.egmanevents\.com")
_KAIZOKU_RE = re.compile(r"deckbuilder\.cardkaizoku\.com")
_OPTD_RE = re.compile(r"onepiecetopdecks\.com/deck-list/([^/?#]+)")
_GUMGUM_RE = re.compile(r"gumgum(?:tcg)?\.(?:gg|com)/decks?/([A-Za-z0-9_-]+)")
_LIMITLESS_RE = re.compile(r"(?:play\.)?limitlesstcg\.com/decks/(?:onepiece/)?([A-Za-z0-9_-]+)")

# Riftbound Regexes
_PILTOVER_RE = re.compile(r"piltoverarchive\.com/decks/view/([0-9a-fA-F-]{36})")
_RIFTMANA_RE = re.compile(r"riftmana\.com/decks?/([^/?#]+)")
_RIFTBINDER_RE = re.compile(r"riftbinder\.com/decks/([A-Za-z0-9_-]+)")
_RIFTDEX_RE = re.compile(r"riftdex\.com/decks?/([0-9a-fA-F-]{36})")
_RIFTBOUND_GG_RE = re.compile(r"riftbound\.gg/decks/([^/?#]+)")


class DeckImportError(Exception):
    def __init__(self, message: str, platform: str = "") -> None:
        self.platform = platform
        super().__init__(message)


@dataclass
class DeckCard:
    name: str
    set_code: str = ""
    collector_number: str = ""
    quantity: int = 1
    zone: str = "main"  # "main" | "side" | "token" | "commander" | "leader" | "legend" | ...
    is_commander: bool = False
    type_line: str = ""
    layout: str = ""
    game: str = "mtg"
    image_url: str = ""


@dataclass
class FetchedDeck:
    name: str
    cards: list[DeckCard] = field(default_factory=list)
    format: str = "commander"
    raw_json: dict | None = None
    game: str = "mtg"
    source: str = ""


# ===========================================================================
# 1. Base Game Deck Adapter Strategy Interface
# ===========================================================================


class GameDeckAdapter(abc.ABC):
    """Abstract Strategy Interface for Game Deck Ingestion."""

    @property
    @abc.abstractmethod
    def game_name(self) -> str:
        """Identifier for the game (e.g. 'mtg', 'lorcana', 'onepiece', 'riftbound')."""
        ...

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable display name of the game."""
        ...

    @abc.abstractmethod
    def can_handle_url(self, url: str) -> bool:
        """Return True if this adapter can process the given URL."""
        ...

    @abc.abstractmethod
    def can_handle_text(self, text: str) -> bool:
        """Return True if text list format belongs to this game."""
        ...

    @abc.abstractmethod
    def fetch_deck(self, url: str) -> FetchedDeck:
        """Fetch and return deck from the given URL."""
        ...

    @abc.abstractmethod
    def parse_deck_list(self, text: str) -> FetchedDeck:
        """Parse raw text list into a FetchedDeck."""
        ...


# ===========================================================================
# 2. MTG Deck Adapter
# ===========================================================================


class MtgDeckAdapter(GameDeckAdapter):
    """Adapter for Magic: The Gathering (Moxfield, Archidekt, Scryfall, Deckstats, TappedOut, Manabox)."""

    @property
    def game_name(self) -> str:
        return "mtg"

    @property
    def display_name(self) -> str:
        return "Magic: The Gathering"

    def can_handle_url(self, url: str) -> bool:
        return bool(
            _MOXFIELD_RE.search(url)
            or _ARCHIDEKT_RE.search(url)
            or _DECKSTATS_RE.search(url)
            or _TAPPEDOUT_RE.search(url)
            or _MANABOX_RE.search(url)
            or _SCRYFALL_RE.search(url)
        )

    def can_handle_text(self, text: str) -> bool:
        lower = text.lower()
        if any(marker in lower for marker in ("*cmdr*", "// commander", "// sideboard", "sb:", "commander:", "sideboard:")):
            return True
        if re.search(r"^\s*\d+x?\s+[\w\s',/-]+\s+\([A-Za-z0-9]{2,6}\)\s+\w+", text, re.MULTILINE):
            return True
        return False

    def fetch_deck(self, url: str) -> FetchedDeck:
        m = _MOXFIELD_RE.search(url)
        if m:
            return _fetch_moxfield(m.group(1))
        m = _ARCHIDEKT_RE.search(url)
        if m:
            return _fetch_archidekt(m.group(1))
        m = _DECKSTATS_RE.search(url)
        if m:
            return _fetch_deckstats(m.group(1), m.group(2))
        m = _TAPPEDOUT_RE.search(url)
        if m:
            return _fetch_tappedout(m.group(1))
        m = _MANABOX_RE.search(url)
        if m:
            return _fetch_manabox(m.group(1))
        m = _SCRYFALL_RE.search(url)
        if m:
            return _fetch_scryfall(m.group(1))
        raise DeckImportError(
            "URL no reconocida. Webs soportadas: moxfield.com, archidekt.com, "
            "deckstats.net, tappedout.net, manabox.app, scryfall.com",
            "mtg",
        )

    def parse_deck_list(self, text: str) -> FetchedDeck:
        cards: list[DeckCard] = []
        current_zone = "main"
        deck_name = "Imported MTG Deck"

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith(("// name:", "// deck:", "# name:")):
                deck_name = line.split(":", 1)[1].strip()
                continue
            if lower.startswith(("// commander", "commander:", "commanders:")) and not re.match(r"^(?://\s*)?commanders?:\s*\d+", lower):
                current_zone = "commander"
                continue
            if lower.startswith(("// sideboard", "sideboard:")) or (lower.startswith("sb:") and not re.match(r"^sb:\s*\d+", lower)):
                current_zone = "side"
                continue
            if lower.startswith(("// main", "mainboard:", "// deck", "deck:")) and not re.match(r"^(?://\s*)?(?:main|mainboard|deck):\s*\d+", lower):
                current_zone = "main"
                continue
            if line.startswith("//"):
                continue

            zone = current_zone
            is_commander = (zone == "commander")
            if "*cmdr*" in lower:
                is_commander = True
                zone = "main"
                line = re.sub(r"\*cmdr\*", "", line, flags=re.IGNORECASE).strip()

            if line.upper().startswith("SB:"):
                zone = "side"
                line = line[3:].strip()

            # Pattern: Quantity Name (SET) CN or Quantity Name [SET:CN] or Quantity Name
            m = re.match(r"^(\d+)x?\s+(.+)$", line)
            if not m:
                continue
            qty = int(m.group(1))
            remainder = m.group(2).strip()

            set_code = ""
            collector_num = ""

            bracket_m = re.search(
                r"(?:\((?P<set1>[A-Za-z0-9]{2,6})\)\s*(?P<cn1>[A-Za-z0-9_-]+)?|"
                r"\[(?P<set2>[A-Za-z0-9]{2,6})(?::|\s+)?(?P<cn2>[A-Za-z0-9_-]+)?\]|"
                r"\<(?P<set3>[A-Za-z0-9]{2,6})(?::|\s+)?(?P<cn3>[A-Za-z0-9_-]+)?\>|"
                r"\((?P<set4>[A-Za-z0-9]{2,6}):(?P<cn4>[A-Za-z0-9_-]+)\))\s*$",
                remainder,
            )
            if bracket_m:
                groups = bracket_m.groupdict()
                set_code = (
                    groups.get("set1")
                    or groups.get("set2")
                    or groups.get("set3")
                    or groups.get("set4")
                    or ""
                ).lower()
                collector_num = (
                    groups.get("cn1")
                    or groups.get("cn2")
                    or groups.get("cn3")
                    or groups.get("cn4")
                    or ""
                ).strip()
                card_name = remainder[: bracket_m.start()].strip()
            else:
                card_name = remainder

            cards.append(
                DeckCard(
                    name=card_name,
                    set_code=set_code,
                    collector_number=collector_num,
                    quantity=qty,
                    zone="side" if zone == "side" else "main",
                    is_commander=is_commander,
                    game="mtg",
                )
            )

        return FetchedDeck(name=deck_name, cards=cards, format="commander", game="mtg")


# ===========================================================================
# 3. Lorcana Deck Adapter
# ===========================================================================


class LorcanaDeckAdapter(GameDeckAdapter):
    """Adapter for Disney Lorcana (LorcanaGG, Inkdecks, Dreamborn, Inktable)."""

    @property
    def game_name(self) -> str:
        return "lorcana"

    @property
    def display_name(self) -> str:
        return "Disney Lorcana"

    def can_handle_url(self, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(d in host for d in ("lorcana.gg", "inkdecks.com", "dreamborn.ink", "inktable.net"))

    def can_handle_text(self, text: str) -> bool:
        lower = text.lower()
        if any(marker in lower for marker in ("lorcana", "dreamborn", "inkdecks", "inktable")):
            return True
        if re.search(r"^\s*\d+x?\s+[A-Za-z\s',/]+\s+-\s+[A-Za-z\s',/]+", text, re.MULTILINE):
            return True
        return False

    def fetch_deck(self, url: str) -> FetchedDeck:
        host = urllib.parse.urlparse(url).netloc.lower()
        if "lorcana.gg" in host:
            m = _LORCANA_GG_RE.search(url)
            slug = m.group(1).strip("/") if m else ""
            return _fetch_lorcana_gg(slug or url)
        if "inkdecks.com" in host:
            m = _INKDECKS_RE.search(url)
            deck_id = m.group(1) if m else ""
            return _fetch_inkdecks(deck_id or url)
        if "dreamborn.ink" in host:
            m = _DREAMBORN_RE.search(url)
            return _fetch_dreamborn(m.group(1) if m else url)
        if "inktable.net" in host:
            m = _INKTABLE_RE.search(url)
            return _fetch_inktable(m.group(1) if m else url)
        raise DeckImportError(f"URL de Lorcana no soportada: {url}", "lorcana")

    def parse_deck_list(self, text: str) -> FetchedDeck:
        cards: list[DeckCard] = []
        deck_name = "Imported Lorcana Deck"
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("//", "#")):
                if line.lower().startswith(("// name:", "# name:")):
                    deck_name = line.split(":", 1)[1].strip()
                continue
            m = re.match(r"^(\d+)x?\s+(.+)$", line)
            if not m:
                continue
            qty = int(m.group(1))
            name = m.group(2).strip()
            cards.append(DeckCard(name=name, quantity=qty, zone="main", game="lorcana"))
        return FetchedDeck(name=deck_name, cards=cards, format="standard", game="lorcana")


# ===========================================================================
# 4. One Piece Deck Adapter
# ===========================================================================


class OnePieceDeckAdapter(GameDeckAdapter):
    """Adapter for One Piece Card Game (OnepieceGG, Egman, Kaizoku, OnePieceTopDecks, GumGum, Limitless)."""

    @property
    def game_name(self) -> str:
        return "onepiece"

    @property
    def display_name(self) -> str:
        return "One Piece Card Game"

    def can_handle_url(self, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(
            d in host
            for d in (
                "onepiece.gg",
                "egmanevents.com",
                "cardkaizoku.com",
                "onepiecetopdecks.com",
                "gumgum.gg",
                "gumgumtcg.com",
                "limitlesstcg.com",
            )
        )

    def can_handle_text(self, text: str) -> bool:
        lower = text.lower()
        if "one piece" in lower or "optcg" in lower:
            return True
        if re.search(r"\b(?:OP|ST|EB|PRB)\d{2}-\d{3}\b", text, re.IGNORECASE):
            return True
        return False

    def fetch_deck(self, url: str) -> FetchedDeck:
        host = urllib.parse.urlparse(url).netloc.lower()
        if "onepiece.gg" in host:
            m = _ONEPIECE_GG_RE.search(url)
            slug = m.group(1).strip("/") if m else ""
            return _fetch_onepiece_gg(slug or url)
        if "egmanevents.com" in host:
            return _fetch_egman_op(url)
        if "cardkaizoku.com" in host:
            return _fetch_kaizoku_op(url)
        if "onepiecetopdecks.com" in host:
            m = _OPTD_RE.search(url)
            return _fetch_optopdecks(m.group(1) if m else url)
        if "gumgum" in host:
            m = _GUMGUM_RE.search(url)
            return _fetch_gumgum(m.group(1) if m else url)
        if "limitless" in host:
            m = _LIMITLESS_RE.search(url)
            return _fetch_limitless_op(m.group(1) if m else url)
        raise DeckImportError(f"URL de One Piece no soportada: {url}", "onepiece")

    def parse_deck_list(self, text: str) -> FetchedDeck:
        cards: list[DeckCard] = []
        deck_name = "Imported One Piece Deck"
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("//", "#")):
                if line.lower().startswith(("// name:", "# name:")):
                    deck_name = line.split(":", 1)[1].strip()
                continue
            is_leader = "leader" in line.lower()
            clean_line = re.sub(r"\(?leader\)?", "", line, flags=re.IGNORECASE).strip()
            m = re.match(r"^(\d+)x?\s+(.+)$", clean_line)
            if m:
                qty = int(m.group(1))
                card_str = m.group(2).strip()
                code_m = re.search(r"((?:OP|ST|EB|PRB)\d{2}-\d{3})", card_str, re.IGNORECASE)
                card_code = code_m.group(1).upper() if code_m else ""
                name = card_str if not card_code else card_str
                cards.append(
                    DeckCard(
                        name=name,
                        collector_number=card_code,
                        quantity=qty,
                        zone="leader" if is_leader else "main",
                        is_commander=is_leader,
                        game="onepiece",
                    )
                )
            elif ":" in line:
                # Egman string format: OP01-001:1
                parts = line.split(":")
                card_code = parts[0].strip().upper()
                try:
                    qty = int(parts[1].strip())
                except ValueError:
                    qty = 1
                cards.append(
                    DeckCard(
                        name=card_code,
                        collector_number=card_code,
                        quantity=qty,
                        zone="main",
                        game="onepiece",
                    )
                )
        return FetchedDeck(name=deck_name, cards=cards, format="standard", game="onepiece")


# ===========================================================================
# 5. Riftbound Deck Adapter
# ===========================================================================


class RiftboundDeckAdapter(GameDeckAdapter):
    """Adapter for Riftbound (PiltoverArchive, Riftmana, Riftbinder, Riftdex, RiftboundGG)."""

    @property
    def game_name(self) -> str:
        return "riftbound"

    @property
    def display_name(self) -> str:
        return "Riftbound"

    def can_handle_url(self, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(
            d in host
            for d in (
                "piltoverarchive.com",
                "riftmana.com",
                "riftbinder.com",
                "riftdex.com",
                "riftbound.gg",
            )
        )

    def can_handle_text(self, text: str) -> bool:
        lower = text.lower()
        if "riftbound" in lower or "piltover" in lower:
            return True
        if any(marker in lower for marker in ("legend:", "battlefield:", "rune:", "maindeck:")):
            return True
        return False

    def fetch_deck(self, url: str) -> FetchedDeck:
        from src import rb_scraper

        try:
            rb_deck = rb_scraper.scrape_deck(url)
            cards: list[DeckCard] = []
            for c in rb_deck.cards:
                cards.append(
                    DeckCard(
                        name=c.name,
                        collector_number=c.card_id,
                        quantity=c.quantity,
                        zone=c.section,
                        type_line=c.card_type,
                        image_url=c.image_url,
                        game="riftbound",
                    )
                )
            return FetchedDeck(
                name=rb_deck.name,
                cards=cards,
                format="standard",
                game="riftbound",
                source="rb_scraper",
            )
        except Exception as exc:
            raise DeckImportError(f"Error al importar mazo de Riftbound: {exc}", "riftbound") from exc

    def parse_deck_list(self, text: str) -> FetchedDeck:
        cards: list[DeckCard] = []
        deck_name = "Imported Riftbound Deck"
        current_zone = "maindeck"
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("//", "#")):
                if line.lower().startswith(("// name:", "# name:")):
                    deck_name = line.split(":", 1)[1].strip()
                continue
            lower = line.lower()
            if lower.startswith(("legend:", "legend")):
                current_zone = "legend"
                continue
            if lower.startswith(("battlefield:", "battlefield")):
                current_zone = "battlefield"
                continue
            if lower.startswith(("rune:", "runes:")):
                current_zone = "rune"
                continue
            if lower.startswith(("champion:", "champions:")):
                current_zone = "champion"
                continue
            if lower.startswith(("sideboard:", "side:")):
                current_zone = "sideboard"
                continue
            if lower.startswith(("maindeck:", "main:")):
                current_zone = "maindeck"
                continue

            m = re.match(r"^(\d+)x?\s+(.+)$", line)
            if not m:
                continue
            qty = int(m.group(1))
            name = m.group(2).strip()
            cards.append(DeckCard(name=name, quantity=qty, zone=current_zone, game="riftbound"))

        return FetchedDeck(name=deck_name, cards=cards, format="standard", game="riftbound")


# ===========================================================================
# 6. Adapter Registry
# ===========================================================================


class AdapterRegistry:
    """Registry maintaining active game deck adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, GameDeckAdapter] = {}

    def register(self, adapter: GameDeckAdapter) -> None:
        self._adapters[adapter.game_name] = adapter

    def get(self, game_name: str) -> GameDeckAdapter | None:
        return self._adapters.get(game_name)

    def get_for_url(self, url: str) -> GameDeckAdapter | None:
        for adapter in self._adapters.values():
            if adapter.can_handle_url(url):
                return adapter
        return None

    def get_for_text(self, text: str) -> GameDeckAdapter | None:
        for adapter in self._adapters.values():
            if adapter.can_handle_text(text):
                return adapter
        return None

    def all_adapters(self) -> list[GameDeckAdapter]:
        return list(self._adapters.values())


# Global Registry Singleton
_REGISTRY = AdapterRegistry()
_REGISTRY.register(MtgDeckAdapter())
_REGISTRY.register(LorcanaDeckAdapter())
_REGISTRY.register(OnePieceDeckAdapter())
_REGISTRY.register(RiftboundDeckAdapter())


def register_adapter(adapter: GameDeckAdapter) -> None:
    """Register a new GameDeckAdapter in the engine."""
    _REGISTRY.register(adapter)


def get_adapter(game_name: str) -> GameDeckAdapter | None:
    """Look up an adapter by game identifier."""
    return _REGISTRY.get(game_name)


# ===========================================================================
# 7. Unified Top-Level Dispatchers & Backward-Compatible Functions
# ===========================================================================


def detect_game(url_or_text: str) -> str:
    """Detect game identifier ('mtg', 'lorcana', 'onepiece', 'riftbound', or 'unknown')."""
    trimmed = url_or_text.strip()
    if trimmed.startswith(("http://", "https://", "www.")):
        adapter = _REGISTRY.get_for_url(trimmed)
        if adapter:
            return adapter.game_name
    else:
        lower = trimmed.lower()
        if "riftbound" in lower or "piltover" in lower or any(k in lower for k in ("legend:", "battlefield:", "rune:")):
            return "riftbound"
        if "one piece" in lower or "optcg" in lower or re.search(r"\b(?:OP|ST|EB|PRB)\d{2}-\d{3}\b", trimmed, re.IGNORECASE):
            return "onepiece"
        if "lorcana" in lower or "dreamborn" in lower or "inkdecks" in lower or re.search(r"^\s*\d+x?\s+[A-Za-z\s',/]+\s+-\s+[A-Za-z\s',/]+", trimmed, re.MULTILINE):
            return "lorcana"
        if any(marker in lower for marker in ("*cmdr*", "// commander", "// sideboard", "sb:", "commander:", "sideboard:")):
            return "mtg"
        adapter = _REGISTRY.get_for_text(trimmed)
        if adapter:
            return adapter.game_name
        if re.search(r"^\s*\d+x?\s+.+", trimmed, re.MULTILINE):
            return "mtg"
    return "unknown"


def parse_deck_list(text: str, game: str = "mtg") -> FetchedDeck:
    """Parse raw deck list text into a FetchedDeck."""
    if game == "auto" or game == "unknown":
        game = detect_game(text)
        if game == "unknown":
            game = "mtg"
    adapter = _REGISTRY.get(game) or _REGISTRY.get("mtg")
    if adapter is None:
        raise DeckImportError(f"No hay adaptador disponible para el juego: {game}")
    return adapter.parse_deck_list(text)


def fetch_deck(url: str, game: str | None = None) -> FetchedDeck:
    """Return the deck name and card list for the deck at url."""
    if game:
        adapter = _REGISTRY.get(game)
        if adapter and adapter.can_handle_url(url):
            return adapter.fetch_deck(url)

    adapter = _REGISTRY.get_for_url(url)
    if adapter:
        return adapter.fetch_deck(url)

    # Legacy fallback checks for exact backward-compatibility test matching
    m = _MOXFIELD_RE.search(url)
    if m:
        return _fetch_moxfield(m.group(1))
    m = _ARCHIDEKT_RE.search(url)
    if m:
        return _fetch_archidekt(m.group(1))
    m = _DECKSTATS_RE.search(url)
    if m:
        return _fetch_deckstats(m.group(1), m.group(2))
    m = _TAPPEDOUT_RE.search(url)
    if m:
        return _fetch_tappedout(m.group(1))
    m = _MANABOX_RE.search(url)
    if m:
        return _fetch_manabox(m.group(1))

    raise DeckImportError(
        "URL no reconocida. Webs soportadas: moxfield.com, archidekt.com, "
        "deckstats.net, tappedout.net, manabox.app"
    )


# ===========================================================================
# 8. Concrete Platform Fetchers (MTG)
# ===========================================================================


def _fetch_moxfield(deck_id: str) -> FetchedDeck:
    url = _MOXFIELD_API.format(deck_id=deck_id)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise DeckImportError(f"Error al acceder a Moxfield: {exc}", "moxfield") from exc

    deck_name: str = data.get("name", "")
    deck_format: str = data.get("format", "commander")
    cards: list[DeckCard] = []
    boards = data.get("boards", {})
    for board_name, zone in (
        ("mainboard", "main"),
        ("commanders", "main"),
        ("companions", "main"),
        ("sideboard", "side"),
    ):
        board = boards.get(board_name, {})
        for entry in board.get("cards", {}).values():
            card = entry.get("card", {})
            set_code = str(card.get("set", "")).lower()
            collector_number = str(card.get("cn", ""))
            name = card.get("name", "")
            qty = int(entry.get("quantity", 1))
            type_line = card.get("type_line", "")
            if not set_code or not collector_number:
                continue
            is_commander = (board_name == "commanders")
            cards.append(
                DeckCard(
                    name,
                    set_code,
                    collector_number,
                    qty,
                    zone,
                    is_commander=is_commander,
                    type_line=type_line,
                    game="mtg",
                )
            )

    # Parse tokens from Moxfield JSON response
    for token_card in data.get("tokens", []):
        set_code = str(token_card.get("set", "")).lower()
        collector_number = str(token_card.get("cn", ""))
        name = token_card.get("name", "")
        qty = int(token_card.get("quantity") or 1)
        type_line = token_card.get("type_line", "") or "Token"
        if not set_code or not collector_number:
            continue
        cards.append(DeckCard(name, set_code, collector_number, qty, "token", type_line=type_line, game="mtg"))

    return FetchedDeck(name=deck_name, cards=cards, format=deck_format, raw_json=data, game="mtg", source="moxfield")


def _fetch_archidekt(deck_id: str) -> FetchedDeck:
    url = _ARCHIDEKT_API.format(deck_id=deck_id)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise DeckImportError(f"Error al acceder a Archidekt: {exc}", "archidekt") from exc

    deck_name: str = data.get("name", "")
    deck_format: str = data.get("deckFormat", "commander")
    cards: list[DeckCard] = []
    for entry in data.get("cards", []):
        categories = entry.get("categories", [])
        if any(c in _ARCHIDEKT_SKIP_CATEGORIES for c in categories):
            continue
        card_data = entry.get("card", {})
        edition = card_data.get("edition", {})
        set_code = str(edition.get("editioncode", "")).lower()
        collector_number = str(card_data.get("collectorNumber", ""))
        oracle_card = card_data.get("oracleCard", {})
        name = oracle_card.get("name", "")
        qty = int(entry.get("quantity", 1))
        zone = "side" if any(c in _ARCHIDEKT_SIDE_CATEGORIES for c in categories) else "main"

        type_line = oracle_card.get("type", "")
        if not type_line and "types" in oracle_card:
            types_list = oracle_card.get("types", [])
            if isinstance(types_list, list):
                type_line = " ".join(types_list)

        if not set_code or not collector_number:
            continue
        is_commander = "Commander" in categories
        cards.append(
            DeckCard(
                name,
                set_code,
                collector_number,
                qty,
                zone,
                is_commander=is_commander,
                type_line=type_line,
                game="mtg",
            )
        )
    return FetchedDeck(name=deck_name, cards=cards, format=deck_format, raw_json=data, game="mtg", source="archidekt")


def _fetch_deckstats(owner_id: str, deck_id: str) -> FetchedDeck:
    url = _DECKSTATS_API.format(owner_id=owner_id, deck_id=deck_id)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise DeckImportError(f"Error al acceder a Deckstats: {exc}", "deckstats") from exc

    deck_name: str = data.get("name", "")
    cards: list[DeckCard] = []
    for section in data.get("sections", []):
        for entry in section.get("cards", []):
            name = entry.get("name", "")
            qty = int(entry.get("amount", 1))
            if not name:
                continue
            cards.append(DeckCard(name, "", "", qty, "main", game="mtg"))
    for entry in data.get("sideboard", []):
        name = entry.get("name", "")
        qty = int(entry.get("amount", 1))
        if not name:
            continue
        cards.append(DeckCard(name, "", "", qty, "side", game="mtg"))
    return FetchedDeck(name=deck_name, cards=cards, raw_json=data, game="mtg", source="deckstats")


def _fetch_tappedout(slug: str) -> FetchedDeck:
    url = _TAPPEDOUT_API.format(slug=slug)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DeckImportError(f"Error al acceder a TappedOut: {exc}", "tappedout") from exc

    deck_name = re.sub(r"\s+", " ", slug.replace("-", " ")).strip().title()
    cards: list[DeckCard] = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        is_commander = "*CMDR*" in line
        if is_commander:
            line = line.replace("*CMDR*", "").strip()
        m = _TAPPEDOUT_SIDE_RE.match(line)
        if m:
            cards.append(DeckCard(m.group(2).strip(), "", "", int(m.group(1)), "side", is_commander=is_commander, game="mtg"))
            continue
        m = _TAPPEDOUT_MAIN_RE.match(line)
        if m:
            cards.append(DeckCard(m.group(2).strip(), "", "", int(m.group(1)), "main", is_commander=is_commander, game="mtg"))

    if not cards:
        raise DeckImportError("No se encontraron cartas en el mazo de TappedOut", "tappedout")
    return FetchedDeck(name=deck_name, cards=cards, game="mtg", source="tappedout")


def _fetch_scryfall(deck_id: str) -> FetchedDeck:
    url = _SCRYFALL_API.format(deck_id=deck_id)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise DeckImportError(f"Error al acceder a Scryfall: {exc}", "scryfall") from exc

    deck_name: str = data.get("name", "Scryfall Deck")
    deck_format: str = data.get("format", "commander")
    cards: list[DeckCard] = []
    entries = data.get("entries", {})

    if isinstance(entries, dict):
        for board_name, entry_list in entries.items():
            zone = "side" if "side" in board_name.lower() else "main"
            is_commander = "command" in board_name.lower()
            for item in entry_list:
                digest = item.get("card_digest", {})
                name = digest.get("name", item.get("name", ""))
                set_code = str(digest.get("set", item.get("set", ""))).lower()
                collector_num = str(digest.get("collector_number", item.get("collector_number", "")))
                qty = int(item.get("count", 1))
                type_line = digest.get("type_line", "")
                if name:
                    cards.append(
                        DeckCard(
                            name=name,
                            set_code=set_code,
                            collector_number=collector_num,
                            quantity=qty,
                            zone=zone,
                            is_commander=is_commander,
                            type_line=type_line,
                            game="mtg",
                        )
                    )
    elif isinstance(entries, list):
        for item in entries:
            digest = item.get("card_digest", {})
            name = digest.get("name", item.get("name", ""))
            set_code = str(digest.get("set", item.get("set", ""))).lower()
            collector_num = str(digest.get("collector_number", item.get("collector_number", "")))
            qty = int(item.get("count", 1))
            zone = item.get("zone", "main")
            if name:
                cards.append(
                    DeckCard(
                        name=name,
                        set_code=set_code,
                        collector_number=collector_num,
                        quantity=qty,
                        zone="side" if "side" in zone else "main",
                        game="mtg",
                    )
                )

    return FetchedDeck(name=deck_name, cards=cards, format=deck_format, raw_json=data, game="mtg", source="scryfall")


# ── Astro Dehydrated Props Helpers (Manabox) ─────────────────────────────────


def _astro_val(wrapped: object) -> object:
    if isinstance(wrapped, list) and len(wrapped) == 2 and isinstance(wrapped[0], int):
        signal_type, content = wrapped[0], wrapped[1]
        if signal_type == 0:
            return content
        if signal_type == 1:  # Astro type 1 = array; each element is also [type, value]
            return [_astro_val(item) for item in content]
    return wrapped


def _astro_find(data: object, key: str) -> object:
    """Search recursively for key in Astro dehydrated props; returns unwrapped value or None."""
    if isinstance(data, dict):
        if key in data:
            return _astro_val(data[key])
        for v in data.values():
            result = _astro_find(_astro_val(v), key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _astro_find(item, key)
            if result is not None:
                return result
    return None


def _manabox_extract_props(raw: str) -> str:
    """Return the HTML-encoded value of the props attribute containing &quot;cards&quot;."""
    marker = "&quot;cards&quot;:"
    pos = raw.find(marker)
    if pos == -1:
        return ""
    attr_pos = raw.rfind('props="', 0, pos)
    if attr_pos == -1:
        return ""
    i = attr_pos + 7
    end = i
    while end < len(raw):
        if raw[end] == '"':
            break
        if raw[end : end + 6] == "&quot;":
            end += 6
            continue
        end += 1
    return raw[i:end]


def _fetch_manabox(deck_id: str) -> FetchedDeck:
    url = f"https://manabox.app/decks/{deck_id}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DeckImportError(f"Error al acceder a Manabox: {exc}", "manabox") from exc

    props_encoded = _manabox_extract_props(resp.text)
    if not props_encoded:
        raise DeckImportError("No se encontraron datos del mazo en Manabox", "manabox")

    try:
        props = _json.loads(_html.unescape(props_encoded))
    except _json.JSONDecodeError as exc:
        raise DeckImportError(f"Error al parsear datos de Manabox: {exc}", "manabox") from exc

    raw_cards = _astro_find(props, "cards")
    if not isinstance(raw_cards, list):
        raise DeckImportError("Formato de mazos inesperado en Manabox", "manabox")

    deck_name = str(_astro_find(props, "name") or deck_id)
    cards: list[DeckCard] = []
    for entry in raw_cards:
        if not isinstance(entry, dict):
            continue
        name = str(_astro_val(entry.get("name", "")))
        set_code = str(_astro_val(entry.get("setId", ""))).lower()
        collector_number = str(_astro_val(entry.get("collectorNumber", "")))
        quantity = int(_astro_val(entry.get("quantity", 1)))  # type: ignore[arg-type]
        board_cat = int(_astro_val(entry.get("boardCategory", 0)))  # type: ignore[arg-type]
        zone = "side" if board_cat in _MANABOX_SIDE_CATS else "main"
        type_line = str(_astro_val(entry.get("typeLine", ""))) or ""
        if not name:
            continue
        cards.append(DeckCard(name, set_code, collector_number, quantity, zone, type_line=type_line, game="mtg"))

    return FetchedDeck(name=deck_name, cards=cards, game="mtg", source="manabox")


# ===========================================================================
# 9. Concrete Platform Fetchers (Lorcana, One Piece, etc.)
# ===========================================================================


def _fetch_lorcana_gg(slug: str) -> FetchedDeck:
    from src import lorcana_scraper

    url = slug if slug.startswith("http") else f"https://lorcana.gg/decks/{slug}/"
    deck = lorcana_scraper.scrape_deck(url)
    cards = [
        DeckCard(name=c.name, collector_number=c.card_id, quantity=c.quantity, zone="main", image_url=c.image_url, game="lorcana")
        for c in deck.cards
    ]
    return FetchedDeck(name=deck.name, cards=cards, format="standard", game="lorcana", source="lorcana_gg")


def _fetch_inkdecks(deck_id: str) -> FetchedDeck:
    from src import lorcana_scraper

    url = deck_id if deck_id.startswith("http") else f"https://inkdecks.com/lorcana-metagame/deck-{deck_id}"
    deck = lorcana_scraper.scrape_deck(url)
    cards = [
        DeckCard(name=c.name, collector_number=c.card_id, quantity=c.quantity, zone="main", image_url=c.image_url, game="lorcana")
        for c in deck.cards
    ]
    return FetchedDeck(name=deck.name, cards=cards, format="standard", game="lorcana", source="inkdecks")


def _fetch_dreamborn(deck_id: str) -> FetchedDeck:
    url = deck_id if deck_id.startswith("http") else f"https://dreamborn.ink/api/decks/{deck_id}"
    try:
        data = fetch_json(url, headers=_HEADERS, timeout=_TIMEOUT)
        name = data.get("name", "Dreamborn Deck")
        cards: list[DeckCard] = []
        for c in data.get("cards", []):
            cards.append(
                DeckCard(
                    name=c.get("name", c.get("id", "")),
                    collector_number=c.get("id", ""),
                    quantity=int(c.get("count", c.get("quantity", 1))),
                    zone="main",
                    game="lorcana",
                )
            )
        return FetchedDeck(name=name, cards=cards, format="standard", raw_json=data, game="lorcana", source="dreamborn")
    except Exception as exc:
        raise DeckImportError(f"Error al acceder a Dreamborn: {exc}", "dreamborn") from exc


def _fetch_inktable(deck_id: str) -> FetchedDeck:
    url = deck_id if deck_id.startswith("http") else f"https://inktable.net/api/lorcana/decks/{deck_id}"
    try:
        data = fetch_json(url, headers=_HEADERS, timeout=_TIMEOUT)
        name = data.get("name", "Inktable Deck")
        cards: list[DeckCard] = []
        for c in data.get("cards", []):
            cards.append(
                DeckCard(
                    name=c.get("name", c.get("id", "")),
                    collector_number=c.get("id", ""),
                    quantity=int(c.get("quantity", 1)),
                    zone="main",
                    game="lorcana",
                )
            )
        return FetchedDeck(name=name, cards=cards, format="standard", raw_json=data, game="lorcana", source="inktable")
    except Exception as exc:
        raise DeckImportError(f"Error al acceder a Inktable: {exc}", "inktable") from exc


def _fetch_onepiece_gg(slug: str) -> FetchedDeck:
    from src import op_scraper

    url = slug if slug.startswith("http") else f"https://onepiece.gg/decks/{slug}/"
    deck = op_scraper.scrape_deck(url)
    cards = [
        DeckCard(
            name=c.name,
            collector_number=c.card_id,
            quantity=c.quantity,
            zone="leader" if c.is_leader else "main",
            is_commander=c.is_leader,
            game="onepiece",
        )
        for c in deck.cards
    ]
    return FetchedDeck(name=deck.name, cards=cards, format="standard", game="onepiece", source="onepiece_gg")


def _fetch_egman_op(url: str) -> FetchedDeck:
    from src import op_scraper

    deck = op_scraper.scrape_deck(url)
    cards = [
        DeckCard(
            name=c.name,
            collector_number=c.card_id,
            quantity=c.quantity,
            zone="leader" if c.is_leader else "main",
            is_commander=c.is_leader,
            game="onepiece",
        )
        for c in deck.cards
    ]
    return FetchedDeck(name=deck.name, cards=cards, format="standard", game="onepiece", source="egman")


def _fetch_kaizoku_op(url: str) -> FetchedDeck:
    from src import op_scraper

    deck = op_scraper.scrape_deck(url)
    cards = [
        DeckCard(
            name=c.name,
            collector_number=c.card_id,
            quantity=c.quantity,
            zone="leader" if c.is_leader else "main",
            is_commander=c.is_leader,
            game="onepiece",
        )
        for c in deck.cards
    ]
    return FetchedDeck(name=deck.name, cards=cards, format="standard", game="onepiece", source="kaizoku")


def _fetch_optopdecks(slug: str) -> FetchedDeck:
    url = slug if slug.startswith("http") else f"https://onepiecetopdecks.com/deck-list/{slug}"
    try:
        html_text = fetch_text(url, headers=_HEADERS, timeout=_TIMEOUT)
        name = "OnePieceTopDecks Deck"
        m_name = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.IGNORECASE)
        if m_name:
            name = re.sub(r"<[^>]+>", "", m_name.group(1)).strip() or name
        cards_found = re.findall(r"((?:OP|ST|EB|PRB)\d{2}-\d{3})", html_text, re.IGNORECASE)
        card_counts: dict[str, int] = {}
        for c in cards_found:
            c_up = c.upper()
            card_counts[c_up] = card_counts.get(c_up, 0) + 1
        cards = [
            DeckCard(name=cid, collector_number=cid, quantity=cnt, zone="main", game="onepiece")
            for cid, cnt in card_counts.items()
        ]
        return FetchedDeck(name=name, cards=cards, format="standard", game="onepiece", source="onepiecetopdecks")
    except Exception as exc:
        raise DeckImportError(f"Error al acceder a OnePieceTopDecks: {exc}", "onepiecetopdecks") from exc


def _fetch_gumgum(deck_id: str) -> FetchedDeck:
    url = deck_id if deck_id.startswith("http") else f"https://gumgum.gg/decks/{deck_id}"
    try:
        data = fetch_json(url, headers=_HEADERS, timeout=_TIMEOUT)
        name = data.get("name", "GumGum Deck")
        cards: list[DeckCard] = []
        for c in data.get("cards", []):
            cid = c.get("card_id", c.get("id", ""))
            cards.append(
                DeckCard(
                    name=c.get("name", cid),
                    collector_number=cid,
                    quantity=int(c.get("quantity", 1)),
                    zone="leader" if c.get("is_leader") else "main",
                    is_commander=bool(c.get("is_leader")),
                    game="onepiece",
                )
            )
        return FetchedDeck(name=name, cards=cards, format="standard", raw_json=data, game="onepiece", source="gumgum")
    except Exception as exc:
        raise DeckImportError(f"Error al acceder a GumGum: {exc}", "gumgum") from exc


def _fetch_limitless_op(deck_id: str) -> FetchedDeck:
    url = deck_id if deck_id.startswith("http") else f"https://play.limitlesstcg.com/decks/{deck_id}"
    try:
        html_text = fetch_text(url, headers=_HEADERS, timeout=_TIMEOUT)
        name = "Limitless Deck"
        m_name = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.IGNORECASE)
        if m_name:
            name = re.sub(r"<[^>]+>", "", m_name.group(1)).strip() or name
        cards_found = re.findall(r"((?:OP|ST|EB|PRB)\d{2}-\d{3})", html_text, re.IGNORECASE)
        card_counts: dict[str, int] = {}
        for c in cards_found:
            c_up = c.upper()
            card_counts[c_up] = card_counts.get(c_up, 0) + 1
        cards = [
            DeckCard(name=cid, collector_number=cid, quantity=cnt, zone="main", game="onepiece")
            for cid, cnt in card_counts.items()
        ]
        return FetchedDeck(name=name, cards=cards, format="standard", game="onepiece", source="limitless")
    except Exception as exc:
        raise DeckImportError(f"Error al acceder a Limitless: {exc}", "limitless") from exc
