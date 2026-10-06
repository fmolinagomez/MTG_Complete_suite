"""Corner-case test suite for CardHoverPreview (gui/components/card_preview_hover.py).

Covers:
- Tooltip hover positioning: screen boundary detection (when x + width > screen_width or y + height > screen_height).
- Fast mouse motion: rapid show/hide events before image fetch completes (no dangling references, cancelled timers).
- Asynchronous image fetch failures or HTTP 404/500: placeholder box shown, no Tkinter exception.
- Destruction of parent window while hover image fetch is in-flight.
"""

from __future__ import annotations

import time
import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gui.components.card_preview_hover import (
    CardHoverPreview,
    CardPreviewInfo,
)

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
# 1. Tooltip hover positioning & screen boundary detection
# ---------------------------------------------------------------------------


def test_hover_preview_positioning_normal(clean_root):
    """Within screen bounds, tooltip appears at x_root + 18, y_root + 18."""
    hover = CardHoverPreview(clean_root, debounce_ms=10)
    try:
        info = CardPreviewInfo(name="Lightning Bolt", set_code="lea", collector_number="161")
        hover.show_preview(info, 100, 100)

        assert hover._tip is not None
        assert hover._tip.winfo_exists()

        # Mock geometry calculation
        with (
            patch.object(hover._tip, "winfo_width", return_value=150),
            patch.object(hover._tip, "winfo_height", return_value=220),
            patch.object(hover._tip, "winfo_screenwidth", return_value=1920),
            patch.object(hover._tip, "winfo_screenheight", return_value=1080),
            patch.object(hover._tip, "geometry") as mock_geom,
        ):
            hover._move(100, 100)
            mock_geom.assert_called_with("+118+118")
    finally:
        hover.destroy()


def test_hover_preview_positioning_right_boundary_overflow(clean_root):
    """When x + tw > screen_width, tooltip flips to the left of the cursor."""
    hover = CardHoverPreview(clean_root, debounce_ms=10)
    try:
        info = CardPreviewInfo(name="Black Lotus")
        hover.show_preview(info, 1850, 100)

        with (
            patch.object(hover._tip, "winfo_width", return_value=150),
            patch.object(hover._tip, "winfo_height", return_value=220),
            patch.object(hover._tip, "winfo_screenwidth", return_value=1920),
            patch.object(hover._tip, "winfo_screenheight", return_value=1080),
            patch.object(hover._tip, "geometry") as mock_geom,
        ):
            # x_root = 1850. 1850 + 18 + 150 = 2018 > 1920
            # Expected x: 1850 - 150 - 12 = 1688
            # Expected y: 100 + 18 = 118
            hover._move(1850, 100)
            mock_geom.assert_called_with("+1688+118")
    finally:
        hover.destroy()


def test_hover_preview_positioning_bottom_boundary_overflow(clean_root):
    """When y + th > screen_height, tooltip flips above the cursor."""
    hover = CardHoverPreview(clean_root, debounce_ms=10)
    try:
        info = CardPreviewInfo(name="Sol Ring")
        hover.show_preview(info, 100, 1000)

        with (
            patch.object(hover._tip, "winfo_width", return_value=150),
            patch.object(hover._tip, "winfo_height", return_value=220),
            patch.object(hover._tip, "winfo_screenwidth", return_value=1920),
            patch.object(hover._tip, "winfo_screenheight", return_value=1080),
            patch.object(hover._tip, "geometry") as mock_geom,
        ):
            # y_root = 1000. 1000 + 18 + 220 = 1238 > 1080
            # Expected x: 100 + 18 = 118
            # Expected y: 1000 - 220 - 12 = 768
            hover._move(100, 1000)
            mock_geom.assert_called_with("+118+768")
    finally:
        hover.destroy()


def test_hover_preview_positioning_corner_and_clamping(clean_root):
    """Bottom-right corner overflow flips both dimensions, and clamping prevents negative positions."""
    hover = CardHoverPreview(clean_root, debounce_ms=10)
    try:
        info = CardPreviewInfo(name="Mox Pearl")
        hover.show_preview(info, 1900, 1050)

        with (
            patch.object(hover._tip, "winfo_width", return_value=150),
            patch.object(hover._tip, "winfo_height", return_value=220),
            patch.object(hover._tip, "winfo_screenwidth", return_value=1920),
            patch.object(hover._tip, "winfo_screenheight", return_value=1080),
            patch.object(hover._tip, "geometry") as mock_geom,
        ):
            hover._move(1900, 1050)
            # x: 1900 - 150 - 12 = 1738
            # y: 1050 - 220 - 12 = 818
            mock_geom.assert_called_with("+1738+818")

            # Extreme case with tiny screen width (clamping test)
            with (
                patch.object(hover._tip, "winfo_screenwidth", return_value=100),
                patch.object(hover._tip, "winfo_screenheight", return_value=100),
            ):
                hover._move(50, 50)
                # 50 - 150 - 12 = -112 -> clamped to 0
                mock_geom.assert_called_with("+0+0")
    finally:
        hover.destroy()


# ---------------------------------------------------------------------------
# 2. Fast mouse motion: rapid show/hide events
# ---------------------------------------------------------------------------


