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
        include_proxies: bool = True,
    ) -> MissingProxyPlan:
        """Evaluate physical and proxy card availability vs deck requirements.

        Under EXCLUSIVE / OPTION_C_SHARED policy, cards assigned to other registered decks
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

        # Aggregate collection totals by oracle_id and normalized name
        stock_by_oracle: dict[str, int] = {}
        stock_by_name: dict[str, int] = {}

        for card in inventory_cards:
            if card.quantity <= 0:
                continue
            if not include_proxies and card.is_proxy:
                continue
            qty = card.quantity
            oid = (card.oracle_id or "").strip()
            cname = normalize_card_name(card.name)
            if oid:
                stock_by_oracle[oid] = stock_by_oracle.get(oid, 0) + qty
            if cname:
                stock_by_name[cname] = stock_by_name.get(cname, 0) + qty
                cname_front = cname.split(" // ")[0].strip()
                if cname_front != cname:
                    stock_by_name[cname_front] = stock_by_name.get(cname_front, 0) + qty

        # Subtract cards already assigned to other registered decks if exclusive
        if (
            policy in (AllocationPolicy.EXCLUSIVE, AllocationPolicy.OPTION_C_SHARED)
            and other_deck_cards
        ):
            for assigned in other_deck_cards:
                if current_deck_id is not None and assigned.deck_id == current_deck_id:
                    continue
                if assigned.proxy_quantity > 0 and assigned.physical_quantity == 0:
                    continue
                if assigned.is_proxy and assigned.physical_quantity == 0:
                    continue

                assigned_qty = (
                    assigned.physical_quantity
                    if assigned.physical_quantity > 0
                    else assigned.quantity_required
                )
                if assigned_qty <= 0:
                    continue

                oid = (assigned.oracle_id or "").strip()
                cname = normalize_card_name(assigned.name)
                cname_front = cname.split(" // ")[0].strip()
                if oid in stock_by_oracle:
                    stock_by_oracle[oid] = max(0, stock_by_oracle[oid] - assigned_qty)
                if cname in stock_by_name:
                    stock_by_name[cname] = max(0, stock_by_name[cname] - assigned_qty)
                if cname_front != cname and cname_front in stock_by_name:
                    stock_by_name[cname_front] = max(0, stock_by_name[cname_front] - assigned_qty)

        # Allocate stock for requested deck cards
        owned_list: list[DeckCardEntry] = []
        missing_list: list[DeckCardEntry] = []

        for entry in deck_cards:
            needed = entry.quantity_required
            oid = (entry.oracle_id or "").strip()
            cname = normalize_card_name(entry.name)
            cname_front = cname.split(" // ")[0].strip()

            has_explicit = entry.proxy_quantity > 0 or entry.physical_quantity > 0
            if has_explicit:
                owned_qty = min(needed, entry.physical_quantity)
                missing_qty = (
                    entry.proxy_quantity if entry.proxy_quantity > 0 else (needed - owned_qty)
                )
            elif entry.is_proxy:
                owned_qty = 0
                missing_qty = needed
            else:
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
                elif cname_front and cname_front in stock_by_name:
                    available = stock_by_name[cname_front]
                    match_key = cname_front
                    is_oracle_match = False

                owned_qty = min(needed, available)
                missing_qty = needed - owned_qty

                if is_oracle_match and match_key:
                    stock_by_oracle[match_key] -= owned_qty
                    if cname in stock_by_name:
                        stock_by_name[cname] = max(0, stock_by_name[cname] - owned_qty)
                    if cname_front != cname and cname_front in stock_by_name:
                        stock_by_name[cname_front] = max(0, stock_by_name[cname_front] - owned_qty)
                elif match_key:
                    stock_by_name[match_key] -= owned_qty
                    if match_key != cname and cname in stock_by_name:
                        stock_by_name[cname] = max(0, stock_by_name[cname] - owned_qty)
                    if match_key != cname_front and cname_front in stock_by_name:
                        stock_by_name[cname_front] = max(0, stock_by_name[cname_front] - owned_qty)

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

        stock_pool = [
            {
                "id": card.id,
                "oracle_id": (card.oracle_id or "").strip(),
                "name": (card.name or "").strip(),
                "set_code": (card.set_code or "").strip().lower(),
                "collector_number": (card.collector_number or "").strip().lower(),
                "quantity": int(card.quantity),
                "is_foil": bool(card.is_foil),
                "condition": str(card.condition or "NM"),
                "is_proxy": bool(card.is_proxy),
            }
            for card in inventory_cards
            if card.quantity > 0 and (include_proxies or not card.is_proxy)
        ]
        # Prioritize physical cards (is_proxy=False) before proxies (is_proxy=True)
        stock_pool.sort(key=lambda item: 1 if item["is_proxy"] else 0)

        def _card_matches(entry_oid: str, entry_name: str, item: dict) -> bool:
            if entry_oid and item["oracle_id"] and entry_oid == item["oracle_id"]:
                return True
            return names_match(entry_name, item["name"])

        def _print_matches(entry_sc: str, entry_num: str, item: dict) -> bool:
            return item["set_code"] == entry_sc and numbers_match(
                item["collector_number"], entry_num
            )

        # Deduct cards allocated to other registered decks
        if (
            policy in (AllocationPolicy.EXCLUSIVE, AllocationPolicy.OPTION_C_SHARED)
            and other_deck_cards
        ):
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

                assigned_sc = (assigned.set_code or "").strip().lower()
                assigned_num = (assigned.collector_number or "").strip().lower()
                assigned_oid = (assigned.oracle_id or "").strip()
                assigned_name = (assigned.name or "").strip()

                # Specific card ID fulfillment deduction first
                if assigned.fulfilled_card_id is not None:
                    for item in stock_pool:
                        if item["id"] == assigned.fulfilled_card_id and item["quantity"] > 0:
                            take = min(qty_to_deduct, item["quantity"])
                            item["quantity"] -= take
                            qty_to_deduct -= take
                            break

                # Exact match deduction next
                if qty_to_deduct > 0:
                    for item in stock_pool:
                        if qty_to_deduct <= 0:
                            break
                        if (
                            item["quantity"] > 0
                            and _card_matches(assigned_oid, assigned_name, item)
                            and _print_matches(assigned_sc, assigned_num, item)
                        ):
                            take = min(qty_to_deduct, item["quantity"])
                            item["quantity"] -= take
                            qty_to_deduct -= take

                # Alternative match deduction next
                if qty_to_deduct > 0:
                    for item in stock_pool:
                        if qty_to_deduct <= 0:
                            break
                        if item["quantity"] > 0 and _card_matches(
                            assigned_oid, assigned_name, item
                        ):
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
            entry_name = (entry.name or "").strip()

            exact_stock = 0
            alt_stock = 0
            available_versions: list[dict] = []

            for item in stock_pool:
                if item["quantity"] <= 0:
                    continue
                if not _card_matches(entry_oid, entry_name, item):
                    continue
                is_exact = _print_matches(entry_sc, entry_num, item)
                if is_exact:
                    exact_stock += item["quantity"]
                else:
                    alt_stock += item["quantity"]

                foil_str = " (Foil)" if item.get("is_foil") else ""
                proxy_str = " [Proxy]" if item.get("is_proxy") else ""
                label = f"{item['set_code'].upper()} #{item['collector_number']} - {item['quantity']} disp.{foil_str}{proxy_str}"
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
                        "label": label,
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
            else:
                if entry.fulfilled_card_id is not None:
                    # Allocate from specific card if available
                    target_item = next(
                        (it for it in stock_pool if it["id"] == entry.fulfilled_card_id), None
                    )
                    avail_target = target_item["quantity"] if target_item else 0
                    allocated_physical = min(needed, avail_target)
                    allocated_proxy = needed - allocated_physical
                else:
                    # Auto allocation
                    allocated_physical = min(needed, exact_stock + alt_stock)
                    allocated_proxy = needed - allocated_physical

            # Deduct allocated physical copies from stock pool
            deduct_rem = allocated_physical
            if deduct_rem > 0 and entry.fulfilled_card_id is not None:
                for item in stock_pool:
                    if item["id"] == entry.fulfilled_card_id and item["quantity"] > 0:
                        take = min(deduct_rem, item["quantity"])
                        item["quantity"] -= take
                        deduct_rem -= take
                        break

            # Deduct exact matches first
            if deduct_rem > 0:
                for item in stock_pool:
                    if deduct_rem <= 0:
                        break
                    if (
                        item["quantity"] > 0
                        and _card_matches(entry_oid, entry_name, item)
                        and _print_matches(entry_sc, entry_num, item)
                    ):
                        take = min(deduct_rem, item["quantity"])
                        item["quantity"] -= take
                        deduct_rem -= take

            # Deduct alternative matches next
            if deduct_rem > 0:
                for item in stock_pool:
                    if deduct_rem <= 0:
                        break
                    if (
                        item["quantity"] > 0
                        and _card_matches(entry_oid, entry_name, item)
                        and not _print_matches(entry_sc, entry_num, item)
                    ):
                        take = min(deduct_rem, item["quantity"])
                        item["quantity"] -= take
                        deduct_rem -= take

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

        for dc in deck_cards:
            if dc.proxy_quantity > 0 and dc.physical_quantity == 0:
                continue
            if dc.is_proxy and dc.physical_quantity == 0:
                continue

            dc_qty = int(dc.physical_quantity if dc.physical_quantity > 0 else dc.quantity_required)
            if dc_qty <= 0:
                continue

            # If a specific inventory card was chosen to fulfill this requirement
            if dc.fulfilled_card_id is not None and dc.fulfilled_card_id in avail_by_card_id:
                avail = avail_by_card_id[dc.fulfilled_card_id]
                if avail > 0:
                    take = min(dc_qty, avail)
                    avail_by_card_id[dc.fulfilled_card_id] -= take
                    usage_by_card_id[dc.fulfilled_card_id] += take
                    dc_qty -= take

            if dc_qty <= 0:
                continue

            dc_sc = (dc.set_code or "").strip().lower()
            dc_num = (dc.collector_number or "").strip().lower()
            dc_oid = (dc.oracle_id or "").strip()
            dc_name = (dc.name or "").strip()

            def _card_matches_dc(card: InventoryCard) -> bool:
                if dc_oid and card.oracle_id and dc_oid == card.oracle_id.strip():
                    return True
                return names_match(dc_name, card.name)

            def _print_matches_dc(card: InventoryCard) -> bool:
                return (card.set_code or "").strip().lower() == dc_sc and numbers_match(
                    card.collector_number, dc_num
                )

            # Pass 1: exact print match (prefer physical cards, then proxies)
            targets = [False, True] if include_proxies else [False]
            for target_proxy in targets:
                if dc_qty <= 0:
                    break
                for card in inventory_cards:
                    if dc_qty <= 0:
                        break
                    if card.id is None or card.is_proxy != target_proxy:
                        continue
                    if _card_matches_dc(card) and _print_matches_dc(card):
                        avail = avail_by_card_id[card.id]
                        if avail > 0:
                            take = min(dc_qty, avail)
                            avail_by_card_id[card.id] -= take
                            usage_by_card_id[card.id] += take
                            dc_qty -= take

            # Pass 2: alternative print match (prefer physical cards, then proxies)
            if dc_qty > 0:
                for target_proxy in targets:
                    if dc_qty <= 0:
                        break
                    for card in inventory_cards:
                        if dc_qty <= 0:
                            break
                        if card.id is None or card.is_proxy != target_proxy:
                            continue
                        if _card_matches_dc(card):
                            avail = avail_by_card_id[card.id]
                            if avail > 0:
                                take = min(dc_qty, avail)
                                avail_by_card_id[card.id] -= take
                                usage_by_card_id[card.id] += take
                                dc_qty -= take

        return usage_by_card_id, avail_by_card_id
