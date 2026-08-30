"""Tests for InventoryService business logic and proxy planning."""

from pathlib import Path
import pytest

from src.inventory.models import DeckCardEntry, DeckRecord, InventoryCard
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
