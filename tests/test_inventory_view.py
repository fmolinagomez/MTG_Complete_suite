"""Tests for InventoryView GUI component, actions, and service integrations."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gui.views.inventory_view import (
    AddCardDialog,
    BatchAssignDeckDialog,
    BatchEditCardsDialog,
    CardDecksUsageDialog,
    DeckImportOptionDialog,
    InventoryView,
    ManaBoxImportDialog,
)
from src.deck_importer import DeckCard, FetchedDeck
from src.engine.task_manager import TaskManager
from src.inventory.models import DeckCardEntry, DeckRecord, InventoryCard
from src.inventory.service import InventoryService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tk_root():
    """Module-level headless Tkinter root."""
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        root.destroy()
    except tk.TclError:
        pytest.skip("No display available — Tkinter tests skipped")


@pytest.fixture
def clean_root(tk_root):
    """Yield tk_root and clean up all child widgets after each test."""
    yield tk_root
    for child in tk_root.winfo_children():
        try:
            child.destroy()
        except Exception:
            pass


@pytest.fixture
def inv_service(tmp_path: Path) -> InventoryService:
    db_path = tmp_path / "test_inventory_view.db"
    return InventoryService(db_path=db_path)


@pytest.fixture
def task_manager() -> TaskManager:
    tm = TaskManager(max_workers=2)
    yield tm
    tm.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Test Dialogs
# ---------------------------------------------------------------------------


class TestInventoryDialogs:
    def test_manabox_import_dialog_merge_selection(self, clean_root):
        dlg = ManaBoxImportDialog(clean_root)
        dlg._mode_var.set("merge")
        dlg._on_accept()
        assert dlg.result == "merge"

    def test_manabox_import_dialog_replace_selection(self, clean_root):
        dlg = ManaBoxImportDialog(clean_root)
        dlg._mode_var.set("replace")
        dlg._on_accept()
        assert dlg.result == "replace"

    def test_manabox_import_dialog_cancel(self, clean_root):
        dlg = ManaBoxImportDialog(clean_root)
        dlg._on_cancel()
        assert dlg.result is None

    def test_add_card_dialog_accept(self, clean_root):
        dlg = AddCardDialog(clean_root)
        dlg._name_var.set("Sol Ring")
        dlg._set_var.set("C21")
        dlg._num_var.set("263")
        dlg._qty_var.set(2)
        dlg._foil_var.set(True)
        dlg._proxy_var.set(False)
        dlg._cond_var.set("NM")
        dlg._lang_var.set("en")

        dlg._on_accept()

        card = dlg.result
        assert card is not None
        assert card.name == "Sol Ring"
        assert card.set_code == "c21"
        assert card.collector_number == "263"
        assert card.quantity == 2
        assert card.is_foil is True
        assert card.is_proxy is False
        assert card.condition == "NM"
        assert card.language == "en"

    def test_add_card_dialog_empty_name_shows_warning(self, clean_root):
        dlg = AddCardDialog(clean_root)
        dlg._name_var.set("")
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            dlg._on_accept()
            mock_warn.assert_called_once()
            assert dlg.result is None

    def test_deck_import_option_dialog(self, clean_root):
        dlg1 = DeckImportOptionDialog(clean_root)
        dlg1._choice_var.set("physical")
        dlg1._on_accept()
        assert dlg1.result is False  # not a proxy -> physical

        dlg2 = DeckImportOptionDialog(clean_root)
        dlg2._choice_var.set("proxy")
        dlg2._on_accept()
        assert dlg2.result is True  # is proxy

        dlg3 = DeckImportOptionDialog(clean_root)
        dlg3._on_cancel()
        assert dlg3.result is None

    def test_card_decks_usage_dialog_with_usages(self, clean_root, inv_service):
        card_id = inv_service.add_or_update_card(
            InventoryCard(oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity=2)
        )
        card = inv_service.get_card_by_id(card_id)
        assert card is not None

        inv_service.register_deck(
            DeckRecord(name="EDH Artifacts", format="Commander"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="orc-1",
                    name="Sol Ring",
                    set_code="c21",
                    collector_number="263",
                    quantity_required=1,
                    is_proxy=False,
                ),
            ],
        )

        dlg = CardDecksUsageDialog(clean_root, card, inv_service, in_decks=1, available=1)
        assert dlg.lbl_empty is None
        assert dlg.usage_tree is not None

        children = dlg.usage_tree.get_children()
        assert len(children) == 1
        vals = dlg.usage_tree.item(children[0])["values"]
        assert vals[0] == "EDH Artifacts"
        assert vals[1] == "Commander"
        assert vals[2] == 1
        assert "C21 #263" in vals[3]
        assert vals[4] == "Física"

        dlg.destroy()

    def test_card_decks_usage_dialog_empty_usages(self, clean_root, inv_service):
        card = InventoryCard(oracle_id="orc-unused", name="Unused Card", set_code="mh2", collector_number="10", quantity=1)
        dlg = CardDecksUsageDialog(clean_root, card, inv_service)
        assert dlg.lbl_empty is not None
        assert "Esta carta no está asignada a ningún mazo" in dlg.lbl_empty.cget("text")
        assert dlg.usage_tree is None

        dlg.destroy()

    def test_batch_edit_cards_dialog(self, clean_root):
        dlg = BatchEditCardsDialog(clean_root, count=3)
        assert dlg.count == 3

        # Test cancel
        dlg._on_cancel()
        assert dlg.result is None

        # Reopen and test no changes warning
        dlg2 = BatchEditCardsDialog(clean_root, count=3)
        with patch("tkinter.messagebox.showinfo") as mock_info:
            dlg2._on_accept()
            mock_info.assert_called_once()
            assert dlg2.result is None

        # Set valid changes
        dlg2._cond_var.set("LP")
        dlg2._lang_var.set("es")
        dlg2._foil_var.set("Solo Foil")
        dlg2._proxy_var.set("Solo Proxies")
        dlg2._qty_mode_var.set("Sumar copias (+)")
        dlg2._qty_val_var.set(2)
        dlg2._on_accept()

        assert dlg2.result is not None
        assert dlg2.result["condition"] == "LP"
        assert dlg2.result["language"] == "es"
        assert dlg2.result["is_foil"] is True
        assert dlg2.result["is_proxy"] is True
        assert dlg2.result["quantity_delta"] == 2
        assert dlg2.result["set_quantity"] is None

    def test_batch_assign_deck_dialog(self, clean_root):
        cards = [
            InventoryCard(oracle_id="1", name="Sol Ring", set_code="c21", collector_number="263", quantity=2),
            InventoryCard(oracle_id="2", name="Command Tower", set_code="cmr", collector_number="350", quantity=1),
        ]
        decks = [DeckRecord(id=10, name="EDH Deck", format="Commander")]

        # Case 1: Existing deck
        dlg = BatchAssignDeckDialog(clean_root, cards=cards, decks=decks)
        dlg._deck_choice_var.set("EDH Deck (Commander)")
        dlg._copies_mode_var.set("one")
        dlg._on_accept()

        assert dlg.result is not None
        assert dlg.result["deck_id"] == 10
        assert dlg.result["copies_mode"] == "one"

        # Case 2: New deck creation
        dlg2 = BatchAssignDeckDialog(clean_root, cards=cards, decks=decks)
        dlg2._deck_choice_var.set("➕ Crear nuevo mazo...")
        dlg2._on_deck_selection_changed()
        dlg2._new_name_var.set("Modern Elves")
        dlg2._new_format_var.set("Modern")
        dlg2._copies_mode_var.set("available")
        dlg2._on_accept()

        assert dlg2.result is not None
        assert dlg2.result["deck_id"] is None
        assert dlg2.result["new_name"] == "Modern Elves"
        assert dlg2.result["new_format"] == "Modern"
        assert dlg2.result["copies_mode"] == "available"



# ---------------------------------------------------------------------------
# Test InventoryView
# ---------------------------------------------------------------------------


class TestInventoryView:
    def test_instantiation_and_layout(self, clean_root, inv_service, task_manager):
        nav_callback = MagicMock()
        view = InventoryView(
            clean_root,
            inventory_service=inv_service,
            task_manager=task_manager,
            on_navigate=nav_callback,
        )
        view.pack(fill=tk.BOTH, expand=True)

        assert view.notebook is not None
        assert len(view.notebook.tabs()) == 2
        assert view.inv_tree is not None
        assert view.deck_tree is not None
        assert view._preview_hover is not None

        view.destroy()

    def test_collection_refresh_and_filters(self, clean_root, inv_service, task_manager):
        # Seed test cards
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="1", name="Sol Ring", set_code="c21", collector_number="263", quantity=2, is_foil=False, is_proxy=False)
        )
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="2", name="Mana Crypt", set_code="2xm", collector_number="270", quantity=1, is_foil=True, is_proxy=True)
        )
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="3", name="Black Lotus", set_code="lea", collector_number="232", quantity=1, is_foil=True, is_proxy=False)
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        # Initial view: All 3 cards present
        children = view.inv_tree.get_children()
        assert len(children) == 3

        # Filter: Solo Reales
        view._filter_type_var.set("Solo Reales")
        view._filter_collection_table()
        assert len(view.inv_tree.get_children()) == 2

        # Filter: Solo Proxies
        view._filter_type_var.set("Solo Proxies")
        view._filter_collection_table()
        assert len(view.inv_tree.get_children()) == 1

        # Filter: Solo Foils
        view._filter_type_var.set("Solo Foils")
        view._filter_collection_table()
        assert len(view.inv_tree.get_children()) == 2

        # Search Query
        view._filter_type_var.set("Todas")
        view._search_var.set("Lotus")
        view._filter_collection_table()
        assert len(view.inv_tree.get_children()) == 1

        # Verify preview resolver for selected row
        item_id = view.inv_tree.get_children()[0]
        preview_info = view._resolve_inv_card_preview(item_id)
        assert preview_info is not None
        assert preview_info.name == "Black Lotus"
        assert preview_info.set_code == "lea"
        assert preview_info.collector_number == "232"

        view.destroy()

    def test_delete_card_action(self, clean_root, inv_service, task_manager):
        card_id = inv_service.add_or_update_card(
            InventoryCard(oracle_id="", name="Mox Diamond", set_code="tpr", collector_number="228", quantity=1)
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        children = view.inv_tree.get_children()
        assert len(children) == 1
        view.inv_tree.selection_set(children[0])

        with patch("tkinter.messagebox.askyesno", return_value=True):
            view._on_delete_card_click()

        # Database and view should be updated
        assert inv_service.get_card_by_id(card_id) is None
        assert len(view.inv_tree.get_children()) == 0

        view.destroy()

    def test_add_card_action(self, clean_root, inv_service, task_manager):
        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        new_card = InventoryCard(oracle_id="", name="Birds of Paradise", set_code="rav", collector_number="153", quantity=4)

        with patch.object(view, "wait_window"):
            with patch("gui.views.inventory_view.AddCardDialog") as mock_dlg_cls:
                dlg_inst = MagicMock()
                dlg_inst.result = new_card
                mock_dlg_cls.return_value = dlg_inst
                view._on_add_card_click()

        cards = inv_service.get_inventory_cards(query="Birds")
        assert len(cards) == 1
        assert cards[0].quantity == 4
        assert len(view.inv_tree.get_children()) == 1

        view.destroy()

    def test_double_click_and_view_decks_opens_usage_dialog(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="orc-1", name="Sol Ring", set_code="c21", collector_number="263", quantity=1)
        )
        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        children = view.inv_tree.get_children()
        assert len(children) == 1
        view.inv_tree.selection_set(children[0])

        with patch.object(view, "wait_window") as mock_wait:
            view._on_card_double_click()
            mock_wait.assert_called_once()
            dlg = mock_wait.call_args[0][0]
            assert isinstance(dlg, CardDecksUsageDialog)
            assert dlg.card.name == "Sol Ring"
            dlg.destroy()

        with patch.object(view, "wait_window") as mock_wait2:
            view._on_view_card_decks_click()
            mock_wait2.assert_called_once()
            dlg2 = mock_wait2.call_args[0][0]
            assert isinstance(dlg2, CardDecksUsageDialog)
            dlg2.destroy()

        view.destroy()

    def test_advanced_filters_toggle_and_reset(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="1", name="Sol Ring", set_code="c21", collector_number="263", quantity=2)
        )
        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        assert not view._adv_filters_visible
        assert "🔽" in view.btn_toggle_filters.cget("text")

        # Toggle open
        view._toggle_advanced_filters()
        assert view._adv_filters_visible
        assert "🔼" in view.btn_toggle_filters.cget("text")

        # Toggle closed
        view._toggle_advanced_filters()
        assert not view._adv_filters_visible
        assert "🔽" in view.btn_toggle_filters.cget("text")

        # Test reset filters
        view._search_var.set("Sol")
        view._filter_type_var.set("Solo Reales")
        view._filter_usage_var.set("Solo Asignadas a Mazos")
        view._filter_set_var.set("C21")
        view._filter_rarity_var.set("Rara")

        view._on_reset_filters_click()

        assert view._search_var.get() == ""
        assert view._filter_type_var.get() == "Todas"
        assert view._filter_usage_var.get() == "Todas"
        assert view._filter_set_var.get() == "Todas las ediciones"
        assert view._filter_rarity_var.get() == "Todas"

        view.destroy()

    def test_advanced_filters_by_usage(self, clean_root, inv_service, task_manager):
        # Card 1 assigned to a deck
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="orc-assigned", name="Arcane Signet", set_code="eld", collector_number="331", quantity=1)
        )
        inv_service.register_deck(
            DeckRecord(name="Test Deck"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="orc-assigned",
                    name="Arcane Signet",
                    set_code="eld",
                    collector_number="331",
                    quantity_required=1,
                )
            ],
        )

        # Card 2 free
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="orc-free", name="Free Spell", set_code="eld", collector_number="332", quantity=2)
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        # All
        view._filter_usage_var.set("Todas")
        view._filter_collection_table()
        assert len(view.inv_tree.get_children()) == 2

        # Solo Disponibles
        view._filter_usage_var.set("Solo Disponibles (libres)")
        view._filter_collection_table()
        rows = view.inv_tree.get_children()
        assert len(rows) == 1
        assert view.inv_tree.item(rows[0])["values"][0] == "Free Spell"

        # Solo Asignadas
        view._filter_usage_var.set("Solo Asignadas a Mazos")
        view._filter_collection_table()
        rows = view.inv_tree.get_children()
        assert len(rows) == 1
        assert view.inv_tree.item(rows[0])["values"][0] == "Arcane Signet"

        view.destroy()

    def test_advanced_filters_by_set_and_rarity(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="1", name="Black Lotus", set_code="lea", collector_number="232", quantity=1, metadata_json='{"rarity": "rare"}')
        )
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="2", name="Mox Diamond", set_code="tpr", collector_number="228", quantity=1, metadata_json='{"rarity": "mythic"}')
        )
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="3", name="Lightning Bolt", set_code="lea", collector_number="161", quantity=4, metadata_json='{"rarity": "common"}')
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        # Filter by set LEA
        view._filter_set_var.set("lea")
        view._filter_collection_table()
        assert len(view.inv_tree.get_children()) == 2

        # Filter by rarity mythic
        view._filter_set_var.set("Todas las ediciones")
        view._filter_rarity_var.set("Mítica")
        view._filter_collection_table()
        rows = view.inv_tree.get_children()
        assert len(rows) == 1
        assert view.inv_tree.item(rows[0])["values"][0] == "Mox Diamond"

        view.destroy()

    def test_column_sorting_numeric_and_text(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="1", name="Zendikar Resurgent", set_code="ogw", collector_number="147", quantity=10)
        )
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="2", name="Abrupt Decay", set_code="rtr", collector_number="142", quantity=2)
        )
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="3", name="Counterspell", set_code="ema", collector_number="43", quantity=5)
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        # Sort by name ASC
        view._sort_collection_by_column("name")
        rows = [view.inv_tree.item(r)["values"][0] for r in view.inv_tree.get_children()]
        assert rows == ["Abrupt Decay", "Counterspell", "Zendikar Resurgent"]
        assert "▲" in view.inv_tree.heading("name")["text"]

        # Sort by name DESC
        view._sort_collection_by_column("name")
        rows_desc = [view.inv_tree.item(r)["values"][0] for r in view.inv_tree.get_children()]
        assert rows_desc == ["Zendikar Resurgent", "Counterspell", "Abrupt Decay"]
        assert "▼" in view.inv_tree.heading("name")["text"]

        # Sort by quantity ASC (numeric)
        view._sort_collection_by_column("quantity")
        qtys = [view.inv_tree.item(r)["values"][3] for r in view.inv_tree.get_children()]
        assert qtys == [2, 5, 10]
        assert "▲" in view.inv_tree.heading("quantity")["text"]

        view.destroy()

    def test_manabox_csv_import_task_dispatch_and_callback(self, clean_root, inv_service, task_manager, tmp_path):
        csv_file = tmp_path / "manabox.csv"
        csv_file.write_text("dummy csv")

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        with patch("tkinter.filedialog.askopenfilename", return_value=str(csv_file)):
            with patch.object(view, "wait_window"):
                with patch("gui.views.inventory_view.ManaBoxImportDialog") as mock_dlg_cls:
                    dlg_inst = MagicMock()
                    dlg_inst.result = "merge"
                    mock_dlg_cls.return_value = dlg_inst

                    with patch.object(inv_service, "import_manabox_csv", return_value=42):
                        with patch("tkinter.messagebox.showinfo") as mock_info:
                            view._on_import_csv_click()

                            # Let background task complete and poll events
                            task_manager.get_all_jobs()[0].future.result(timeout=3)
                            view._poll_background_tasks()

                            mock_info.assert_called_once()
                            assert "42" in mock_info.call_args[0][1]

        view.destroy()

    def test_deck_fetch_and_breakdown_display(self, clean_root, inv_service, task_manager):
        # 1. Add physical stock: 1 Sol Ring
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=1, is_proxy=False)
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        # Mock deck fetched from Moxfield
        mock_deck = FetchedDeck(
            name="Test Commander",
            format="Commander",
            cards=[
                DeckCard(name="Sol Ring", set_code="c21", collector_number="263", quantity=1, zone="main"),
                DeckCard(name="Mana Vault", set_code="uma", collector_number="229", quantity=1, zone="main"),
            ],
        )

        view._deck_url_var.set("https://moxfield.com/decks/test1234")

        with patch("gui.views.inventory_view.fetch_deck", return_value=mock_deck):
            with patch("tkinter.messagebox.showinfo") as mock_info:
                view._on_load_deck_click()

                # Process background job
                task_manager.get_all_jobs()[0].future.result(timeout=3)
                view._poll_background_tasks()

                mock_info.assert_called_once()

        # Check registered decks
        decks = inv_service.get_all_decks()
        assert len(decks) == 1
        assert decks[0].name == "Test Commander"

        # Check Deck Details breakdown table
        rows = view.deck_tree.get_children()
        assert len(rows) == 2

        # Sol Ring is owned (stock = 1, status = 🟢 Exacta)
        sol_row = [view.deck_tree.item(r)["values"] for r in rows if "Sol Ring" in view.deck_tree.item(r)["values"][0]][0]
        assert sol_row[3] == 1  # required
        assert sol_row[4] == 1  # exact stock
        assert sol_row[5] == 0  # alt stock
        assert "Exacta" in sol_row[6]

        # Mana Vault is missing (stock = 0, status = 🔴 Faltante (Proxy))
        vault_row = [view.deck_tree.item(r)["values"] for r in rows if "Mana Vault" in view.deck_tree.item(r)["values"][0]][0]
        assert vault_row[3] == 1  # required
        assert vault_row[4] == 0  # exact stock
        assert vault_row[5] == 0  # alt stock
        assert "Faltante" in vault_row[6]

        # Plan summary text
        assert "Requeridas: 2" in view.lbl_plan_summary.cget("text")
        assert "Stock Exacto: 1" in view.lbl_plan_summary.cget("text")
        assert "Faltantes (Proxies a Imprimir): 1" in view.lbl_plan_summary.cget("text")

        # Deck card preview info resolver
        vault_item = [r for r in rows if "Mana Vault" in view.deck_tree.item(r)["values"][0]][0]
        preview_info = view._resolve_deck_card_preview(vault_item)
        assert preview_info is not None
        assert preview_info.name == "Mana Vault"
        assert preview_info.set_code == "uma"

        view.destroy()

    def test_deck_sync_breakdown_exact_and_alt_stock(self, clean_root, inv_service, task_manager):
        # Stock: 1 Sol Ring (c21 #263), 1 Sol Ring (lea #269), 0 Mana Crypt
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity=1, is_proxy=False)
        )
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="orc-sol", name="Sol Ring", set_code="lea", collector_number="269", quantity=1, is_proxy=False)
        )

        # Register deck requiring 1 exact c21 Sol Ring, 1 commander alt Sol Ring, and 1 Mana Crypt
        deck_id = inv_service.register_deck(
            DeckRecord(name="Sync Test Deck", format="Commander"),
            [
                DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1),
                DeckCardEntry(deck_id=0, oracle_id="orc-sol", name="Sol Ring", set_code="cmd", collector_number="123", quantity_required=1),
                DeckCardEntry(deck_id=0, oracle_id="orc-crypt", name="Mana Crypt", set_code="2xm", collector_number="270", quantity_required=1),
            ],
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()
        view._select_deck_by_id(deck_id)

        rows = view.deck_tree.get_children()
        assert len(rows) == 3

        items = [view.deck_tree.item(r) for r in rows]

        # Exact match
        exact_item = [it for it in items if it["values"][1] == "C21"][0]
        assert exact_item["values"][3] == 1  # required
        assert exact_item["values"][4] == 1  # exact_stock
        assert exact_item["values"][5] == 1  # alt_stock (1 other Sol Ring print in collection)
        assert "Exacta" in exact_item["values"][6]
        assert "owned" in exact_item["tags"]

        # Alternative match
        alt_item = [it for it in items if it["values"][1] == "CMD"][0]
        assert alt_item["values"][3] == 1  # required
        assert alt_item["values"][4] == 0  # exact_stock
        assert alt_item["values"][5] == 1  # alt_stock
        assert "Alternativa" in alt_item["values"][6]
        assert "alt_stock" in alt_item["tags"]

        # Missing
        missing_item = [it for it in items if "Mana Crypt" in it["values"][0]][0]
        assert missing_item["values"][3] == 1  # required
        assert missing_item["values"][4] == 0  # exact_stock
        assert missing_item["values"][5] == 0  # alt_stock
        assert "Faltante" in missing_item["values"][6]
        assert "missing" in missing_item["tags"]

        # Summary text
        summary = view.lbl_plan_summary.cget("text")
        assert "Requeridas: 3" in summary
        assert "Stock Exacto: 1" in summary
        assert "Stock Alternativo: 1" in summary
        assert "Faltantes (Proxies a Imprimir): 1" in summary

        view.destroy()

    def test_import_deck_cards_to_inventory_action(self, clean_root, inv_service, task_manager):
        deck_id = inv_service.register_deck(
            DeckRecord(name="Urza Deck"),
            [
                DeckCardEntry(deck_id=0, oracle_id="", name="Island", set_code="unf", collector_number="236", quantity_required=10),
            ],
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()
        view._select_deck_by_id(deck_id)

        with patch.object(view, "wait_window"):
            with patch("gui.views.inventory_view.DeckImportOptionDialog") as mock_dlg_cls:
                dlg_inst = MagicMock()
                dlg_inst.result = True  # as proxy
                mock_dlg_cls.return_value = dlg_inst

                with patch("tkinter.messagebox.showinfo") as mock_info:
                    view._on_import_deck_to_inventory_click()
                    mock_info.assert_called_once()
                    assert "10" in mock_info.call_args[0][1]

        proxies = inv_service.get_inventory_cards(filter_proxy=True)
        assert len(proxies) == 1
        assert proxies[0].name == "Island"
        assert proxies[0].quantity == 10

        view.destroy()

    def test_generate_missing_proxies_pdf_action(self, clean_root, inv_service, task_manager, tmp_path):
        deck_id = inv_service.register_deck(
            DeckRecord(name="Urza Deck"),
            [
                DeckCardEntry(deck_id=0, oracle_id="", name="Mox Sapphire", set_code="lea", collector_number="265", quantity_required=1),
            ],
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()
        view._select_deck_by_id(deck_id)

        fake_pdf = tmp_path / "Urza_Deck_proxies.pdf"
        fake_pdf.write_text("fake pdf")

        with patch.object(view, "_task_generate_missing_proxies_pdf", return_value=[fake_pdf]) as mock_gen:
            with patch("tkinter.messagebox.showinfo") as mock_info:
                view._on_generate_missing_pdf_click()

                # Process background job
                task_manager.get_all_jobs()[0].future.result(timeout=3)
                view._poll_background_tasks()

                mock_gen.assert_called_once()
                mock_info.assert_called_once()
                assert str(fake_pdf) in mock_info.call_args[0][1]

        view.destroy()

    def test_delete_deck_action(self, clean_root, inv_service, task_manager):
        deck_id = inv_service.register_deck(DeckRecord(name="Delete Me"), [])

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()
        view._select_deck_by_id(deck_id)

        with patch("tkinter.messagebox.askyesno", return_value=True):
            view._on_delete_deck_click()

        assert inv_service.get_deck_by_id(deck_id) is None
        assert len(inv_service.get_all_decks()) == 0

        view.destroy()

    def test_deck_view_include_proxies_toggle(self, clean_root, inv_service, task_manager):
        # Add proxy card to inventory
        inv_service.add_or_update_card(
            InventoryCard(oracle_id="", name="Sol Ring", set_code="c21", collector_number="263", quantity=1, is_proxy=True)
        )
        deck_id = inv_service.register_deck(
            DeckRecord(name="Proxy Test Deck"),
            [
                DeckCardEntry(deck_id=0, oracle_id="", name="Sol Ring", set_code="c21", collector_number="263", quantity_required=1, is_proxy=False),
            ],
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()
        view._select_deck_by_id(deck_id)

        rows = view.deck_tree.get_children()
        sol_row = view.deck_tree.item(rows[0])["values"]
        # With default include_proxies=True:
        assert sol_row[4] == 1  # exact stock = 1
        assert "Exacta" in sol_row[6]

        # Toggle include_proxies to False
        view._deck_include_proxies_var.set(False)
        view._on_deck_options_changed()

        rows_after = view.deck_tree.get_children()
        sol_row_after = view.deck_tree.item(rows_after[0])["values"]
        assert sol_row_after[4] == 0  # exact stock = 0
        assert "Faltante" in sol_row_after[6]

        # Toggle back to True
        view._deck_include_proxies_var.set(True)
        view._on_deck_options_changed()
        rows_back = view.deck_tree.get_children()
        sol_row_back = view.deck_tree.item(rows_back[0])["values"]
        assert sol_row_back[4] == 1
        assert "Exacta" in sol_row_back[6]

        view.destroy()

    def test_inventory_view_batch_selection_and_shortcuts(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(InventoryCard(oracle_id="1", name="Card A", set_code="set", collector_number="1", quantity=2))
        inv_service.add_or_update_card(InventoryCard(oracle_id="2", name="Card B", set_code="set", collector_number="2", quantity=3))
        inv_service.add_or_update_card(InventoryCard(oracle_id="3", name="Card C", set_code="set", collector_number="3", quantity=1))

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        # Treeview selectmode must be extended
        assert str(view.inv_tree.cget("selectmode")) == "extended"

        rows = view.inv_tree.get_children()
        assert len(rows) == 3

        # Initially no selection, batch bar should be hidden
        assert len(view.inv_tree.selection()) == 0
        assert view.batch_bar_frame.winfo_manager() != "pack"

        # Select 1 item
        view.inv_tree.selection_set(rows[0])
        view._update_batch_bar()
        assert view.batch_bar_frame.winfo_manager() == "pack"
        assert "1 carta seleccionada" in view.lbl_batch_count.cget("text")

        # Select 2 items
        view.inv_tree.selection_set(rows[0], rows[1])
        view._update_batch_bar()
        assert view.batch_bar_frame.winfo_manager() == "pack"
        assert "2 cartas seleccionadas (5 copias en total)" in view.lbl_batch_count.cget("text")

        # Shortcut: Select All
        res = view._on_select_all_cards()
        assert res == "break"
        assert len(view.inv_tree.selection()) == 3
        assert "3 cartas seleccionadas (6 copias en total)" in view.lbl_batch_count.cget("text")

        # Shortcut: Deselect All
        res = view._on_deselect_all_cards()
        assert res == "break"
        assert len(view.inv_tree.selection()) == 0
        assert view.batch_bar_frame.winfo_manager() != "pack"

        view.destroy()

    def test_inventory_view_batch_toggle_proxy(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(InventoryCard(oracle_id="1", name="Card 1", set_code="set", collector_number="1", quantity=1, is_proxy=False))
        inv_service.add_or_update_card(InventoryCard(oracle_id="2", name="Card 2", set_code="set", collector_number="2", quantity=1, is_proxy=False))

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        rows = view.inv_tree.get_children()
        view.inv_tree.selection_set(rows[0], rows[1])
        view._update_batch_bar()

        # Toggle all physical to proxy
        view._on_batch_toggle_proxy_click()
        cards = inv_service.get_inventory_cards()
        assert all(c.is_proxy is True for c in cards)

        # Toggle all proxy to physical
        rows = view.inv_tree.get_children()
        view.inv_tree.selection_set(rows[0], rows[1])
        view._on_batch_toggle_proxy_click()
        cards = inv_service.get_inventory_cards()
        assert all(c.is_proxy is False for c in cards)

        view.destroy()

    def test_inventory_view_batch_toggle_foil(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(InventoryCard(oracle_id="1", name="Card 1", set_code="set", collector_number="1", quantity=1, is_foil=False))
        inv_service.add_or_update_card(InventoryCard(oracle_id="2", name="Card 2", set_code="set", collector_number="2", quantity=1, is_foil=False))

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        rows = view.inv_tree.get_children()
        view.inv_tree.selection_set(rows[0], rows[1])
        view._update_batch_bar()

        # Toggle all non-foil to foil
        view._on_batch_toggle_foil_click()
        cards = inv_service.get_inventory_cards()
        assert all(c.is_foil is True for c in cards)

        # Toggle all foil to non-foil
        rows = view.inv_tree.get_children()
        view.inv_tree.selection_set(rows[0], rows[1])
        view._on_batch_toggle_foil_click()
        cards = inv_service.get_inventory_cards()
        assert all(c.is_foil is False for c in cards)

        view.destroy()

    def test_inventory_view_batch_edit_click(self, clean_root, inv_service, task_manager):
        id1 = inv_service.add_or_update_card(InventoryCard(oracle_id="1", name="Card 1", set_code="set", collector_number="1", quantity=1, condition="NM", language="en"))
        id2 = inv_service.add_or_update_card(InventoryCard(oracle_id="2", name="Card 2", set_code="set", collector_number="2", quantity=1, condition="NM", language="en"))

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        rows = view.inv_tree.get_children()
        view.inv_tree.selection_set(rows[0], rows[1])

        mock_result = {
            "is_foil": None,
            "is_proxy": None,
            "condition": "HP",
            "language": "ja",
            "set_quantity": 4,
            "quantity_delta": None,
        }

        with patch.object(view, "wait_window"):
            with patch("gui.views.inventory_view.BatchEditCardsDialog") as MockDialog:
                mock_dlg_instance = MagicMock()
                mock_dlg_instance.result = mock_result
                MockDialog.return_value = mock_dlg_instance

                view._on_batch_edit_click()

        c1 = inv_service.get_card_by_id(id1)
        c2 = inv_service.get_card_by_id(id2)
        assert c1.condition == "HP"
        assert c1.language == "ja"
        assert c1.quantity == 4
        assert c2.condition == "HP"
        assert c2.language == "ja"
        assert c2.quantity == 4

        view.destroy()

    def test_inventory_view_batch_assign_deck_click(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(InventoryCard(oracle_id="1", name="Card 1", set_code="set", collector_number="1", quantity=2))
        inv_service.add_or_update_card(InventoryCard(oracle_id="2", name="Card 2", set_code="set", collector_number="2", quantity=1))

        deck_id = inv_service.register_deck(DeckRecord(name="Commander Deck"), cards=[])

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        rows = view.inv_tree.get_children()
        view.inv_tree.selection_set(rows[0], rows[1])

        mock_result = {
            "deck_id": deck_id,
            "new_name": None,
            "new_format": None,
            "copies_mode": "one",
        }

        with patch.object(view, "wait_window"):
            with patch("gui.views.inventory_view.BatchAssignDeckDialog") as MockDialog, \
                 patch("tkinter.messagebox.showinfo") as mock_info:
                mock_dlg_instance = MagicMock()
                mock_dlg_instance.result = mock_result
                MockDialog.return_value = mock_dlg_instance

                view._on_batch_assign_deck_click()
                mock_info.assert_called_once()

        deck_info = inv_service.get_deck_by_id(deck_id)
        assert deck_info is not None
        _, cards = deck_info
        assert len(cards) == 2
        assert all(c.quantity_required == 1 for c in cards)

        view.destroy()

    def test_inventory_view_batch_delete_with_warning(self, clean_root, inv_service, task_manager):
        inv_service.add_or_update_card(InventoryCard(oracle_id="1", name="Card In Deck", set_code="set", collector_number="1", quantity=1))
        inv_service.add_or_update_card(InventoryCard(oracle_id="2", name="Card Free", set_code="set", collector_number="2", quantity=1))

        # Register deck using Card 1
        inv_service.register_deck(
            DeckRecord(name="Ur-Dragon"),
            [DeckCardEntry(deck_id=0, oracle_id="1", name="Card In Deck", set_code="set", collector_number="1", quantity_required=1)],
        )

        view = InventoryView(clean_root, inventory_service=inv_service, task_manager=task_manager)
        view.pack()

        rows = view.inv_tree.get_children()
        view.inv_tree.selection_set(rows[0], rows[1])

        with patch("tkinter.messagebox.askyesno", return_value=True) as mock_ask:
            view._on_batch_delete_click()
            mock_ask.assert_called_once()
            # Verify message contains warning and deck name
            prompt_text = mock_ask.call_args[0][1]
            assert "ADVERTENCIA" in prompt_text
            assert "Ur-Dragon" in prompt_text

        # Cards should be deleted
        assert len(inv_service.get_inventory_cards()) == 0

        view.destroy()


