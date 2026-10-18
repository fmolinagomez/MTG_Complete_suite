"""ManaBox CSV parser and import adapter."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from src.inventory.models import InventoryCard

FOIL_VALUES = frozenset({"foil", "etched", "foil_etched", "true", "1", "yes"})


def _normalize_header(header: str) -> str:
    """Normalize CSV header strings for fuzzy mapping."""
    return header.strip("\ufeff \t\r\n").lower().replace("_", " ").replace("-", " ")


def _read_text_with_fallback(path: Path) -> str:
    """Read text from a file path trying UTF-8/BOM, CP1252, and Latin-1 encodings."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin-1", errors="replace")


def parse_manabox_csv(source: Path | str | io.TextIOBase) -> list[InventoryCard]:
    """Parse a ManaBox CSV export into a list of InventoryCard objects."""
    content: str
    if isinstance(source, Path):
        content = _read_text_with_fallback(source)
    elif isinstance(source, str):
        if source.strip() and not ("\n" in source or "," in source) and Path(source).is_file():
            content = _read_text_with_fallback(Path(source))
        else:
            content = source
    else:
        content = source.read()

    if not content.strip():
        return []

    reader = csv.reader(io.StringIO(content))
    try:
        raw_headers = next(reader)
    except StopIteration:
        return []

    header_map: dict[str, int] = {}
    for idx, h in enumerate(raw_headers):
        norm = _normalize_header(h)
        header_map[norm] = idx

    def get_field(row: list[str], *aliases: str, default: str = "") -> str:
        for alias in aliases:
            norm = _normalize_header(alias)
            if norm in header_map and header_map[norm] < len(row):
                val = row[header_map[norm]].strip()
                if val:
                    return val
        return default

    cards: list[InventoryCard] = []
    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue

        name = get_field(row, "Name", "Card Name", "Card", "Cardname")
        if not name:
            continue

        set_code = get_field(row, "Set code", "Set", "Expansion", "Edition", "Set Code").lower()
        collector_number = get_field(
            row,
            "Collector number",
            "Number",
            "Card Number",
            "Collector Number",
            "Collector #",
            "Card #",
            "Collector No",
            "Collector No.",
        )
        raw_qty = get_field(row, "Quantity", "Count", "Qty", "Quantity Owned", default="1")
        try:
            quantity = int(raw_qty)
            if quantity <= 0:
                continue
        except ValueError:
            quantity = 1

        raw_foil = get_field(
            row, "Foil", "Foil / Etched", "Finish", "Is Foil", "Foil/Etched", default=""
        )
        is_foil = raw_foil.lower() in FOIL_VALUES

        condition = get_field(row, "Condition", "Card Condition", default="NM")
        language = get_field(row, "Language", "Lang", default="en").lower()

        scryfall_id = get_field(row, "Scryfall ID", "Scryfall Id", "Scryfall", "scryfall_id")
        oracle_id = get_field(row, "Oracle ID", "Oracle Id", "oracle_id", default=scryfall_id)

        rarity = get_field(row, "Rarity", default="")
        binder_name = get_field(row, "Binder Name", "Binder", default="")
        purchase_price = get_field(row, "Purchase price", "Price", default="")
        altered = get_field(row, "Altered", default="")
        misprint = get_field(row, "Misprint", default="")

        metadata: dict[str, Any] = {}
        if scryfall_id:
            metadata["scryfall_id"] = scryfall_id
        if rarity:
            metadata["rarity"] = rarity
        if binder_name:
            metadata["binder_name"] = binder_name
        if purchase_price:
            metadata["purchase_price"] = purchase_price
        if altered.lower() in ("true", "1", "yes"):
            metadata["altered"] = True
        if misprint.lower() in ("true", "1", "yes"):
            metadata["misprint"] = True

        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else "{}"

        cards.append(
            InventoryCard(
                oracle_id=oracle_id,
                name=name,
                set_code=set_code,
                collector_number=collector_number,
                quantity=quantity,
                is_foil=is_foil,
                is_proxy=False,
                condition=condition,
                language=language,
                metadata_json=metadata_json,
            )
        )

    return cards


def import_parsed_cards(
    cards: list[InventoryCard],
    db_path: Path | str | None = None,
    mode: str = "merge",
) -> int:
    """Import parsed cards into the inventory database using InventoryService."""
    from src.inventory.service import InventoryService

    return InventoryService(db_path=db_path).import_cards(cards, mode=mode)
