"""Tests for GeneratorView and ValidatorView."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from unittest.mock import MagicMock, patch

import pytest

from gui.components.card_preview_hover import CardHoverPreview
from gui.views.generator_view import GeneratorView, MtgUrlDeck
from gui.views.validator_view import CardValidationResult, ValidatorView
from src.deck_importer import DeckCard
from src.engine.task_manager import TaskManager
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
# Test GeneratorView
# ---------------------------------------------------------------------------


class TestGeneratorView:
    def test_instantiation_and_structure(self, clean_root):
        nav_callback = MagicMock()
        tm = TaskManager(max_workers=2)
        try:
            view = GeneratorView(clean_root, task_manager=tm, on_navigate=nav_callback)
            view.pack(fill=tk.BOTH, expand=True)

            assert isinstance(view, ttk.Frame)
            assert view.task_manager is tm
            assert view.on_navigate is nav_callback
            assert isinstance(view._game_notebook, ttk.Notebook)
            assert hasattr(view, "soriano_btn")
            assert hasattr(view, "fronts_only_btn")
            assert hasattr(view, "stop_btn")
            assert hasattr(view, "progress")
            assert hasattr(view, "status_var")

            # Check tabs in notebook
            tabs = view._game_notebook.tabs()
            assert len(tabs) >= 4  # Magic, One Piece, Riftbound, Lorcana, Configuración
        finally:
            tm.shutdown(wait=False)

    def test_button_states_empty_vs_ready(self, clean_root, tmp_path):
        view = GeneratorView(clean_root)
        view.pack()

        # Initially empty -> disabled
        assert "disabled" in view.soriano_btn.state()
        assert "disabled" in view.fronts_only_btn.state()

        # Add an XML path -> should enable
        xml_file = tmp_path / "deck.xml"
        xml_file.write_text("<order></order>", encoding="utf-8")
        view.state.xml_paths.append(xml_file)
        view._refresh_generate_state()

        assert "disabled" not in view.soriano_btn.state()
        assert "disabled" not in view.fronts_only_btn.state()

        # Clear state -> disabled again
        view.state.xml_paths.clear()
        view._refresh_generate_state()
        assert "disabled" in view.soriano_btn.state()
        assert "disabled" in view.fronts_only_btn.state()

    def test_crop_map_and_extra_backs(self, clean_root, tmp_path):
        view = GeneratorView(clean_root)
        view.pack()

        f1 = make_rgb_image(tmp_path / "front1.jpg")
        f2 = make_rgb_image(tmp_path / "front2.jpg")
        b1 = make_rgb_image(tmp_path / "back1.jpg")

        view.state.local_fronts.extend([f1, f2])
        view.state.local_backs.append(b1)
        view.state.front_back_paths.extend([b1, None])
        view.state.local_front_crop.extend([True, False])
        view.state.local_back_crop.append(False)

        crop_map = view._build_crop_map()
        assert crop_map[f1] is True
        assert crop_map[f2] is False
        assert crop_map[b1] is False

        extra_backs = view._resolve_extra_backs()
        assert extra_backs == [b1, None]

    def test_preflight_summary_update(self, clean_root, tmp_path):
        view = GeneratorView(clean_root)
        view.pack()

        card = DeckCard(
            name="Black Lotus", set_code="LEA", collector_number="232", quantity=1, zone="main"
        )
        deck = MtgUrlDeck(url="https://moxfield.com/decks/123", cards=[card], name="Test MTG")
        view.state.mtg_url_decks.append(deck)

        view._update_preflight()
        assert view._preflight_frame.winfo_manager() != ""
        assert len(view._preflight_labels) >= 2

    def test_event_handling(self, clean_root, tmp_path):
        view = GeneratorView(clean_root)
        view.pack()

        view._handle(("file", 1, 3, "test_job"))
        assert "[1/3] test_job" in view.status_var.get()

        view._handle(("progress", "download", 5, 10, "Deck"))
        assert view.progress["value"] == 50.0
        assert "5/10" in view.status_var.get()

        view._handle(("download_speed", 12.5, 45.0))
        assert "12.5 MB/s" in view._dl_speed_str

        out_dir = tmp_path / "run_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_mock = out_dir / "deck.pdf"
        pdf_mock.write_bytes(b"%PDF-1.4")

        with patch.object(view, "_open_output_folder"):
            view._handle(("done", [pdf_mock], None, out_dir, "Total: 10s", 0))
        assert "1 PDF(s) generados" in view.status_var.get()
        assert view.running is False

    def test_start_with_task_manager(self, clean_root, tmp_path):
        tm = TaskManager(max_workers=2)
        try:
            view = GeneratorView(clean_root, task_manager=tm)
            view.pack()

            xml_file = tmp_path / "deck.xml"
            xml_file.write_text("<order></order>", encoding="utf-8")
            view.state.xml_paths.append(xml_file)

            with (
                patch.object(view, "_confirm_cut_lines", return_value=True),
                patch("gui.views.generator_view.analyze", return_value=[]),
                patch("gui.views.generator_view.plan", return_value=MagicMock(jobs=[])),
                patch("gui.views.generator_view.format_warning", return_value=None),
                patch.object(tm, "submit_job", wraps=tm.submit_job) as mock_submit,
            ):
                view._start(fronts_only=True)
                assert view.running is True
                mock_submit.assert_called_once()
        finally:
            tm.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Test ValidatorView
# ---------------------------------------------------------------------------


class TestValidatorView:
    def test_instantiation_and_structure(self, clean_root):
        nav_callback = MagicMock()
        tm = TaskManager(max_workers=2)
        try:
            view = ValidatorView(clean_root, task_manager=tm, on_navigate=nav_callback)
            view.pack(fill=tk.BOTH, expand=True)

            assert isinstance(view, ttk.Frame)
            assert view.task_manager is tm
            assert view.on_navigate is nav_callback
            assert isinstance(view.tree, ttk.Treeview)
            assert isinstance(view.hover_preview, CardHoverPreview)
            assert hasattr(view, "url_entry")
            assert hasattr(view, "load_btn")
            assert hasattr(view, "status_var")
            assert hasattr(view, "preview_title")
            assert hasattr(view, "front_lbl")
            assert hasattr(view, "back_lbl")
        finally:
            tm.shutdown(wait=False)

    def test_validate_single_card_valid(self, clean_root):
        view = ValidatorView(clean_root)
        view.pack()

        card = DeckCard("Sol Ring", "cmm", "345", 1, "main")
        mock_data = {
            "name": "Sol Ring",
            "set": "cmm",
            "collector_number": "345",
            "highres_image": True,
            "digital": False,
            "rarity": "uncommon",
            "type_line": "Artifact",
            "image_uris": {"small": "https://cards.scryfall.io/small/front/s/o/solring.jpg"},
        }

        with patch("gui.views.validator_view._fetch_raw_card_data", return_value=mock_data):
            res = view._validate_single_card(card)

        assert res.status == "🟢 Válido"
        assert res.details == "Todo correcto"
        assert res.front_url == "https://cards.scryfall.io/small/front/s/o/solring.jpg"
        assert res.back_url is None

    def test_validate_single_card_low_res_and_digital(self, clean_root):
        view = ValidatorView(clean_root)
        view.pack()

        card = DeckCard("Black Lotus", "vma", "4", 1, "main")
        mock_data = {
            "name": "Black Lotus",
            "set": "vma",
            "collector_number": "4",
            "highres_image": False,
            "digital": True,
            "rarity": "mythic",
            "type_line": "Artifact",
            "image_uris": {"small": "https://cards.scryfall.io/small/front/0/0/lotus.jpg"},
        }

        with patch("gui.views.validator_view._fetch_raw_card_data", return_value=mock_data):
            res = view._validate_single_card(card)

        assert res.status == "⚠️ Advertencia"
        assert "Imagen de baja resolución" in res.details
        assert "Carta digital" in res.details

    def test_validate_single_card_mdfc(self, clean_root):
        view = ValidatorView(clean_root)
        view.pack()

        card = DeckCard("Esika, God of the Tree", "khm", "168", 1, "main")
        mock_data = {
            "name": "Esika, God of the Tree // The Prismatic Bridge",
            "set": "khm",
            "collector_number": "168",
            "highres_image": True,
            "digital": False,
            "rarity": "mythic",
            "type_line": "Legendary Creature // Legendary Enchantment",
            "card_faces": [
                {"image_uris": {"small": "https://cards.scryfall.io/small/front/e/s/esika.jpg"}},
                {"image_uris": {"small": "https://cards.scryfall.io/small/back/e/s/bridge.jpg"}},
            ],
        }

        with patch("gui.views.validator_view._fetch_raw_card_data", return_value=mock_data):
            res = view._validate_single_card(card)

        assert res.status == "🟢 Válido"
        assert "Doble Cara (MDFC)" in res.details
        assert res.front_url == "https://cards.scryfall.io/small/front/e/s/esika.jpg"
        assert res.back_url == "https://cards.scryfall.io/small/back/e/s/bridge.jpg"

    def test_tree_row_resolution_for_hover_preview(self, clean_root):
        view = ValidatorView(clean_root)
        view.pack()

        card = DeckCard("Sol Ring", "cmm", "345", 1, "main")
        res = CardValidationResult(
            card=card,
            status="🟢 Válido",
            details="Todo correcto",
            set_code="cmm",
            collector_number="345",
            front_url="https://cards.scryfall.io/small/front/s/o/solring.jpg",
            back_url=None,
            type_line="Artifact",
            rarity="uncommon",
        )
        view.current_deck_cards.append(res)
        item_id = view.tree.insert(
            "",
            tk.END,
            values=(
                "Sol Ring",
                1,
                "Principal",
                "CMM",
                "345",
                "Artifact",
                "Uncommon",
                "🟢 Válido",
                "Todo correcto",
            ),
        )

        # Test hover resolver
        info = view._resolve_tree_card(item_id)
        assert info is not None
        assert info.name == "Sol Ring"
        assert info.set_code == "cmm"
        assert info.collector_number == "345"
        assert info.front_url == "https://cards.scryfall.io/small/front/s/o/solring.jpg"

    def test_process_queue_deck_lifecycle(self, clean_root):
        view = ValidatorView(clean_root)
        view.pack()

        card1 = DeckCard("Sol Ring", "cmm", "345", 1, "main")
        card2 = DeckCard("Arcane Signet", "cmm", "346", 1, "main")
        raw_json = {"name": "Commander Deck", "cards": []}

        # 1. Deck loaded
        view.event_queue.put(("deck_loaded", ("Commander Deck", [card1, card2], raw_json)))
        view._process_queue()

        assert len(view.tree.get_children()) == 2
        assert "Commander Deck" in view.preview_title.cget("text")
        assert "JSON Moxfield" in [view.notebook.tab(t, "text") for t in view.notebook.tabs()]

        # 2. Card validated
        res1 = CardValidationResult(
            card=card1,
            status="🟢 Válido",
            details="Todo correcto",
            set_code="cmm",
            collector_number="345",
            front_url="https://example.com/solring.jpg",
            back_url=None,
            type_line="Artifact",
            rarity="uncommon",
        )
        view.event_queue.put(("card_validated", (res1, 0, 2)))
        view._process_queue()

        assert len(view.current_deck_cards) == 1
        assert "Válidas: 1" in view.summary_var.get()

        # 3. Validation complete
        view.event_queue.put(("validation_complete", None))
        view._process_queue()

        assert view.status_var.get() == "Validación técnica finalizada."
        assert "disabled" not in view.load_btn.state()

    def test_on_load_clicked_submits_to_task_manager(self, clean_root):
        tm = TaskManager(max_workers=2)
        try:
            view = ValidatorView(clean_root, task_manager=tm)
            view.pack()

            view.url_var.set("https://moxfield.com/decks/test123")

            with patch.object(tm, "submit_job") as mock_submit:
                view._on_load_clicked()
                mock_submit.assert_called_once_with(
                    "Validate Deck",
                    view._async_load_and_validate,
                    "https://moxfield.com/decks/test123",
                )
        finally:
            tm.shutdown(wait=False)
