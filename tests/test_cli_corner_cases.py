"""Corner-case test suite for CLI (cli/main.py).

Covers:
- Invalid CLI arguments: --chunk-size 0/-5, --workers 0/-2.
- Empty or non-existent XML input directories (clean exit code, no unhandled traceback).
- Non-XML files in input directory (.txt, .json, .DS_Store).
- --quiet mode suppressing console outputs, --verbose mode enabling detailed debug logging.
- Interruption / keyboard interrupt simulation.
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

import cli.main as cli_main
from tests.conftest import make_rgb_image

# ---------------------------------------------------------------------------
# 1. Invalid CLI arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", ["0", "-5", "-1"])
def test_cli_invalid_chunk_size_exits(tmp_path, capsys, chunk_size):
    """--chunk-size <= 0 should cleanly exit with code 1 and error message on stderr."""
    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--chunk-size={chunk_size}",
            f"--xml-dir={tmp_path / 'xml'}",
            f"--out-dir={tmp_path / 'out'}",
            f"--workdir={tmp_path / 'work'}",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            cli_main.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "--chunk-size" in err


@pytest.mark.parametrize("workers", ["0", "-2", "-10"])
def test_cli_invalid_workers_exits(tmp_path, capsys, workers):
    """--workers <= 0 should cleanly exit with code 1 and error message on stderr."""
    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--workers={workers}",
            f"--xml-dir={tmp_path / 'xml'}",
            f"--out-dir={tmp_path / 'out'}",
            f"--workdir={tmp_path / 'work'}",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            cli_main.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "--workers" in err


# ---------------------------------------------------------------------------
# 2. Empty or non-existent XML input directories
# ---------------------------------------------------------------------------


def test_cli_nonexistent_xml_dir_without_locals_exits(tmp_path, capsys):
    """Non-existent xml directory without --local-fronts exits with code 1 and stderr message."""
    missing_dir = tmp_path / "does_not_exist"
    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={missing_dir}",
            f"--out-dir={tmp_path / 'out'}",
            f"--workdir={tmp_path / 'work'}",
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            cli_main.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "no existe" in err


def test_cli_nonexistent_xml_dir_with_locals_continues(tmp_path, capsys):
    """Non-existent xml directory WITH --local-fronts and --local-cardback proceeds gracefully."""
    front = make_rgb_image(tmp_path / "front.jpg")
    back = make_rgb_image(tmp_path / "back.jpg")
    missing_dir = tmp_path / "does_not_exist"
    out_dir = tmp_path / "out"

    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={missing_dir}",
            f"--out-dir={out_dir}",
            f"--workdir={tmp_path / 'work'}",
            f"--local-fronts={front}",
            f"--local-cardback={back}",
        ],
    ), patch("cli.main.run_locals_only", return_value=[out_dir / "locales.pdf"]):
        # Should not raise SystemExit
        cli_main.main()
        out = capsys.readouterr().out
        assert "Sin XMLs" in out


def test_cli_empty_xml_dir_without_locals_clean_return(tmp_path, capsys):
    """Empty xml directory without locals prints informative message and returns cleanly."""
    empty_dir = tmp_path / "empty_xml"
    empty_dir.mkdir()
    out_dir = tmp_path / "out"

    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={empty_dir}",
            f"--out-dir={out_dir}",
            f"--workdir={tmp_path / 'work'}",
        ],
    ):
        cli_main.main()
        out = capsys.readouterr().out
        assert "No hay archivos .xml" in out


# ---------------------------------------------------------------------------
# 3. Non-XML files in input directory
# ---------------------------------------------------------------------------


def test_cli_ignores_non_xml_files_in_input_directory(tmp_path, capsys):
    """Directory containing .txt, .json, .DS_Store, and 1 valid .xml should only process the .xml."""
    xml_dir = tmp_path / "mixed_input"
    xml_dir.mkdir()
    (xml_dir / "notes.txt").write_text("Hello world", encoding="utf-8")
    (xml_dir / "config.json").write_text("{}", encoding="utf-8")
    (xml_dir / ".DS_Store").write_bytes(b"\x00\x00\x00\x01")
    (xml_dir / "backup.xml.bak").write_text("<order></order>", encoding="utf-8")

    # Valid XML
    valid_xml = xml_dir / "deck.xml"
    valid_xml.write_text(
        """<order><card><name>Black Lotus</name><id>12345</id><slots>1</slots></card></order>""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"

    mock_report = MagicMock()
    mock_report.path = valid_xml
    mock_report.cards = 1
    mock_report.has_blanks = False

    mock_plan = MagicMock()
    mock_plan.jobs = []

    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={xml_dir}",
            f"--out-dir={out_dir}",
            f"--workdir={tmp_path / 'work'}",
            "--yes",
        ],
    ), patch("cli.main.analyze", return_value=[mock_report]), patch(
        "cli.main.plan", return_value=mock_plan
    ), patch(
        "cli.main.collect_drive_ids", return_value=[("12345", "Black Lotus")]
    ), patch(
        "cli.main.check_drive_access", return_value=[]
    ), patch(
        "cli.main.run_plan", return_value=[out_dir / "deck.pdf"]
    ), patch(
        "cli.main.write_manifest", return_value=out_dir / "manifest.json"
    ):
        cli_main.main()
        out = capsys.readouterr().out
        assert "Encontrados 1 XML(s)" in out


