"""Tests for src/scraper_utils.py."""

import pytest
from PIL import Image

from src.scraper_utils import generate_fallback_back, sanitize_filename


class TestSanitizeFilename:
    def test_basic_name(self):
        assert sanitize_filename("My Deck") == "My Deck"

    def test_removes_invalid_characters(self):
        assert sanitize_filename('Deck: /\\*?"<>| Name') == "Deck_ ________ Name"

    def test_strips_leading_trailing_spaces_and_dots(self):
        assert sanitize_filename(" . My Deck . ") == "My Deck"

    def test_empty_fallback(self):
        assert sanitize_filename("::???::") == "deck"


class TestGenerateFallbackBack:
    def test_creates_file_at_path(self, tmp_path):
        path = tmp_path / "back.png"
        generate_fallback_back(path, "#0A1628", "#B0B8C8")
        assert path.exists()

    def test_returns_the_path(self, tmp_path):
        path = tmp_path / "back.png"
        result = generate_fallback_back(path, "#0A1628", "#B0B8C8")
        assert result == path

    def test_default_size(self, tmp_path):
        path = tmp_path / "back.png"
        generate_fallback_back(path, "#000000", "#FFFFFF")
        img = Image.open(path)
        assert img.size == (480, 670)

    def test_custom_size(self, tmp_path):
        path = tmp_path / "back.png"
        generate_fallback_back(path, "#000000", "#FFFFFF", size=(100, 140))
        img = Image.open(path)
        assert img.size == (100, 140)

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "back.png"
        generate_fallback_back(path, "#123456", "#ABCDEF")
        assert path.exists()

    def test_image_is_rgb(self, tmp_path):
        path = tmp_path / "back.png"
        generate_fallback_back(path, "#FF0000", "#00FF00")
        img = Image.open(path)
        assert img.mode == "RGB"

    def test_background_color_applied(self, tmp_path):
        path = tmp_path / "back.png"
        generate_fallback_back(path, "#FF0000", "#000000", size=(50, 70))
        img = Image.open(path)
        cx, cy = img.size[0] // 2, img.size[1] // 2
        r, g, b = img.getpixel((cx, cy))
        assert r > 200
        assert g < 50
        assert b < 50


class TestScraperCache:
    def test_set_and_get(self):
        from src.scraper_utils import ScraperCache

        cache = ScraperCache()
        cache.set("key1", {"data": 123})
        assert cache.get("key1") == {"data": 123}
        assert cache.get("key2") is None

    def test_get_or_set(self):
        from src.scraper_utils import ScraperCache

        cache = ScraperCache()
        calls = 0

        def factory():
            nonlocal calls
            calls += 1
            return "computed_val"

        res1 = cache.get_or_set("k", factory)
        res2 = cache.get_or_set("k", factory)
        assert res1 == "computed_val"
        assert res2 == "computed_val"
        assert calls == 1

    def test_invalidate_and_clear(self):
        from src.scraper_utils import ScraperCache

        cache = ScraperCache()
        cache.set("k1", 1)
        cache.set("k2", 2)
        cache.invalidate("k1")
        assert cache.get("k1") is None
        assert cache.get("k2") == 2
        cache.clear()
        assert cache.get("k2") is None


class TestFetchUtilities:
    def test_fetch_json_success(self):
        from unittest.mock import MagicMock, patch

        from src.scraper_utils import fetch_json

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"hello": "world"}
        mock_resp.raise_for_status.return_value = None

        with patch("requests.request", return_value=mock_resp):
            data = fetch_json("https://example.com/api")
        assert data == {"hello": "world"}

    def test_fetch_text_success(self):
        from unittest.mock import MagicMock, patch

        from src.scraper_utils import fetch_text

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "raw text data"
        mock_resp.raise_for_status.return_value = None

        with patch("requests.request", return_value=mock_resp):
            text = fetch_text("https://example.com/file.txt")
        assert text == "raw text data"

    def test_fetch_url_retries_on_503(self):
        from unittest.mock import MagicMock, patch

        from src.scraper_utils import fetch_url

        resp_503 = MagicMock()
        resp_503.status_code = 503
        resp_503.headers = {}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.raise_for_status.return_value = None

        with patch("requests.request", side_effect=[resp_503, resp_200]):
            with patch("time.sleep") as mock_sleep:
                resp = fetch_url("https://example.com/retry", retries=2, backoff_factor=0.01)
                assert resp.status_code == 200
                assert mock_sleep.called

    def test_cache_ttl_expiry(self):
        from unittest.mock import patch

        from src.scraper_utils import ScraperCache

        cache = ScraperCache(default_ttl=10.0)
        with patch("time.monotonic", side_effect=[100.0, 105.0, 115.0]):
            cache.set("k", "val")  # expiry at 110.0
            assert cache.get("k") == "val"  # time=105 <= 110
            assert cache.get("k") is None  # time=115 > 110 (expired and removed)

    def test_get_global_cache(self):
        from src.scraper_utils import ScraperCache, get_global_cache

        gc = get_global_cache()
        assert isinstance(gc, ScraperCache)

    def test_fetch_url_retry_after_header(self):
        from unittest.mock import MagicMock, patch

        from src.scraper_utils import fetch_url

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "2"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.raise_for_status.return_value = None

        with patch("requests.request", side_effect=[resp_429, resp_200]):
            with patch("time.sleep") as mock_sleep:
                resp = fetch_url("https://example.com/rate-limited", retries=1)
                assert resp.status_code == 200
                mock_sleep.assert_called_once_with(2.0)

    def test_fetch_url_connection_error_and_timeout_exhaustion(self):
        from unittest.mock import patch

        import requests

        from src.scraper_utils import fetch_url

        with patch("requests.request", side_effect=requests.ConnectionError("Failed connection")):
            with patch("time.sleep"):
                with pytest.raises(requests.ConnectionError):
                    fetch_url("https://example.com/conn-fail", retries=1, backoff_factor=0.001)

        with patch("requests.request", side_effect=requests.Timeout("Timed out")):
            with patch("time.sleep"):
                with pytest.raises(requests.Timeout):
                    fetch_url("https://example.com/timeout-fail", retries=1, backoff_factor=0.001)

    def test_fetch_url_http_error_non_retryable_and_retryable(self):
        from unittest.mock import MagicMock, patch

        import requests

        from src.scraper_utils import fetch_url

        # 404 is not retryable -> raises HTTPError immediately
        resp_404 = MagicMock()
        resp_404.status_code = 404
        err_404 = requests.HTTPError("Not found", response=resp_404)
        resp_404.raise_for_status.side_effect = err_404

        with patch("requests.request", return_value=resp_404):
            with pytest.raises(requests.HTTPError):
                fetch_url("https://example.com/not-found", retries=2)

        # 500 is retryable -> retries then raises
        resp_500 = MagicMock()
        resp_500.status_code = 500
        err_500 = requests.HTTPError("Server error", response=resp_500)
        resp_500.raise_for_status.side_effect = err_500

        with patch("requests.request", side_effect=err_500):
            with patch("time.sleep"):
                with pytest.raises(requests.HTTPError):
                    fetch_url("https://example.com/server-error", retries=1, backoff_factor=0.001)

    def test_resources_dir_frozen_and_dev(self, tmp_path):
        import sys
        from pathlib import Path
        from unittest.mock import patch

        from src.scraper_utils import resources_dir

        # Dev mode
        res_dev = resources_dir()
        assert isinstance(res_dev, Path)

        # Frozen mode (.exe)
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", str(tmp_path), create=True),
        ):
            res_frozen = resources_dir()
            assert res_frozen == tmp_path / "resources"
