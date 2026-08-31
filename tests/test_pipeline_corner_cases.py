"""Corner-case test suite for pipeline execution, cancellation at each stage, and PDF chunking boundaries."""

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from src.cancellation import Cancelled
from src.deck_importer import FetchedDeck
from src.pdf_generator import generate
from src.pipeline import run_deck_url, run_locals_only, run_plan


def _make_dummy_image(path: Path, w: int = 100, h: int = 140, color="blue") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color).save(path)
    return path


class TestPipelineCancellationAtEachStage:
    def test_cancellation_before_pipeline_starts(self, tmp_path):
        """If cancel_event is set before execution, Cancelled is raised immediately."""
        front = _make_dummy_image(tmp_path / "front.jpg")
        back = _make_dummy_image(tmp_path / "back.jpg")

        event = threading.Event()
        event.set()

        with pytest.raises(Cancelled):
            run_locals_only(
                extra_fronts=[front],
                local_cardback=back,
                output_dir=tmp_path / "out",
                base_name="cancelled_early",
                work_dir=tmp_path / "work",
                cancel_event=event,
            )

    def test_cancellation_during_download_stage(self, tmp_path):
        """Setting cancel_event during download stage aborts with Cancelled."""
        front = _make_dummy_image(tmp_path / "front.jpg")
        back = _make_dummy_image(tmp_path / "back.jpg")

        event = threading.Event()

        def mock_download_all(*args, **kwargs):
            cancel_ev = kwargs.get("cancel_event")
            if cancel_ev:
                cancel_ev.set()
            raise Cancelled()

        with patch("src.pipeline.download_all", side_effect=mock_download_all):
            with pytest.raises(Cancelled):
                run_locals_only(
                    extra_fronts=[front],
                    local_cardback=back,
                    output_dir=tmp_path / "out",
                    base_name="cancel_download",
                    work_dir=tmp_path / "work",
                    cancel_event=event,
                )

    def test_cancellation_during_crop_stage(self, tmp_path):
        """Setting cancel_event during parallel cropping stage aborts with Cancelled."""
        front = _make_dummy_image(tmp_path / "front.jpg")
        back = _make_dummy_image(tmp_path / "back.jpg")

        event = threading.Event()

        def mock_crop_parallel(*args, **kwargs):
            event.set()
            raise Cancelled()

        with patch("src.pipeline._run_crop_parallel", side_effect=mock_crop_parallel):
            with pytest.raises(Cancelled):
                run_locals_only(
                    extra_fronts=[front],
                    local_cardback=back,
                    output_dir=tmp_path / "out",
                    base_name="cancel_crop",
                    work_dir=tmp_path / "work",
                    cancel_event=event,
                )

    def test_cancellation_during_pdf_generation_stage(self, tmp_path):
        """Setting cancel_event during PDF generation aborts with Cancelled."""
        front = _make_dummy_image(tmp_path / "front.jpg")
        back = _make_dummy_image(tmp_path / "back.jpg")

        event = threading.Event()

        def mock_generate(*args, **kwargs):
            event.set()
            raise Cancelled()

        with patch("src.pipeline.generate", side_effect=mock_generate):
            with pytest.raises(Cancelled):
                run_locals_only(
                    extra_fronts=[front],
                    local_cardback=back,
                    output_dir=tmp_path / "out",
                    base_name="cancel_pdf",
                    work_dir=tmp_path / "work",
                    cancel_event=event,
                )


