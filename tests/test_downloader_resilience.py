"""Corner-case test suite for downloader resilience, backoff, and Google Drive edge cases."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.downloader import (
    DownloadPermissionError,
    DownloadTimeoutError,
    download_image,
)


def _mock_success_response(data=b"\xff\xd8\xff\xe0" + b"\x00" * 32, content_type="image/jpeg"):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status.return_value = None
    resp.content = data
    resp.iter_content.side_effect = lambda chunk_size=None: iter([data])
    return resp


class TestNetworkTimeoutAndConnectionDrops:
    def test_timeout_triggers_exponential_backoff_and_succeeds(self, tmp_path):
        """A transient Timeout on first attempt triggers backoff and succeeds on attempt 2."""
        attempts = []

        def flaky_get(url, **kwargs):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise requests.exceptions.Timeout("Read timeout on socket")
            return _mock_success_response()

        with (
            patch("requests.get", side_effect=flaky_get),
            patch("time.sleep") as mock_sleep,
        ):
            out = download_image("ID_TIMEOUT", tmp_path / "raw", "card.jpg")

        assert out.exists()
        assert len(attempts) == 2
        mock_sleep.assert_called_once_with(1.0)  # _INITIAL_BACKOFF = 1.0

    def test_connection_error_triggers_exponential_backoff_and_succeeds(self, tmp_path):
        """A transient ConnectionError on attempts 1 and 2 triggers backoff (1s, 2s) and succeeds on attempt 3."""
        attempts = []

        def flaky_conn(url, **kwargs):
            attempts.append(len(attempts) + 1)
            if len(attempts) < 3:
                raise requests.exceptions.ConnectionError("Connection dropped by peer")
            return _mock_success_response()

        with (
            patch("requests.get", side_effect=flaky_conn),
            patch("time.sleep") as mock_sleep,
        ):
            out = download_image("ID_CONN_DROP", tmp_path / "raw", "card.jpg")

        assert out.exists()
        assert len(attempts) == 3
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)

    def test_persistent_timeout_raises_download_timeout_error_after_retries(self, tmp_path):
        """If Timeout persists across all retries, raises DownloadTimeoutError."""
        with (
            patch("requests.get", side_effect=requests.exceptions.Timeout("Persistent timeout")),
            patch("time.sleep"),
        ):
            with pytest.raises(DownloadTimeoutError) as exc_info:
                download_image("ID_DEAD", tmp_path / "raw", "card.jpg")

        assert exc_info.value.drive_id == "ID_DEAD"
        assert exc_info.value.card_name == "card.jpg"


class TestGoogleDriveVirusScanWarning:
    def test_virus_scan_warning_page_retries_with_confirm_token(self, tmp_path):
        """When Google Drive returns an HTML virus scan warning, downloader fetches with confirm=t."""
        warning_html = (
            b"<!DOCTYPE html><html><body>"
            b"<h1>Google Drive - Virus scan warning</h1>"
            b"<a id='uc-download-link' href='/uc?export=download&amp;id=LARGE_ID&amp;confirm=t'>"
            b"Download anyway</a></body></html>"
        )
        warning_resp = _mock_success_response(data=warning_html, content_type="text/html; charset=utf-8")
        binary_resp = _mock_success_response(data=b"\xff\xd8\xff\xe0VALID_JPEG_STREAM", content_type="image/jpeg")

        calls = []

        def mock_get(url, **kwargs):
            calls.append(url)
            if "confirm=t" in url:
                return binary_resp
            return warning_resp

        with patch("requests.get", side_effect=mock_get):
            out = download_image("LARGE_ID", tmp_path / "raw", "card.jpg")

        assert out.exists()
        assert out.read_bytes() == b"\xff\xd8\xff\xe0VALID_JPEG_STREAM"
        assert any("confirm=t" in u for u in calls)


class TestPartialDownloadsAndTmpFiles:
    def test_stale_or_interrupted_tmp_file_is_overwritten_cleanly(self, tmp_path):
        """An existing .tmp file left behind by a crash is overwritten without corruption."""
        dest_dir = tmp_path / "raw"
        dest_dir.mkdir()

        # Place a dummy .tmp file
        tmp_leftover = dest_dir / "ID_STALE_1234_5678.tmp"
        tmp_leftover.write_bytes(b"corrupted old junk")

        with patch("requests.get", return_value=_mock_success_response(data=b"NEW_CLEAN_DATA")):
            out = download_image("ID_STALE", dest_dir, "card.jpg")

        assert out.exists()
        assert out.read_bytes() == b"NEW_CLEAN_DATA"

    def test_failed_download_cleans_up_its_tmp_file(self, tmp_path):
        """When download fails with 403, the in-progress .tmp file is removed."""
        dest_dir = tmp_path / "raw"
        dest_dir.mkdir()

        resp_403 = MagicMock()
        resp_403.status_code = 403
        err_403 = requests.HTTPError(response=resp_403)

        with patch("requests.get", side_effect=err_403):
            with pytest.raises(DownloadPermissionError):
                download_image("ID_403_CLEAN", dest_dir, "card.jpg")

        tmp_files = list(dest_dir.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestNonExistentAndForbiddenUrls:
    def test_403_forbidden_raises_download_permission_error(self, tmp_path):
        resp = MagicMock()
        resp.status_code = 403
        with patch("requests.get", side_effect=requests.HTTPError(response=resp)):
            with pytest.raises(DownloadPermissionError) as exc_info:
                download_image("NO_ACCESS_ID", tmp_path / "raw", "private.jpg")
        assert exc_info.value.drive_id == "NO_ACCESS_ID"

    def test_404_not_found_raises_download_permission_error(self, tmp_path):
        resp = MagicMock()
        resp.status_code = 404
        with patch("requests.get", side_effect=requests.HTTPError(response=resp)):
            with pytest.raises(DownloadPermissionError) as exc_info:
                download_image("DELETED_ID", tmp_path / "raw", "deleted.jpg")
        assert exc_info.value.drive_id == "DELETED_ID"
