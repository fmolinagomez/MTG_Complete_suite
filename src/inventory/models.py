"""Data models for the Card Inventory and Deck Binder system."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class InventoryCard:
    """Represents a card in the user's physical collection or proxy stock."""

    oracle_id: str
    name: str
    set_code: str
    collector_number: str
    quantity: int
    is_foil: bool = False
    is_proxy: bool = False
    condition: str = "NM"
    language: str = "en"
    metadata_json: str = "{}"
    id: int | None = None

    @property
    def metadata(self) -> dict:
        """Parse metadata_json into a dictionary."""
        try:
            return json.loads(self.metadata_json) if self.metadata_json else {}
        except (ValueError, TypeError):
            return {}


@dataclass
class DeckRecord:
    """Represents a registered deck or binder."""

    name: str
    source_url: str = ""
    format: str = "Commander"
    created_at: str = ""
    notes: str = ""
    id: int | None = None


@dataclass
class DeckCardEntry:
    """Represents a card requirement in a registered deck."""

    deck_id: int
    oracle_id: str
    name: str
    set_code: str
    collector_number: str
    quantity_required: int
    is_proxy: bool = False
    physical_quantity: int = 0
    proxy_quantity: int = 0
    fulfilled_card_id: int | None = None
    id: int | None = None


@dataclass
class MissingProxyPlan:
    """Represents the resolution of physical cards vs missing proxies to print."""

    deck_name: str = ""
    owned_cards: list[DeckCardEntry] = field(default_factory=list)
    missing_cards: list[DeckCardEntry] = field(default_factory=list)
    total_required: int = 0
    total_owned: int = 0
    total_missing: int = 0


@dataclass
class DeckCardMatchDetail:
    """Detailed matching result for a single card in a deck against inventory stock."""

    name: str
    set_code: str
    collector_number: str
    quantity_required: int
    exact_stock: int = 0
    alt_stock: int = 0
    status: str = "missing"  # "exact", "alternative", "partial", "missing"
    oracle_id: str = ""
    deck_card_id: int | None = None
    available_versions: list[dict] = field(default_factory=list)
    assigned_version_id: int | None = None
    physical_quantity: int = 0
    proxy_quantity: int = 0
