"""Tests for src/scraper_utils.py."""

from PIL import Image

from src.scraper_utils import generate_fallback_back, sanitize_filename


class TestSanitizeFilename:
    def test_basic_name(self):
        assert sanitize_filename("My Deck") == "My Deck"

    def test_removes_invalid_characters(self):
        assert sanitize_filename("Deck: /\\*?\"<>| Name") == "Deck_ ________ Name"

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

