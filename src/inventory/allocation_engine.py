"""Dedicated pure, database-independent Stock Allocation & Proxy Optimization Engine.

Provides algorithms for:
- Evaluating physical card stock vs deck requirements.
- Distinguishing exact print matches vs alternative (oracle_id / name) matches.
- Calculating missing proxy plans under exclusive (Option C shared-binder) or shared policies.
- Tracking in-deck usage and remaining availability across registered decks.
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


class StockAllocationEngine:
    """Pure, database-independent engine for card matching and multi-deck stock allocation."""

    def __init__(self, default_policy: AllocationPolicy | str = AllocationPolicy.EXCLUSIVE) -> None:
        self.default_policy = AllocationPolicy.from_str(default_policy)

    def calculate_missing_proxies(
        self,
        deck_cards: Sequence[DeckCardEntry],
        inventory_cards: Sequence[InventoryCard],
        other_deck_cards: Sequence[DeckCardEntry] | None = None,
        allocate_mode: AllocationPolicy | str | None = None,
        current_deck_id: int | None = None,
        deck_name: str = "",
    ) -> MissingProxyPlan:
        """Evaluate physical card availability vs deck requirements.

        Under EXCLUSIVE / OPTION_C_SHARED policy, physical cards assigned to other registered decks
        are subtracted from available inventory stock.
        """
        plan = MissingProxyPlan(deck_name=deck_name)
        if not deck_cards:
            return plan

        policy = (
            AllocationPolicy.from_str(allocate_mode)
            if allocate_mode is not None
            else self.default_policy
        )

        # Aggregate physical collection totals by oracle_id and normalized name
        stock_by_oracle: dict[str, int] = {}
        stock_by_name: dict[str, int] = {}

        for card in inventory_cards:
            if card.is_proxy or card.quantity <= 0:
                continue
            qty = card.quantity
            oid = (card.oracle_id or "").strip()
            cname = (card.name or "").strip().lower()
            if oid:
                stock_by_oracle[oid] = stock_by_oracle.get(oid, 0) + qty
            if cname:
                stock_by_name[cname] = stock_by_name.get(cname, 0) + qty

        # Subtract physical cards already assigned to other registered decks if exclusive
        if policy in (AllocationPolicy.EXCLUSIVE, AllocationPolicy.OPTION_C_SHARED) and other_deck_cards:
            for assigned in other_deck_cards:
                if assigned.is_proxy or assigned.quantity_required <= 0:
                    continue
                if current_deck_id is not None and assigned.deck_id == current_deck_id:
                    continue

                assigned_qty = assigned.quantity_required
                oid = (assigned.oracle_id or "").strip()
                cname = (assigned.name or "").strip().lower()
                if oid in stock_by_oracle:
                    stock_by_oracle[oid] = max(0, stock_by_oracle[oid] - assigned_qty)
                if cname in stock_by_name:
                    stock_by_name[cname] = max(0, stock_by_name[cname] - assigned_qty)

        # Allocate stock for requested deck cards
        owned_list: list[DeckCardEntry] = []
        missing_list: list[DeckCardEntry] = []

        for entry in deck_cards:
            needed = entry.quantity_required
            oid = (entry.oracle_id or "").strip()
            cname = (entry.name or "").strip().lower()

            available = 0
            match_key = ""
            is_oracle_match = False

            if oid and oid in stock_by_oracle:
                available = stock_by_oracle[oid]
                match_key = oid
                is_oracle_match = True
            elif cname and cname in stock_by_name:
                available = stock_by_name[cname]
                match_key = cname
                is_oracle_match = False

            owned_qty = min(needed, available)
            missing_qty = needed - owned_qty

            if is_oracle_match and match_key:
                stock_by_oracle[match_key] -= owned_qty
                if cname in stock_by_name:
                    stock_by_name[cname] = max(0, stock_by_name[cname] - owned_qty)
            elif match_key:
                stock_by_name[match_key] -= owned_qty

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
    ) -> tuple[list[DeckCardMatchDetail], int, int, int]:
        """Analyze physical card matches for deck cards with exact vs alternative print distinction.

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

        stock_pool = [
            {
                "id": card.id,
                "oracle_id": (card.oracle_id or "").strip(),
                "name": (card.name or "").strip(),
                "set_code": (card.set_code or "").strip().lower(),
                "collector_number": (card.collector_number or "").strip().lower(),
                "quantity": int(card.quantity),
            }
            for card in inventory_cards
            if not card.is_proxy and card.quantity > 0
        ]

        # Deduct physical cards allocated to other registered decks
        if policy in (AllocationPolicy.EXCLUSIVE, AllocationPolicy.OPTION_C_SHARED) and other_deck_cards:
            for assigned in other_deck_cards:
                if assigned.is_proxy or assigned.quantity_required <= 0:
                    continue
                if current_deck_id is not None and assigned.deck_id == current_deck_id:
                    continue

                qty_to_deduct = int(assigned.quantity_required)
                assigned_sc = (assigned.set_code or "").strip().lower()
                assigned_num = (assigned.collector_number or "").strip().lower()
                assigned_oid = (assigned.oracle_id or "").strip()
                assigned_name = (assigned.name or "").strip().lower()

                # Exact match deduction first
                for item in stock_pool:
                    if qty_to_deduct <= 0:
                        break
                    if (
                        item["quantity"] > 0
                        and item["set_code"] == assigned_sc
                        and item["collector_number"] == assigned_num
                    ):
                        take = min(qty_to_deduct, item["quantity"])
                        item["quantity"] -= take
                        qty_to_deduct -= take

                # Alternative match deduction next
                if qty_to_deduct > 0:
                    for item in stock_pool:
                        if qty_to_deduct <= 0:
                            break
                        if item["quantity"] > 0:
                            matches = (assigned_oid and item["oracle_id"] == assigned_oid) or (
                                assigned_name and item["name"].lower() == assigned_name
                            )
                            if matches:
                                take = min(qty_to_deduct, item["quantity"])
                                item["quantity"] -= take
                                qty_to_deduct -= take

        details_list: list[DeckCardMatchDetail] = []
        total_exact = 0
        total_alt = 0
        total_missing = 0

        for entry in deck_cards:
            needed = entry.quantity_required
            entry_sc = (entry.set_code or "").strip().lower()
            entry_num = (entry.collector_number or "").strip().lower()
            entry_oid = (entry.oracle_id or "").strip()
            entry_name = (entry.name or "").strip().lower()

            exact_stock = 0
            alt_stock = 0

            for item in stock_pool:
                if item["quantity"] <= 0:
                    continue
                matches_card = (entry_oid and item["oracle_id"] == entry_oid) or (
                    entry_name and item["name"].lower() == entry_name
                )
                if not matches_card:
                    continue
                if item["set_code"] == entry_sc and item["collector_number"] == entry_num:
                    exact_stock += item["quantity"]
                else:
                    alt_stock += item["quantity"]

            if exact_stock >= needed:
                status = "exact"
            elif (exact_stock + alt_stock) >= needed and exact_stock == 0:
                status = "alternative"
            elif (exact_stock + alt_stock) > 0:
                status = "partial"
            else:
                status = "missing"

            exact_fulfilled = min(needed, exact_stock)
            rem_needed = needed - exact_fulfilled
            alt_fulfilled = min(rem_needed, alt_stock)
            missing = rem_needed - alt_fulfilled

            # Deduct allocated stock from pool: exact first, then alt
            deduct_exact = exact_fulfilled
            if deduct_exact > 0:
                for item in stock_pool:
                    if deduct_exact <= 0:
                        break
                    matches_card = (entry_oid and item["oracle_id"] == entry_oid) or (
                        entry_name and item["name"].lower() == entry_name
                    )
                    if (
                        matches_card
                        and item["set_code"] == entry_sc
                        and item["collector_number"] == entry_num
                        and item["quantity"] > 0
                    ):
                        take = min(deduct_exact, item["quantity"])
                        item["quantity"] -= take
                        deduct_exact -= take

            deduct_alt = alt_fulfilled
            if deduct_alt > 0:
                for item in stock_pool:
                    if deduct_alt <= 0:
                        break
                    matches_card = (entry_oid and item["oracle_id"] == entry_oid) or (
                        entry_name and item["name"].lower() == entry_name
                    )
                    is_exact = (
                        item["set_code"] == entry_sc and item["collector_number"] == entry_num
                    )
                    if matches_card and not is_exact and item["quantity"] > 0:
                        take = min(deduct_alt, item["quantity"])
                        item["quantity"] -= take
                        deduct_alt -= take

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
                )
            )

            total_exact += exact_fulfilled
            total_alt += alt_fulfilled
            total_missing += missing

        return details_list, total_exact, total_alt, total_missing

    def calculate_inventory_usage(
        self,
        inventory_cards: Sequence[InventoryCard],
        deck_cards: Sequence[DeckCardEntry],
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

        for dc in deck_cards:
            dc_qty = int(dc.quantity_required)
            dc_proxy = bool(dc.is_proxy)
            dc_sc = (dc.set_code or "").strip().lower()
            dc_num = (dc.collector_number or "").strip().lower()
            dc_oid = (dc.oracle_id or "").strip()
            dc_name = (dc.name or "").strip().lower()

            # First pass: exact print match
            for card in inventory_cards:
                if dc_qty <= 0:
                    break
                if card.id is None or card.is_proxy != dc_proxy:
                    continue
                c_sc = (card.set_code or "").strip().lower()
                c_num = (card.collector_number or "").strip().lower()
                if c_sc == dc_sc and c_num == dc_num:
                    avail = avail_by_card_id[card.id]
                    if avail > 0:
                        take = min(dc_qty, avail)
                        avail_by_card_id[card.id] -= take
                        usage_by_card_id[card.id] += take
                        dc_qty -= take

            # Second pass: alternative print match
            if dc_qty > 0:
                for card in inventory_cards:
                    if dc_qty <= 0:
                        break
                    if card.id is None or card.is_proxy != dc_proxy:
                        continue
                    c_oid = (card.oracle_id or "").strip()
                    c_name = (card.name or "").strip().lower()
                    matches = (dc_oid and c_oid == dc_oid) or (dc_name and c_name == dc_name)
                    if matches:
                        avail = avail_by_card_id[card.id]
                        if avail > 0:
                            take = min(dc_qty, avail)
                            avail_by_card_id[card.id] -= take
                            usage_by_card_id[card.id] += take
                            dc_qty -= take

        return usage_by_card_id, avail_by_card_id
