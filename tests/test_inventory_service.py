"""Tests for InventoryService business logic and proxy planning."""

from pathlib import Path

import pytest

from src.inventory.models import DeckCardEntry, DeckCardMatchDetail, DeckRecord, InventoryCard
from src.inventory.service import InventoryService


@pytest.fixture
def service(tmp_path: Path) -> InventoryService:
    db_path = tmp_path / "service_test.db"
    return InventoryService(db_path=db_path)


class TestInventoryService:
    def test_add_and_get_inventory_cards(self, service: InventoryService):
        card1 = InventoryCard(
            oracle_id="orc-1",
            name="Sol Ring",
            set_code="c21",
            collector_number="263",
            quantity=2,
            is_foil=False,
            is_proxy=False,
        )
        card2 = InventoryCard(
            oracle_id="orc-2",
            name="Mana Crypt",
            set_code="2xm",
            collector_number="270",
            quantity=1,
            is_foil=True,
            is_proxy=True,
        )

        id1 = service.add_or_update_card(card1)
        id2 = service.add_or_update_card(card2)
        assert id1 > 0
        assert id2 > 0

        # Fetch all
        all_cards = service.get_inventory_cards()
        assert len(all_cards) == 2

        # Filter proxies
        proxies = service.get_inventory_cards(filter_proxy=True)
        assert len(proxies) == 1
        assert proxies[0].name == "Mana Crypt"

        # Filter physical
        physical = service.get_inventory_cards(filter_proxy=False)
        assert len(physical) == 1
        assert physical[0].name == "Sol Ring"

        # Query search
        search_res = service.get_inventory_cards(query="sol")
        assert len(search_res) == 1
        assert search_res[0].name == "Sol Ring"

    def test_add_existing_increments_quantity(self, service: InventoryService):
        card = InventoryCard(
            oracle_id="orc-1",
            name="Sol Ring",
            set_code="c21",
            collector_number="263",
            quantity=2,
        )
        first_id = service.add_or_update_card(card)

        # Add 3 more copies of same card
        more = InventoryCard(
            oracle_id="orc-1",
            name="Sol Ring",
            set_code="c21",
            collector_number="263",
            quantity=3,
        )
        updated_id = service.add_or_update_card(more)
        assert first_id == updated_id

        fetched = service.get_card_by_id(first_id)
        assert fetched is not None
        assert fetched.quantity == 5

    def test_update_card_by_id(self, service: InventoryService):
        card = InventoryCard(
            oracle_id="orc-1",
            name="Sol Ring",
            set_code="c21",
            collector_number="263",
            quantity=1,
        )
        card_id = service.add_or_update_card(card)

        # Modify fields
        card.id = card_id
        card.quantity = 10
        card.condition = "LP"
        service.add_or_update_card(card)

        updated = service.get_card_by_id(card_id)
        assert updated is not None
        assert updated.quantity == 10
        assert updated.condition == "LP"

    def test_delete_card(self, service: InventoryService):
        card = InventoryCard(
            oracle_id="orc-1",
            name="Sol Ring",
            set_code="c21",
            collector_number="263",
            quantity=1,
        )
        card_id = service.add_or_update_card(card)

        assert service.delete_card(card_id) is True
        assert service.delete_card(card_id) is False
        assert service.get_card_by_id(card_id) is None

    def test_deck_registration_and_retrieval(self, service: InventoryService):
        deck = DeckRecord(name="Urza Power", source_url="https://moxfield.com/decks/123", format="Commander")
        cards = [
            DeckCardEntry(deck_id=0, oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1),
            DeckCardEntry(deck_id=0, oracle_id="orc-2", name="Island", set_code="unf", collector_number="236", quantity_required=30),
        ]

        deck_id = service.register_deck(deck, cards)
        assert deck_id > 0

        # Retrieve deck
        res = service.get_deck_by_id(deck_id)
        assert res is not None
        fetched_deck, fetched_cards = res
        assert fetched_deck.name == "Urza Power"
        assert len(fetched_cards) == 2
        assert fetched_cards[0].name == "Island"
        assert fetched_cards[0].quantity_required == 30

        # Get all decks
        all_decks = service.get_all_decks()
        assert len(all_decks) == 1
        assert all_decks[0].id == deck_id

    def test_calculate_missing_proxies_option_c(self, service: InventoryService):
        # Add physical inventory stock:
        # - 2 physical Sol Rings
        # - 10 physical Islands
        # - 0 Mana Crypts
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_proxy=False)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-island", name="Island", set_code="dom", collector_number="257", quantity=10, is_proxy=False)
        )

        # Deck 1 is already registered and uses 1 physical Sol Ring and 5 physical Islands
        deck1 = DeckRecord(name="Deck 1")
        deck1_cards = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1, is_proxy=False),
            DeckCardEntry(deck_id=0, oracle_id="orc-island", name="Island", set_code="dom", collector_number="257", quantity_required=5, is_proxy=False),
        ]
        service.register_deck(deck1, deck1_cards)

        # Now evaluate Deck 2 which needs:
        # - 2 Sol Rings (1 remaining available -> 1 owned, 1 missing proxy)
        # - 8 Islands (5 remaining available -> 5 owned, 3 missing proxies)
        # - 1 Mana Crypt (0 available -> 0 owned, 1 missing proxy)
        deck2_requirements = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=2),
            DeckCardEntry(deck_id=0, oracle_id="orc-island", name="Island", set_code="dom", collector_number="257", quantity_required=8),
            DeckCardEntry(deck_id=0, oracle_id="orc-crypt", name="Mana Crypt", set_code="2xm", collector_number="270", quantity_required=1),
        ]

        plan = service.calculate_missing_proxies(deck2_requirements, allocate_mode="exclusive")

        assert plan.total_required == 11
        assert plan.total_owned == 6  # 1 Sol Ring + 5 Islands
        assert plan.total_missing == 5  # 1 Sol Ring + 3 Islands + 1 Mana Crypt

        # Check owned cards breakdown
        owned_sol = next(c for c in plan.owned_cards if c.name == "Sol Ring")
        assert owned_sol.quantity_required == 1
        assert owned_sol.is_proxy is False

        owned_island = next(c for c in plan.owned_cards if c.name == "Island")
        assert owned_island.quantity_required == 5
        assert owned_island.is_proxy is False

        # Check missing cards breakdown
        missing_sol = next(c for c in plan.missing_cards if c.name == "Sol Ring")
        assert missing_sol.quantity_required == 1
        assert missing_sol.is_proxy is True

        missing_island = next(c for c in plan.missing_cards if c.name == "Island")
        assert missing_island.quantity_required == 3
        assert missing_island.is_proxy is True

        missing_crypt = next(c for c in plan.missing_cards if c.name == "Mana Crypt")
        assert missing_crypt.quantity_required == 1
        assert missing_crypt.is_proxy is True

    def test_import_deck_cards_to_inventory(self, service: InventoryService):
        deck = DeckRecord(name="Proxy Export Deck")
        cards = [
            DeckCardEntry(deck_id=0, oracle_id="orc-1", name="Mox Diamond", set_code="tpr", collector_number="228", quantity_required=1),
            DeckCardEntry(deck_id=0, oracle_id="orc-2", name="Chrome Mox", set_code="2xm", collector_number="240", quantity_required=2),
        ]
        deck_id = service.register_deck(deck, cards)

        # Import into inventory as proxies
        imported_count = service.import_deck_cards_to_inventory(deck_id, as_proxy=True)
        assert imported_count == 3

        inventory_cards = service.get_inventory_cards(filter_proxy=True)
        assert len(inventory_cards) == 2
        assert sum(c.quantity for c in inventory_cards) == 3

    def test_get_inventory_stats(self, service: InventoryService):
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_proxy=False)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-2", name="Black Lotus", set_code="lea", collector_number="232", quantity=1, is_proxy=True)
        )

        deck = DeckRecord(name="Commander 1")
        service.register_deck(deck, [])

        stats = service.get_inventory_stats()
        assert stats["total_cards"] == 3
        assert stats["unique_oracle_ids"] == 2
        assert stats["physical_count"] == 2
        assert stats["proxy_count"] == 1
        assert stats["deck_count"] == 1

    def test_get_unique_set_codes(self, service: InventoryService):
        # Initially empty
        assert service.get_unique_set_codes() == []

        # Add cards with different set codes, some duplicate and lower/upper case, some empty
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity=1)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-2", name="Mana Crypt", set_code="2xm", collector_number="270", quantity=1)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-3", name="Black Lotus", set_code="LEA", collector_number="232", quantity=1)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-4", name="Forest", set_code="c21", collector_number="400", quantity=1)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-5", name="Unknown Token", set_code="", collector_number="1", quantity=1)
        )

        codes = service.get_unique_set_codes()
        assert codes == ["2XM", "C21", "LEA"]

    def test_get_card_deck_usages(self, service: InventoryService):
        # Empty calls
        assert service.get_card_deck_usages("", "") == []

        # Register two decks
        deck1 = DeckRecord(name="Deck Alpha", format="Commander")
        deck1_cards = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1, is_proxy=False),
            DeckCardEntry(deck_id=0, oracle_id="orc-island", name="Island", set_code="dom", collector_number="257", quantity_required=10, is_proxy=False),
        ]
        deck1_id = service.register_deck(deck1, deck1_cards)

        deck2 = DeckRecord(name="Deck Beta", format="Modern")
        deck2_cards = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="2xm", collector_number="270", quantity_required=1, is_proxy=True),
        ]
        deck2_id = service.register_deck(deck2, deck2_cards)

        # Query by oracle_id
        usages_sol = service.get_card_deck_usages(oracle_id="orc-sol")
        assert len(usages_sol) == 2
        assert usages_sol[0]["deck_id"] == deck1_id
        assert usages_sol[0]["deck_name"] == "Deck Alpha"
        assert usages_sol[0]["format"] == "Commander"
        assert usages_sol[0]["quantity_required"] == 1
        assert usages_sol[0]["set_code"] == "c21"
        assert usages_sol[0]["collector_number"] == "263"
        assert usages_sol[0]["is_proxy"] is False

        assert usages_sol[1]["deck_id"] == deck2_id
        assert usages_sol[1]["deck_name"] == "Deck Beta"
        assert usages_sol[1]["format"] == "Modern"
        assert usages_sol[1]["quantity_required"] == 1
        assert usages_sol[1]["is_proxy"] is True

        # Query by name (case-insensitive)
        usages_island = service.get_card_deck_usages(card_name="ISLAND")
        assert len(usages_island) == 1
        assert usages_island[0]["deck_id"] == deck1_id
        assert usages_island[0]["quantity_required"] == 10

        # Query by non-existent card
        usages_none = service.get_card_deck_usages(card_name="Nonexistent Card")
        assert usages_none == []

    def test_get_inventory_cards_with_usage(self, service: InventoryService):
        # Add inventory cards
        card_sol = InventoryCard(
            oracle_id="orc-sol",
            name="Sol Ring",
            set_code="c21",
            collector_number="263",
            quantity=2,
            is_proxy=False,
            metadata_json='{"rarity": "uncommon"}',
        )
        card_island = InventoryCard(
            oracle_id="orc-island",
            name="Island",
            set_code="dom",
            collector_number="257",
            quantity=10,
            is_proxy=False,
            metadata_json='{"rarity": "common"}',
        )
        card_lotus = InventoryCard(
            oracle_id="orc-lotus",
            name="Black Lotus",
            set_code="lea",
            collector_number="232",
            quantity=1,
            is_proxy=True,
            metadata_json='{"rarity": "rare"}',
        )
        service.add_or_update_card(card_sol)
        service.add_or_update_card(card_island)
        service.add_or_update_card(card_lotus)

        # Register deck using 1 Sol Ring and 10 Islands
        deck = DeckRecord(name="Test Deck")
        deck_cards = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1, is_proxy=False),
            DeckCardEntry(deck_id=0, oracle_id="orc-island", name="Island", set_code="dom", collector_number="257", quantity_required=10, is_proxy=False),
        ]
        service.register_deck(deck, deck_cards)

        # 1. Fetch all with usage
        results = service.get_inventory_cards_with_usage()
        assert len(results) == 3

        # Map results by card name
        res_by_name = {c.name: (c, in_decks, avail) for c, in_decks, avail in results}
        sol_c, sol_in_decks, sol_avail = res_by_name["Sol Ring"]
        assert sol_c.quantity == 2
        assert sol_in_decks == 1
        assert sol_avail == 1

        island_c, island_in_decks, island_avail = res_by_name["Island"]
        assert island_c.quantity == 10
        assert island_in_decks == 10
        assert island_avail == 0

        lotus_c, lotus_in_decks, lotus_avail = res_by_name["Black Lotus"]
        assert lotus_c.quantity == 1
        assert lotus_in_decks == 0
        assert lotus_avail == 1

        # 2. Filter usage: available
        avail_results = service.get_inventory_cards_with_usage(filter_usage="available")
        avail_names = [c.name for c, _, _ in avail_results]
        assert "Sol Ring" in avail_names
        assert "Black Lotus" in avail_names
        assert "Island" not in avail_names

        # 3. Filter usage: in_decks
        in_decks_results = service.get_inventory_cards_with_usage(filter_usage="in_decks")
        in_decks_names = [c.name for c, _, _ in in_decks_results]
        assert "Sol Ring" in in_decks_names
        assert "Island" in in_decks_names
        assert "Black Lotus" not in in_decks_names

        # 4. Filter proxy
        proxy_results = service.get_inventory_cards_with_usage(filter_proxy=True)
        assert len(proxy_results) == 1
        assert proxy_results[0][0].name == "Black Lotus"

        real_results = service.get_inventory_cards_with_usage(filter_proxy=False)
        assert len(real_results) == 2

        # 5. Filter set (case-insensitive)
        set_results = service.get_inventory_cards_with_usage(filter_set="C21")
        assert len(set_results) == 1
        assert set_results[0][0].name == "Sol Ring"

        # 6. Filter rarity (case-insensitive)
        rarity_results = service.get_inventory_cards_with_usage(filter_rarity="Rare")
        assert len(rarity_results) == 1
        assert rarity_results[0][0].name == "Black Lotus"

        # 7. Query search
        query_results = service.get_inventory_cards_with_usage(query="lotus")
        assert len(query_results) == 1
        assert query_results[0][0].name == "Black Lotus"

    def test_analyze_deck_card_matches(self, service: InventoryService):
        # Empty requirements
        details, exact, alt, missing = service.analyze_deck_card_matches([])
        assert details == []
        assert exact == 0
        assert alt == 0
        assert missing == 0

        # Physical stock:
        # - 2x Sol Ring (c21, 263)
        # - 1x Sol Ring (2xm, 270)
        # - 1x Arcane Signet (eld, 331)
        # - 0x Mana Vault
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_proxy=False)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-sol", name="Sol Ring", set_code="2xm", collector_number="270", quantity=1, is_proxy=False)
        )
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-signet", name="Arcane Signet", set_code="eld", collector_number="331", quantity=1, is_proxy=False)
        )

        # Requirement 1: 2x Sol Ring (c21, 263) -> status: exact (exact_stock=2, needed=2)
        # Requirement 2: 1x Sol Ring (lea, 100) -> status: alternative (exact_stock=0, alt_stock=1 [2xm since c21 is used])
        # Requirement 3: 2x Arcane Signet (eld, 331) -> status: partial (exact_stock=1, needed=2)
        # Requirement 4: 1x Mana Vault (uma, 229) -> status: missing (exact_stock=0, alt_stock=0)
        deck_requirements = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=2),
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="lea", collector_number="100", quantity_required=1),
            DeckCardEntry(deck_id=0, oracle_id="orc-signet", name="Arcane Signet", set_code="eld", collector_number="331", quantity_required=2),
            DeckCardEntry(deck_id=0, oracle_id="orc-vault", name="Mana Vault", set_code="uma", collector_number="229", quantity_required=1),
        ]

        details, total_exact, total_alt, total_missing = service.analyze_deck_card_matches(deck_requirements)

        assert len(details) == 4
        assert isinstance(details[0], DeckCardMatchDetail)

        # Check detail 1 (Exact)
        d1 = details[0]
        assert d1.name == "Sol Ring"
        assert d1.status == "exact"
        assert d1.exact_stock == 2
        assert d1.alt_stock == 1

        # Check detail 2 (Alternative)
        d2 = details[1]
        assert d2.name == "Sol Ring"
        assert d2.status == "alternative"
        assert d2.exact_stock == 0
        assert d2.alt_stock == 1

        # Check detail 3 (Partial)
        d3 = details[2]
        assert d3.name == "Arcane Signet"
        assert d3.status == "partial"
        assert d3.exact_stock == 1
        assert d3.alt_stock == 0

        # Check detail 4 (Missing)
        d4 = details[3]
        assert d4.name == "Mana Vault"
        assert d4.status == "missing"
        assert d4.exact_stock == 0
        assert d4.alt_stock == 0

        # Check totals:
        # Total required = 2 + 1 + 2 + 1 = 6
        # Exact = 2 (Sol Ring 1) + 1 (Arcane Signet) = 3
        # Alt = 1 (Sol Ring 2) = 1
        # Missing = 1 (Arcane Signet) + 1 (Mana Vault) = 2
        assert total_exact == 3
        assert total_alt == 1
        assert total_missing == 2
        assert total_exact + total_alt + total_missing == 6

    def test_analyze_deck_card_matches_with_other_decks_option_c(self, service: InventoryService):
        # Stock: 2 physical Sol Rings (c21, 263)
        service.add_or_update_card(
            InventoryCard(oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_proxy=False)
        )

        # Registered Deck 1 uses 1 physical Sol Ring
        deck1 = DeckRecord(name="Deck 1")
        deck1_cards = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1, is_proxy=False),
        ]
        deck1_id = service.register_deck(deck1, deck1_cards)

        # Evaluating Deck 2 (requires 2 Sol Rings):
        deck2_reqs = [
            DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=2),
        ]
        # Option C deducts Deck 1's physical card -> 1 available
        details, exact, alt, missing = service.analyze_deck_card_matches(deck2_reqs, current_deck_id=None)
        assert details[0].exact_stock == 1
        assert details[0].status == "partial"
        assert exact == 1
        assert alt == 0
        assert missing == 1

        # Re-evaluating Deck 1 with current_deck_id=deck1_id -> Deck 1 sees full 2 stock available
        details_d1, exact_d1, alt_d1, missing_d1 = service.analyze_deck_card_matches(deck1_cards, current_deck_id=deck1_id)
        assert details_d1[0].exact_stock == 2
        assert details_d1[0].status == "exact"
        assert exact_d1 == 1
        assert alt_d1 == 0
        assert missing_d1 == 0

