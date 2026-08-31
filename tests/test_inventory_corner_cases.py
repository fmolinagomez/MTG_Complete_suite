"""Corner-case test suite for ManaBox CSV parsing, various encodings, malformed CSVs, and Option C multi-deck stock allocation."""


import pytest

from src.inventory.manabox_csv import parse_manabox_csv
from src.inventory.models import DeckCardEntry, DeckRecord, InventoryCard
from src.inventory.service import InventoryService


class TestManaBoxCSVEncodings:
    def test_utf8_with_bom(self, tmp_path):
        """CSV file saved with UTF-8 BOM is parsed without corrupting first header."""
        csv_text = "\ufeffName,Set code,Collector number,Quantity,Foil\nLightning Bolt,2ED,162,4,normal\n"
        csv_path = tmp_path / "bom.csv"
        csv_path.write_bytes(csv_text.encode("utf-8-sig"))

        cards = parse_manabox_csv(csv_path)
        assert len(cards) == 1
        assert cards[0].name == "Lightning Bolt"
        assert cards[0].set_code == "2ed"
        assert cards[0].collector_number == "162"
        assert cards[0].quantity == 4
        assert cards[0].is_foil is False

    def test_latin1_encoding_with_accents(self, tmp_path):
        """CSV file with Latin-1 accented characters (e.g. Spanish names/sets)."""
        csv_text = "Name,Set code,Collector number,Quantity,Foil\nRelámpago,4ED,123,2,normal\n"
        csv_path = tmp_path / "latin1.csv"
        csv_path.write_bytes(csv_text.encode("iso-8859-1"))

        cards = parse_manabox_csv(csv_path)
        assert len(cards) == 1
        assert cards[0].name == "Relámpago"
        assert cards[0].quantity == 2

    def test_windows_1252_encoding_with_smart_quotes(self, tmp_path):
        """CSV file with CP1252 specific characters."""
        csv_text = "Name,Set code,Collector number,Quantity,Foil\nUrza’s Tower,ATQ,85a,1,normal\n"
        csv_path = tmp_path / "cp1252.csv"
        csv_path.write_bytes(csv_text.encode("cp1252"))

        cards = parse_manabox_csv(csv_path)
        assert len(cards) == 1
        assert "Urza" in cards[0].name


class TestNonStandardHeadersAndAliases:
    def test_alternative_header_names(self):
        """Headers like 'Card Name', 'Expansion', 'Collector #', 'Qty', 'Foil / Etched' are correctly recognized."""
        csv_text = (
            "Card Name,Expansion,Collector #,Qty,Foil / Etched\n"
            "Sol Ring,C21,263,3,Foil\n"
            "Counterspell,MH2,267,1,Etched\n"
        )
        cards = parse_manabox_csv(csv_text)
        assert len(cards) == 2
        assert cards[0].name == "Sol Ring"
        assert cards[0].set_code == "c21"
        assert cards[0].collector_number == "263"
        assert cards[0].quantity == 3
        assert cards[0].is_foil is True

        assert cards[1].name == "Counterspell"
        assert cards[1].set_code == "mh2"
        assert cards[1].collector_number == "267"
        assert cards[1].quantity == 1
        assert cards[1].is_foil is True

    def test_more_header_aliases_and_case_insensitivity(self):
        csv_text = (
            "Card,Set Code,Card #,Quantity Owned,Finish\n"
            "Demonic Tutor,STA,27,2,normal\n"
        )
        cards = parse_manabox_csv(csv_text)
        assert len(cards) == 1
        assert cards[0].name == "Demonic Tutor"
        assert cards[0].set_code == "sta"
        assert cards[0].collector_number == "27"
        assert cards[0].quantity == 2


