"""Normalized printable-card inputs and batch combination rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.scraper_utils import sanitize_filename

if TYPE_CHECKING:
    from src.pdf_generator import CardLayout


@dataclass(frozen=True)
class CardSlot:
    """Single printable card slot binding front, optional back, and MPC crop flag."""

    front: Path
    back: Path | None = None
    crop: bool = False


@dataclass
class PrintBatch:
    """Parallel printable cards plus their complete print-destination policy."""

    name: str
    fronts: list[Path] = field(default_factory=list)
    backs: list[Path | None] = field(default_factory=list)
    crop_map: dict[Path, bool] = field(default_factory=dict)
    default_back: Path | None = None
    layout: CardLayout | None = None
    separate_pdf: bool = False

    @classmethod
    def from_slots(
        cls,
        name: str,
        slots: list[CardSlot],
        *,
        default_back: Path | None = None,
        layout: CardLayout | None = None,
        separate_pdf: bool = False,
        extra_crop_map: dict[Path, bool] | None = None,
    ) -> PrintBatch:
        """Create a PrintBatch from paired CardSlot items while keeping parallel lists synced."""
        fronts = [slot.front for slot in slots]
        backs = [slot.back for slot in slots]
        crop_map: dict[Path, bool] = {slot.front: slot.crop for slot in slots}
        if extra_crop_map:
            crop_map.update(extra_crop_map)
        return cls(
            name=name,
            fronts=fronts,
            backs=backs,
            crop_map=crop_map,
            default_back=default_back,
            layout=layout,
            separate_pdf=separate_pdf,
        )

    @property
    def slots(self) -> list[CardSlot]:
        """Return index-aligned CardSlot objects for this batch."""
        return [
            CardSlot(front=front, back=back, crop=self.crop_map.get(front, False))
            for front, back in zip(self.fronts, self.backs, strict=False)
        ]

    def extend_into(
        self, fronts: list[Path], backs: list[Path | None], crop_map: dict[Path, bool]
    ) -> None:
        """Append this batch while keeping the parallel front/back interface aligned."""
        fronts.extend(self.fronts)
        backs.extend(self.backs)
        crop_map.update(self.crop_map)


@dataclass(frozen=True)
class CombinedPrintRun:
    """The standard run assembled from local and imported-card batches."""

    fronts: list[Path]
    backs: list[Path | None]
    crop_map: dict[Path, bool]
    default_back: Path | None
    base_name: str

    @property
    def slots(self) -> list[CardSlot]:
        """Return index-aligned CardSlot objects for the combined run."""
        return [
            CardSlot(front=front, back=back, crop=self.crop_map.get(front, False))
            for front, back in zip(self.fronts, self.backs, strict=False)
        ]


def combine_batches(
    batches: list[PrintBatch],
) -> tuple[list[Path], list[Path | None], dict[Path, bool]]:
    """Combine batches in caller order, preserving front/back index pairing."""
    fronts: list[Path] = []
    backs: list[Path | None] = []
    crop_map: dict[Path, bool] = {}
    for batch in batches:
        batch.extend_into(fronts, backs, crop_map)
    if len(fronts) != len(backs):
        raise ValueError("Cada frontal debe conservar un reverso emparejado.")
    return fronts, backs, crop_map


def build_combined_print_run(
    batches: list[PrintBatch],
    *,
    single_deck_name: str | None = None,
) -> CombinedPrintRun:
    """Combine non-separate batches in input order and derive the default back and PDF name."""
    combined_batches = [batch for batch in batches if not batch.separate_pdf]
    fronts, backs, crop_map = combine_batches(combined_batches)
    active = [batch for batch in combined_batches if batch.fronts]
    default_back = next((batch.default_back for batch in active if batch.default_back), None)
    if single_deck_name is not None:
        base_name = sanitize_filename(single_deck_name) if single_deck_name.strip() else "combinado"
    else:
        base_name = "_".join(batch.name for batch in active) or "combinado"
    return CombinedPrintRun(
        fronts,
        backs,
        crop_map,
        default_back,
        base_name,
    )


def standalone_print_batches(batches: list[PrintBatch]) -> list[PrintBatch]:
    """Return non-empty batches that own a separate PDF destination."""
    return [batch for batch in batches if batch.separate_pdf and batch.fronts]
