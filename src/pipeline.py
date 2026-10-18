import hashlib
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from src.app_settings import (
    DEFAULT_CUT_LINE_COLOR,
    DEFAULT_CUT_LINE_OVER_BACKS,
    DEFAULT_CUT_LINE_OVER_CARDS,
    DEFAULT_CUT_LINE_OVER_FRONTS,
    DEFAULT_CUT_LINE_STYLE,
    DEFAULT_CUT_LINE_WIDTH,
)
from src.cache_key import crop_cache_path
from src.cancellation import Cancelled
from src.constants import JobPdfStartCallback, SpeedCallback, Stage, StageCallback
from src.cropper import process_for_pdf
from src.deck_importer import fetch_deck
from src.downloader import (
    DownloadPartialError,
    DownloadPermissionError,
    DownloadTimeoutError,
    download_all,
)
from src.parser import CardOrder, parse
from src.pdf_generator import MAX_PDF_BYTES, STANDARD_LAYOUT, CardLayout, generate
from src.precheck import check_drive_access, collect_drive_ids, write_manifest
from src.print_batch import (
    CardSlot,
    PrintBatch,
    build_combined_print_run,
    standalone_print_batches,
)
from src.scraper_utils import resources_dir, sanitize_filename
from src.scryfall import ScryfallError, download_deck_images
from src.tcg_batches import (
    LorcanaBatchAdapter,
    MagicBatchAdapter,
    OnePieceBatchAdapter,
    RiftboundBatchAdapter,
    YugiohBatchAdapter,
    count_skipped_tokens,
    create_separator_image,
    write_tokens_markdown,
)

CROP_THREADS = 5
_log = logging.getLogger(__name__)


@dataclass
class PrintJobSpec:
    """Specification defining a complete print job or batch of print jobs."""

    # Sources
    xml_paths: list[str | Path] = field(default_factory=list)
    jobs: list[Any] = field(default_factory=list)
    plan: Any = None
    reports: list[Any] | None = None
    slots: list[CardSlot] = field(default_factory=list)
    batches: list[PrintBatch] = field(default_factory=list)
    extra_fronts: list[str | Path] = field(default_factory=list)
    extra_backs: list[str | Path | None] = field(default_factory=list)
    local_backs: list[str | Path] = field(default_factory=list)
    local_cardback: str | Path | None = None
    deck_url: str | None = None

    # Multi-TCG Deck Sources
    op_decks: list[Any] = field(default_factory=list)
    rb_decks: list[tuple[Any, bool]] = field(default_factory=list)
    lorcana_decks: list[Any] = field(default_factory=list)
    mtg_decks: list[Any] = field(default_factory=list)
    ygo_decks: list[tuple[Any, bool]] = field(default_factory=list)

    # Deck URL & Scryfall options
    include_sideboard: bool = False
    deck_lang: str = "en"
    deck_quality: str = "large"
    deck_fail_policy: str = "english"
    deck_quality_check: bool = True
    deck_blur_threshold: float = 100.0
    deck_brightness_contrast_check: bool = True
    deck_minimize_tokens: bool = False

    # Output & Working Directories
    output_dir: str | Path = Path(".")
    work_dir: str | Path = Path("workdir")
    base_name: str = "deck"

    # Bleed & Crop options
    local_crop_map: dict[Path, bool] = field(default_factory=dict)
    crop_threads: int = CROP_THREADS

    # Layout & Cut line options
    fronts_only: bool = False
    cut_line_color: str = DEFAULT_CUT_LINE_COLOR
    cut_line_style: str = DEFAULT_CUT_LINE_STYLE
    cut_line_width: float = DEFAULT_CUT_LINE_WIDTH
    cut_line_over_cards: bool = DEFAULT_CUT_LINE_OVER_CARDS
    cut_line_over_fronts: bool = DEFAULT_CUT_LINE_OVER_FRONTS
    cut_line_over_backs: bool = DEFAULT_CUT_LINE_OVER_BACKS
    layout: CardLayout = STANDARD_LAYOUT
    max_pdf_bytes: int = MAX_PDF_BYTES

    # Drive verification & Progress Callbacks
    verify_drive_access: bool = False
    on_verify_warning: Callable[[list[tuple[str, str]]], bool] | None = None
    on_stage_progress: Callable[[str, int, int, str], None] | None = None
    cancel_event: Event | None = None
    on_job_pdf_start: JobPdfStartCallback = None
    on_xml_download_progress: StageCallback = None
    on_xml_crop_progress: StageCallback = None
    on_speed_update: SpeedCallback = None

    # Optional dependency hooks for adapters/runners
    scryfall_downloader: Callable | None = None
    separator_creator: Callable | None = None
    mtg_default_back: Path | None = None
    locals_runner: Callable | None = None
    plan_runner: Callable | None = None

    @property
    def card_slots(self) -> list[CardSlot]:
        """Return index-aligned CardSlot objects for extra local/imported cards."""
        if self.slots:
            return list(self.slots)
        fronts = [Path(p) for p in self.extra_fronts]
        backs = [Path(p) if p is not None else None for p in self.extra_backs]
        if len(backs) < len(fronts):
            backs.extend([None] * (len(fronts) - len(backs)))
        crop_lookup = {Path(k): bool(v) for k, v in self.local_crop_map.items()}
        return [
            CardSlot(front=f, back=b, crop=crop_lookup.get(f, False))
            for f, b in zip(fronts, backs, strict=False)
        ]


