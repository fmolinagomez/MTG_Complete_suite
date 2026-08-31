"""Corner-case test suite for GUI views (GeneratorView, ValidatorView, InventoryView).

Covers:
1. GeneratorView:
   - Drag and drop simulation with non-XML file paths.
   - Output PDF file collision with existing read-only file or locked file.
   - Bad URLs in direct game scraper tabs (empty strings, malformed URLs).
2. ValidatorView:
   - Empty deck input / whitespace only -> warning message, no crash.
   - Mixed validity results (all valid, all error, all warning, mixed).
   - Export to CSV when 0 cards or when file dialog is cancelled.
3. InventoryView:
   - Double-clicking card with 0 deck usages vs multiple deck usages.
   - Complex filter combinations (search query + usage filter + set filter + rarity filter simultaneously).
   - Reset filters when filters are already default.
   - Natural sorting on empty table and table with special characters.
"""

from __future__ import annotations

import csv
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import pytest

from gui.views.generator_view import GeneratorView
from gui.views.inventory_view import CardDecksUsageDialog, InventoryView
from gui.views.validator_view import CardValidationResult, ValidatorView
from src.deck_importer import DeckCard
from src.engine.task_manager import TaskManager
from src.inventory.models import DeckCardEntry, DeckRecord, InventoryCard
from src.inventory.service import InventoryService
from tests.conftest import make_rgb_image

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
    """Yield clean Tk root with teardown of child widgets."""
    yield tk_root
    for child in tk_root.winfo_children():
        try:
            child.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. GeneratorView Corner Cases
# ---------------------------------------------------------------------------


