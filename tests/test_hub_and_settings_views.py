"""Tests for HubView and SettingsView modular GUI views."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from unittest.mock import MagicMock, patch

import pytest

from gui.views.hub_view import HubView
from gui.views.settings_view import SettingsView
from src.app_settings import (
    DEFAULT_CUT_LINE_COLOR,
    DEFAULT_CUT_LINE_STYLE,
    DEFAULT_CUT_LINE_WIDTH,
    DEFAULT_SCRYFALL_BLUR_THRESHOLD,
    DEFAULT_SCRYFALL_LANG,
    DEFAULT_SCRYFALL_QUALITY,
    AppSettings,
    load_settings,
)
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
    """Yield tk_root and clean up all child widgets after test."""
    yield tk_root
    for child in tk_root.winfo_children():
        try:
            child.destroy()
        except Exception:
            pass


@pytest.fixture
def temp_service(tmp_path):
    """Create an InventoryService using a temporary SQLite database."""
    db_file = tmp_path / "test_inventario.db"
    return InventoryService(db_path=db_file)


# ---------------------------------------------------------------------------
# TestHubView
# ---------------------------------------------------------------------------


class TestHubView:
    def test_hub_view_creation_and_layout(self, clean_root, temp_service):
        hub = HubView(clean_root, inventory_service=temp_service)
        hub.pack(fill=tk.BOTH, expand=True)

        assert hub._total_cards_var.get() == "0"
        assert hub._unique_cards_var.get() == "0"
        assert hub._physical_cards_var.get() == "0"
        assert hub._proxy_cards_var.get() == "0"
        assert hub._deck_count_var.get() == "0"

    def test_hub_navigation_callbacks(self, clean_root, temp_service):
        callback = MagicMock()
        hub = HubView(clean_root, on_navigate=callback, inventory_service=temp_service)
        hub.pack()

        # Trigger navigation to all primary routes
        routes = ["generator", "validator", "inventory", "settings"]
        for r in routes:
            hub._navigate(r)
            callback.assert_called_with(r)

    def test_hub_stats_refresh_with_data(self, clean_root, temp_service):
        # Insert test data into the inventory service
        temp_service.add_or_update_card(
            InventoryCard(
                oracle_id="oracle-1",
                name="Sol Ring",
                set_code="lea",
                collector_number="269",
                quantity=2,
                is_proxy=False,
            )
        )
        temp_service.add_or_update_card(
            InventoryCard(
                oracle_id="oracle-2",
                name="Black Lotus",
                set_code="lea",
                collector_number="232",
                quantity=1,
                is_proxy=True,
            )
        )
        temp_service.register_deck(
            DeckRecord(name="Commander Deck 1"),
            [
                DeckCardEntry(
                    deck_id=0,
                    oracle_id="oracle-1",
                    name="Sol Ring",
                    set_code="lea",
                    collector_number="269",
                    quantity_required=1,
                )
            ],
        )

        hub = HubView(clean_root, inventory_service=temp_service)
        hub.pack()

        stats = hub.refresh_stats()
        assert stats["total_cards"] == 3
        assert stats["unique_oracle_ids"] == 2
        assert stats["physical_count"] == 2
        assert stats["proxy_count"] == 1
        assert stats["deck_count"] == 1

        assert hub._total_cards_var.get() == "3"
        assert hub._unique_cards_var.get() == "2"
        assert hub._physical_cards_var.get() == "2"
        assert hub._proxy_cards_var.get() == "1"
        assert hub._deck_count_var.get() == "1"

    def test_hub_handles_none_inventory_service(self, clean_root, tmp_path):
        with patch("gui.views.hub_view.InventoryService") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_inventory_stats.return_value = {
                "total_cards": 10,
                "unique_oracle_ids": 5,
                "physical_count": 8,
                "proxy_count": 2,
                "deck_count": 1,
            }
            mock_cls.return_value = mock_inst

            hub = HubView(clean_root, inventory_service=None)
            hub.pack()

            assert hub._total_cards_var.get() == "10"
            assert hub._deck_count_var.get() == "1"


# ---------------------------------------------------------------------------
# TestSettingsView
# ---------------------------------------------------------------------------


class TestSettingsView:
    def test_settings_view_defaults(self, clean_root, tmp_path):
        view = SettingsView(clean_root, base_dir=tmp_path)
        view.pack(fill=tk.BOTH, expand=True)

        assert view.settings.cut_line_color == DEFAULT_CUT_LINE_COLOR
        assert view.settings.cut_line_style == DEFAULT_CUT_LINE_STYLE
        assert view.settings.cut_line_width == DEFAULT_CUT_LINE_WIDTH
        assert view.settings.scryfall_lang == DEFAULT_SCRYFALL_LANG
        assert view.settings.scryfall_quality == DEFAULT_SCRYFALL_QUALITY

    def test_settings_view_custom_initial_settings(self, clean_root, tmp_path):
        custom = AppSettings(
            cut_line_color="#123456",
            cut_line_style="full",
            cut_line_width=3.5,
            scryfall_lang="es",
            scryfall_quality="png",
        )
        view = SettingsView(clean_root, settings=custom, base_dir=tmp_path)
        view.pack()

        assert view._settings_color_var.get() == "#123456"
        assert view._settings_style_var.get() == "full"
        assert view._settings_width_var.get() == "3.5"
        assert view._settings_sf_lang_var.get() == "Español"
        assert view._settings_sf_quality_var.get() == "PNG (Alta calidad)"

    def test_settings_view_pick_directory(self, clean_root, tmp_path):
        target_dir = tmp_path / "custom_pdf_out"
        target_dir.mkdir()

        view = SettingsView(clean_root, base_dir=tmp_path)
        view.pack()

        with patch("tkinter.filedialog.askdirectory", return_value=str(target_dir)):
            view._settings_pick_dir()

        assert view.settings.output_dir == target_dir
        assert view._settings_out_var.get() == str(target_dir)

        # Ensure persisted to settings.json
        reloaded = load_settings(tmp_path)
        assert reloaded.output_dir == target_dir

    def test_settings_view_pick_color(self, clean_root, tmp_path):
        view = SettingsView(clean_root, base_dir=tmp_path)
        view.pack()

        with patch("tkinter.colorchooser.askcolor", return_value=((255, 0, 0), "#ff0000")):
            view._settings_pick_color()

        assert view.settings.cut_line_color == "#ff0000"
        assert view._settings_color_var.get() == "#ff0000"

        reloaded = load_settings(tmp_path)
        assert reloaded.cut_line_color == "#ff0000"

    def test_settings_view_width_changes(self, clean_root, tmp_path):
        view = SettingsView(clean_root, base_dir=tmp_path)
        view.pack()

        view._settings_width_var.set("2.4")
        assert view.settings.cut_line_width == pytest.approx(2.4)

        reloaded = load_settings(tmp_path)
        assert reloaded.cut_line_width == pytest.approx(2.4)

    def test_settings_view_style_toggle_and_fronts_backs(self, clean_root, tmp_path):
        view = SettingsView(clean_root, base_dir=tmp_path)
        view.pack()

        view._settings_style_var.set("full")
        view._on_settings_style_changed()

        assert view.settings.cut_line_style == "full"
        assert view.settings.cut_line_over_cards is True
        assert view.settings.cut_line_over_fronts is True
        assert view.settings.cut_line_over_backs is True

        # Toggle fronts and backs
        view._settings_over_fronts_var.set(False)
        view._on_settings_over_fronts_changed()
        assert view.settings.cut_line_over_fronts is False

        view._settings_over_backs_var.set(False)
        view._on_settings_over_backs_changed()
        assert view.settings.cut_line_over_backs is False

        # Switch back to ticks
        view._settings_style_var.set("ticks")
        view._on_settings_style_changed()
        assert view.settings.cut_line_style == "ticks"
        assert view.settings.cut_line_over_cards is False

    def test_settings_view_scryfall_options(self, clean_root, tmp_path):
        view = SettingsView(clean_root, base_dir=tmp_path)
        view.pack()

        # Language: Spanish
        view._settings_sf_lang_var.set("Español")
        view._on_sf_lang_changed()
        assert view.settings.scryfall_lang == "es"
        assert str(view._sf_policy_combo.cget("state")) == "readonly"

        # Policy
        view._settings_sf_policy_var.set(
            "Buscar versión alternativa en idioma preferido y si no descargar en inglés"
        )
        view._on_sf_policy_changed()
        assert view.settings.scryfall_fail_policy == "alternative"

        # Language back to English -> policy disabled
        view._settings_sf_lang_var.set("Inglés")
        view._on_sf_lang_changed()
        assert view.settings.scryfall_lang == "en"
        assert str(view._sf_policy_combo.cget("state")) == "disabled"

        # Quality
        view._settings_sf_quality_var.set("PNG (Alta calidad)")
        view._on_sf_quality_changed()
        assert view.settings.scryfall_quality == "png"

        # Quality check and blur threshold
        view._settings_sf_quality_check_var.set(True)
        view._on_sf_quality_check_changed()
        assert view.settings.scryfall_quality_check is True

        view._settings_sf_blur_threshold_var.set("125.5")
        assert view.settings.scryfall_blur_threshold == pytest.approx(125.5)

        # Brightness & contrast, deck separators, minimize tokens
        view._settings_sf_bc_check_var.set(False)
        view._on_sf_bc_check_changed()
        assert view.settings.scryfall_brightness_contrast_check is False

        view._settings_sf_deck_separators_var.set(True)
        view._on_sf_deck_separators_changed()
        assert view.settings.scryfall_deck_separators is True

        view._settings_sf_minimize_tokens_var.set(True)
        view._on_sf_minimize_tokens_changed()
        assert view.settings.scryfall_minimize_tokens is True

    def test_settings_view_clear_cache(self, clean_root, tmp_path):
        cache_dir = tmp_path / "cache_test"
        cache_dir.mkdir(parents=True, exist_ok=True)
        dummy_file = cache_dir / "temp.jpg"
        dummy_file.write_text("fake_content")

        with patch("gui.views.settings_view.work_dir", return_value=cache_dir):
            with patch("tkinter.messagebox.askyesno", return_value=True):
                with patch("tkinter.messagebox.showinfo"):
                    view = SettingsView(clean_root, base_dir=tmp_path)
                    view.pack()

                    view._clear_cache()
                    assert not dummy_file.exists()

    def test_settings_view_reset_to_defaults(self, clean_root, tmp_path):
        custom = AppSettings(
            cut_line_color="#999999",
            cut_line_style="full",
            cut_line_width=4.0,
            scryfall_lang="es",
            scryfall_quality="png",
            scryfall_blur_threshold=200.0,
        )
        view = SettingsView(clean_root, settings=custom, base_dir=tmp_path)
        view.pack()

        # Reset
        view._settings_reset()

        assert view.settings.cut_line_color == DEFAULT_CUT_LINE_COLOR
        assert view.settings.cut_line_style == DEFAULT_CUT_LINE_STYLE
        assert view.settings.cut_line_width == DEFAULT_CUT_LINE_WIDTH
        assert view.settings.scryfall_lang == DEFAULT_SCRYFALL_LANG
        assert view.settings.scryfall_quality == DEFAULT_SCRYFALL_QUALITY
        assert view.settings.scryfall_blur_threshold == pytest.approx(DEFAULT_SCRYFALL_BLUR_THRESHOLD)

        reloaded = load_settings(tmp_path)
        assert reloaded.cut_line_color == DEFAULT_CUT_LINE_COLOR

    def test_settings_view_back_navigation(self, clean_root, tmp_path):
        callback = MagicMock()
        view = SettingsView(clean_root, on_navigate=callback, base_dir=tmp_path)
        view.pack()

        # Trigger on_navigate with hub
        view._on_navigate("hub")
        callback.assert_called_with("hub")