class TestMalformedAndEmptyRows:
    def test_empty_rows_and_whitespace_rows_are_skipped(self):
        csv_text = (
            "Name,Set code,Collector number,Quantity\n"
            "\n"
            "   ,   ,   ,   \n"
            "Swamp,UNF,238,10\n"
            "\n"
            "      \n"
        )
        cards = parse_manabox_csv(csv_text)
        assert len(cards) == 1
        assert cards[0].name == "Swamp"
        assert cards[0].quantity == 10

    def test_negative_and_zero_quantities_skipped(self):
        csv_text = (
            "Name,Set code,Collector number,Quantity\n"
            "Negative Card,LEA,1,-5\n"
            "Zero Card,LEA,2,0\n"
            "Valid Card,LEA,3,1\n"
        )
        cards = parse_manabox_csv(csv_text)
        assert len(cards) == 1
        assert cards[0].name == "Valid Card"
        assert cards[0].quantity == 1

    def test_missing_name_row_skipped(self):
        csv_text = (
            "Name,Set code,Collector number,Quantity\n"
            ",LEA,1,1\n"
            "   ,LEA,2,1\n"
            "Valid,LEA,3,1\n"
        )
        cards = parse_manabox_csv(csv_text)
        assert len(cards) == 1
        assert cards[0].name == "Valid"