class TestGeneratorViewCornerCases:
    def test_drag_and_drop_decoding_and_non_xml_files(self, clean_root, tmp_path):
        """Simulate drag and drop with mixed file types and byte strings."""
        view = GeneratorView(clean_root)
        view.pack()

        # 1. Test _decode_drop with strings and encoded bytes
        raw_files = [
            str(tmp_path / "deck1.xml"),
            str(tmp_path / "deck2.xml").encode("utf-8"),
            str(tmp_path / "image.png").encode("utf-8"),
            str(tmp_path / "doc.txt"),
        ]
        decoded = view._decode_drop(raw_files)
        assert len(decoded) == 4
        assert isinstance(decoded[0], Path)
        assert isinstance(decoded[1], Path)

        # 2. Test dropping mixed files on XML tab -> only .xml accepted
        xml_file = tmp_path / "deck1.xml"
        xml_file.write_text("<order></order>", encoding="utf-8")
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("not an xml", encoding="utf-8")
        png_file = make_rgb_image(tmp_path / "image.png")

        with patch("gui.views.generator_tabs.analyze", return_value=[]), patch("gui.views.generator_tabs.parse", return_value=None):
            view._on_drop_xmls([str(xml_file), str(txt_file), str(png_file)])

        assert len(view.state.xml_paths) == 1
        assert view.state.xml_paths[0] == xml_file

        # 3. Test dropping non-images on backs / fronts tab -> non-images ignored
        view._on_drop_backs([str(xml_file), str(txt_file), str(png_file)])
        assert len(view.state.local_backs) == 1
        assert view.state.local_backs[0] == png_file

        view._on_drop_fronts([str(xml_file), str(txt_file), str(png_file)])
        assert len(view.state.local_fronts) == 1
        assert view.state.local_fronts[0] == png_file

    def test_output_pdf_collision_permission_error_handled_cleanly(self, clean_root, tmp_path):
        """PermissionError when writing locked/read-only output PDF is cleanly reported."""
        view = GeneratorView(clean_root)
        view.pack()

        xml_file = tmp_path / "deck.xml"
        xml_file.write_text("<order></order>", encoding="utf-8")
        view.state.xml_paths.append(xml_file)

        run_dir = tmp_path / "run_out"
        run_dir.mkdir()

        # Simulate PermissionError posted to generator events
        ev = ("error", "PermissionError: [Errno 13] Permission denied: 'output.pdf'", run_dir)
        with patch.object(view, "_show_error_dialog") as mock_err_dlg:
            view._handle(ev)
            assert "Error durante la generación" in view.status_var.get()
            mock_err_dlg.assert_called_once()
            assert "Permission denied" in mock_err_dlg.call_args[0][0]

    def test_direct_game_scraper_tabs_empty_or_whitespace_urls(self, clean_root):
        """Empty or whitespace-only URLs in OP, RB, and Lorcana tabs trigger warning messages."""
        view = GeneratorView(clean_root)
        view.pack()

        with patch("tkinter.messagebox.showwarning") as mock_warn:
            # 1. One Piece empty URL
            view._op_url_var.set("   ")
            view._op_load_deck()
            mock_warn.assert_called_with("MPCFillToPDF", "Introduce una URL de mazo de One Piece.")

            # 2. Riftbound empty URL
            mock_warn.reset_mock()
            view._rb_url_var.set("")
            view._rb_load_deck()
            mock_warn.assert_called_with("MPCFillToPDF", "Introduce una URL de mazo de Riftbound.")

            # 3. Lorcana empty URL
            mock_warn.reset_mock()
            view._lorcana_url_var.set(" \t\n ")
            view._lorcana_load_deck()
            mock_warn.assert_called_with("MPCFillToPDF", "Introduce una URL de mazo de Lorcana.")

    def test_direct_game_scraper_tabs_malformed_urls_handled(self, clean_root):
        """Malformed or unsupported game URLs trigger error events and display error dialogs."""
        view = GeneratorView(clean_root)
        view.pack()

        with patch.object(view, "_show_error_dialog") as mock_err_dlg:
            # One piece scraper error event
            view._handle(("op_deck_error", "Dominio no soportado: example.com"))
            assert view._op_status_var.get() == "Error al cargar el mazo."
            mock_err_dlg.assert_called_with("Dominio no soportado: example.com")

            # Riftbound scraper error event
            mock_err_dlg.reset_mock()
            view._handle(("rb_deck_error", "No se pudo extraer mazo de Riftbound"))
            assert view._rb_status_var.get() == "Error al cargar el mazo."
            mock_err_dlg.assert_called_with("No se pudo extraer mazo de Riftbound")

            # Lorcana scraper error event
            mock_err_dlg.reset_mock()
            view._handle(("lorcana_deck_error", "Error de red 404"))
            assert view._lorcana_status_var.get() == "Error al cargar el mazo."
            mock_err_dlg.assert_called_with("Error de red 404")


# ---------------------------------------------------------------------------
# 2. ValidatorView Corner Cases
# ---------------------------------------------------------------------------


