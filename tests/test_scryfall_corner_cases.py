"""Corner-case test suite for Scryfall client and downloader integration."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.deck_importer import DeckCard
from src.scryfall import (
    ScryfallCard,
    ScryfallError,
    _extract_images,
    _throttled_get,
    download_card_images,
    download_deck_images,
    fetch_card,
    fetch_card_by_name,
)


def _mock_resp(json_data=None, content=b"IMAGEDATA", status=200, headers=None, text=""):
    resp = MagicMock()
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("No JSON object could be decoded")
    resp.content = content
    resp.text = text or (content.decode("utf-8", errors="ignore") if content else "")
    resp.status_code = status
    resp.headers = headers or {}
    if status >= 400:
        err = requests.HTTPError(response=resp)
        resp.raise_for_status.side_effect = err
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestScryfallCacheAndCorruptFiles:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()
        sf._name_cache.clear()

    def test_corrupt_0_byte_cached_image_refetches(self, tmp_path):
        """If a cached image on disk is 0-bytes, it should re-download from Scryfall."""
        slug_file = tmp_path / "ltr_152_en_large.jpg"
        slug_file.write_bytes(b"")  # 0-byte corrupt file
        assert slug_file.stat().st_size == 0

        card = DeckCard("Lightning Bolt", "ltr", "152", 1, "main")
        mock_scryfall = ScryfallCard("https://cards.scryfall.io/large/front/0/0/bolt.jpg", None)

        with (
            patch("src.scryfall.fetch_card", return_value=mock_scryfall),
            patch(
                "src.scryfall._throttled_get", return_value=_mock_resp(content=b"VALID_IMAGE_DATA")
            ) as mock_get,
            patch("src.scryfall.check_image_quality", return_value=(True, 150.0, (800, 1120))),
        ):
            front, back = download_card_images(card, tmp_path, quality_check=True)

        assert mock_get.call_count == 1
        assert front.exists()
        assert front.read_bytes() == b"VALID_IMAGE_DATA"
        assert back is None

    def test_corrupt_json_response_raises_scryfall_error(self):
        """Malformed JSON response from Scryfall API raises ScryfallError without unhandled crash."""
        bad_resp = _mock_resp(json_data=None, status=200, content=b"<html>502 Bad Gateway</html>")
        with patch("src.scryfall._throttled_get", return_value=bad_resp):
            with pytest.raises(ScryfallError, match="Error de conexión con Scryfall"):
                fetch_card("m21", "1")

    def test_fetch_card_by_name_corrupt_json_raises_scryfall_error(self):
        bad_resp = _mock_resp(json_data=None, status=200, content=b"invalid json")
        with patch("src.scryfall._throttled_get", return_value=bad_resp):
            with pytest.raises(ScryfallError, match="Error de conexión con Scryfall"):
                fetch_card_by_name("Black Lotus")


class TestScryfallRateLimitingAndRetries:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_http_429_with_numeric_retry_after_header(self):
        """429 with Retry-After header backs off and succeeds on next attempt."""
        resp_429 = _mock_resp(status=429, headers={"Retry-After": "0.05"})
        resp_200 = _mock_resp(
            json_data={"image_uris": {"large": "https://example.com/ok.jpg"}}, status=200
        )

        with (
            patch("requests.get", side_effect=[resp_429, resp_200]) as mock_req,
            patch("time.sleep") as mock_sleep,
        ):
            resp = _throttled_get("https://api.scryfall.com/test")

        assert resp.status_code == 200
        assert mock_req.call_count == 2
        mock_sleep.assert_any_call(0.05)

    def test_http_429_with_invalid_retry_after_falls_back_to_exponential(self):
        """429 with malformed Retry-After header falls back to default backoff."""
        resp_429 = _mock_resp(status=429, headers={"Retry-After": "not-a-number"})
        resp_200 = _mock_resp(
            json_data={"image_uris": {"large": "https://example.com/ok.jpg"}}, status=200
        )

        with (
            patch("requests.get", side_effect=[resp_429, resp_200]) as mock_req,
            patch("time.sleep") as mock_sleep,
        ):
            resp = _throttled_get("https://api.scryfall.com/test")

        assert resp.status_code == 200
        assert mock_req.call_count == 2
        mock_sleep.assert_any_call(1.0)  # 2**0 = 1.0

    def test_http_429_exhausted_raises_scryfall_error(self):
        """If 429 persists across all retry attempts, raise ScryfallError."""
        resp_429 = _mock_resp(status=429, headers={"Retry-After": "0.01"})

        with (
            patch("requests.get", return_value=resp_429),
            patch("time.sleep"),
        ):
            with pytest.raises(ScryfallError):
                fetch_card("m21", "1")


class TestDigitalOnlyAndMissingFaceData:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_digital_card_without_image_uris_or_faces_raises_scryfall_error(self):
        """MTGA / digital cards with no root image_uris or card_faces cleanly raise ScryfallError."""
        digital_json = {
            "name": "A-Omnath",
            "set": "znr",
            "collector_number": "A-244",
            "digital": True,
            "image_uris": None,
            "card_faces": None,
        }
        with pytest.raises(ScryfallError, match="Formato de respuesta inesperado"):
            _extract_images(digital_json, "large")

    def test_empty_card_faces_raises_scryfall_error(self):
        """Empty card_faces list raises ScryfallError without IndexError."""
        empty_faces_json = {
            "name": "Empty Face Card",
            "card_faces": [],
        }
        with pytest.raises(ScryfallError, match="Formato de respuesta inesperado"):
            _extract_images(empty_faces_json, "large")

    def test_card_faces_with_missing_image_uris_raises_scryfall_error(self):
        """Card face without image_uris raises ScryfallError without AttributeError."""
        broken_face_json = {
            "name": "Broken Face",
            "card_faces": [
                {"name": "Front Face", "image_uris": None},
            ],
        }
        with pytest.raises(ScryfallError, match="No se encontró imagen de frente en card_faces"):
            _extract_images(broken_face_json, "large")

    def test_adventure_or_split_card_with_root_images_and_face_descriptions(self):
        """Adventure and Split cards with root image_uris and face descriptions extract correctly."""
        adventure_json = {
            "name": "Brazen Borrower // Petty Theft",
            "layout": "adventure",
            "oracle_id": "uuid-adventure",
            "image_uris": {
                "large": "https://cards.scryfall.io/large/front/b/r/brazen.jpg",
                "png": "https://cards.scryfall.io/png/front/b/r/brazen.png",
            },
            "card_faces": [
                {"name": "Brazen Borrower", "type_line": "Creature — Faerie Rogue"},
                {"name": "Petty Theft", "type_line": "Instant — Adventure"},
            ],
        }
        card = _extract_images(adventure_json, "large")
        assert card.front_url == "https://cards.scryfall.io/large/front/b/r/brazen.jpg"
        assert card.back_url is None
        assert card.layout == "adventure"

    def test_mdfc_transform_with_missing_back_face_uris(self):
        """MDFC card where back face has no image_uris returns front_url and None for back_url."""
        partial_mdfc = {
            "name": "Partial MDFC",
            "layout": "transform",
            "oracle_id": "uuid-mdfc",
            "card_faces": [
                {
                    "name": "Front",
                    "image_uris": {"large": "https://cards.scryfall.io/large/front.jpg"},
                },
                {"name": "Back", "image_uris": None},
            ],
        }
        card = _extract_images(partial_mdfc, "large")
        assert card.front_url == "https://cards.scryfall.io/large/front.jpg"
        assert card.back_url is None


class TestBulkDeckDownloadsMixedResponses:
    def setup_method(self):
        import src.scryfall as sf

        sf._cache.clear()

    def test_download_deck_images_with_mixed_success_and_failure(self, tmp_path):
        """Bulk download fails with ScryfallError when one of the cards fails permanently."""
        cards = [
            DeckCard("Good Card 1", "m21", "1", 1, "main"),
            DeckCard("404 Card", "badset", "999", 1, "main"),
            DeckCard("Good Card 2", "m21", "2", 1, "main"),
        ]

        def mock_download(card, dest, *args, **kwargs):
            if "404" in card.name:
                raise ScryfallError("Carta no encontrada en Scryfall: badset/999")
            p = dest / f"{card.set_code}_{card.collector_number}.jpg"
            p.write_bytes(b"DATA")
            return p, None

        with patch("src.scryfall.download_card_images", side_effect=mock_download):
            with pytest.raises(ScryfallError, match="Carta no encontrada"):
                download_deck_images(cards, tmp_path)

    def test_throttled_get_connection_error_and_timeout_exhaustion(self):
        with patch("requests.get", side_effect=requests.ConnectionError("Connection lost")):
            with patch("time.sleep"):
                with pytest.raises(requests.ConnectionError):
                    _throttled_get("https://api.scryfall.com/fail")

    def test_extract_images_missing_front_in_dict_raises(self):
        with pytest.raises(ScryfallError, match="No se encontró imagen de frente"):
            _extract_images({"image_uris": {"small": ""}}, "large")

    def test_fetch_card_language_and_english_both_fail_raises(self):
        resp_404 = _mock_resp(status=404)
        with patch("src.scryfall._throttled_get", return_value=resp_404):
            with pytest.raises(ScryfallError, match="Error al descargar versión en inglés"):
                fetch_card("m11", "149", lang="es")

    def test_download_deck_images_cancel_and_progress_callback(self, tmp_path):
        import threading

        from src.cancellation import Cancelled

        cards = [DeckCard("Card 1", "m21", "1", 1, "main")]
        cancel_ev = threading.Event()
        cancel_ev.set()

        with patch("src.scryfall.download_card_images", return_value=(tmp_path / "1.jpg", None)):
            with pytest.raises(Cancelled):
                download_deck_images(cards, tmp_path, cancel_event=cancel_ev)

        progress_mock = MagicMock()
        with patch("src.scryfall.download_card_images", return_value=(tmp_path / "1.jpg", None)):
            res = download_deck_images(cards, tmp_path, progress_cb=progress_mock)
            assert len(res) == 1
            progress_mock.assert_called_once_with(1, 1)

    def test_download_card_images_quality_fallback_failure_handling(self, tmp_path):
        card = DeckCard("Spanish Card", "m21", "1", 1, "main")
        fake_scryfall = ScryfallCard("https://cards.scryfall.io/large/front/0/0/front.jpg", None)

        with (
            patch(
                "src.scryfall.fetch_card",
                side_effect=[fake_scryfall, Exception("English fallback failed")],
            ),
            patch("src.scryfall._throttled_get", return_value=_mock_resp(content=b"IMG")),
            patch("src.scryfall.check_image_quality", return_value=(False, 10.0, (100, 100))),
        ):
            f, b = download_card_images(card, tmp_path, lang="es", quality_check=True)
            assert f.exists()
            assert b is None
