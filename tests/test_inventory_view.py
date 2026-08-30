"""Tests for InventoryView GUI component, actions, and service integrations."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gui.views.inventory_view import (
    AddCardDialog,
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

        # Sol Ring is owned (stock = 1, status = 🟢 Poseída)
        sol_row = [view.deck_tree.item(r)["values"] for r in rows if "Sol Ring" in view.deck_tree.item(r)["values"][0]][0]
        assert sol_row[3] == 1  # required
        assert sol_row[4] == 1  # stock
        assert "Poseída" in sol_row[5]

        # Mana Vault is missing (stock = 0, status = 🔴 Faltante (Proxy))
        vault_row = [view.deck_tree.item(r)["values"] for r in rows if "Mana Vault" in view.deck_tree.item(r)["values"][0]][0]
        assert vault_row[3] == 1  # required
        assert vault_row[4] == 0  # stock
        assert "Faltante" in vault_row[5]

        # Plan summary text
        assert "Requeridas: 2" in view.lbl_plan_summary.cget("text")
        assert "Poseídas en Stock: 1" in view.lbl_plan_summary.cget("text")
        assert "Faltantes (Proxies a Imprimir): 1" in view.lbl_plan_summary.cget("text")

        # Deck card preview info resolver
        vault_item = [r for r in rows if "Mana Vault" in view.deck_tree.item(r)["values"][0]][0]
        preview_info = view._resolve_deck_card_preview(vault_item)
        assert preview_info is not None
        assert preview_info.name == "Mana Vault"
        assert preview_info.set_code == "uma"

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