class TestValidatorViewCornerCases:
    def test_empty_deck_url_shows_warning(self, clean_root):
        """Empty or whitespace URL input shows warning dialog without crashing."""
        view = ValidatorView(clean_root)
        view.pack()

        with patch("tkinter.messagebox.showwarning") as mock_warn:
            view.url_var.set("   ")
            view._on_load_clicked()
            mock_warn.assert_called_once_with("Advertencia", "Por favor ingresa una URL válida.")
            assert "disabled" not in view.load_btn.state()

    def test_mixed_validity_results_and_summary_counts(self, clean_root):
        """Validating mixed cards (valid, warning, error) updates table and summary counts correctly."""
        view = ValidatorView(clean_root)
        view.pack()

        c_valid = DeckCard("Lightning Bolt", "lea", "161", 4, "main")
        c_warn = DeckCard("Digital Card", "vma", "10", 1, "main")
        c_err = DeckCard("Nonexistent Card", "xyz", "999", 2, "side")

        res_valid = CardValidationResult(
            card=c_valid,
            status="🟢 Válido",
            details="Todo correcto",
            set_code="lea",
            collector_number="161",
            front_url="https://example.com/bolt.jpg",
            back_url=None,
            type_line="Instant",
            rarity="common",
        )
        res_warn = CardValidationResult(
            card=c_warn,
            status="⚠️ Advertencia",
            details="Carta digital",
            set_code="vma",
            collector_number="10",
            front_url="https://example.com/digital.jpg",
            back_url=None,
            type_line="Creature",
            rarity="rare",
        )
        res_err = CardValidationResult(
            card=c_err,
            status="🔴 Error",
            details="Error en Scryfall: Not found",
            set_code="xyz",
            collector_number="999",
            front_url=None,
            back_url=None,
            type_line="Sorcery",
            rarity="unknown",
        )

        # Inject deck loaded event
        view.event_queue.put(("deck_loaded", ("Test Deck", [c_valid, c_warn, c_err], None)))
        # Inject card validated events
        view.event_queue.put(("card_validated", (res_valid, 0, 3)))
        view.event_queue.put(("card_validated", (res_warn, 1, 3)))
        view.event_queue.put(("card_validated", (res_err, 2, 3)))
        view.event_queue.put(("validation_complete", None))

        view._process_queue()

        assert len(view.current_deck_cards) == 3
        summary_text = view.summary_var.get()
        assert "Total: 3" in summary_text
        assert "Válidas: 1" in summary_text
        assert "Advertencias: 1" in summary_text
        assert "Errores: 1" in summary_text

        # Verify treeview rows
        children = view.tree.get_children()
        assert len(children) == 3
        row0_vals = view.tree.item(children[0], "values")
        assert row0_vals[0] == "Lightning Bolt"
        assert row0_vals[7] == "🟢 Válido"

        row2_vals = view.tree.item(children[2], "values")
        assert row2_vals[0] == "Nonexistent Card"
        assert row2_vals[7] == "🔴 Error"

    def test_export_csv_when_empty_or_cancelled(self, clean_root, tmp_path):
        """Exporting to CSV with 0 cards shows warning; cancelling file dialog returns cleanly."""
        view = ValidatorView(clean_root)
        view.pack()

        # 1. Export with 0 cards
        with patch("tkinter.messagebox.showwarning") as mock_warn:
            view._on_export_csv_click()
            mock_warn.assert_called_once_with(
                "Aviso", "No hay cartas validadas para exportar.", parent=view
            )

        # 2. Add a card and simulate cancelling the file dialog
        card = DeckCard("Sol Ring", "lea", "269", 1, "main")
        res = CardValidationResult(
            card=card,
            status="🟢 Válido",
            details="Todo correcto",
            set_code="lea",
            collector_number="269",
            front_url=None,
            back_url=None,
            type_line="Artifact",
            rarity="uncommon",
        )
        view.current_deck_cards.append(res)

        with patch("tkinter.filedialog.asksaveasfilename", return_value=""):
            # Should return cleanly without creating any file
            view._on_export_csv_click()

        # 3. Export to an actual CSV path
        csv_target = tmp_path / "validation_results.csv"
        with patch("tkinter.filedialog.asksaveasfilename", return_value=str(csv_target)):
            view._on_export_csv_click()

        assert csv_target.exists()
        with open(csv_target, encoding="utf-8") as f:
            reader = list(csv.reader(f))
            assert len(reader) == 2  # Header + 1 row
            assert reader[0] == [
                "Nombre",
                "Cantidad",
                "Zona",
                "Edición",
                "Nº",
                "Tipo",
                "Rareza",
                "Estado",
                "Detalles",
            ]
            assert reader[1][0] == "Sol Ring"
            assert reader[1][3] == "LEA"
            assert reader[1][7] == "🟢 Válido"


# ---------------------------------------------------------------------------
# 3. InventoryView Corner Cases
# ---------------------------------------------------------------------------


