"""Dedicated pure, database-independent Stock Allocation & Proxy Optimization Engine.

Provides algorithms for:
- Single card identity and print matching (exact, oracle_id, DFC, apostrophes, collector numbers).
- Evaluating physical card stock vs deck requirements.
- Distinguishing exact print matches vs alternative (oracle_id / name) matches.
- Calculating missing proxy plans under exclusive (Option C shared-binder) or shared policies.
- Tracking in-deck usage, remaining availability, and natural collection sorting/filtering.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from src.inventory.models import (
    DeckCardEntry,
    DeckCardMatchDetail,
    InventoryCard,
    MissingProxyPlan,
)

RARITY_FILTER_ALIASES: dict[str, str] = {
    "todas": "",
    "all": "",
    "mítica": "mythic",
    "mitica": "mythic",
    "mythic": "mythic",
    "rara": "rare",
    "rare": "rare",
    "infrecuente": "uncommon",
    "uncommon": "uncommon",
    "común": "common",
    "comun": "common",
    "common": "common",
}

USAGE_FILTER_ALIASES: dict[str, str] = {
    "todas": "all",
    "all": "all",
    "available": "available",
    "solo disponibles (libres)": "available",
    "solo disponibles": "available",
    "in_decks": "in_decks",
    "solo asignadas a mazos": "in_decks",
    "solo asignadas": "in_decks",
}

SET_FILTER_WILDCARDS: frozenset[str] = frozenset({"", "todas", "todas las ediciones", "all"})


class AllocationPolicy(str, Enum):
    """Stock allocation policy for resolving deck card requirements."""

    OPTION_C_SHARED = "option_c_shared"
    EXCLUSIVE = "exclusive"
    SHARED = "shared"

    @classmethod
    def from_str(cls, value: str | AllocationPolicy) -> AllocationPolicy:
        """Parse a string or enum value into an AllocationPolicy."""
        if isinstance(value, cls):
            return value
        val_str = str(value).strip().lower()
        if val_str in ("option_c_shared", "option_c", "exclusive"):
            return cls.EXCLUSIVE
        if val_str in ("shared", "unlimited", "none"):
            return cls.SHARED
        return cls.EXCLUSIVE


def normalize_card_name(name: str) -> str:
    """Normalize card name by trimming, lowercasing, and normalizing apostrophes."""
    if not name:
        return ""
    return name.strip().lower().replace("’", "'").replace("`", "'")


def names_match(n1: str, n2: str) -> bool:
    """Match card names with exact and double-faced / adventure / split card tolerance."""
    s1 = normalize_card_name(n1)
    s2 = normalize_card_name(n2)
    if not s1 or not s2:
        return False
    if s1 == s2:
        return True
    f1 = s1.split(" // ")[0].strip()
    f2 = s2.split(" // ")[0].strip()
    return f1 == f2


def numbers_match(num1: str, num2: str) -> bool:
    """Match collector numbers ignoring leading zeros and casing."""
    c1 = (num1 or "").strip().lower()
    c2 = (num2 or "").strip().lower()
    if c1 == c2:
        return True
    c1_clean = c1.lstrip("0") or "0"
    c2_clean = c2.lstrip("0") or "0"
    return c1_clean == c2_clean


def cards_match(oracle_id_1: str, name_1: str, oracle_id_2: str, name_2: str) -> bool:
    """Match two card identities by canonical oracle_id or DFC-tolerant normalized name."""
    oid1 = (oracle_id_1 or "").strip()
    oid2 = (oracle_id_2 or "").strip()
    if oid1 and oid2 and oid1 == oid2:
        return True
    return names_match(name_1, name_2)


def prints_match(
    set_code_1: str,
    collector_number_1: str,
    set_code_2: str,
    collector_number_2: str,
) -> bool:
    """Match two card printings by set code and normalized collector number."""
    sc1 = (set_code_1 or "").strip().lower()
    sc2 = (set_code_2 or "").strip().lower()
    return sc1 == sc2 and numbers_match(collector_number_1, collector_number_2)


def normalize_rarity_filter(value: str) -> str:
    """Normalize a rarity filter string (Spanish or English) to canonical lowercase rarity."""
    clean = (value or "").strip().lower()
    return RARITY_FILTER_ALIASES.get(clean, clean)


def normalize_usage_filter(value: str) -> str:
    """Normalize a usage filter string to 'all', 'available', or 'in_decks'."""
    clean = (value or "").strip().lower()
    return USAGE_FILTER_ALIASES.get(clean, "all")


def normalize_set_filter(value: str) -> str:
    """Normalize a set code filter string, returning empty string for 'all sets' wildcards."""
    clean = (value or "").strip().lower()
    return "" if clean in SET_FILTER_WILDCARDS else clean


def card_usage_sort_key(item: tuple[InventoryCard, int, int], col: str) -> object:
    """Return a natural sort key for an (InventoryCard, in_decks_qty, available_qty) tuple."""
    card, in_decks_qty, available_qty = item
    if col == "name":
        return (card.name or "").lower()
    if col == "set_code":
        return (card.set_code or "").lower()
    if col == "collector_number":
        try:
            return (0, int(card.collector_number))
        except (ValueError, TypeError):
            return (1, (card.collector_number or "").lower())
    if col == "quantity":
        return card.quantity
    if col == "available":
        return available_qty
    if col == "in_decks":
        return in_decks_qty
    if col == "foil":
        return 1 if card.is_foil else 0
    if col == "proxy":
        return 1 if card.is_proxy else 0
    if col == "condition":
        return (card.condition or "").lower()
    if col == "language":
        return (card.language or "").lower()
    return ""


class StockAllocationEngine:
    """Pure, database-independent engine for card matching and multi-deck stock allocation."""

    def __init__(self, default_policy: AllocationPolicy | str = AllocationPolicy.EXCLUSIVE) -> None:
        self.default_policy = AllocationPolicy.from_str(default_policy)

    @staticmethod
    def _build_stock_pool(
        inventory_cards: Sequence[InventoryCard],
        include_proxies: bool,
    ) -> list[dict]:
        """Build a mutable stock pool prioritizing physical cards before proxies."""
        pool = [
            {
                "id": card.id,
                "oracle_id": (card.oracle_id or "").strip(),
                "name": (card.name or "").strip(),
                "set_code": (card.set_code or "").strip().lower(),
                "collector_number": (card.collector_number or "").strip().lower(),
                "quantity": int(card.quantity),
                "initial_quantity": int(card.quantity),
                "is_foil": bool(card.is_foil),
                "condition": str(card.condition or "NM"),
                "is_proxy": bool(card.is_proxy),
            }
            for card in inventory_cards
            if card.quantity > 0 and (include_proxies or not card.is_proxy)
        ]
        pool.sort(key=lambda item: 1 if item["is_proxy"] else 0)
        return pool

    @staticmethod
    def _deduct_from_pool(
        stock_pool: list[dict],
        qty_to_deduct: int,
        oracle_id: str,
        name: str,
        set_code: str,
        collector_number: str,
        fulfilled_card_id: int | None = None,
    ) -> int:
        """Deduct up to qty_to_deduct copies from stock_pool in 3 priority passes.

        Order:
        1. Specific fulfilled_card_id match (if set).
        2. Exact print match (physical before proxy).
        3. Alternative print match (physical before proxy).
        """
        remaining = int(qty_to_deduct)
        if remaining <= 0:
            return 0

        # Pass 1: Specific card ID fulfillment
        if fulfilled_card_id is not None:
            for item in stock_pool:
                if item["id"] == fulfilled_card_id and item["quantity"] > 0:
                    take = min(remaining, item["quantity"])
                    item["quantity"] -= take
                    remaining -= take
                    break

        # Pass 2: Exact print match
        if remaining > 0:
            for item in stock_pool:
                if remaining <= 0:
                    break
                if (
                    item["quantity"] > 0
                    and cards_match(oracle_id, name, item["oracle_id"], item["name"])
                    and prints_match(
                        set_code, collector_number, item["set_code"], item["collector_number"]
                    )
                ):
                    take = min(remaining, item["quantity"])
                    item["quantity"] -= take
                    remaining -= take

        # Pass 3: Alternative print match
        if remaining > 0:
            for item in stock_pool:
                if remaining <= 0:
                    break
                if item["quantity"] > 0 and cards_match(
                    oracle_id, name, item["oracle_id"], item["name"]
                ):
                    take = min(remaining, item["quantity"])
                    item["quantity"] -= take
                    remaining -= take

        return int(qty_to_deduct) - remaining

    @classmethod
    def _deduct_other_decks(
        cls,
        stock_pool: list[dict],
        other_deck_cards: Sequence[DeckCardEntry] | None,
        policy: AllocationPolicy,
        current_deck_id: int | None,
    ) -> None:
        """Deduct copies reserved by other registered decks under EXCLUSIVE policy."""
        if (
            policy not in (AllocationPolicy.EXCLUSIVE, AllocationPolicy.OPTION_C_SHARED)
            or not other_deck_cards
        ):
            return

        for assigned in other_deck_cards:
            if current_deck_id is not None and assigned.deck_id == current_deck_id:
                continue
            if assigned.proxy_quantity > 0 and assigned.physical_quantity == 0:
                continue
            if assigned.is_proxy and assigned.physical_quantity == 0:
                continue

            qty_to_deduct = int(
                assigned.physical_quantity
                if assigned.physical_quantity > 0
                else assigned.quantity_required
            )
            if qty_to_deduct <= 0:
                continue

            cls._deduct_from_pool(
                stock_pool,
                qty_to_deduct,
                assigned.oracle_id,
                assigned.name,
                assigned.set_code,
                assigned.collector_number,
                assigned.fulfilled_card_id,
            )

    def calculate_missing_proxies(
        self,
        deck_cards: Sequence[DeckCardEntry],
        inventory_cards: Sequence[InventoryCard],
        other_deck_cards: Sequence[DeckCardEntry] | None = None,
        allocate_mode: AllocationPolicy | str | None = None,
        current_deck_id: int | None = None,
        deck_name: str = "",
        include_proxies: bool = True,
    ) -> MissingProxyPlan:
        """Evaluate physical and proxy card availability vs deck requirements.

        Uses the unified per-card allocation pass so fulfilled_card_id, granular
        physical/proxy splits, and exact-before-alternative deductions never diverge.
        """
        plan = MissingProxyPlan(deck_name=deck_name)
        if not deck_cards:
            return plan

        details_list, _, _, _ = self.analyze_deck_card_matches(
            deck_cards=deck_cards,
            inventory_cards=inventory_cards,
            other_deck_cards=other_deck_cards,
            allocate_mode=allocate_mode,
            current_deck_id=current_deck_id,
            include_proxies=include_proxies,
        )

        owned_list: list[DeckCardEntry] = []
        missing_list: list[DeckCardEntry] = []

        for entry, detail in zip(deck_cards, details_list, strict=True):
            owned_qty = detail.physical_quantity
            missing_qty = detail.proxy_quantity

            if owned_qty > 0:
                owned_list.append(
                    DeckCardEntry(
                        id=entry.id,
                        deck_id=entry.deck_id,
                        oracle_id=entry.oracle_id,
                        name=entry.name,
                        set_code=entry.set_code,
                        collector_number=entry.collector_number,
                        quantity_required=owned_qty,
                        is_proxy=False,
                        physical_quantity=owned_qty,
                        proxy_quantity=0,
                        fulfilled_card_id=entry.fulfilled_card_id,
                    )
                )

            if missing_qty > 0:
                missing_list.append(
                    DeckCardEntry(
                        id=entry.id,
                        deck_id=entry.deck_id,
                        oracle_id=entry.oracle_id,
                        name=entry.name,
                        set_code=entry.set_code,
                        collector_number=entry.collector_number,
                        quantity_required=missing_qty,
                        is_proxy=True,
                        physical_quantity=0,
                        proxy_quantity=missing_qty,
                        fulfilled_card_id=None,
                    )
                )

        plan.owned_cards = owned_list
        plan.missing_cards = missing_list
        plan.total_required = sum(c.quantity_required for c in deck_cards)
        plan.total_owned = sum(c.quantity_required for c in owned_list)
        plan.total_missing = sum(c.quantity_required for c in missing_list)
        return plan

    def analyze_deck_card_matches(
        self,
        deck_cards: Sequence[DeckCardEntry],
        inventory_cards: Sequence[InventoryCard],
        other_deck_cards: Sequence[DeckCardEntry] | None = None,
        allocate_mode: AllocationPolicy | str | None = None,
        current_deck_id: int | None = None,
        include_proxies: bool = True,
    ) -> tuple[list[DeckCardMatchDetail], int, int, int]:
        """Analyze physical and proxy card matches for deck cards with exact vs alternative print distinction.

        Returns:
            (details_list, total_exact, total_alt, total_missing)
        """
        if not deck_cards:
            return [], 0, 0, 0

        policy = (
            AllocationPolicy.from_str(allocate_mode)
            if allocate_mode is not None
            else self.default_policy
        )

        stock_pool = self._build_stock_pool(inventory_cards, include_proxies=include_proxies)
        self._deduct_other_decks(stock_pool, other_deck_cards, policy, current_deck_id)

        details_list: list[DeckCardMatchDetail] = []
        total_exact = 0
        total_alt = 0
        total_missing = 0

        for entry in deck_cards:
            needed = entry.quantity_required
            entry_sc = (entry.set_code or "").strip().lower()
            entry_num = (entry.collector_number or "").strip().lower()
            entry_oid = (entry.oracle_id or "").strip()
            entry_name = (entry.name or "").strip()

            exact_stock = 0
            alt_stock = 0
            available_versions: list[dict] = []

            for item in stock_pool:
                if item["quantity"] <= 0:
                    continue
                if not cards_match(entry_oid, entry_name, item["oracle_id"], item["name"]):
                    continue
                is_exact = prints_match(
                    entry_sc, entry_num, item["set_code"], item["collector_number"]
                )
                if is_exact:
                    exact_stock += item["quantity"]
                else:
                    alt_stock += item["quantity"]

                available_versions.append(
                    {
                        "card_id": item["id"],
                        "set_code": item["set_code"].upper(),
                        "collector_number": item["collector_number"],
                        "is_foil": item.get("is_foil", False),
                        "condition": item.get("condition", "NM"),
                        "is_proxy": item.get("is_proxy", False),
                        "quantity_available": item["quantity"],
                        "is_exact": is_exact,
                    }
                )

            # Determine physical vs proxy allocation
            has_explicit = entry.physical_quantity > 0 or entry.proxy_quantity > 0
            if has_explicit:
                allocated_physical = min(needed, entry.physical_quantity)
                allocated_proxy = (
                    entry.proxy_quantity
                    if entry.proxy_quantity > 0
                    else (needed - allocated_physical)
                )
            elif entry.is_proxy:
                allocated_physical = 0
                allocated_proxy = needed
            elif entry.fulfilled_card_id is not None:
                target_item = next(
                    (it for it in stock_pool if it["id"] == entry.fulfilled_card_id), None
                )
                avail_target = target_item["quantity"] if target_item else 0
                allocated_physical = min(needed, avail_target)
                allocated_proxy = needed - allocated_physical
            else:
                allocated_physical = min(needed, exact_stock + alt_stock)
                allocated_proxy = needed - allocated_physical

            # Deduct allocated physical copies from stock pool
            if allocated_physical > 0:
                self._deduct_from_pool(
                    stock_pool,
                    allocated_physical,
                    entry_oid,
                    entry_name,
                    entry_sc,
                    entry_num,
                    entry.fulfilled_card_id,
                )

            # Calculate status
            if allocated_physical >= needed:
                if exact_stock >= needed and (
                    entry.fulfilled_card_id is None
                    or any(
                        v["card_id"] == entry.fulfilled_card_id and v["is_exact"]
                        for v in available_versions
                    )
                ):
                    status = "exact"
                else:
                    status = "alternative"
            elif allocated_physical > 0:
                status = "partial"
            else:
                status = "missing"

            # Compute breakdown of fulfilled physical copies (exact vs alt)
            if entry.fulfilled_card_id is not None:
                is_fulfilled_exact = any(
                    v["card_id"] == entry.fulfilled_card_id and v["is_exact"]
                    for v in available_versions
                )
                if is_fulfilled_exact:
                    exact_fulfilled = min(allocated_physical, exact_stock)
                    alt_fulfilled = allocated_physical - exact_fulfilled
                else:
                    alt_fulfilled = allocated_physical
                    exact_fulfilled = 0
            else:
                exact_fulfilled = min(allocated_physical, exact_stock)
                alt_fulfilled = allocated_physical - exact_fulfilled

            details_list.append(
                DeckCardMatchDetail(
                    name=entry.name,
                    set_code=entry.set_code,
                    collector_number=entry.collector_number,
                    quantity_required=entry.quantity_required,
                    exact_stock=exact_stock,
                    alt_stock=alt_stock,
                    status=status,
                    oracle_id=entry.oracle_id,
                    deck_card_id=entry.id,
                    available_versions=available_versions,
                    assigned_version_id=entry.fulfilled_card_id,
                    physical_quantity=allocated_physical,
                    proxy_quantity=allocated_proxy,
                )
            )

            total_exact += exact_fulfilled
            total_alt += alt_fulfilled
            total_missing += allocated_proxy

        return details_list, total_exact, total_alt, total_missing

    def calculate_inventory_usage(
        self,
        inventory_cards: Sequence[InventoryCard],
        deck_cards: Sequence[DeckCardEntry],
        include_proxies: bool = True,
    ) -> tuple[dict[int, int], dict[int, int]]:
        """Calculate dynamic in-deck usage and available quantity for each inventory card.

        Returns:
            (usage_by_card_id, avail_by_card_id)
        """
        usage_by_card_id: dict[int, int] = {
            card.id: 0 for card in inventory_cards if card.id is not None
        }
        avail_by_card_id: dict[int, int] = {
            card.id: card.quantity for card in inventory_cards if card.id is not None
        }

        stock_pool = self._build_stock_pool(inventory_cards, include_proxies=include_proxies)

        for dc in deck_cards:
            if dc.proxy_quantity > 0 and dc.physical_quantity == 0:
                continue
            if dc.is_proxy and dc.physical_quantity == 0:
                continue

            dc_qty = int(dc.physical_quantity if dc.physical_quantity > 0 else dc.quantity_required)
            if dc_qty <= 0:
                continue

            self._deduct_from_pool(
                stock_pool,
                dc_qty,
                dc.oracle_id,
                dc.name,
                dc.set_code,
                dc.collector_number,
                dc.fulfilled_card_id,
            )

        for item in stock_pool:
            cid = item["id"]
            if cid is not None:
                avail_by_card_id[cid] = item["quantity"]
                usage_by_card_id[cid] = item["initial_quantity"] - item["quantity"]

        return usage_by_card_id, avail_by_card_id
