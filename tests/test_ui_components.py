"""Tests for reusable GUI components: NavBar and CardHoverPreview."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gui.components.card_preview_hover import (
    CardHoverPreview,
    CardPreviewInfo,
    create_placeholder_image,
    resolve_preview_cache_path,
    sanitize_filename,
)
from gui.components.nav_bar import DEFAULT_ROUTES, NavBar

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


# ---------------------------------------------------------------------------
# TestNavBar
# ---------------------------------------------------------------------------


class TestNavBar:
    def test_default_routes_and_initial_active(self, clean_root):
        nav = NavBar(clean_root)
        nav.pack()

        assert len(nav._buttons) == len(DEFAULT_ROUTES)
        assert nav.active_route == "hub"
        assert nav.is_busy is False

    def test_custom_routes_and_default_route(self, clean_root):
        routes = [("r1", "Route 1"), ("r2", "Route 2")]
        nav = NavBar(clean_root, routes=routes, default_route="r2")
        nav.pack()

        assert len(nav._buttons) == 2
        assert nav.active_route == "r2"

    def test_set_active_route(self, clean_root):
        nav = NavBar(clean_root)
        nav.pack()

        success = nav.set_active_route("validator")
        assert success is True
        assert nav.active_route == "validator"

        # Invalid route
        fail = nav.set_active_route("non_existent_route")
        assert fail is False
        assert nav.active_route == "validator"

    def test_button_click_triggers_callback(self, clean_root):
        callback = MagicMock()
        nav = NavBar(clean_root, on_navigate=callback)
        nav.pack()

        # Click "inventory" button
        nav._on_button_clicked("inventory")
        assert nav.active_route == "inventory"
        callback.assert_called_once_with("inventory")

        # Clicking already active route does not re-trigger callback
        callback.reset_mock()
        nav._on_button_clicked("inventory")
        callback.assert_not_called()

    def test_activity_indicator_lifecycle(self, clean_root):
        nav = NavBar(clean_root)
        nav.pack()

        assert nav.is_busy is False

        # Turn on activity
        nav.set_activity(True, "Descargando imágenes...")
        assert nav.is_busy is True
        assert nav._activity_text == "Descargando imágenes..."
        assert nav._spinner_after_id is not None

        # Update text
        nav.update_activity_text("Procesando PDF...")
        assert nav._activity_label.cget("text") == "Procesando PDF..."

        # Turn off activity
        nav.set_activity(False)
        assert nav.is_busy is False
        assert nav._spinner_after_id is None
        assert nav._spinner_label.cget("text") == ""


# ---------------------------------------------------------------------------
# TestCardPreviewHover
# ---------------------------------------------------------------------------


class TestCardPreviewHover:
    def test_sanitize_filename(self):
        assert sanitize_filename("Jace, the Mind Sculptor") == "Jace_the_Mind_Sculptor"
        assert sanitize_filename('Card: "Special" / Token?') == "Card_Special_Token"

    def test_resolve_preview_cache_path(self, tmp_path):
        with patch("gui.components.card_preview_hover.work_dir", return_value=tmp_path):
            # By scryfall_id
            p_id = resolve_preview_cache_path(scryfall_id="abc-123")
            assert p_id.name == "preview_id_abc-123_front.jpg"

            # By set_code and collector_number
            p_set = resolve_preview_cache_path(set_code="MH2", collector_number="450", suffix="back")
            assert p_set.name == "preview_mh2_450_back.jpg"

            # By name
            p_name = resolve_preview_cache_path(name="Sol Ring")
            assert p_name.name == "preview_name_sol_ring_front.jpg"

    def test_card_preview_info_coercion(self):
        # From CardPreviewInfo
        info1 = CardPreviewInfo(name="Black Lotus", set_code="lea", collector_number="232")
        assert CardPreviewInfo.from_any(info1) is info1

        # From dict
        info2 = CardPreviewInfo.from_any({
            "name": "Lightning Bolt",
            "set": "m11",
            "number": "149",
            "image_url": "https://example.com/bolt.jpg",
        })
        assert info2 is not None
        assert info2.name == "Lightning Bolt"
        assert info2.set_code == "m11"
        assert info2.collector_number == "149"
        assert info2.front_url == "https://example.com/bolt.jpg"

        # From tuple
        info3 = CardPreviewInfo.from_any(("Counterspell", "ema", "43"))
        assert info3 is not None
        assert info3.name == "Counterspell"
        assert info3.set_code == "ema"
        assert info3.collector_number == "43"

        # From string
        info4 = CardPreviewInfo.from_any("Swamp")
        assert info4 is not None
        assert info4.name == "Swamp"

        # None / invalid
        assert CardPreviewInfo.from_any(None) is None
        assert CardPreviewInfo.from_any(12345) is None

    def test_create_placeholder_image(self):
        photo = create_placeholder_image("Prueba")
        assert photo is not None
        assert photo.width() > 0
        assert photo.height() > 0

    def test_preview_show_hide_lifecycle(self, clean_root):
        preview = CardHoverPreview(clean_root, debounce_ms=10)
        try:
            info = CardPreviewInfo(name="Forest", set_code="unh", collector_number="140")
            preview.show_preview(info, 100, 100)

            assert preview._tip is not None
            assert preview._tip.winfo_exists()
            assert "Forest" in preview._header_label.cget("text")

            preview.hide()
            assert preview._tip is None
        finally:
            preview.destroy()

    def test_preview_cached_disk_image_loads_immediately(self, clean_root, tmp_path):
        preview_dir = tmp_path / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        cached_file = preview_dir / "preview_mh2_100_front.jpg"
        img = Image.new("RGB", (146, 204), color=(255, 0, 0))
        img.save(cached_file)

        with patch("gui.components.card_preview_hover.work_dir", return_value=tmp_path):
            preview = CardHoverPreview(clean_root, debounce_ms=10)
            try:
                info = CardPreviewInfo(name="Archon", set_code="mh2", collector_number="100")
                preview.show_preview(info, 50, 50)

                # In-memory cache should be populated
                mem_key = "preview_mh2_100_front_front"
                assert mem_key in preview._photo_cache
                preview.hide()
            finally:
                preview.destroy()

    def test_preview_async_download_success(self, clean_root, tmp_path):
        preview_dir = tmp_path / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        fake_image = Image.new("RGB", (146, 204), color=(0, 255, 0))
        fake_img_bytes = tmp_path / "temp.jpg"
        fake_image.save(fake_img_bytes)
        raw_bytes = fake_img_bytes.read_bytes()

        mock_resp = MagicMock()
        mock_resp.content = raw_bytes
        mock_resp.raise_for_status = MagicMock()

        with patch("gui.components.card_preview_hover.work_dir", return_value=tmp_path):
            with patch("requests.get", return_value=mock_resp):
                preview = CardHoverPreview(clean_root, debounce_ms=10)
                try:
                    info = CardPreviewInfo(
                        name="Counterspell",
                        front_url="https://cards.scryfall.io/small/front/1.jpg",
                    )
                    preview.show_preview(info, 50, 50)

                    # Wait for executor thread to finish
                    preview._executor.shutdown(wait=True)
                    clean_root.update_idletasks()

                    cache_path = resolve_preview_cache_path(name="Counterspell")
                    assert cache_path.exists()
                    preview.hide()
                finally:
                    preview.destroy()

    def test_attach_to_treeview(self, clean_root):
        preview = CardHoverPreview(clean_root, debounce_ms=50)
        try:
            tree = ttk.Treeview(clean_root, columns=("name", "set"))
            tree.pack()
            item1 = tree.insert("", tk.END, values=("Sol Ring", "LEA"))
            tree.insert("", tk.END, values=("Black Lotus", "LEA"))

            resolver = MagicMock(return_value={"name": "Sol Ring", "set_code": "lea", "collector_number": "269"})
            preview.attach_to_treeview(tree, resolver)

            # Trigger motion over treeview row
            with patch.object(tree, "identify_region", return_value="cell"):
                with patch.object(tree, "identify_row", return_value=item1):
                    # Trigger the motion callback bound on tree
                    tree.event_generate("<Motion>", x=10, y=10)

            preview.hide()
        finally:
            preview.destroy()

    def test_attach_to_widget(self, clean_root):
        preview = CardHoverPreview(clean_root, debounce_ms=10)
        try:
            lbl = ttk.Label(clean_root, text="Hover me")
            lbl.pack()

            preview.attach_to_widget(lbl, {"name": "Mox Pearl", "set": "lea", "number": "263"})
            # Verify bind events attached
            events = lbl.bind()
            assert "<Enter>" in events
            assert "<Leave>" in events
            assert "<Motion>" in events
            preview.hide()
        finally:
            preview.destroy()
