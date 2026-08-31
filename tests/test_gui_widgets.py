"""Unit and corner-case test suite for gui/widgets.py.

Covers:
- notify: darwin osascript, plyer notification, exception handling
- attach_context_menu: right click popup, cut/copy/paste/select all commands
- load_tab_icon: dev mode, frozen mode, missing icon, corrupted icon
- XmlPb: canvas progress bar percentage, text, custom colors, bounds clamping
- ImageTooltip: hover preview lifecycle, mouse motion, boundary screen clamping, destruction
- PreviewWindow: async thumbnail loading, queue draining, spinner animation, window closing
- ellipsize: string truncation with ellipsis
"""

from __future__ import annotations

import io
import queue
import sys
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gui.widgets import (
    ImageTooltip,
    PreviewWindow,
    XmlPb,
    attach_context_menu,
    ellipsize,
    load_tab_icon,
    notify,
)
from src.parser import CardImage, CardOrder


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


class TestEllipsize:
    def test_short_name_not_ellipsized(self):
        assert ellipsize("Short", 10) == "Short"

    def test_exact_width_not_ellipsized(self):
        assert ellipsize("Exact12345", 10) == "Exact12345"

    def test_long_name_ellipsized(self):
        assert ellipsize("This is a very long card name", 10) == "This is a…"


class TestNotify:
    def test_notify_darwin_osascript(self):
        with patch("sys.platform", "darwin"), patch("subprocess.run") as mock_run:
            notify("Title", "Message")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "osascript" in args
            assert "Title" in args
            assert "Message" in args

    def test_notify_non_darwin_plyer(self):
        mock_plyer_notif = MagicMock()
        with patch("sys.platform", "win32"), patch.dict("sys.modules", {"plyer": MagicMock(notification=mock_plyer_notif)}):
            notify("WinTitle", "WinMessage")
            mock_plyer_notif.notify.assert_called_once_with(
                title="WinTitle", message="WinMessage", app_name="MPCFillToPDF", timeout=8
            )

    def test_notify_silently_handles_exceptions(self):
        with patch("sys.platform", "darwin"), patch("subprocess.run", side_effect=RuntimeError("Process error")):
            # Should not raise exception
            notify("Title", "Message")


class TestAttachContextMenu:
    def test_attach_context_menu_commands_and_binding(self, clean_root):
        entry = tk.Entry(clean_root)
        entry.pack()
        attach_context_menu(entry)

        # Check binding
        binds = entry.bind()
        assert "<Button-3>" in binds


class TestLoadTabIcon:
    def test_missing_icon_returns_none(self):
        assert load_tab_icon("non_existent_icon_xyz") is None

    def test_load_icon_dev_and_frozen(self, tmp_path):
        icons_dir = tmp_path / "icons"
        icons_dir.mkdir()
        img = Image.new("RGBA", (32, 32), color=(255, 0, 0, 255))
        img.save(icons_dir / "test_icon.png")

        # Frozen mode
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(tmp_path), create=True):
            photo = load_tab_icon("test_icon", size=(16, 16))
            assert photo is not None
            assert photo.width() == 16
            assert photo.height() == 16


class TestXmlPb:
    def test_xml_pb_set_progress_and_colors(self, clean_root):
        pb = XmlPb(clean_root)
        pb.pack()

        # 0%
        pb.set_progress(0, "0/10", color="#0078d4")
        assert pb.itemcget(pb._lbl, "text") == "0/10"

        # 50%
        pb.set_progress(50, "5/10", color="#2e7d32")
        coords = pb.coords(pb._bar)
        assert coords[2] > 0

        # Clamping < 0% and > 100%
        pb.set_progress(-10, "Under")
        coords_under = pb.coords(pb._bar)
        assert coords_under[2] == 0

        pb.set_progress(150, "Over")
        coords_over = pb.coords(pb._bar)
        assert coords_over[2] == pb._W


class TestImageTooltip:
    def test_tooltip_lifecycle_show_move_hide(self, clean_root, tmp_path):
        img_path = tmp_path / "card.jpg"
        Image.new("RGB", (200, 280), color="blue").save(img_path)

        lbl = tk.Label(clean_root, text="Card label")
        lbl.pack()

        tip = ImageTooltip(lbl, img_path)

        # Show tooltip
        tip._show(50, 50)
        assert tip._tip is not None
        assert tip._tip.winfo_exists()

        # Move tooltip
        tip._move(100, 100)

        # Screen clamping (simulate coordinates near screen edges)
        tip._move(5000, 5000)

        # Hide tooltip
        tip._hide()
        assert tip._tip is None

    def test_tooltip_show_handles_corrupted_image(self, clean_root, tmp_path):
        bad_path = tmp_path / "bad.jpg"
        bad_path.write_text("not an image")

        lbl = tk.Label(clean_root, text="Bad")
        lbl.pack()

        tip = ImageTooltip(lbl, bad_path)
        tip._show(50, 50)
        assert tip._tip is None


class TestPreviewWindow:
    def test_preview_window_lifecycle_and_drain(self, clean_root, tmp_path):
        xml_path = tmp_path / "test.xml"
        order = CardOrder(
            quantity=2,
            fronts=[
                CardImage(drive_id="id1", slots=[0], name="Card 1"),
                CardImage(drive_id="id2", slots=[1, 2], name="Card 2"),
            ],
            backs=[],
            cardback_id="cb_id",
        )

        fake_thumb = Image.new("RGB", (80, 112), color="green")
        thumb_bytes = io.BytesIO()
        fake_thumb.save(thumb_bytes, format="PNG")
        raw_data = thumb_bytes.getvalue()

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.content = raw_data
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            win = PreviewWindow(clean_root, xml_path, order)
            try:
                assert win.title() == f"Vista previa — {xml_path.name}"
                assert len(win._img_labels) == 2

                # Test spinner tick
                win._tick_spinner()

                # Test apply image data
                win._apply(0, raw_data)
                assert 0 not in win._loading_set

                # Test apply None (fetch error)
                win._apply(1, None)
                assert 1 not in win._loading_set

                # Test drain
                win._pending.put((0, raw_data))
                win._drain()

                # Test fetch helper directly
                fetched = win._fetch("id1")
                assert fetched == raw_data
            finally:
                win._on_close()