def test_cli_directory_with_only_non_xml_files(tmp_path, capsys):
    """Directory with only non-XML files is treated as having no XMLs."""
    xml_dir = tmp_path / "non_xml_only"
    xml_dir.mkdir()
    (xml_dir / "readme.txt").write_text("some text")
    (xml_dir / "data.json").write_text("[]")
    (xml_dir / ".DS_Store").write_bytes(b"data")

    out_dir = tmp_path / "out"

    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={xml_dir}",
            f"--out-dir={out_dir}",
            f"--workdir={tmp_path / 'work'}",
        ],
    ):
        cli_main.main()
        out = capsys.readouterr().out
        assert "No hay archivos .xml" in out


# ---------------------------------------------------------------------------
# 4. --quiet and --verbose modes
# ---------------------------------------------------------------------------


def test_cli_quiet_mode_suppresses_stdout(tmp_path, capsys):
    """--quiet / -q should suppress standard console outputs."""
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    valid_xml = xml_dir / "deck.xml"
    valid_xml.write_text("<order></order>", encoding="utf-8")
    out_dir = tmp_path / "out"

    mock_report = MagicMock()
    mock_report.path = valid_xml
    mock_report.cards = 9
    mock_report.has_blanks = False
    mock_plan = MagicMock()
    mock_plan.jobs = []

    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={xml_dir}",
            f"--out-dir={out_dir}",
            f"--workdir={tmp_path / 'work'}",
            "--quiet",
            "--yes",
        ],
    ), patch("cli.main.analyze", return_value=[mock_report]), patch(
        "cli.main.plan", return_value=mock_plan
    ), patch(
        "cli.main.collect_drive_ids", return_value=[]
    ), patch(
        "cli.main.run_plan", return_value=[out_dir / "deck.pdf"]
    ), patch(
        "cli.main.write_manifest", return_value=None
    ):
        cli_main.main()
        captured = capsys.readouterr()
        # In quiet mode, stdout should be completely empty
        assert captured.out == ""


def test_cli_verbose_mode_enables_debug_stream_logging(tmp_path):
    """--verbose / -v should configure root logging with DEBUG level."""
    log_file = tmp_path / "run.log"
    cli_main._setup_logging(log_file, verbose=True, quiet=False)

    root_logger = logging.getLogger()
    assert root_logger.level == logging.DEBUG
    # Check that a StreamHandler is present
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root_logger.handlers
    )
    assert has_stream_handler


def test_cli_quiet_mode_suppresses_stream_logging(tmp_path):
    """--quiet should not attach a StreamHandler even if verbose is set."""
    log_file = tmp_path / "run.log"
    cli_main._setup_logging(log_file, verbose=True, quiet=True)

    root_logger = logging.getLogger()
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root_logger.handlers
    )
    assert not has_stream_handler


# ---------------------------------------------------------------------------
# 5. KeyboardInterrupt simulation
# ---------------------------------------------------------------------------


def test_cli_keyboard_interrupt_clean_exit(tmp_path, capsys):
    """KeyboardInterrupt during run_plan should exit with code 130 and clean up workdir."""
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    valid_xml = xml_dir / "deck.xml"
    valid_xml.write_text("<order></order>", encoding="utf-8")
    out_dir = tmp_path / "out"
    workdir = tmp_path / "work"

    mock_report = MagicMock()
    mock_report.path = valid_xml
    mock_report.cards = 9
    mock_report.has_blanks = False
    mock_plan = MagicMock()
    mock_plan.jobs = []

    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={xml_dir}",
            f"--out-dir={out_dir}",
            f"--workdir={workdir}",
            "--yes",
        ],
    ), patch("cli.main.analyze", return_value=[mock_report]), patch(
        "cli.main.plan", return_value=mock_plan
    ), patch(
        "cli.main.collect_drive_ids", return_value=[]
    ), patch(
        "cli.main.run_plan", side_effect=KeyboardInterrupt
    ), patch("cli.main._cleanup") as mock_cleanup:
        with pytest.raises(SystemExit) as exc:
            cli_main.main()
        assert exc.value.code == 130
        mock_cleanup.assert_called_once_with(workdir)
        err = capsys.readouterr().err
        assert "cancelado" in err.lower() or "interrumpido" in err.lower()


def test_cli_keyboard_interrupt_in_test_mode_preserves_workdir(tmp_path):
    """KeyboardInterrupt with --test mode does not call _cleanup."""
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    valid_xml = xml_dir / "deck.xml"
    valid_xml.write_text("<order></order>", encoding="utf-8")
    out_dir = tmp_path / "out"
    workdir = tmp_path / "work"

    mock_report = MagicMock()
    mock_report.path = valid_xml
    mock_report.cards = 9
    mock_report.has_blanks = False
    mock_plan = MagicMock()
    mock_plan.jobs = []

    with patch.object(
        sys,
        "argv",
        [
            "cli",
            f"--xml-dir={xml_dir}",
            f"--out-dir={out_dir}",
            f"--workdir={workdir}",
            "--test",
            "--yes",
        ],
    ), patch("cli.main.analyze", return_value=[mock_report]), patch(
        "cli.main.plan", return_value=mock_plan
    ), patch(
        "cli.main.collect_drive_ids", return_value=[]
    ), patch(
        "cli.main.run_plan", side_effect=KeyboardInterrupt
    ), patch("cli.main._cleanup") as mock_cleanup:
        with pytest.raises(SystemExit) as exc:
            cli_main.main()
        assert exc.value.code == 130
        mock_cleanup.assert_not_called()
