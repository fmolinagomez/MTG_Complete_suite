"""Dedicated unit tests for pure StockAllocationEngine and AllocationPolicy."""

import pytest

from src.inventory.allocation_engine import AllocationPolicy, StockAllocationEngine
from src.inventory.models import DeckCardEntry, InventoryCard, MissingProxyPlan


class TestAllocationPolicy:
    def test_from_str_conversions(self):
        assert AllocationPolicy.from_str("exclusive") == AllocationPolicy.EXCLUSIVE
        assert AllocationPolicy.from_str("OPTION_C_SHARED") == AllocationPolicy.EXCLUSIVE
        assert AllocationPolicy.from_str("option_c") == AllocationPolicy.EXCLUSIVE
        assert AllocationPolicy.from_str("shared") == AllocationPolicy.SHARED
        assert AllocationPolicy.from_str("unlimited") == AllocationPolicy.SHARED
        assert AllocationPolicy.from_str(AllocationPolicy.SHARED) == AllocationPolicy.SHARED
        assert AllocationPolicy.from_str("unknown_fallback") == AllocationPolicy.EXCLUSIVE


class TestStockAllocationEngine:
    @pytest.fixture
    def engine(self) -> StockAllocationEngine:
        return StockAllocationEngine()

    def test_calculate_missing_proxies_empty(self, engine: StockAllocationEngine):
        plan = engine.calculate_missing_proxies([], [])
        assert isinstance(plan, MissingProxyPlan)
        assert plan.total_required == 0
        assert plan.total_owned == 0
        assert plan.total_missing == 0
        assert plan.owned_cards == []
        assert plan.missing_cards == []

    def test_calculate_missing_proxies_pure_memory(self, engine: StockAllocationEngine):
        inventory = [
            InventoryCard(id=1, oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_proxy=False),
            InventoryCard(id=2, oracle_id="orc-2", name="Mana Vault", set_code="uma", collector_number="229", quantity=1, is_proxy=False),
            InventoryCard(id=3, oracle_id="orc-3", name="Proxy Lotus", set_code="lea", collector_number="232", quantity=4, is_proxy=True),
        ]

        deck_cards = [
            DeckCardEntry(deck_id=1, oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=3),
            DeckCardEntry(deck_id=1, oracle_id="orc-2", name="Mana Vault", set_code="uma", collector_number="229", quantity_required=1),
            DeckCardEntry(deck_id=1, oracle_id="orc-3", name="Proxy Lotus", set_code="lea", collector_number="232", quantity_required=1),
            DeckCardEntry(deck_id=1, oracle_id="orc-4", name="Mox Pearl", set_code="lea", collector_number="233", quantity_required=1),
        ]

        # Default (include_proxies=True): Proxy Lotus in inventory counts as owned
        plan = engine.calculate_missing_proxies(deck_cards, inventory, include_proxies=True)
        assert plan.total_required == 6
        assert plan.total_owned == 4
        assert plan.total_missing == 2

        # Physical only (include_proxies=False): Proxy Lotus in inventory is excluded
        plan_phys = engine.calculate_missing_proxies(deck_cards, inventory, include_proxies=False)
        assert plan_phys.total_required == 6
        assert plan_phys.total_owned == 3
        assert plan_phys.total_missing == 3

        owned_sol = next(c for c in plan.owned_cards if c.name == "Sol Ring")
        assert owned_sol.quantity_required == 2
        assert owned_sol.is_proxy is False

        missing_sol = next(c for c in plan.missing_cards if c.name == "Sol Ring")
        assert missing_sol.quantity_required == 1
        assert missing_sol.is_proxy is True

    def test_calculate_missing_proxies_name_fallback_matching(self, engine: StockAllocationEngine):
        # Stock has empty oracle_id but identical name
        inventory = [
            InventoryCard(id=1, oracle_id="", name="Counterspell", set_code="ema", collector_number="43", quantity=2, is_proxy=False),
        ]
        deck_cards = [
            DeckCardEntry(deck_id=1, oracle_id="orc-counter", name="COUNTERSPELL", set_code="mh2", collector_number="267", quantity_required=3),
        ]

        plan = engine.calculate_missing_proxies(deck_cards, inventory)
        assert plan.total_required == 3
        assert plan.total_owned == 2
        assert plan.total_missing == 1

    def test_exclusive_vs_shared_allocation_policy(self, engine: StockAllocationEngine):
        inventory = [
            InventoryCard(id=1, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_proxy=False),
        ]
        other_deck_cards = [
            DeckCardEntry(deck_id=10, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1, is_proxy=False),
        ]
        requested_cards = [
            DeckCardEntry(deck_id=20, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=2),
        ]

        # Exclusive: Deck 10 takes 1 of 2 -> 1 left for Deck 20
        plan_exclusive = engine.calculate_missing_proxies(
            requested_cards,
            inventory,
            other_deck_cards=other_deck_cards,
            allocate_mode=AllocationPolicy.EXCLUSIVE,
        )
        assert plan_exclusive.total_owned == 1
        assert plan_exclusive.total_missing == 1

        # Shared: Deck 10 does not reserve physical stock -> 2 available for Deck 20
        plan_shared = engine.calculate_missing_proxies(
            requested_cards,
            inventory,
            other_deck_cards=other_deck_cards,
            allocate_mode=AllocationPolicy.SHARED,
        )
        assert plan_shared.total_owned == 2
        assert plan_shared.total_missing == 0

        # Current deck id exclusion: if current_deck_id == 10, other_deck_cards with deck_id 10 are ignored
        plan_current_deck = engine.calculate_missing_proxies(
            requested_cards,
            inventory,
            other_deck_cards=other_deck_cards,
            allocate_mode=AllocationPolicy.EXCLUSIVE,
            current_deck_id=10,
        )
        assert plan_current_deck.total_owned == 2
        assert plan_current_deck.total_missing == 0

    def test_analyze_deck_card_matches(self, engine: StockAllocationEngine):
        inventory = [
            InventoryCard(id=1, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_proxy=False),
            InventoryCard(id=2, oracle_id="orc-sol", name="Sol Ring", set_code="2xm", collector_number="270", quantity=1, is_proxy=False),
            InventoryCard(id=3, oracle_id="orc-crypt", name="Mana Crypt", set_code="ema", collector_number="225", quantity=1, is_proxy=False),
        ]

        deck_cards = [
            # Exact match: 2 required, 2 exact stock (c21 #263)
            DeckCardEntry(deck_id=1, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=2),
            # Alt match: 1 required, 0 exact (lea #100), 1 alt remaining (2xm #270)
            DeckCardEntry(deck_id=1, oracle_id="orc-sol", name="Sol Ring", set_code="lea", collector_number="100", quantity_required=1),
            # Partial match: 2 required, 1 exact available
            DeckCardEntry(deck_id=1, oracle_id="orc-crypt", name="Mana Crypt", set_code="ema", collector_number="225", quantity_required=2),
            # Missing: 1 required, 0 available
            DeckCardEntry(deck_id=1, oracle_id="orc-lotus", name="Black Lotus", set_code="lea", collector_number="232", quantity_required=1),
        ]

        details, total_exact, total_alt, total_missing = engine.analyze_deck_card_matches(deck_cards, inventory)

        assert len(details) == 4
        assert details[0].status == "exact"
        assert details[0].exact_stock == 2
        assert details[0].alt_stock == 1

        assert details[1].status == "alternative"
        assert details[1].exact_stock == 0
        assert details[1].alt_stock == 1

        assert details[2].status == "partial"
        assert details[2].exact_stock == 1
        assert details[2].alt_stock == 0

        assert details[3].status == "missing"
        assert details[3].exact_stock == 0
        assert details[3].alt_stock == 0

        # Totals: 2 (sol exact) + 1 (crypt exact) = 3 exact
        # 1 (sol alt) = 1 alt
        # 1 (crypt missing) + 1 (lotus missing) = 2 missing
        assert total_exact == 3
        assert total_alt == 1
        assert total_missing == 2

    def test_calculate_inventory_usage(self, engine: StockAllocationEngine):
        inventory = [
            InventoryCard(id=101, oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity=3, is_proxy=False),
            InventoryCard(id=102, oracle_id="orc-1", name="Sol Ring", set_code="2xm", collector_number="270", quantity=1, is_proxy=False),
            InventoryCard(id=103, oracle_id="orc-2", name="Island", set_code="unf", collector_number="236", quantity=10, is_proxy=False),
        ]

        deck_cards = [
            # Exact match takes 2 from id 101
            DeckCardEntry(deck_id=1, oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=2, is_proxy=False),
            # Alt match for a different set (lea) takes remaining 1 from 101, then 1 from 102
            DeckCardEntry(deck_id=2, oracle_id="orc-1", name="Sol Ring", set_code="lea", collector_number="1", quantity_required=2, is_proxy=False),
        ]

        usage, avail = engine.calculate_inventory_usage(inventory, deck_cards)

        assert usage[101] == 3
        assert avail[101] == 0

        assert usage[102] == 1
        assert avail[102] == 0

        assert usage[103] == 0
        assert avail[103] == 10
