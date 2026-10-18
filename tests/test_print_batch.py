from pathlib import Path

import pytest
from PIL import Image

from src.deck_importer import DeckCard, MtgUrlDeck
from src.pdf_generator import YUGIOH_LAYOUT
from src.pipeline import PrintJobSpec, PrintPipeline
from src.print_batch import (
    CardSlot,
    PrintBatch,
    build_combined_print_run,
    combine_batches,
    standalone_print_batches,
)
from src.tcg_batches import (
    MagicBatchAdapter,
    count_skipped_tokens,
    create_separator_image,
    write_tokens_markdown,
)


def test_combine_batches_preserves_front_back_pairing():
    front_a, front_b = Path("a.jpg"), Path("b.jpg")
    back_a, back_b = Path("ra.jpg"), Path("rb.jpg")

    fronts, backs, crop_map = combine_batches(
        [
            PrintBatch("first", [front_a], [back_a], {front_a: False}),
            PrintBatch("second", [front_b], [back_b], {front_b: True}),
        ]
    )

    assert fronts == [front_a, front_b]
    assert backs == [back_a, back_b]
    assert crop_map == {front_a: False, front_b: True}


def test_card_slot_roundtrip_on_print_batch_and_job_spec():
    front_a, front_b = Path("a.jpg"), Path("b.jpg")
    back_a = Path("ra.jpg")
    slots = [
        CardSlot(front=front_a, back=back_a, crop=True),
        CardSlot(front=front_b, back=None, crop=False),
    ]

    batch = PrintBatch.from_slots("custom", slots, default_back=back_a)
    assert batch.fronts == [front_a, front_b]
    assert batch.backs == [back_a, None]
    assert batch.crop_map == {front_a: True, front_b: False}
    assert batch.slots == slots

    spec = PrintJobSpec(slots=slots, local_cardback=back_a)
    assert spec.card_slots == slots


def test_combine_batches_rejects_misaligned_pairs():
    with pytest.raises(ValueError, match="emparejado"):
        combine_batches([PrintBatch("invalid", [Path("a.jpg")], [])])


def test_build_combined_print_run_uses_first_available_default_back():
    local = PrintBatch("locales", [Path("local.jpg")], [None], {})
    imported = PrintBatch("importado", [Path("card.jpg")], [Path("back.jpg")], {}, Path("back.jpg"))

    run = build_combined_print_run([local, imported])

    assert run.fronts == [Path("local.jpg"), Path("card.jpg")]
    assert run.backs == [None, Path("back.jpg")]
    assert run.default_back == Path("back.jpg")
    assert run.base_name == "locales_importado"


def test_build_combined_print_run_sanitizes_single_deck_name():
    imported = PrintBatch("magic_url", [Path("card.jpg")], [Path("back.jpg")], {}, Path("back.jpg"))

    run = build_combined_print_run([imported], single_deck_name="My Deck: <Special>")

    assert run.base_name == "My Deck_ _Special_"


def test_separate_batch_is_not_combined_with_standard_run():
    standard = PrintBatch("locales", [Path("local.jpg")], [Path("back.jpg")])
    separate = PrintBatch("yugioh", [Path("ygo.jpg")], [Path("ygo_back.jpg")], separate_pdf=True)

    combined = build_combined_print_run([standard, separate])

    assert combined.fronts == [Path("local.jpg")]
    assert standalone_print_batches([standard, separate]) == [separate]


def test_yugioh_standalone_batch_generates_its_own_pdf_with_real_images(tmp_path):
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    Image.new("RGB", (590, 860), "navy").save(front)
    Image.new("RGB", (3012, 4210), "brown").save(back)
    batch = PrintBatch(
        "yugioh_proxies",
        [front],
        [back],
        {front: False, back: False},
        back,
        YUGIOH_LAYOUT,
        True,
    )

    result = PrintPipeline.execute(
        PrintJobSpec(
            batches=[batch],
            output_dir=tmp_path / "out",
            work_dir=tmp_path / "work",
            fronts_only=False,
        )
    )

    assert len(result.pdf_paths) == 1
    assert result.pdf_paths[0].read_bytes().startswith(b"%PDF")


def test_create_separator_image_and_tokens_markdown(tmp_path):
    sep_path = tmp_path / "separator_0.jpg"
    cards = [
        DeckCard(
            "Atraxa", "2xm", "190", 1, "main", is_commander=True, type_line="Legendary Creature"
        ),
        DeckCard("Command Tower", "c21", "264", 1, "main", type_line="Land"),
        DeckCard("Treasure", "t2xm", "1", 2, "token", layout="token"),
        DeckCard("Copy", "t2xm", "2", 1, "token", layout="double_faced_token"),
        DeckCard("Clue", "t2xm", "3", 3, "token", layout="normal"),
    ]
    create_separator_image("Atraxa Superfriends", "commander", cards, sep_path)
    assert sep_path.exists()
    with Image.open(sep_path) as im:
        assert im.size == (745, 1040)

    deck = MtgUrlDeck(
        url="https://moxfield.com/decks/atraxa",
        cards=cards,
        include_tokens=True,
        name="Atraxa Superfriends",
        include_separator=True,
    )
    assert count_skipped_tokens([deck]) == 3

    md_path = write_tokens_markdown([deck], tmp_path)
    assert md_path is not None and md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "## Mazo: Atraxa Superfriends" in text
    assert "Treasure" in text


def test_magic_batch_adapter_prepares_separator_and_cards(tmp_path):
    front = tmp_path / "card_front.jpg"
    default_back = tmp_path / "mtg_back.jpg"
    Image.new("RGB", (745, 1040), "darkgreen").save(front)
    Image.new("RGB", (745, 1040), "saddlebrown").save(default_back)

    card = DeckCard("Llanowar Elves", "m19", "182", 2, "main", type_line="Creature — Elf Druid")
    deck = MtgUrlDeck(
        url="https://moxfield.com/decks/elves",
        cards=[card],
        name="Elves",
        include_separator=True,
    )

    def fake_downloader(cards_to_dl, scryfall_dir, prog_cb, **kwargs):
        return [(card, front, None)]

    batch = MagicBatchAdapter(
        decks=(deck,),
        work_dir=tmp_path / "work",
        default_back=default_back,
        local_backs=(),
        cancel_event=None,
        report_progress=lambda *_: None,
        scryfall_downloader=fake_downloader,
    ).prepare()

    assert len(batch.fronts) == 3  # 1 separator + 2 Llanowar Elves
    assert batch.fronts[0].name == "separator_0.jpg"
    assert batch.backs == [default_back, default_back, default_back]
