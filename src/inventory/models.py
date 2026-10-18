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

    def max_physical_for_version(self, version_id: int | None = None) -> int:
        """Return the maximum physical copies available for a specific version or auto-pool."""
        if version_id is not None:
            target = next(
                (v for v in self.available_versions if v.get("card_id") == version_id),
                None,
            )
            avail = int(target["quantity_available"]) if target else 0
        elif self.available_versions:
            avail = sum(int(v.get("quantity_available", 0)) for v in self.available_versions)
        else:
            avail = self.exact_stock + self.alt_stock
        return max(0, min(self.quantity_required, avail))

    def resolve_proxy_toggle(self) -> tuple[int, int, int | None]:
        """Compute (physical_quantity, proxy_quantity, fulfilled_card_id) when toggling proxy mode."""
        if self.physical_quantity > 0:
            return 0, self.quantity_required, self.assigned_version_id
        new_phys = self.max_physical_for_version(self.assigned_version_id)
        return new_phys, self.quantity_required - new_phys, self.assigned_version_id

    def resolve_version_selection(self, target_card_id: int | None) -> tuple[int, int, int | None]:
        """Compute (physical_quantity, proxy_quantity, fulfilled_card_id) when selecting a version.

        Pass target_card_id=-1 for forced proxy ('Ninguna'), None for auto-combination,
        or a positive inventory card ID for a specific print.
        """
        if target_card_id == -1:
            return 0, self.quantity_required, None
        new_phys = self.max_physical_for_version(target_card_id)
        return new_phys, self.quantity_required - new_phys, target_card_id

    def resolve_physical_quantity(self, requested_physical: int) -> tuple[int, int, int | None]:
        """Clamp a requested physical copy count and return (physical_qty, proxy_qty, fulfilled_card_id)."""
        max_limit = self.max_physical_for_version(self.assigned_version_id)
        new_phys = max(0, min(max_limit, int(requested_physical)))
        return new_phys, self.quantity_required - new_phys, self.assigned_version_id