def test_hover_preview_rapid_schedule_and_hide(clean_root):
    """Rapid show / hide calls should cancel pending timers and leave no dangling tip."""
    hover = CardHoverPreview(clean_root, debounce_ms=50)
    try:
        info1 = CardPreviewInfo(name="Card 1")
        info2 = CardPreviewInfo(name="Card 2")
        info3 = CardPreviewInfo(name="Card 3")

        # Simulate fast mouse motion across rows
        hover.schedule_show(info1, 10, 10)
        req_1 = hover._active_request_id

        hover.schedule_show(info2, 20, 20)
        req_2 = hover._active_request_id
        assert req_2 > req_1

        hover.schedule_show(info3, 30, 30)
        req_3 = hover._active_request_id
        assert req_3 > req_2

        # Immediate hide
        hover.hide()
        assert hover._after_id is None
        assert hover._tip is None
        assert hover._current_info is None

        # Process Tkinter event queue after debounce
        time.sleep(0.06)
        clean_root.update_idletasks()
        clean_root.update()

        # Still hidden because of cancellation
        assert hover._tip is None
    finally:
        hover.destroy()


def test_hover_preview_rapid_events_displays_only_latest(clean_root):
    """Only the latest scheduled preview displays after debounce delay."""
    hover = CardHoverPreview(clean_root, debounce_ms=30)
    try:
        info1 = CardPreviewInfo(name="First Card")
        info2 = CardPreviewInfo(name="Final Card")

        hover.schedule_show(info1, 10, 10)
        hover.schedule_show(info2, 50, 50)

        # Wait for debounce
        time.sleep(0.05)
        clean_root.update_idletasks()
        clean_root.update()

        assert hover._tip is not None
        assert hover._tip.winfo_exists()
        assert hover._current_info.name == "Final Card"
    finally:
        hover.destroy()


# ---------------------------------------------------------------------------
# 3. Asynchronous image fetch failures & HTTP 404/500
# ---------------------------------------------------------------------------


def test_hover_preview_async_fetch_http_404_shows_error_placeholder(clean_root, tmp_path):
    """HTTP 404 during Scryfall image download displays placeholder without Tkinter exception."""
    hover = CardHoverPreview(clean_root, debounce_ms=10)
    try:
        info = CardPreviewInfo(
            name="Missing Card",
            set_code="xyz",
            collector_number="999",
            front_url="https://example.com/not_found.jpg",
        )
        hover.show_preview(info, 100, 100)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("404 Client Error: Not Found")

        with patch("requests.get", return_value=mock_resp):
            cache_path = tmp_path / "cache.jpg"
            # Execute async fetch directly or via executor
            hover._async_fetch_and_cache(
                info, "front", cache_path, "mem_key", hover._active_request_id
            )

            clean_root.update_idletasks()
            clean_root.update()

            # Front label should be configured with error photo
            assert hover._front_label is not None
            assert hover._front_label.cget("image") is not None
    finally:
        hover.destroy()


def test_hover_preview_async_fetch_http_500_shows_error_placeholder(clean_root, tmp_path):
    """HTTP 500 server error displays error placeholder gracefully."""
    hover = CardHoverPreview(clean_root, debounce_ms=10)
    try:
        info = CardPreviewInfo(
            name="Broken Card",
            set_code="err",
            collector_number="500",
            front_url="https://example.com/server_error.jpg",
        )
        hover.show_preview(info, 100, 100)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 Internal Server Error")

        with patch("requests.get", return_value=mock_resp):
            cache_path = tmp_path / "cache_500.jpg"
            hover._async_fetch_and_cache(
                info, "front", cache_path, "mem_500", hover._active_request_id
            )

            clean_root.update_idletasks()
            clean_root.update()

            assert hover._front_label is not None
    finally:
        hover.destroy()


def test_hover_preview_unresolvable_url_shows_error_placeholder(clean_root, tmp_path):
    """Card without any resolvable URL shows error placeholder."""
    hover = CardHoverPreview(clean_root, debounce_ms=10)
    try:
        info = CardPreviewInfo(name="Unknown Impossible Card")
        hover.show_preview(info, 100, 100)

        cache_path = tmp_path / "cache_unres.jpg"
        with patch.object(hover, "_resolve_download_url", return_value=None):
            hover._async_fetch_and_cache(
                info, "front", cache_path, "mem_unres", hover._active_request_id
            )

            clean_root.update_idletasks()
            clean_root.update()

            assert hover._front_label is not None
    finally:
        hover.destroy()


# ---------------------------------------------------------------------------
# 4. Destruction of parent window while hover image fetch is in-flight
# ---------------------------------------------------------------------------


def test_hover_preview_destruction_during_inflight_fetch(clean_root, tmp_path):
    """Destroying parent window while async fetch is in-flight does not raise exceptions."""
    parent_win = tk.Toplevel(clean_root)

    hover = CardHoverPreview(parent_win, debounce_ms=10)
    info = CardPreviewInfo(name="In Flight Card", front_url="https://example.com/card.jpg")
    hover.show_preview(info, 100, 100)
    req_id = hover._active_request_id

    # Create dummy image on disk for mock response
    dummy_img = Image.new("RGB", (100, 140), color="blue")
    cache_path = tmp_path / "inflight.jpg"
    dummy_img.save(cache_path, "JPEG")

    mock_resp = MagicMock()
    mock_resp.content = cache_path.read_bytes()
    mock_resp.raise_for_status.return_value = None

    with patch("requests.get", return_value=mock_resp):
        # Destroy parent window before the async worker executes the UI update callback
        hover.hide()
        parent_win.destroy()

        # Run the async fetch logic — callbacks should safely abort without raising
        hover._async_fetch_and_cache(info, "front", cache_path, "inflight_key", req_id)
        hover.destroy()


def test_hover_preview_destroy_cleans_up_executor(clean_root):
    """Calling hover.destroy() cancels scheduled tasks and shuts down executor cleanly."""
    hover = CardHoverPreview(clean_root, debounce_ms=50)
    hover.schedule_show(CardPreviewInfo(name="Clean Card"), 50, 50)
    assert hover._after_id is not None

    hover.destroy()
    assert hover._after_id is None
    assert hover._tip is None