@dataclass
class PrintJobResult:
    """Result of executing a PrintJobSpec through PrintPipeline."""

    pdf_paths: list[Path] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)
    manifest_path: Path | None = None
    tokens_markdown_path: Path | None = None
    skipped_tokens_count: int = 0

    def __iter__(self):
        return iter(self.pdf_paths)

    def __len__(self) -> int:
        return len(self.pdf_paths)

    def __getitem__(self, index: int) -> Path:
        return self.pdf_paths[index]


@dataclass
class _PlanJob:
    xml_paths: list[Path]
    base_name: str


@dataclass
class _JobData:
    base_name: str
    ordered_slots: list[int]
    front_slot_to_id: dict[int, str]
    back_slot_to_id: dict[int, str]
    local_id_to_path: dict[str, Path]
    id_name_map: dict[str, str]
    drive_id_context: dict[str, tuple[str, int]]
    xml_needed_ids: dict[str, set[str]]
    fallback_base_name: str | None = None


def _check_cancel(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()


def _build_crop_tasks(
    id_to_raw: dict[str, Path],
    bled_dir: Path,
    local_id_to_path: dict[str, Path],
    crop_map: dict[Path, bool],
    layout: CardLayout = STANDARD_LAYOUT,
) -> list[tuple[str, Path, Path, bool]]:
    """Return (drive_id, raw_path, bled_path, crop_borders) for each image."""
    tasks = []
    for drive_id, raw_path in id_to_raw.items():
        if drive_id in local_id_to_path:
            local_path = local_id_to_path[drive_id]
            crop_borders = crop_map.get(local_path, False)
        else:
            crop_borders = True
        bled_path = crop_cache_path(bled_dir, drive_id, raw_path, crop_borders, layout.trim_size_mm)
        tasks.append((drive_id, raw_path, bled_path, crop_borders))
    return tasks


def _run_crop_parallel(
    tasks: list[tuple[str, Path, Path, bool]],
    cancel_event: Event | None,
    on_done: StageCallback = None,
    layout: CardLayout = STANDARD_LAYOUT,
) -> dict[str, Path]:
    """Crop images in parallel using CROP_THREADS workers."""
    total = len(tasks)
    id_to_bled: dict[str, Path] = {}
    done = 0
    if not tasks:
        return id_to_bled

    with ThreadPoolExecutor(max_workers=CROP_THREADS) as executor:
        futures = {
            executor.submit(process_for_pdf, raw, bled, crop, *layout.trim_size_mm): did
            for did, raw, bled, crop in tasks
        }
        for future in as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                for f in futures:
                    f.cancel()
                raise Cancelled()
            drive_id = futures[future]
            id_to_bled[drive_id] = future.result()
            done += 1
            if on_done:
                on_done(drive_id, done, total)

    return id_to_bled


def _local_synthetic_id(path: Path) -> str:
    """Stable synthetic 'drive ID' for a local file, derived from its absolute
    path. The hash keeps it short, filesystem-safe, and cache-friendly across
    re-runs of the same file."""
    h = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"local_{h}"


def _build_slot_maps(
    xml_paths: list[Path],
    orders: list[CardOrder],
    next_slot: int,
) -> tuple[
    dict[int, str],
    dict[int, str],
    dict[str, str],
    dict[str, tuple[str, int]],
    dict[str, set[str]],
    int,
]:
    """Build slot→driveID maps for a list of parsed XMLs.

    Returns (front_slot_to_id, back_slot_to_id, id_name_map,
             drive_id_context, xml_needed_ids, updated_next_slot).
    Callers that don't need xml_needed_ids can discard it with _.
    """
    front_slot_to_id: dict[int, str] = {}
    back_slot_to_id: dict[int, str] = {}
    id_name_map: dict[str, str] = {}
    drive_id_context: dict[str, tuple[str, int]] = {}
    xml_needed_ids: dict[str, set[str]] = {}

    for path, order in zip(xml_paths, orders):
        xml_name = path.name
        entries = sorted(
            (
                (c.name.casefold(), orig_slot, c.drive_id)
                for c in order.fronts
                for orig_slot in c.slots
            ),
            key=lambda e: (e[0], e[1]),
        )
        needed: set[str] = set()
        for _, orig_slot, fid in entries:
            ns = next_slot
            next_slot += 1
            bid = order.back_for_slot(orig_slot)
            front_slot_to_id[ns] = fid
            back_slot_to_id[ns] = bid
            needed.add(fid)
            needed.add(bid)
            if fid not in drive_id_context:
                drive_id_context[fid] = (xml_name, ns + 1)
            if bid not in drive_id_context:
                drive_id_context[bid] = (xml_name, ns + 1)
        needed.add(order.cardback_id)
        xml_needed_ids[xml_name] = needed
        for card in order.fronts + order.backs:
            id_name_map[card.drive_id] = card.name
        id_name_map[order.cardback_id] = "cardback.jpg"
        if order.cardback_id not in drive_id_context:
            drive_id_context[order.cardback_id] = (xml_name, 0)

    return (
        front_slot_to_id,
        back_slot_to_id,
        id_name_map,
        drive_id_context,
        xml_needed_ids,
        next_slot,
    )


def _build_job_data(
    xml_paths: list[Path],
    base_name: str,
    fallback_base_name: str | None = None,
    next_slot: int = 0,
    extra_fronts: list[Path] | None = None,
    extra_backs: list[Path | None] | None = None,
    local_cardback_path: Path | None = None,
) -> tuple[_JobData, int]:
    """Parse XMLs and build all slot maps for one job.
    Returns (job_data, updated next_slot). Does not download or crop anything."""
    orders = [parse(p) for p in xml_paths]
    extra_fronts = extra_fronts or []
    extra_backs = extra_backs or []

    local_id_to_path: dict[str, Path] = {}
    front_slot_to_id, back_slot_to_id, id_name_map, drive_id_context, xml_needed_ids, next_slot = (
        _build_slot_maps(xml_paths, orders, next_slot)
    )

    if local_cardback_path is not None:
        fallback_cardback_id = _local_synthetic_id(local_cardback_path)
        local_id_to_path[fallback_cardback_id] = local_cardback_path
    elif orders:
        fallback_cardback_id = orders[0].cardback_id
    else:
        fallback_cardback_id = ""

    for i, fp in enumerate(extra_fronts):
        sid = _local_synthetic_id(fp)
        local_id_to_path[sid] = fp
        ns = next_slot
        next_slot += 1
        front_slot_to_id[ns] = sid
        bp = extra_backs[i] if i < len(extra_backs) else None
        if bp is not None:
            bsid = _local_synthetic_id(bp)
            local_id_to_path[bsid] = bp
            back_slot_to_id[ns] = bsid
        else:
            back_slot_to_id[ns] = fallback_cardback_id

    return _JobData(
        base_name=base_name,
        fallback_base_name=fallback_base_name,
        ordered_slots=sorted(front_slot_to_id.keys()),
        front_slot_to_id=front_slot_to_id,
        back_slot_to_id=back_slot_to_id,
        local_id_to_path=local_id_to_path,
        id_name_map=id_name_map,
        drive_id_context=drive_id_context,
        xml_needed_ids=xml_needed_ids,
    ), next_slot


class PrintPipeline:
    """Unified orchestration pipeline for print jobs."""

    def __init__(self, crop_threads: int = CROP_THREADS):
        self.crop_threads = crop_threads

    @classmethod
    def execute(
        cls,
        spec: PrintJobSpec,
        progress_callback: StageCallback = None,
    ) -> PrintJobResult:
        """Deep entrypoint for executing a print job specification."""
        pipeline = cls(crop_threads=spec.crop_threads)
        return pipeline.run_job(spec, progress_callback=progress_callback)

    def run_job(
        self,
        spec: PrintJobSpec,
        progress_callback: StageCallback = None,
    ) -> PrintJobResult:
        _check_cancel(spec.cancel_event)

        output_dir = Path(spec.output_dir)
        work_dir = Path(spec.work_dir)
        raw_dir = work_dir / "raw"
        bled_dir = work_dir / "bled"

        if spec.slots and not spec.extra_fronts:
            extra_fronts_p = [slot.front for slot in spec.slots]
            extra_backs_p = [slot.back for slot in spec.slots]
            crop_map: dict[Path, bool] = {slot.front: slot.crop for slot in spec.slots}
            if spec.local_crop_map:
                crop_map.update({Path(k): bool(v) for k, v in spec.local_crop_map.items()})
        else:
            extra_fronts_p = [Path(p) for p in (spec.extra_fronts or [])]
            extra_backs_p = [Path(p) if p is not None else None for p in (spec.extra_backs or [])]
            crop_map = {Path(k): bool(v) for k, v in (spec.local_crop_map or {}).items()}

        local_cardback_path = Path(spec.local_cardback) if spec.local_cardback else None
        base_name = spec.base_name

        def _cb(stage: str):
            def _inner(done: int, total: int):
                if progress_callback:
                    progress_callback(stage, done, total)

            return _inner

        def _report_tcg(stage: str, done: int, total: int, label: str) -> None:
            if spec.on_stage_progress:
                spec.on_stage_progress(stage, done, total, label)
            elif progress_callback:
                progress_callback(stage, done, total)

        # 0a. Prepare and combine multi-TCG batches if provided
        has_tcg_inputs = bool(
            spec.op_decks
            or spec.rb_decks
            or spec.lorcana_decks
            or spec.mtg_decks
            or spec.ygo_decks
            or spec.batches
        )
        standalone_batches: list[PrintBatch] = []
        if has_tcg_inputs:
            prepared_batches: list[PrintBatch] = []
            has_local_fronts = bool(extra_fronts_p)
            if has_local_fronts:
                local_default_back = (
                    Path(spec.local_backs[0]) if spec.local_backs else local_cardback_path
                )
                prepared_batches.append(
                    PrintBatch(
                        "locales",
                        list(extra_fronts_p),
                        list(extra_backs_p),
                        dict(crop_map),
                        default_back=local_default_back,
                    )
                )

            if spec.op_decks:
                prepared_batches.append(
                    OnePieceBatchAdapter(
                        tuple(spec.op_decks), work_dir, spec.cancel_event, _report_tcg
                    ).prepare()
                )
            if spec.rb_decks:
                prepared_batches.append(
                    RiftboundBatchAdapter(
                        tuple(spec.rb_decks), work_dir, spec.cancel_event, _report_tcg
                    ).prepare()
                )
            if spec.lorcana_decks:
                prepared_batches.append(
                    LorcanaBatchAdapter(
                        tuple(spec.lorcana_decks), work_dir, spec.cancel_event, _report_tcg
                    ).prepare()
                )
            if spec.mtg_decks:
                mtg_back = spec.mtg_default_back or (resources_dir() / "backs" / "mtg" / "back.jpg")
                prepared_batches.append(
                    MagicBatchAdapter(
                        decks=tuple(spec.mtg_decks),
                        work_dir=work_dir,
                        default_back=mtg_back,
                        local_backs=tuple(Path(p) for p in spec.local_backs),
                        cancel_event=spec.cancel_event,
                        report_progress=_report_tcg,
                        scryfall_downloader=spec.scryfall_downloader or download_deck_images,
                        separator_creator=spec.separator_creator or create_separator_image,
                        scryfall_lang=spec.deck_lang,
                        scryfall_quality=spec.deck_quality,
                        scryfall_fail_policy=spec.deck_fail_policy,
                        scryfall_quality_check=spec.deck_quality_check,
                        scryfall_blur_threshold=spec.deck_blur_threshold,
                        scryfall_brightness_contrast_check=spec.deck_brightness_contrast_check,
                        scryfall_minimize_tokens=spec.deck_minimize_tokens,
                    ).prepare()
                )
            if spec.ygo_decks:
                prepared_batches.append(
                    YugiohBatchAdapter(
                        tuple(spec.ygo_decks), work_dir, spec.cancel_event, _report_tcg
                    ).prepare()
                )
            if spec.batches:
                prepared_batches.extend(spec.batches)

            total_standard_decks = (
                len(spec.mtg_decks)
                + len(spec.op_decks)
                + len(spec.rb_decks)
                + len(spec.lorcana_decks)
            )
            single_deck_name: str | None = None
            if total_standard_decks == 1 and not has_local_fronts:
                if spec.mtg_decks:
                    single_deck_name = getattr(spec.mtg_decks[0], "name", "") or ""
                elif spec.op_decks:
                    single_deck_name = getattr(spec.op_decks[0], "name", "") or ""
                elif spec.rb_decks:
                    rb_item = spec.rb_decks[0]
                    rb_deck = rb_item[0] if isinstance(rb_item, tuple) else rb_item
                    single_deck_name = getattr(rb_deck, "name", "") or ""
                elif spec.lorcana_decks:
                    single_deck_name = getattr(spec.lorcana_decks[0], "name", "") or ""

            combined = build_combined_print_run(prepared_batches, single_deck_name=single_deck_name)
            extra_fronts_p = combined.fronts
            extra_backs_p = combined.backs
            crop_map.update(combined.crop_map)
            if local_cardback_path is None:
                local_cardback_path = combined.default_back
            if not spec.jobs and not spec.plan and not spec.xml_paths and spec.base_name == "deck":
                base_name = combined.base_name
            standalone_batches = standalone_print_batches(prepared_batches)

        # 0b. Handle deck URL if provided
        if spec.deck_url:
            scryfall_dir = work_dir / "scryfall"
            fetched = fetch_deck(spec.deck_url)
            if fetched.name and fetched.name.strip():
                base_name = sanitize_filename(fetched.name)

            cards = [
                c
                for c in fetched.cards
                if c.zone == "main" or (spec.include_sideboard and c.zone == "side")
            ]
            if not cards:
                raise ValueError("El mazo importado no contiene cartas.")

            def _dl_progress(done: int, total: int) -> None:
                if progress_callback:
                    progress_callback(Stage.DOWNLOAD, done, total)

            if progress_callback:
                progress_callback(Stage.DOWNLOAD, 0, len(cards))

            try:
                dl_results = download_deck_images(
                    cards,
                    scryfall_dir,
                    _dl_progress,
                    spec.cancel_event,
                    lang=spec.deck_lang,
                    quality=spec.deck_quality,
                    fail_policy=spec.deck_fail_policy,
                    quality_check=spec.deck_quality_check,
                    blur_threshold=spec.deck_blur_threshold,
                )
            except ScryfallError as exc:
                raise ValueError(f"Error al descargar imágenes de Scryfall: {exc}") from exc

            _check_cancel(spec.cancel_event)

            mtg_back = resources_dir() / "backs" / "mtg" / "back.jpg"
            local_cardback_path = mtg_back

            for card, front_path, back_path in dl_results:
                crop_map[front_path] = False
                if back_path:
                    crop_map[back_path] = False
                for _ in range(card.quantity):
                    extra_fronts_p.append(front_path)
                    extra_backs_p.append(back_path)

        spec_jobs = spec.jobs or (spec.plan.jobs if spec.plan is not None else [])

        # 0c. Optional Drive pre-verification for XML jobs
        if spec.verify_drive_access and spec_jobs:
            xml_paths_flat = [Path(p) for job in spec_jobs for p in job.xml_paths]
            all_ids = collect_drive_ids(xml_paths_flat)
            to_check = [(did, name) for did, name in all_ids if not list(raw_dir.glob(f"{did}.*"))]
            if to_check:
                _report_tcg(Stage.VERIFY, 0, len(to_check), "XML seleccionados")

                def _verify_cb(done: int, total: int) -> None:
                    _report_tcg(Stage.VERIFY, done, total, "XML seleccionados")

                inaccessible = check_drive_access(to_check, _verify_cb)
                if inaccessible and not (spec.cancel_event and spec.cancel_event.is_set()):
                    if spec.on_verify_warning is not None and not spec.on_verify_warning(
                        inaccessible
                    ):
                        raise Cancelled()

            _check_cancel(spec.cancel_event)

        # 1. Determine job definitions
        jobs_to_run: list[Any] = []
        if spec_jobs:
            jobs_to_run = spec_jobs
        elif spec.xml_paths:
            jobs_to_run = [
                _PlanJob(xml_paths=[Path(p) for p in spec.xml_paths], base_name=base_name)
            ]
        elif extra_fronts_p:
            jobs_to_run = [_PlanJob(xml_paths=[], base_name=base_name)]
        elif not standalone_batches:
            if has_tcg_inputs:
                raise ValueError("No hay cartas para generar.")
            raise ValueError("Se requiere al menos un XML o imágenes locales.")

        all_outputs: list[Path] = []
        manifest_path: Path | None = None
        total_jobs = 0
        total_cards = 0
        job_names: list[str] = []

        if jobs_to_run:
            has_any_xml = any(bool(getattr(j, "xml_paths", [])) for j in jobs_to_run)
            if not has_any_xml and local_cardback_path is None:
                if has_tcg_inputs:
                    raise ValueError("No se encontró reverso por defecto.")
                raise ValueError("Sin XML se requiere un cardback local (--local-cardback).")

            if spec_jobs and spec.plan_runner is not None:
                pdfs = spec.plan_runner(
                    spec_jobs,
                    output_dir,
                    work_dir,
                    progress_callback,
                    cancel_event=spec.cancel_event,
                    extra_fronts=extra_fronts_p or None,
                    extra_backs=extra_backs_p or None,
                    local_crop_map=crop_map or None,
                    on_job_pdf_start=spec.on_job_pdf_start,
                    on_xml_download_progress=spec.on_xml_download_progress,
                    on_xml_crop_progress=spec.on_xml_crop_progress,
                    fronts_only=spec.fronts_only,
                    on_speed_update=spec.on_speed_update,
                    cut_line_color=spec.cut_line_color,
                    cut_line_style=spec.cut_line_style,
                    cut_line_width=spec.cut_line_width,
                    cut_line_over_cards=spec.cut_line_over_cards,
                    cut_line_over_fronts=spec.cut_line_over_fronts,
                    cut_line_over_backs=spec.cut_line_over_backs,
                    max_pdf_bytes=spec.max_pdf_bytes,
                )
                all_outputs.extend(pdfs)
                total_jobs = len(spec_jobs)
                job_names = [getattr(j, "base_name", base_name) for j in spec_jobs]
            elif not has_any_xml and spec.locals_runner is not None:
                if spec.on_job_pdf_start:
                    spec.on_job_pdf_start(1, 1, base_name)

                def _locals_cb(stage: str, done: int, total: int, _label: str = base_name) -> None:
                    _report_tcg(stage, done, total, _label)

                pdfs = spec.locals_runner(
                    extra_fronts_p,
                    local_cardback_path,
                    output_dir,
                    base_name,
                    work_dir,
                    _locals_cb,
                    cancel_event=spec.cancel_event,
                    extra_backs=extra_backs_p,
                    local_crop_map=crop_map,
                    fronts_only=spec.fronts_only,
                    cut_line_color=spec.cut_line_color,
                    cut_line_style=spec.cut_line_style,
                    cut_line_width=spec.cut_line_width,
                    cut_line_over_cards=spec.cut_line_over_cards,
                    cut_line_over_fronts=spec.cut_line_over_fronts,
                    cut_line_over_backs=spec.cut_line_over_backs,
                    max_pdf_bytes=spec.max_pdf_bytes,
                )
                all_outputs.extend(pdfs)
                total_jobs = 1
                total_cards = len(extra_fronts_p)
                job_names = [base_name]
            else:
                # Phase 1: parse all jobs and build slot maps
                job_data_list: list[_JobData] = []
                combined_id_name: dict[str, str] = {}
                combined_locals: dict[str, Path] = {}
                combined_context: dict[str, tuple[str, int]] = {}
                combined_xml_ids: dict[str, set[str]] = {}
                next_slot = 0
                last_idx = len(jobs_to_run) - 1

                for i, job in enumerate(jobs_to_run):
                    is_last = i == last_idx
                    ef = extra_fronts_p if is_last else []
                    eb = extra_backs_p if is_last else []

                    jd, next_slot = _build_job_data(
                        xml_paths=[Path(p) for p in job.xml_paths],
                        base_name=job.base_name,
                        fallback_base_name=getattr(job, "fallback_base_name", None),
                        next_slot=next_slot,
                        extra_fronts=ef,
                        extra_backs=eb,
                        local_cardback_path=local_cardback_path,
                    )
                    job_data_list.append(jd)
                    combined_id_name.update(jd.id_name_map)
                    combined_locals.update(jd.local_id_to_path)
                    combined_context.update(jd.drive_id_context)
                    for xml_name, ids in jd.xml_needed_ids.items():
                        combined_xml_ids.setdefault(xml_name, set()).update(ids)

                _check_cancel(spec.cancel_event)

                # Phase 2: download all remote images
                download_pairs = [
                    (did, name)
                    for did, name in combined_id_name.items()
                    if did not in combined_locals
                ]

                _dl_ids_set = {did for did, _ in download_pairs}
                _xml_totals = {
                    name: len(ids & _dl_ids_set)
                    for name, ids in combined_xml_ids.items()
                    if ids & _dl_ids_set
                }
                _xml_done: dict[str, int] = {name: 0 for name in _xml_totals}
                _dl_id_to_xml: dict[str, list[str]] = {}
                for _xname, _xids in combined_xml_ids.items():
                    if _xname in _xml_totals:
                        for _did in _xids & _dl_ids_set:
                            _dl_id_to_xml.setdefault(_did, []).append(_xname)

                def _on_image_done(drive_id: str) -> None:
                    if not spec.on_xml_download_progress:
                        return
                    for xml_name in _dl_id_to_xml.get(drive_id, []):
                        _xml_done[xml_name] += 1
                        spec.on_xml_download_progress(
                            xml_name, _xml_done[xml_name], _xml_totals[xml_name]
                        )

                if progress_callback and download_pairs:
                    progress_callback(Stage.DOWNLOAD, 0, len(download_pairs))
                try:
                    id_to_raw = download_all(
                        download_pairs,
                        raw_dir,
                        _cb(Stage.DOWNLOAD),
                        cancel_event=spec.cancel_event,
                        on_image_done=_on_image_done,
                        on_speed_update=spec.on_speed_update,
                    )
                except DownloadPartialError as e:
                    for drive_id, _ in e.permission_errors + e.timeout_errors:
                        ctx = combined_context.get(drive_id)
                        if ctx:
                            e.xml_context[drive_id] = ctx
                    raise
                except (DownloadPermissionError, DownloadTimeoutError) as e:
                    ctx = combined_context.get(e.drive_id)
                    if ctx:
                        e.xml_name, e.position = ctx
                    raise
                id_to_raw.update(combined_locals)

                _check_cancel(spec.cancel_event)

                # Phase 3: crop all images (parallel)
                _raw_ids_set = set(id_to_raw.keys())
                _xml_crop_totals = {
                    name: len(ids & _raw_ids_set)
                    for name, ids in combined_xml_ids.items()
                    if ids & _raw_ids_set
                }
                _xml_crop_done: dict[str, int] = {name: 0 for name in _xml_crop_totals}
                _crop_id_to_xml: dict[str, list[str]] = {}
                for _xname, _xids in combined_xml_ids.items():
                    if _xname in _xml_crop_totals:
                        for _did in _xids & _raw_ids_set:
                            _crop_id_to_xml.setdefault(_did, []).append(_xname)

                crop_tasks = _build_crop_tasks(
                    id_to_raw, bled_dir, combined_locals, crop_map, spec.layout
                )

                def _on_crop_plan(drive_id: str, done: int, total: int) -> None:
                    if progress_callback:
                        progress_callback(Stage.CROP, done, total)
                    if spec.on_xml_crop_progress:
                        for xml_name in _crop_id_to_xml.get(drive_id, []):
                            _xml_crop_done[xml_name] += 1
                            spec.on_xml_crop_progress(
                                xml_name, _xml_crop_done[xml_name], _xml_crop_totals[xml_name]
                            )

                if progress_callback and crop_tasks:
                    progress_callback(Stage.CROP, 0, len(crop_tasks))
                id_to_bled = _run_crop_parallel(
                    crop_tasks, spec.cancel_event, on_done=_on_crop_plan, layout=spec.layout
                )

                _check_cancel(spec.cancel_event)

                # Phase 4: generate PDFs for each job
                total_jobs = len(job_data_list)
                for job_idx, jd in enumerate(job_data_list, start=1):
                    _check_cancel(spec.cancel_event)
                    if spec.on_job_pdf_start:
                        spec.on_job_pdf_start(job_idx, total_jobs, jd.base_name)
                    outputs = generate(
                        output_dir,
                        jd.base_name,
                        jd.ordered_slots,
                        jd.front_slot_to_id,
                        jd.back_slot_to_id,
                        id_to_bled,
                        max_bytes=spec.max_pdf_bytes,
                        progress_callback=_cb(Stage.PDF),
                        cancel_event=spec.cancel_event,
                        fronts_only=spec.fronts_only,
                        cut_line_color=spec.cut_line_color,
                        cut_line_style=spec.cut_line_style,
                        cut_line_width=spec.cut_line_width,
                        cut_line_over_cards=spec.cut_line_over_cards,
                        cut_line_over_fronts=spec.cut_line_over_fronts,
                        cut_line_over_backs=spec.cut_line_over_backs,
                        layout=spec.layout,
                        fallback_base_name=jd.fallback_base_name,
                    )
                    all_outputs.extend(outputs)
                    total_cards += len(jd.ordered_slots)
                job_names = [jd.base_name for jd in job_data_list]

            if spec.plan is not None and spec.reports is not None:
                manifest_path = write_manifest(spec.plan, spec.reports, output_dir)

        # Standalone batches (e.g. Yu-Gi-Oh! with separate_pdf=True)
        for standalone in standalone_batches:
            _check_cancel(spec.cancel_event)
            if spec.on_job_pdf_start:
                spec.on_job_pdf_start(1, 1, standalone.name)

            def _standalone_cb(
                stage: str, done: int, total: int, _label: str = standalone.name
            ) -> None:
                _report_tcg(stage, done, total, _label)

            standalone_pdfs = run_print_batch(
                standalone,
                output_dir,
                work_dir,
                _standalone_cb,
                cancel_event=spec.cancel_event,
                fronts_only=spec.fronts_only,
                cut_line_color=spec.cut_line_color,
                cut_line_style=spec.cut_line_style,
                cut_line_width=spec.cut_line_width,
                cut_line_over_cards=spec.cut_line_over_cards,
                cut_line_over_fronts=spec.cut_line_over_fronts,
                cut_line_over_backs=spec.cut_line_over_backs,
                max_pdf_bytes=spec.max_pdf_bytes,
            )
            all_outputs.extend(standalone_pdfs)
            total_jobs += 1
            total_cards += len(standalone.fronts)
            job_names.append(standalone.name)

        skipped_tokens_count = 0
        tokens_markdown_path: Path | None = None
        if spec.mtg_decks:
            skipped_tokens_count = count_skipped_tokens(spec.mtg_decks)
            tokens_markdown_path = write_tokens_markdown(spec.mtg_decks, output_dir)

        manifest = {
            "total_jobs": total_jobs,
            "total_cards": total_cards,
            "pdf_paths": [str(p) for p in all_outputs],
            "job_names": job_names,
        }

        return PrintJobResult(
            pdf_paths=all_outputs,
            manifest=manifest,
            manifest_path=manifest_path,
            tokens_markdown_path=tokens_markdown_path,
            skipped_tokens_count=skipped_tokens_count,
        )


def run(
    xml_path: str | Path,
    output_dir: str | Path,
    work_dir: str | Path = "workdir",
    progress_callback: StageCallback = None,
    cancel_event: Event | None = None,
    extra_fronts: list[str | Path] | None = None,
    extra_backs: list[str | Path | None] | None = None,
    local_crop_map: dict[Path, bool] | None = None,
    fronts_only: bool = False,
) -> list[Path]:
    """Single-XML pipeline: XML → one or more PDFs named after the XML stem.

    `extra_fronts` and `extra_backs` are optional local image paths. The fronts
    are appended after the XML cards; each pairs with the back at the same
    index, or falls back to the XML's default cardback when no paired back is
    supplied.

    `local_crop_map` lets each local image choose whether to apply the MPC
    bleed crop. Missing entries default to `False` (no crop).
    """
    xml_path = Path(xml_path)
    spec = PrintJobSpec(
        xml_paths=[xml_path],
        base_name=xml_path.stem,
        output_dir=output_dir,
        work_dir=work_dir,
        cancel_event=cancel_event,
        extra_fronts=extra_fronts or [],
        extra_backs=extra_backs or [],
        local_crop_map=local_crop_map or {},
        fronts_only=fronts_only,
    )
    return PrintPipeline.execute(spec, progress_callback=progress_callback).pdf_paths


def run_merged(
    xml_paths: list[str | Path],
    output_dir: str | Path,
    base_name: str,
    work_dir: str | Path = "workdir",
    progress_callback: StageCallback = None,
    cancel_event: Event | None = None,
    extra_fronts: list[str | Path] | None = None,
    extra_backs: list[str | Path | None] | None = None,
    local_crop_map: dict[Path, bool] | None = None,
    fronts_only: bool = False,
) -> list[Path]:
    """Multi-XML pipeline: concatenate the XMLs' fronts in order and emit one
    or more PDFs named `<base_name>.pdf` (or `<base_name>_1.pdf`, … when split).
    Each card keeps its own back (from its own XML).

    `extra_fronts` / `extra_backs` behave as in `run`; the first XML's
    cardback is the fallback when no paired back is supplied.
    """
    spec = PrintJobSpec(
        xml_paths=[Path(p) for p in xml_paths],
        base_name=base_name,
        output_dir=output_dir,
        work_dir=work_dir,
        cancel_event=cancel_event,
        extra_fronts=extra_fronts or [],
        extra_backs=extra_backs or [],
        local_crop_map=local_crop_map or {},
        fronts_only=fronts_only,
    )
    return PrintPipeline.execute(spec, progress_callback=progress_callback).pdf_paths


def run_locals_only(
    extra_fronts: list[str | Path],
    local_cardback: str | Path,
    output_dir: str | Path,
    base_name: str,
    work_dir: str | Path = "workdir",
    progress_callback: StageCallback = None,
    cancel_event: Event | None = None,
    extra_backs: list[str | Path | None] | None = None,
    local_crop_map: dict[Path, bool] | None = None,
    fronts_only: bool = False,
    cut_line_color: str = DEFAULT_CUT_LINE_COLOR,
    cut_line_style: str = DEFAULT_CUT_LINE_STYLE,
    cut_line_width: float = DEFAULT_CUT_LINE_WIDTH,
    cut_line_over_cards: bool = DEFAULT_CUT_LINE_OVER_CARDS,
    cut_line_over_fronts: bool = DEFAULT_CUT_LINE_OVER_FRONTS,
    cut_line_over_backs: bool = DEFAULT_CUT_LINE_OVER_BACKS,
    layout: CardLayout = STANDARD_LAYOUT,
    max_pdf_bytes: int = MAX_PDF_BYTES,
) -> list[Path]:
    """Generate PDF(s) only from local images (no XML).

    A `local_cardback` image is required — it's used for every front that
    doesn't have a paired back in `extra_backs`.
    """
    if not extra_fronts:
        raise ValueError("run_locals_only requires at least one front image.")
    spec = PrintJobSpec(
        extra_fronts=extra_fronts,
        local_cardback=local_cardback,
        output_dir=output_dir,
        base_name=base_name,
        work_dir=work_dir,
        cancel_event=cancel_event,
        extra_backs=extra_backs or [],
        local_crop_map=local_crop_map or {},
        fronts_only=fronts_only,
        cut_line_color=cut_line_color,
        cut_line_style=cut_line_style,
        cut_line_width=cut_line_width,
        cut_line_over_cards=cut_line_over_cards,
        cut_line_over_fronts=cut_line_over_fronts,
        cut_line_over_backs=cut_line_over_backs,
        layout=layout,
        max_pdf_bytes=max_pdf_bytes,
    )
    return PrintPipeline.execute(spec, progress_callback=progress_callback).pdf_paths


def run_print_batch(
    batch: PrintBatch,
    output_dir: str | Path,
    work_dir: str | Path = "workdir",
    progress_callback: StageCallback = None,
    cancel_event: Event | None = None,
    **pdf_options,
) -> list[Path]:
    """Render any fully prepared batch through the shared local-image pipeline."""
    if batch.default_back is None:
        raise ValueError("El lote imprimible necesita un reverso predeterminado.")
    return run_locals_only(
        batch.fronts,
        batch.default_back,
        output_dir,
        batch.name,
        work_dir,
        progress_callback,
        cancel_event=cancel_event,
        extra_backs=batch.backs,
        local_crop_map=batch.crop_map,
        layout=batch.layout or STANDARD_LAYOUT,
        **pdf_options,
    )


def run_plan(
    jobs: list,
    output_dir: str | Path,
    work_dir: str | Path = "workdir",
    progress_callback: StageCallback = None,
    cancel_event: Event | None = None,
    extra_fronts: list[str | Path] | None = None,
    extra_backs: list[str | Path | None] | None = None,
    local_crop_map: dict[Path, bool] | None = None,
    on_job_pdf_start: JobPdfStartCallback = None,
    on_xml_download_progress: StageCallback = None,
    on_xml_crop_progress: StageCallback = None,
    fronts_only: bool = False,
    on_speed_update: SpeedCallback = None,
    cut_line_color: str = DEFAULT_CUT_LINE_COLOR,
    cut_line_style: str = DEFAULT_CUT_LINE_STYLE,
    cut_line_width: float = DEFAULT_CUT_LINE_WIDTH,
    cut_line_over_cards: bool = DEFAULT_CUT_LINE_OVER_CARDS,
    cut_line_over_fronts: bool = DEFAULT_CUT_LINE_OVER_FRONTS,
    cut_line_over_backs: bool = DEFAULT_CUT_LINE_OVER_BACKS,
    max_pdf_bytes: int = MAX_PDF_BYTES,
) -> list[Path]:
    """Download ALL images first, then crop all, then generate each job's PDFs.

    on_job_pdf_start(job_idx, total_jobs, job_name) is called just before
    starting PDF generation for each job so callers can update their UI label.
    on_xml_download_progress(xml_name, done, total) is called after each image
    download completes, with the per-XML running totals.
    on_xml_crop_progress(xml_name, done, total) is called after each image
    crop completes, with the per-XML running totals.
    """
    spec = PrintJobSpec(
        jobs=jobs,
        output_dir=output_dir,
        work_dir=work_dir,
        cancel_event=cancel_event,
        extra_fronts=extra_fronts or [],
        extra_backs=extra_backs or [],
        local_crop_map=local_crop_map or {},
        on_job_pdf_start=on_job_pdf_start,
        on_xml_download_progress=on_xml_download_progress,
        on_xml_crop_progress=on_xml_crop_progress,
        fronts_only=fronts_only,
        on_speed_update=on_speed_update,
        cut_line_color=cut_line_color,
        cut_line_style=cut_line_style,
        cut_line_width=cut_line_width,
        cut_line_over_cards=cut_line_over_cards,
        cut_line_over_fronts=cut_line_over_fronts,
        cut_line_over_backs=cut_line_over_backs,
        max_pdf_bytes=max_pdf_bytes,
    )
    return PrintPipeline.execute(spec, progress_callback=progress_callback).pdf_paths


def run_deck_url(
    url: str,
    output_dir: str | Path,
    work_dir: str | Path,
    base_name: str,
    progress_callback: StageCallback = None,
    cancel_event: Event | None = None,
    include_sideboard: bool = False,
    fronts_only: bool = False,
    lang: str = "en",
    quality: str = "large",
    fail_policy: str = "english",
    quality_check: bool = True,
    blur_threshold: float = 100.0,
) -> list[Path]:
    """Fetch a deck from Moxfield/Archidekt, download images from Scryfall, generate PDF(s).

    MDFCs use card_faces[0] as front and card_faces[1] as back.
    Normal cards use resources/backs/mtg/back.jpg as their back.
    No MPC bleed crop is applied (Scryfall images have no bleed).
    """
    spec = PrintJobSpec(
        deck_url=url,
        output_dir=output_dir,
        work_dir=work_dir,
        base_name=base_name,
        cancel_event=cancel_event,
        include_sideboard=include_sideboard,
        fronts_only=fronts_only,
        deck_lang=lang,
        deck_quality=quality,
        deck_fail_policy=fail_policy,
        deck_quality_check=quality_check,
        deck_blur_threshold=blur_threshold,
    )
    return PrintPipeline.execute(spec, progress_callback=progress_callback).pdf_paths