class TestZeroCardsHandling:
    def test_run_locals_only_with_zero_fronts_raises_value_error(self, tmp_path):
        back = _make_dummy_image(tmp_path / "back.jpg")
        with pytest.raises(ValueError, match="requires at least one front image"):
            run_locals_only(
                extra_fronts=[],
                local_cardback=back,
                output_dir=tmp_path / "out",
                base_name="empty_locals",
            )

    def test_run_with_no_xml_and_no_locals_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Se requiere al menos un XML o imágenes locales"):
            run_plan(
                jobs=[],
                output_dir=tmp_path / "out",
            )
            # or run_merged with []
            from src.pipeline import run_merged
            run_merged(
                xml_paths=[],
                output_dir=tmp_path / "out",
                base_name="empty",
            )

    def test_run_deck_url_with_empty_deck_raises_value_error(self, tmp_path):
        empty_deck = FetchedDeck(name="Empty Deck", cards=[])
        with patch("src.pipeline.fetch_deck", return_value=empty_deck):
            with pytest.raises(ValueError, match="no contiene cartas"):
                run_deck_url("https://moxfield.com/decks/dummy", tmp_path / "out", tmp_path / "work", "empty")

    def test_generate_pdf_with_zero_slots_returns_empty_list_and_creates_no_file(self, tmp_path):
        out_dir = tmp_path / "out_empty"
        res = generate(
            output_dir=out_dir,
            base_name="empty_test",
            ordered_slots=[],
            front_slot_to_id={},
            back_slot_to_id={},
            id_to_path={},
        )
        assert res == []
        assert list(out_dir.glob("*.pdf")) == []


class TestChunkingBoundaries:
    def _run_gen_test(self, tmp_path, card_count, max_bytes=500_000_000):
        img_path = _make_dummy_image(tmp_path / "img.jpg")
        ordered_slots = list(range(card_count))
        front_map = {i: f"F_{i}" for i in ordered_slots}
        back_map = {i: f"B_{i}" for i in ordered_slots}
        id_to_path = {**{f"F_{i}": img_path for i in ordered_slots}, **{f"B_{i}": img_path for i in ordered_slots}}

        return generate(
            output_dir=tmp_path / f"out_{card_count}",
            base_name=f"test_{card_count}",
            ordered_slots=ordered_slots,
            front_slot_to_id=front_map,
            back_slot_to_id=back_map,
            id_to_path=id_to_path,
            max_bytes=max_bytes,
        )

    def test_boundary_1_card(self, tmp_path):
        """1 card generates 1 PDF with 1 page pair (2 PDF pages: front + back)."""
        outputs = self._run_gen_test(tmp_path, 1)
        assert len(outputs) == 1
        content = outputs[0].read_bytes()
        page_count = content.count(b"/Page\n") + content.count(b"/Page\r")
        assert page_count == 2

    def test_boundary_9_cards_exact_1_page(self, tmp_path):
        """9 cards (exact 1 full page) generates 1 PDF with 2 PDF pages."""
        outputs = self._run_gen_test(tmp_path, 9)
        assert len(outputs) == 1
        content = outputs[0].read_bytes()
        page_count = content.count(b"/Page\n") + content.count(b"/Page\r")
        assert page_count == 2

    def test_boundary_10_cards_spills_to_page_2(self, tmp_path):
        """10 cards generates 1 PDF with 2 page pairs (4 PDF pages: 2 fronts + 2 backs)."""
        outputs = self._run_gen_test(tmp_path, 10)
        assert len(outputs) == 1
        content = outputs[0].read_bytes()
        page_count = content.count(b"/Page\n") + content.count(b"/Page\r")
        assert page_count == 4

    def test_boundary_18_cards_exact_2_pages(self, tmp_path):
        """18 cards (exact 2 full pages) generates 1 PDF with 4 PDF pages."""
        outputs = self._run_gen_test(tmp_path, 18)
        assert len(outputs) == 1
        content = outputs[0].read_bytes()
        page_count = content.count(b"/Page\n") + content.count(b"/Page\r")
        assert page_count == 4

    def test_boundary_19_cards_spills_to_page_3(self, tmp_path):
        """19 cards generates 1 PDF with 3 page pairs (6 PDF pages)."""
        outputs = self._run_gen_test(tmp_path, 19)
        assert len(outputs) == 1
        content = outputs[0].read_bytes()
        page_count = content.count(b"/Page\n") + content.count(b"/Page\r")
        assert page_count == 6

    def test_boundary_chunking_with_small_max_bytes_splits_pages(self, tmp_path):
        """When max_bytes is 1, each 9-card page pair is split into its own chunk PDF."""
        outputs = self._run_gen_test(tmp_path, 19, max_bytes=1)
        # 19 cards = 3 pages = 3 chunk PDFs
        assert len(outputs) == 3
        assert outputs[0].name.endswith("_1.pdf")
        assert outputs[1].name.endswith("_2.pdf")
        assert outputs[2].name.endswith("_3.pdf")
