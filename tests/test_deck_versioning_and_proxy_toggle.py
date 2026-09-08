"""Unit and integration tests for deck card versioning, stock fulfillment, and proxy toggling."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.inventory.allocation_engine import StockAllocationEngine
from src.inventory.db import get_db, init_db
from src.inventory.models import (
    DeckCardEntry,
    DeckRecord,
    InventoryCard,
)
from src.inventory.service import InventoryService


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_versioning.db"
    init_db(db_file)
    return db_file


@pytest.fixture
def service(temp_db: Path) -> InventoryService:
    return InventoryService(db_path=temp_db)


class TestDeckCardsMigration:
    """Test automated schema migration and backfill for deck_cards table."""

    def test_migration_adds_columns_and_backfills(self, tmp_path: Path) -> None:
        db_file = tmp_path / "legacy.db"
        # Create legacy table schema without new columns
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            """
            CREATE TABLE decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_url TEXT DEFAULT '',
                format TEXT DEFAULT 'Commander',
                created_at TEXT NOT NULL,
                notes TEXT DEFAULT ''
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE deck_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_id INTEGER NOT NULL REFERENCES decks(id),
                oracle_id TEXT NOT NULL,
                name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                collector_number TEXT NOT NULL,
                quantity_required INTEGER NOT NULL DEFAULT 1,
                is_proxy BOOLEAN NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute("INSERT INTO decks (id, name, created_at) VALUES (1, 'Legacy Deck', '2026-01-01')")
        conn.execute(
            """
            INSERT INTO deck_cards (deck_id, oracle_id, name, set_code, collector_number, quantity_required, is_proxy)
            VALUES
                (1, 'oid-sol', 'Sol Ring', 'c21', '263', 1, 0),
                (1, 'oid-lotus', 'Black Lotus', 'lea', '232', 1, 1)
            """
        )
        conn.commit()
        conn.close()

        # Run init_db which should execute the migration
        init_db(db_file)

        # Verify new columns exist and backfilled correctly
        with get_db(db_file) as conn:
            rows = conn.execute(
                "SELECT name, quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id FROM deck_cards ORDER BY id ASC"
            ).fetchall()

            assert len(rows) == 2

            # Sol Ring (is_proxy=0) -> physical_quantity=1, proxy_quantity=0
            sol = rows[0]
            assert sol["name"] == "Sol Ring"
            assert sol["physical_quantity"] == 1
            assert sol["proxy_quantity"] == 0
            assert sol["fulfilled_card_id"] is None

            # Black Lotus (is_proxy=1) -> physical_quantity=0, proxy_quantity=1
            lotus = rows[1]
            assert lotus["name"] == "Black Lotus"
            assert lotus["physical_quantity"] == 0
            assert lotus["proxy_quantity"] == 1
            assert lotus["fulfilled_card_id"] is None