class TestComplexStockAllocationOptionC:
    @pytest.fixture
    def inventory_service(self, tmp_path):
        db_file = tmp_path / "inventory.db"
        service = InventoryService(db_file)
        return service

    def test_multiple_decks_competing_for_limited_physical_stock(self, inventory_service):
        """Option C: Physical cards allocated to registered decks cannot be double-used by competing decks."""
        # 1. Add 2 physical copies of Sol Ring (oracle-1) to inventory
        inventory_service.add_or_update_card(
            InventoryCard(
                oracle_id="oracle-sol-ring",
                name="Sol Ring",
                set_code="c21",
                collector_number="263",
                quantity=2,
                is_foil=False,
                is_proxy=False,
            )
        )

        # 2. Register Deck 1 requesting 1 Sol Ring
        deck1_id = inventory_service.register_deck(
            DeckRecord(name="Deck 1", format="Commander"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="oracle-sol-ring",
                    name="Sol Ring",
                    set_code="c21",
                    collector_number="263",
                    quantity_required=1,
                    is_proxy=False,
                )
            ],
        )

        # 3. Evaluate Deck 2 requesting 2 Sol Rings (competing with Deck 1)
        deck2_cards = [
            DeckCardEntry(
                deck_id=0,
                oracle_id="oracle-sol-ring",
                name="Sol Ring",
                set_code="c21",
                collector_number="263",
                quantity_required=2,
                is_proxy=False,
            )
        ]
        plan2 = inventory_service.calculate_missing_proxies(deck2_cards, allocate_mode="exclusive")
        # 2 total required: 1 owned (since Deck 1 took 1 of 2), 1 missing (proxy required)
        assert plan2.total_required == 2
        assert plan2.total_owned == 1
        assert plan2.total_missing == 1
        assert len(plan2.owned_cards) == 1 and plan2.owned_cards[0].quantity_required == 1
        assert len(plan2.missing_cards) == 1 and plan2.missing_cards[0].quantity_required == 1

        # Register Deck 2 with its 1 physical + 1 proxy
        inventory_service.register_deck(
            DeckRecord(name="Deck 2", format="Commander"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="oracle-sol-ring",
                    name="Sol Ring",
                    set_code="c21",
                    collector_number="263",
                    quantity_required=1,
                    is_proxy=False,
                ),
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="oracle-sol-ring",
                    name="Sol Ring",
                    set_code="c21",
                    collector_number="263",
                    quantity_required=1,
                    is_proxy=True,
                ),
            ],
        )

        # 4. Evaluate Deck 3 requesting 1 Sol Ring (now 0 physical copies left)
        deck3_cards = [
            DeckCardEntry(
                deck_id=0,
                oracle_id="oracle-sol-ring",
                name="Sol Ring",
                set_code="c21",
                collector_number="263",
                quantity_required=1,
                is_proxy=False,
            )
        ]
        plan3 = inventory_service.calculate_missing_proxies(deck3_cards, allocate_mode="exclusive")
        assert plan3.total_owned == 0
        assert plan3.total_missing == 1

        # 5. Editing Deck 1 with current_deck_id=deck1_id releases its own reserved cards during evaluation
        plan_deck1_edit = inventory_service.calculate_missing_proxies(
            [
                DeckCardEntry(
                    deck_id=deck1_id,
                    oracle_id="oracle-sol-ring",
                    name="Sol Ring",
                    set_code="c21",
                    collector_number="263",
                    quantity_required=1,
                    is_proxy=False,
                )
            ],
            allocate_mode="exclusive",
            current_deck_id=deck1_id,
        )
        # Total physical = 2, Deck 2 used 1 physical. So 1 physical remains for Deck 1
        assert plan_deck1_edit.total_owned == 1
        assert plan_deck1_edit.total_missing == 0

    def test_register_deck_with_existing_and_non_existing_id(self, inventory_service):
        # 1. Insert new deck
        deck1 = DeckRecord(name="Explicit ID Deck", format="Modern")
        d1_id = inventory_service.register_deck(deck1, [DeckCardEntry(deck_id=0, oracle_id="oid-bolt", name="Bolt", set_code="m11", collector_number="149", quantity_required=4)])
        assert d1_id > 0

        # 2. Update existing deck with ID d1_id
        deck1_updated = DeckRecord(id=d1_id, name="Updated Explicit ID Deck", format="Legacy")
        d1_updated_id = inventory_service.register_deck(deck1_updated, [DeckCardEntry(deck_id=d1_id, oracle_id="oid-bolt", name="Bolt", set_code="m11", collector_number="149", quantity_required=3)])
        assert d1_updated_id == d1_id

        loaded_deck, loaded_cards = inventory_service.get_deck_by_id(d1_id)
        assert loaded_deck.name == "Updated Explicit ID Deck"
        assert loaded_deck.format == "Legacy"
        assert len(loaded_cards) == 1
        assert loaded_cards[0].quantity_required == 3

    def test_import_deck_cards_to_inventory_edge_cases(self, inventory_service):
        # 1. Non-existent deck
        assert inventory_service.import_deck_cards_to_inventory(12345) == 0

        # 2. Deck with empty cards
        empty_deck = DeckRecord(name="Empty Deck")
        empty_id = inventory_service.register_deck(empty_deck, [])
        assert inventory_service.import_deck_cards_to_inventory(empty_id) == 0

        # 3. Deck with cards: import as physical cards
        deck_with_cards = DeckRecord(name="Deck With Cards")
        cards = [
            DeckCardEntry(deck_id=0, oracle_id="o-1", name="Card 1", set_code="set1", collector_number="1", quantity_required=2),
            DeckCardEntry(deck_id=0, oracle_id="o-2", name="Card 2", set_code="set2", collector_number="2", quantity_required=3),
        ]
        deck_id = inventory_service.register_deck(deck_with_cards, cards)

        # First import: inserts into inventory
        count = inventory_service.import_deck_cards_to_inventory(deck_id, as_proxy=False)
        assert count > 0

        # Second import: updates quantities in inventory
        count2 = inventory_service.import_deck_cards_to_inventory(deck_id, as_proxy=False)
        assert count2 > 0

        inv_cards = inventory_service.get_inventory_cards()
        c1 = next(c for c in inv_cards if c.name == "Card 1")
        assert c1.quantity == 4  # 2 + 2

    def test_stock_allocation_alternative_match_deduction(self, inventory_service):
        # Inventory has 2 copies of Counterspell (EMA edition)
        inventory_service.add_or_update_card(
            InventoryCard(oracle_id="oid-cs", name="Counterspell", set_code="ema", collector_number="43", quantity=2)
        )

        # Deck 1 used 1 copy of Counterspell in ICE edition (alternative/oracle match)
        deck1_cards = [
            DeckCardEntry(deck_id=0, oracle_id="oid-cs", name="Counterspell", set_code="ice", collector_number="57", quantity_required=1, is_proxy=False)
        ]
        inventory_service.register_deck(DeckRecord(name="Deck 1"), deck1_cards)

        # Deck 2 requests 2 copies of Counterspell
        deck2_cards = [
            DeckCardEntry(deck_id=0, oracle_id="oid-cs", name="Counterspell", set_code="ema", collector_number="43", quantity_required=2, is_proxy=False)
        ]

        # In exclusive mode, 1 physical copy was deducted by Deck 1 alternative match, so only 1 exact match remains and 1 is missing
        plan = inventory_service.calculate_missing_proxies(deck2_cards, allocate_mode="exclusive")
        assert plan.total_owned == 1
        assert plan.total_missing == 1