class TestInventoryViewCornerCases:
    def test_double_click_card_zero_usages_vs_multiple_usages(self, clean_root, tmp_path):
        """Double clicking card with 0 usages shows empty label; card with multiple usages shows table."""
        db_path = tmp_path / "inventory.db"
        service = InventoryService(db_path)
        tm = TaskManager(max_workers=2)

        try:
            # Card 1: 0 usages
            card1 = InventoryCard(
                oracle_id="oid-mox-jet",
                name="Mox Jet",
                set_code="lea",
                collector_number="262",
                quantity=2,
            )
            c1_id = service.add_or_update_card(card1)
            card1.id = c1_id

            # Card 2: 2 deck usages
            card2 = InventoryCard(
                oracle_id="oid-swamp",
                name="Swamp",
                set_code="lea",
                collector_number="288",
                quantity=10,
            )
            c2_id = service.add_or_update_card(card2)
            card2.id = c2_id

            # Create 2 decks using Swamp
            service.register_deck(
                DeckRecord(name="Mono Black 1", format="Legacy"),
                [
                    DeckCardEntry(
                        deck_id=0,
                        oracle_id="oid-swamp",
                        name="Swamp",
                        set_code="lea",
                        collector_number="288",
                        quantity_required=4,
                    )
                ],
            )
            service.register_deck(
                DeckRecord(name="Mono Black 2", format="Commander"),
                [
                    DeckCardEntry(
                        deck_id=0,
                        oracle_id="oid-swamp",
                        name="Swamp",
                        set_code="lea",
                        collector_number="288",
                        quantity_required=6,
                    )
                ],
            )

            view = InventoryView(clean_root, inventory_service=service, task_manager=tm)
            view.pack()
            view.refresh_data()

            # 1. Dialog for Card 1 (0 usages)
            dlg1 = CardDecksUsageDialog(view, card1, service, in_decks=0, available=2)
            try:
                assert dlg1.lbl_empty is not None
                assert dlg1.usage_tree is None
            finally:
                dlg1.destroy()

            # 2. Dialog for Card 2 (multiple usages)
            dlg2 = CardDecksUsageDialog(view, card2, service, in_decks=10, available=0)
            try:
                assert dlg2.lbl_empty is None
                assert dlg2.usage_tree is not None
                items = dlg2.usage_tree.get_children()
                assert len(items) == 2
            finally:
                dlg2.destroy()

            view.destroy()
        finally:
            tm.shutdown(wait=False)

    def test_complex_filter_combinations(self, clean_root, tmp_path):
        """Search query + usage filter + set filter + rarity filter simultaneously."""
        db_path = tmp_path / "inventory.db"
        service = InventoryService(db_path)
        tm = TaskManager(max_workers=2)

        try:
            # Populate diverse inventory
            c1 = InventoryCard(
                oracle_id="oid-counterspell",
                name="Counterspell",
                set_code="ema",
                collector_number="43",
                quantity=4,
                is_foil=True,
                metadata_json='{"rarity": "uncommon"}',
            )
            c2 = InventoryCard(
                oracle_id="oid-counterspell",
                name="Counterspell",
                set_code="mh2",
                collector_number="267",
                quantity=2,
                is_foil=False,
                metadata_json='{"rarity": "rare"}',
            )
            c3 = InventoryCard(
                oracle_id="oid-fow",
                name="Force of Will",
                set_code="ema",
                collector_number="49",
                quantity=1,
                is_foil=False,
                metadata_json='{"rarity": "mythic"}',
            )
            service.add_or_update_card(c1)
            service.add_or_update_card(c2)
            service.add_or_update_card(c3)

            # Assign c1 to a deck
            service.register_deck(
                DeckRecord(name="Control Deck", format="Legacy"),
                [
                    DeckCardEntry(
                        deck_id=0,
                        oracle_id="oid-counterspell",
                        name="Counterspell",
                        set_code="ema",
                        collector_number="43",
                        quantity_required=4,
                    )
                ],
            )

            view = InventoryView(clean_root, inventory_service=service, task_manager=tm)
            view.pack()
            view.refresh_data()

            # Simultaneous filter: query="Counterspell", set="EMA", usage="Solo Asignadas a Mazos", rarity="Infrecuente"
            view._search_var.set("Counterspell")
            view._filter_set_var.set("ema")
            view._filter_usage_var.set("Solo Asignadas a Mazos")
            view._filter_rarity_var.set("Infrecuente")
            view._filter_collection_table()

            items = view.inv_tree.get_children()
            assert len(items) == 1
            item_vals = view.inv_tree.item(items[0], "values")
            assert item_vals[0] == "Counterspell"
            assert item_vals[1] == "EMA"
            assert int(item_vals[5]) == 4  # in_decks == 4

            # Non-matching filter combo should yield 0 rows
            view._filter_rarity_var.set("Mítica")
            view._filter_collection_table()
            assert len(view.inv_tree.get_children()) == 0

            view.destroy()
        finally:
            tm.shutdown(wait=False)

    def test_reset_filters_when_default_and_when_active(self, clean_root, tmp_path):
        """Reset filters resets all inputs and retains full dataset without crash."""
        db_path = tmp_path / "inventory.db"
        service = InventoryService(db_path)
        tm = TaskManager(max_workers=2)

        try:
            service.add_or_update_card(
                InventoryCard(
                    oracle_id="oid-a", name="Card A", set_code="set1", collector_number="1", quantity=1
                )
            )
            service.add_or_update_card(
                InventoryCard(
                    oracle_id="oid-b", name="Card B", set_code="set2", collector_number="2", quantity=2
                )
            )

            view = InventoryView(clean_root, inventory_service=service, task_manager=tm)
            view.pack()
            view.refresh_data()

            # 1. Reset when already default
            view._on_reset_filters_click()
            assert len(view.inv_tree.get_children()) == 2

            # 2. Modify filters
            view._search_var.set("Nonexistent")
            view._filter_type_var.set("Solo Foils")
            view._filter_collection_table()
            assert len(view.inv_tree.get_children()) == 0

            # 3. Reset
            view._on_reset_filters_click()
            assert view._search_var.get() == ""
            assert view._filter_type_var.get() == "Todas"
            assert len(view.inv_tree.get_children()) == 2

            view.destroy()
        finally:
            tm.shutdown(wait=False)

    def test_natural_sorting_empty_table_and_special_characters(self, clean_root, tmp_path):
        """Natural sorting handles empty inventory table and special character names cleanly."""
        db_path = tmp_path / "inventory.db"
        service = InventoryService(db_path)
        tm = TaskManager(max_workers=2)

        try:
            # 1. Empty table sort
            view = InventoryView(clean_root, inventory_service=service, task_manager=tm)
            view.pack()
            view.refresh_data()

            # Sort all columns on empty table
            for col in view._inv_col_headings:
                view._sort_collection_by_column(col)
            assert len(view.inv_tree.get_children()) == 0

            # 2. Populate table with special characters and alphanumeric collector numbers
            c_accent = InventoryCard(
                oracle_id="oid-1",
                name="Lim-Dûl's Vault",
                set_code="all",
                collector_number="12a",
                quantity=1,
            )
            c_aether = InventoryCard(
                oracle_id="oid-2",
                name="Ætherling",
                set_code="dgm",
                collector_number="11",
                quantity=1,
            )
            c_star = InventoryCard(
                oracle_id="oid-3",
                name="★ Promo Card ★",
                set_code="prm",
                collector_number="★1",
                quantity=1,
            )
            c_numeric = InventoryCard(
                oracle_id="oid-4",
                name="100 Words",
                set_code="tst",
                collector_number="2",
                quantity=1,
            )

            service.add_or_update_card(c_accent)
            service.add_or_update_card(c_aether)
            service.add_or_update_card(c_star)
            service.add_or_update_card(c_numeric)

            view.refresh_data()
            assert len(view.inv_tree.get_children()) == 4

            # Sort by Name (Ascending & Descending)
            view._sort_collection_by_column("name")
            view._sort_collection_by_column("name")

            # Sort by Collector Number
            view._sort_collection_by_column("collector_number")
            items = view.inv_tree.get_children()
            assert len(items) == 4

            # Sort by Quantity, Available, in_decks, Foil, Proxy, Condition, Language
            for col in ("quantity", "available", "in_decks", "foil", "proxy", "condition", "language"):
                view._sort_collection_by_column(col)

            assert len(view.inv_tree.get_children()) == 4
            view.destroy()
        finally:
            tm.shutdown(wait=False)