class TestDeckCardVersioningAndAllocation:
    """Test multi-version discovery, selection, and proxy split calculations."""

    def test_available_versions_contains_exact_and_alt_prints(self, service: InventoryService) -> None:
        # Add 2 different prints of Counterspell to inventory
        c1 = InventoryCard(
            oracle_id="oid-counterspell",
            name="Counterspell",
            set_code="dmr",
            collector_number="45",
            quantity=1,
            is_foil=True,
        )
        c2 = InventoryCard(
            oracle_id="oid-counterspell",
            name="Counterspell",
            set_code="ice",
            collector_number="82",
            quantity=2,
            is_foil=False,
        )
        id1 = service.add_or_update_card(c1)
        id2 = service.add_or_update_card(c2)

        # Deck requests Counterspell from Modern Horizons 2 (mh2 #267)
        deck_id = service.register_deck(
            DeckRecord(name="Control Deck"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="oid-counterspell",
                    name="Counterspell",
                    set_code="mh2",
                    collector_number="267",
                    quantity_required=2,
                )
            ],
        )

        _, deck_cards = service.get_deck_by_id(deck_id)
        details, exact, alt, missing = service.analyze_deck_card_matches(deck_cards, current_deck_id=deck_id)

        assert len(details) == 1
        detail = details[0]
        assert detail.name == "Counterspell"
        assert detail.exact_stock == 0
        assert detail.alt_stock == 3  # 1 from DMR + 2 from ICE
        assert len(detail.available_versions) == 2

        # Check version details
        v_dmr = next(v for v in detail.available_versions if v["set_code"] == "DMR")
        assert v_dmr["card_id"] == id1
        assert v_dmr["collector_number"] == "45"
        assert v_dmr["is_foil"] is True
        assert v_dmr["quantity_available"] == 1
        assert v_dmr["is_exact"] is False

        v_ice = next(v for v in detail.available_versions if v["set_code"] == "ICE")
        assert v_ice["card_id"] == id2
        assert v_ice["collector_number"] == "82"
        assert v_ice["is_foil"] is False
        assert v_ice["quantity_available"] == 2

    def test_fulfill_with_specific_version(self, service: InventoryService) -> None:
        c1 = InventoryCard(
            oracle_id="oid-counterspell",
            name="Counterspell",
            set_code="dmr",
            collector_number="45",
            quantity=1,
        )
        c2 = InventoryCard(
            oracle_id="oid-counterspell",
            name="Counterspell",
            set_code="ice",
            collector_number="82",
            quantity=3,
        )
        id_dmr = service.add_or_update_card(c1)
        id_ice = service.add_or_update_card(c2)

        deck_id = service.register_deck(
            DeckRecord(name="Control Deck"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="oid-counterspell",
                    name="Counterspell",
                    set_code="mh2",
                    collector_number="267",
                    quantity_required=2,
                    fulfilled_card_id=id_dmr,
                )
            ],
        )

        _, deck_cards = service.get_deck_by_id(deck_id)
        dc = deck_cards[0]
        assert dc.fulfilled_card_id == id_dmr

        # With fulfilled_card_id = id_dmr (which only has 1 copy), physical = 1, proxy = 1
        details, exact, alt, missing = service.analyze_deck_card_matches(deck_cards, current_deck_id=deck_id)
        assert details[0].physical_quantity == 1
        assert details[0].proxy_quantity == 1
        assert details[0].status == "partial"

    def test_update_deck_card_allocation_and_force_proxy(self, service: InventoryService) -> None:
        # Add exact match Sol Ring
        sol_id = service.add_or_update_card(
            InventoryCard(
                oracle_id="oid-sol",
                name="Sol Ring",
                set_code="c21",
                collector_number="263",
                quantity=1,
            )
        )

        deck_id = service.register_deck(
            DeckRecord(name="Sol Deck"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="oid-sol",
                    name="Sol Ring",
                    set_code="c21",
                    collector_number="263",
                    quantity_required=1,
                )
            ],
        )

        _, deck_cards = service.get_deck_by_id(deck_id)
        dc_id = deck_cards[0].id
        assert dc_id is not None

        # User forces proxy on Sol Ring
        updated = service.update_deck_card_allocation(
            deck_card_id=dc_id,
            physical_quantity=0,
            proxy_quantity=1,
            fulfilled_card_id=None,
        )
        assert updated is True

        # Re-fetch deck cards
        _, updated_cards = service.get_deck_by_id(deck_id)
        assert updated_cards[0].is_proxy is True
        assert updated_cards[0].physical_quantity == 0
        assert updated_cards[0].proxy_quantity == 1

        # Missing proxy plan should now require printing Sol Ring
        plan = service.calculate_missing_proxies(updated_cards, current_deck_id=deck_id)
        assert plan.total_missing == 1
        assert len(plan.missing_cards) == 1
        assert plan.missing_cards[0].name == "Sol Ring"
        assert plan.missing_cards[0].set_code == "c21"
        assert plan.missing_cards[0].collector_number == "263"

        # Physical stock should remain completely free for inventory usage
        cards_with_usage = service.get_inventory_cards_with_usage()
        sol_card, in_decks, avail = cards_with_usage[0]
        assert sol_card.id == sol_id
        assert in_decks == 0
        assert avail == 1


class TestDeckViewInteractiveCellEditing:
    """Test GUI interactions for version dropdown, spinbox, and proxy toggle."""

    @pytest.fixture
    def tk_root(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        yield root
        try:
            root.destroy()
        except Exception:
            pass

    @pytest.fixture
    def clean_root(self, tk_root):
        yield tk_root
        for child in tk_root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

    def test_gui_click_proxy_checkbox_toggle(self, clean_root, service: InventoryService) -> None:
        from src.engine.task_manager import TaskManager
        from gui.views.inventory_view import InventoryView
        import tkinter as tk

        tm = TaskManager()

        # Add physical card
        sol_id = service.add_or_update_card(
            InventoryCard(oracle_id="oid-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=1)
        )
        deck_id = service.register_deck(
            DeckRecord(name="Commander Deck"),
            [
                DeckCardEntry(deck_id=0, oracle_id="oid-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1)
            ],
        )

        view = InventoryView(clean_root, inventory_service=service, task_manager=tm)
        view.pack()
        view._select_deck_by_id(deck_id)

        rows = view.deck_tree.get_children()
        assert len(rows) == 1
        item_id = rows[0]

        row_vals = view.deck_tree.item(item_id)["values"]
        assert row_vals[5] == 1  # physical = 1
        assert row_vals[6] == 0  # proxy = 0
        assert row_vals[8] == "[ ]"  # checkbox unchecked

        # Simulate click on column #9 (Proxy? checkbox)
        event = tk.Event()
        event.x = 10
        event.y = 10
        # Mock identify to target row and column #9
        view.deck_tree.identify = lambda what, x, y: "cell" if what == "region" else (item_id if what == "row" else "#9")
        view._on_deck_tree_click(event)

        # Re-check row after click: should be forced proxy
        new_rows = view.deck_tree.get_children()
        assert len(new_rows) == 1
        updated_item_id = new_rows[0]
        updated_vals = view.deck_tree.item(updated_item_id)["values"]
        assert updated_vals[5] == 0  # physical = 0
        assert updated_vals[6] == 1  # proxy = 1
        assert updated_vals[8] == "[x]"  # checkbox checked

        # Verify persisted in database
        _, db_cards = service.get_deck_by_id(deck_id)
        assert db_cards[0].physical_quantity == 0
        assert db_cards[0].proxy_quantity == 1
        assert db_cards[0].is_proxy is True

        # Click again to toggle back
        view.deck_tree.identify = lambda what, x, y: "cell" if what == "region" else (updated_item_id if what == "row" else "#9")
        view._on_deck_tree_click(event)
        restored_rows = view.deck_tree.get_children()
        restored_vals = view.deck_tree.item(restored_rows[0])["values"]
        assert restored_vals[5] == 1
        assert restored_vals[6] == 0
        assert restored_vals[8] == "[ ]"

        view.destroy()
        tm.shutdown()

    def test_gui_click_assigned_version_dropdown(self, clean_root, service: InventoryService) -> None:
        from src.engine.task_manager import TaskManager
        from gui.views.inventory_view import InventoryView
        import tkinter as tk

        tm = TaskManager()

        # Add 2 prints
        id1 = service.add_or_update_card(
            InventoryCard(oracle_id="oid-bolt", name="Lightning Bolt", set_code="m11", collector_number="149", quantity=2)
        )
        id2 = service.add_or_update_card(
            InventoryCard(oracle_id="oid-bolt", name="Lightning Bolt", set_code="clb", collector_number="123", quantity=1)
        )

        deck_id = service.register_deck(
            DeckRecord(name="Burn Deck"),
            [
                DeckCardEntry(deck_id=0, oracle_id="oid-bolt", name="Lightning Bolt", set_code="2x2", collector_number="117", quantity_required=2)
            ],
        )

        view = InventoryView(clean_root, inventory_service=service, task_manager=tm)
        view.pack()
        view._select_deck_by_id(deck_id)

        rows = view.deck_tree.get_children()
        item_id = rows[0]

        # Mock bbox for assigned_version cell
        view.deck_tree.bbox = lambda item, col: (100, 20, 200, 24)
        view.deck_tree.identify = lambda what, x, y: "cell" if what == "region" else (item_id if what == "row" else "#5")

        event = tk.Event()
        event.x = 110
        event.y = 25
        view._on_deck_tree_click(event)

        # Verify active editor is a Combobox
        cb = view._active_cell_editor
        assert cb is not None
        values = cb.cget("values")
        assert len(values) >= 3  # Auto, M11, CLB, Ninguna
        assert any("M11 #149" in v for v in values)

        # Select the M11 version
        m11_val = next(v for v in values if "M11 #149" in v)
        cb.set(m11_val)
        cb.event_generate("<<ComboboxSelected>>")

        # Verify editor was destroyed and deck was re-selected with fulfilled_card_id = id1
        assert view._active_cell_editor is None
        _, db_cards = service.get_deck_by_id(deck_id)
        assert db_cards[0].fulfilled_card_id == id1
        assert db_cards[0].physical_quantity == 2

        view.destroy()
        tm.shutdown()

    def test_gui_click_physical_qty_spinbox(self, clean_root, service: InventoryService) -> None:
        from src.engine.task_manager import TaskManager
        from gui.views.inventory_view import InventoryView
        import tkinter as tk

        tm = TaskManager()

        service.add_or_update_card(
            InventoryCard(oracle_id="oid-bolt", name="Lightning Bolt", set_code="m11", collector_number="149", quantity=4)
        )
        deck_id = service.register_deck(
            DeckRecord(name="Burn Deck"),
            [
                DeckCardEntry(deck_id=0, oracle_id="oid-bolt", name="Lightning Bolt", set_code="m11", collector_number="149", quantity_required=4)
            ],
        )

        view = InventoryView(clean_root, inventory_service=service, task_manager=tm)
        view.pack()
        view._select_deck_by_id(deck_id)

        rows = view.deck_tree.get_children()
        item_id = rows[0]

        # Mock bbox for physical_qty cell (#6)
        view.deck_tree.bbox = lambda item, col: (250, 20, 50, 24)
        view.deck_tree.identify = lambda what, x, y: "cell" if what == "region" else (item_id if what == "row" else "#6")

        event = tk.Event()
        event.x = 260
        event.y = 25
        view._on_deck_tree_click(event)

        # Verify active editor is a Spinbox
        sp = view._active_cell_editor
        assert sp is not None
        assert int(sp.get()) == 4

        # Edit to 2
        sp.delete(0, tk.END)
        sp.insert(0, "2")
        sp.event_generate("<FocusOut>")
        clean_root.update()

        # Verify editor was destroyed and split is physical=2, proxy=2
        assert view._active_cell_editor is None
        _, db_cards = service.get_deck_by_id(deck_id)
        assert db_cards[0].physical_quantity == 2
        assert db_cards[0].proxy_quantity == 2

        view.destroy()
        tm.shutdown()

