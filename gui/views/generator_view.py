"""GeneratorView — Unified PDF generator frame for XML files and direct game URLs."""

from __future__ import annotations

import logging
import math
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

try:
    import windnd

    _WINDND_AVAILABLE = True
except ImportError:
    _WINDND_AVAILABLE = False

from dataclasses import dataclass, field

from gui.paths import app_base_dir, output_dir, work_dir
from gui.views.generator_tabs import (
    LocalsTabMixin,
    LorcanaTabMixin,
    OPTabMixin,
    RBTabMixin,
    SettingsTabMixin,
    XmlTabMixin,
    YGOTabMixin,
)
from gui.widgets import (
    APP_TITLE,
    STAGE_LABELS,
    attach_context_menu,
    ellipsize,
    load_tab_icon,
    notify,
)
from src.app_settings import AppSettings, load_settings
from src.cancellation import Cancelled
from src.constants import CARDS_PER_PAGE, Stage
from src.deck_importer import MtgUrlDeck
from src.downloader import (
    DownloadPartialError,
    DownloadPermissionError,
    DownloadRateLimitError,
    DownloadTimeoutError,
)
from src.lorcana_scraper import LocanaDeck
from src.op_scraper import OPDeck
from src.parser import CardOrder
from src.pipeline import PrintJobSpec, PrintPipeline, run_locals_only, run_plan
from src.precheck import (
    XmlReport,
    analyze,
    format_warning,
    plan,
)
from src.rb_scraper import RBDeck
from src.scraper_utils import resources_dir
from src.scryfall import download_deck_images as scryfall_download
from src.tcg_batches import create_separator_image
from src.validator import ValidationWarning
from src.ygo_scraper import YGODeck

if TYPE_CHECKING:
    from src.engine.task_manager import TaskManager


@dataclass
class AppState:
    xml_paths: list[Path] = field(default_factory=list)
    local_fronts: list[Path] = field(default_factory=list)
    local_backs: list[Path] = field(default_factory=list)
    front_back_paths: list[Path | None] = field(default_factory=list)
    local_front_crop: list[bool] = field(default_factory=list)
    local_back_crop: list[bool] = field(default_factory=list)
    mtg_url_decks: list[MtgUrlDeck] = field(default_factory=list)


_log = logging.getLogger(__name__)


class GeneratorView(
    ttk.Frame,
    XmlTabMixin,
    OPTabMixin,
    RBTabMixin,
    LorcanaTabMixin,
    YGOTabMixin,
    LocalsTabMixin,
    SettingsTabMixin,
):
    """Unified PDF generation view hosting tabs for MPC XMLs and supported TCGs."""

    def __init__(
        self,
        master: tk.Misc,
        task_manager: TaskManager | None = None,
        on_navigate: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.master = master
        self.root = self.winfo_toplevel()
        self.task_manager = task_manager
        self.on_navigate = on_navigate

        self.state = AppState()
        self._xml_rows: list[dict] = []
        self._xml_card_counts: dict[Path, int] = {}
        self._xml_orders: dict[Path, CardOrder] = {}
        self._xml_validations: dict[Path, list[ValidationWarning]] = {}
        self._mtg_deck_rows: list[dict] = []
        self._front_rows: list[dict] = []
        self._back_rows: list[dict] = []
        self._front_multiselect_var = tk.BooleanVar(value=False)
        self._front_selected_indices: set[int] = set()

        self._settings: AppSettings = load_settings(app_base_dir())
        self._custom_output_dir: Path | None = self._settings.output_dir
        self._cut_line_skip_date: str | None = None

        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.running = False
        self.keep_cache = tk.BooleanVar(value=False)
        self._dl_speed_str: str = ""

        self._op_decks: list[OPDeck] = []
        self._op_deck_rows: list[dict] = []

        self._rb_decks: list[RBDeck] = []
        self._rb_deck_rows: list[dict] = []

        self._lorcana_decks: list[LocanaDeck] = []
        self._lorcana_deck_rows: list[dict] = []

        self._ygo_decks: list[YGODeck] = []
        self._ygo_deck_rows: list[dict] = []
        self._active_scroll_canvas: tk.Canvas | None = None

        self._build_ui()
        self.after(80, self._drain_events)
        self.after(200, self._setup_dnd)

    def _build_ui(self) -> None:
        bottom_controls = ttk.Frame(self)
        bottom_controls.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0), padx=10)

        self.keep_cache_cb = ttk.Checkbutton(
            bottom_controls,
            text="Guardar en el PC las imágenes entre ejecuciones",
            variable=self.keep_cache,
        )
        self.keep_cache_cb.pack(anchor=tk.W)

        ttk.Separator(bottom_controls, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        self.soriano_btn = ttk.Button(
            bottom_controls,
            text="Generar PDF con traseras (Para copisteria, con espejo horizontal)",
            command=lambda: self._start(fronts_only=False),
        )
        self.soriano_btn.pack(fill=tk.X)
        self.soriano_btn.state(["disabled"])

        self.fronts_only_btn = ttk.Button(
            bottom_controls,
            text="Generar PDF solo frontales",
            command=lambda: self._start(fronts_only=True),
        )
        self.fronts_only_btn.pack(fill=tk.X, pady=(4, 0))
        self.fronts_only_btn.state(["disabled"])

        self.stop_btn = ttk.Button(bottom_controls, text="Detener", command=self._request_stop)

        self.status_var = tk.StringVar(value="Listo. Selecciona uno o más XML o imágenes locales.")
        ttk.Label(bottom_controls, textvariable=self.status_var, anchor=tk.W).pack(
            fill=tk.X, pady=(10, 2)
        )

        self.progress = ttk.Progressbar(bottom_controls, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X)

        out_row = ttk.Frame(bottom_controls)
        out_row.pack(fill=tk.X, pady=(8, 0))
        self.out_dir_var = tk.StringVar(value=str(self._custom_output_dir or output_dir()))
        self.out_label = ttk.Label(
            out_row, textvariable=self.out_dir_var, foreground="#666", anchor=tk.W
        )
        self.out_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.timing_var = tk.StringVar(value="")
        ttk.Label(
            bottom_controls,
            textvariable=self.timing_var,
            foreground="#888",
            anchor=tk.W,
        ).pack(fill=tk.X)

        self._preflight_frame = ttk.LabelFrame(bottom_controls, text="Resumen")
        self._preflight_inner = ttk.Frame(self._preflight_frame)
        self._preflight_inner.pack(fill=tk.X, padx=6, pady=4)
        self._preflight_labels: list[ttk.Label] = []

        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(6, 0))
        top.columnconfigure(0, weight=1, uniform="cols")
        top.columnconfigure(1, weight=1, uniform="cols")
        top.rowconfigure(0, weight=1)

        self._build_xml_pane(top)
        self._build_locals_pane(top)

    def _build_xml_pane(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._game_notebook = notebook

        self._mtg_icon_img = load_tab_icon("mtg_icon")
        self._op_icon_img = load_tab_icon("op_icon")
        self._rb_icon_img = load_tab_icon("riftbound_icon")
        self._lorcana_icon_img = load_tab_icon("lorcana_icon")
        self._ygo_icon_img = load_tab_icon("yugioh_icon")

        magic_frame = ttk.Frame(notebook)
        magic_kw: dict = {"text": " Magic"}
        if self._mtg_icon_img:
            magic_kw["image"] = self._mtg_icon_img
            magic_kw["compound"] = "left"
        notebook.add(magic_frame, **magic_kw)
        self._build_magic_tab(magic_frame)

        op_frame = ttk.Frame(notebook)
        op_kw: dict = {"text": " One Piece"}
        if self._op_icon_img:
            op_kw["image"] = self._op_icon_img
            op_kw["compound"] = "left"
        notebook.add(op_frame, **op_kw)
        self._build_onepiece_tab(op_frame)

        rb_frame = ttk.Frame(notebook)
        rb_kw: dict = {"text": " Riftbound"}
        if self._rb_icon_img:
            rb_kw["image"] = self._rb_icon_img
            rb_kw["compound"] = "left"
        notebook.add(rb_frame, **rb_kw)
        self._build_riftbound_tab(rb_frame)

        lorcana_frame = ttk.Frame(notebook)
        lorcana_kw: dict = {"text": " Lorcana"}
        if self._lorcana_icon_img:
            lorcana_kw["image"] = self._lorcana_icon_img
            lorcana_kw["compound"] = "left"
        notebook.add(lorcana_frame, **lorcana_kw)
        self._build_lorcana_tab(lorcana_frame)

        ygo_frame = ttk.Frame(notebook)
        ygo_kw: dict = {"text": " Yu-Gi-Oh!"}
        if self._ygo_icon_img:
            ygo_kw["image"] = self._ygo_icon_img
            ygo_kw["compound"] = "left"
        notebook.add(ygo_frame, **ygo_kw)
        self._build_yugioh_tab(ygo_frame)

        settings_frame = ttk.Frame(notebook)
        notebook.add(settings_frame, text=" ⚙ Configuración")
        self._build_settings_tab(settings_frame)
        self._settings_tab_idx = notebook.index("end") - 1

        notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    def _on_notebook_tab_changed(self, _event=None) -> None:
        on_settings = self._game_notebook.index("current") == self._settings_tab_idx
        if on_settings:
            self._locals_pane.grid_remove()
            self._game_notebook.grid_configure(columnspan=2, padx=0)
        else:
            self._game_notebook.grid_configure(columnspan=1, padx=(0, 4))
            self._locals_pane.grid()

    def _build_scrollable_rows(self, parent: ttk.Frame):
        """Create a Canvas + inner Frame for a vertically and horizontally scrolling list of rows."""
        holder = ttk.Frame(parent, relief=tk.SUNKEN, borderwidth=1)
        holder.grid(row=0, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        canvas = tk.Canvas(holder, highlightthickness=0, background="#ffffff")
        vsb = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
        hsb = ttk.Scrollbar(holder, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _update_hsb_visibility(canvas_width=None):
            if canvas_width is None:
                canvas.update_idletasks()
                canvas_width = canvas.winfo_width()
            inner_width = inner.winfo_reqwidth()
            if inner_width > canvas_width:
                hsb.grid(row=1, column=0, sticky="ew")
            else:
                hsb.grid_remove()

        def _on_inner_configure(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            _update_hsb_visibility()

        def _on_canvas_configure(e):
            req_width = inner.winfo_reqwidth()
            if req_width < e.width:
                canvas.itemconfigure(window_id, width=e.width)
            else:
                canvas.itemconfigure(window_id, width=0)
            _update_hsb_visibility(e.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        return canvas, inner, window_id

    @staticmethod
    def _decode_drop(files) -> list[Path]:
        result = []
        for f in files:
            if isinstance(f, bytes):
                try:
                    f = f.decode(sys.getfilesystemencoding())
                except UnicodeDecodeError:
                    f = f.decode("cp1252", errors="replace")
            result.append(Path(str(f)))
        return result

    def _setup_dnd(self) -> None:
        if not _WINDND_AVAILABLE:
            return
        try:
            for w in (self._xml_drop_frame, self.xml_canvas, self.xml_inner):
                windnd.hook_dropfiles(w, func=self._on_drop_xmls)
            for w in (self.backs_canvas, self.backs_inner):
                windnd.hook_dropfiles(w, func=self._on_drop_backs)
            for w in (self.fronts_canvas, self.fronts_inner):
                windnd.hook_dropfiles(w, func=self._on_drop_fronts)
        except Exception:
            pass

    def _bind_mousewheel(self, canvas: tk.Canvas, on: bool) -> None:
        if on:
            self._active_scroll_canvas = canvas
            canvas.bind_all("<MouseWheel>", self._on_mousewheel)
            canvas.bind_all("<Button-4>", self._on_mousewheel)
            canvas.bind_all("<Button-5>", self._on_mousewheel)
        else:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
            self._active_scroll_canvas = None

    def _on_mousewheel(self, event) -> None:
        canvas = getattr(self, "_active_scroll_canvas", None)
        if canvas is None:
            return
        if event.num == 4:
            canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            canvas.yview_scroll(1, "units")
        else:
            canvas.yview_scroll(int(-event.delta / 120), "units")

    def _effective_output_dir(self) -> Path:
        d = self._custom_output_dir or output_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _extra_card_count(self) -> int:
        """Return cards appended to the last XML PDF job."""
        return (
            len(self.state.local_fronts)
            + sum(deck.total_slots for deck in self._op_decks)
            + sum(deck.total_slots for deck in self._rb_decks)
            + sum(deck.total_slots for deck in self._lorcana_decks)
            + sum(deck.active_count for deck in self.state.mtg_url_decks)
        )

    def _loaded_xml_plan(self):
        """Build the current XML plan from already parsed orders, if complete."""
        reports: list[XmlReport] = []
        for path in self.state.xml_paths:
            order = self._xml_orders.get(path)
            if order is None:
                return None
            cards = sum(len(card.slots) for card in order.fronts)
            remainder = cards % CARDS_PER_PAGE
            reports.append(XmlReport(path, cards, (CARDS_PER_PAGE - remainder) if remainder else 0))
        return plan(reports, local_count=self._extra_card_count())

    def _update_preflight(self) -> None:
        for widget in self._preflight_labels:
            widget.destroy()
        self._preflight_labels.clear()

        if (
            not self.state.xml_paths
            and not self.state.local_fronts
            and not self._op_decks
            and not self._rb_decks
            and not self._lorcana_decks
            and not self._ygo_decks
            and not self.state.mtg_url_decks
        ):
            self._preflight_frame.pack_forget()
            return

        def _row(parts: list[tuple[str, str]]) -> None:
            frame = ttk.Frame(self._preflight_inner)
            frame.pack(fill=tk.X)
            self._preflight_labels.append(frame)
            for text, style in parts:
                if style == "warn":
                    kw: dict = {"foreground": "#cc0000", "font": ("Segoe UI", 10, "bold")}
                elif style == "bold":
                    kw = {"foreground": "#444", "font": ("Segoe UI", 10, "bold")}
                else:
                    kw = {"foreground": "#444"}
                ttk.Label(frame, text=text, anchor=tk.W, **kw).pack(side=tk.LEFT)

        xml_total = 0

        for p in self.state.xml_paths:
            order = self._xml_orders.get(p)
            if order is None:
                _row([(f"• {p.name}: (no se pudo leer)", "normal")])
                continue
            n = sum(len(card.slots) for card in order.fronts)
            xml_total += n
            _row(
                [
                    (f"• {p.name}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck in self._op_decks:
            n = deck.total_slots
            xml_total += n
            _row(
                [
                    (f"• One Piece – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck in self._rb_decks:
            n = deck.total_slots
            xml_total += n
            _row(
                [
                    (f"• Riftbound – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck in self._lorcana_decks:
            n = deck.total_slots
            xml_total += n
            _row(
                [
                    (f"• Lorcana – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck in self.state.mtg_url_decks:
            n = deck.active_count
            xml_total += n
            _row(
                [
                    (f"• Magic – {ellipsize(deck.display_name, 28)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas", "normal"),
                ]
            )

        for deck, row in zip(self._ygo_decks, self._ygo_deck_rows):
            include_side = (
                bool(row["include_side_var"].get()) if "include_side_var" in row else False
            )
            n = deck.total_slots(include_side)
            _row(
                [
                    (f"• Yu-Gi-Oh! – {ellipsize(deck.name, 22)}: ", "normal"),
                    (f"{n}", "bold"),
                    (" cartas (PDF separado)", "normal"),
                ]
            )

        local_count = len(self.state.local_fronts)
        if local_count:
            _row(
                [
                    ("• Locales: ", "normal"),
                    (f"{local_count}", "bold"),
                    (" carta(s)", "normal"),
                ]
            )

        total_cards = xml_total + local_count
        if total_cards:
            pairs = math.ceil(total_cards / CARDS_PER_PAGE)
            rem = total_cards % CARDS_PER_PAGE
            blanks = (CARDS_PER_PAGE - rem) if rem else 0

            bled_dir = work_dir() / "bled"
            cached = len([f for f in bled_dir.iterdir() if f.is_file()]) if bled_dir.exists() else 0
            cache_str = f" · {cached} imagen(es) en caché" if cached else ""

            if blanks:
                _row(
                    [
                        ("Total: ", "normal"),
                        (f"{total_cards}", "bold"),
                        (f" cartas · {pairs} par(es) de páginas{cache_str} · ", "normal"),
                        (f"⚠ {blanks} huecos en la última página", "warn"),
                    ]
                )
            else:
                _row(
                    [
                        ("Total: ", "normal"),
                        (f"{total_cards}", "bold"),
                        (f" cartas · {pairs} par(es) de páginas{cache_str}", "normal"),
                    ]
                )

        self._preflight_frame.pack(fill=tk.X, pady=(8, 0))

    def _refresh_generate_state(self) -> None:
        ready = (
            bool(self.state.xml_paths)
            or (self.state.local_fronts and self.state.local_backs)
            or self._op_decks
            or self._rb_decks
            or self._lorcana_decks
            or bool(self._ygo_decks)
            or bool(self.state.mtg_url_decks)
        )
        if ready and not self.running:
            self.soriano_btn.state(["!disabled"])
            self.fronts_only_btn.state(["!disabled"])
        else:
            self.soriano_btn.state(["disabled"])
            self.fronts_only_btn.state(["disabled"])
        self._update_preflight()

    def _resolve_extra_backs(self) -> list[Path | None]:
        return [
            self.state.front_back_paths[i] if i < len(self.state.front_back_paths) else None
            for i in range(len(self.state.local_fronts))
        ]

    def _build_crop_map(self) -> dict[Path, bool]:
        m: dict[Path, bool] = {}
        for p, c in zip(self.state.local_backs, self.state.local_back_crop):
            m[p] = c
        for p, c in zip(self.state.local_fronts, self.state.local_front_crop):
            m[p] = c
        return m

    def _confirm_cut_lines(self) -> bool:
        """Show a modal summary of cut-line settings before generating."""
        today = datetime.now().date().isoformat()
        if self._cut_line_skip_date == today:
            return True

        s = self._settings
        style_label = (
            "Marcas de margen  (para copistería)"
            if s.cut_line_style == "ticks"
            else "Líneas completas  (para autocorte)"
        )
        if s.cut_line_style == "full":
            over_parts = []
            if s.cut_line_over_fronts:
                over_parts.append("Frontal")
            if s.cut_line_over_backs:
                over_parts.append("Trasero")
            over_label = ", ".join(over_parts) if over_parts else "Ninguna"
        else:
            over_label = None

        accepted = [False]
        skip_var = tk.BooleanVar(value=False)

        dlg = tk.Toplevel(self.root)
        dlg.title("Configuración de líneas de corte")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        pad = {"padx": 16, "pady": 6}
        outer = ttk.Frame(dlg)
        outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        ttk.Label(
            outer,
            text="Configuración actual de las líneas de corte:",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, **pad)

        grid = ttk.Frame(outer)
        grid.pack(anchor=tk.W, padx=16, pady=(0, 4))

        def _row(label: str, value: str, color: str | None = None) -> None:
            r = grid.grid_size()[1]
            ttk.Label(grid, text=label, foreground="#555").grid(
                row=r, column=0, sticky=tk.W, pady=2
            )
            if color:
                swatch = tk.Label(grid, width=3, bg=color, relief=tk.SOLID, borderwidth=1)
                swatch.grid(row=r, column=1, sticky=tk.W, padx=(8, 4))
                ttk.Label(grid, text=value, foreground="#222").grid(row=r, column=2, sticky=tk.W)
            else:
                ttk.Label(grid, text=value, foreground="#222").grid(
                    row=r, column=1, columnspan=2, sticky=tk.W, padx=(8, 0)
                )

        _row("Color:", s.cut_line_color, color=s.cut_line_color)
        _row("Grosor:", f"{s.cut_line_width:.1f} pt")
        _row("Estilo:", style_label)
        if over_label is not None:
            _row("Páginas con líneas:", over_label)

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=(8, 6))

        ttk.Checkbutton(
            outer,
            text="No volver a preguntar hoy",
            variable=skip_var,
        ).pack(anchor=tk.W, padx=16, pady=(0, 8))

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, padx=16, pady=(0, 10))

        def _accept():
            accepted[0] = True
            if skip_var.get():
                self._cut_line_skip_date = today
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        ttk.Button(btn_row, text="Cancelar", command=_cancel).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Aceptar", command=_accept).pack(side=tk.RIGHT)

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

        dlg.bind("<Return>", lambda _e: _accept())
        dlg.bind("<Escape>", lambda _e: _cancel())
        self.root.wait_window(dlg)
        return accepted[0]

    def _start(self, fronts_only: bool = False) -> None:
        if self.running:
            return

        if (
            not self.state.xml_paths
            and not self.state.local_fronts
            and not self._op_decks
            and not self._rb_decks
            and not self._lorcana_decks
            and not self.state.mtg_url_decks
        ):
            messagebox.showerror(
                APP_TITLE,
                "Selecciona al menos un XML, imágenes locales, o un mazo de "
                "One Piece, Riftbound, Lorcana o desde una URL.",
            )
            return

        if not self.state.xml_paths and self.state.local_fronts and not self.state.local_backs:
            messagebox.showerror(
                APP_TITLE,
                "Sin XMLs se necesita al menos un back local "
                "(el primero actúa como reverso por defecto).",
            )
            return

        if not self._confirm_cut_lines():
            return

        reports = []
        plan_ = None
        if self.state.xml_paths:
            all_xml_warnings = [
                (p, ws) for p in self.state.xml_paths if (ws := self._xml_validations.get(p))
            ]
            if all_xml_warnings:
                lines = []
                for p, ws in all_xml_warnings:
                    for w in ws:
                        lines.append(f"[{p.name}] {w.message}")
                preview = "\n".join(f"• {line}" for line in lines[:10])
                more = f"\n… y {len(lines) - 10} más" if len(lines) > 10 else ""
                if not messagebox.askyesno(
                    APP_TITLE,
                    f"Se encontraron {len(lines)} advertencia(s) en los XML:\n\n"
                    f"{preview}{more}\n\n¿Continuar de todos modos?",
                    icon=messagebox.WARNING,
                ):
                    return

            try:
                reports = analyze(self.state.xml_paths)
            except Exception as e:
                self._show_error_dialog(f"No se pudo analizar el XML:\n{e}")
                return

            plan_ = plan(reports, local_count=len(self.state.local_fronts))

            if format_warning(plan_):
                combined_total = (
                    sum(r.cards for r in reports)
                    + len(self.state.local_fronts)
                    + sum(d.total_slots for d in self._op_decks)
                    + sum(d.total_slots for d in self._rb_decks)
                    + sum(d.total_slots for d in self._lorcana_decks)
                    + sum(d.active_count for d in self.state.mtg_url_decks)
                )
                combined_rem = combined_total % CARDS_PER_PAGE
                if combined_rem != 0:
                    combined_blanks = CARDS_PER_PAGE - combined_rem
                    s = "hueco" if combined_blanks == 1 else "huecos"
                    combined_warning = (
                        f"Aviso: {combined_total} carta(s) en total no es múltiplo de 9.\n"
                        f"La última página tendrá {combined_blanks} {s} en blanco "
                        "(la imprenta cobra la página entera aunque no esté llena)."
                    )
                    if not messagebox.askyesno(
                        APP_TITLE,
                        combined_warning + "\n\n¿Continuar de todos modos?",
                        icon=messagebox.WARNING,
                    ):
                        return
        else:
            total = (
                len(self.state.local_fronts)
                + sum(d.total_slots for d in self._op_decks)
                + sum(d.total_slots for d in self._rb_decks)
                + sum(d.total_slots for d in self._lorcana_decks)
                + sum(d.active_count for d in self.state.mtg_url_decks)
            )
            rem = total % CARDS_PER_PAGE
            if rem:
                blanks = CARDS_PER_PAGE - rem
                s = "hueco" if blanks == 1 else "huecos"
                warning = (
                    f"Aviso: {total} carta(s) no es múltiplo de 9.\n"
                    f"La última página tendrá {blanks} {s} en blanco "
                    "(la imprenta cobra la página entera aunque no esté llena)."
                    "\n\n¿Continuar de todos modos?"
                )
                if not messagebox.askyesno(APP_TITLE, warning, icon=messagebox.WARNING):
                    return

        self.running = True
        self.cancel_event.clear()
        self._dl_speed_str = ""
        self.timing_var.set("")
        self.soriano_btn.state(["disabled"])
        self.fronts_only_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.stop_btn.pack(fill=tk.X, pady=(4, 0), after=self.fronts_only_btn)
        self.progress["value"] = 0
        self.status_var.set("Preparando…")
        self._reset_xml_download_progress()

        ygo_decks_snapshot = tuple(
            (deck, bool(row["include_side_var"].get()))
            for deck, row in zip(self._ygo_decks, self._ygo_deck_rows)
        )
        work_args = (
            plan_,
            reports,
            fronts_only,
            self._settings.cut_line_color,
            self._settings.cut_line_style,
            self._settings.cut_line_width,
            self._settings.cut_line_over_cards,
            self._settings.cut_line_over_fronts,
            self._settings.cut_line_over_backs,
            None,
            ygo_decks_snapshot,
        )

        if self.task_manager is not None:
            self.worker = self.task_manager.submit_job("PDF Generation", self._work, *work_args)
        else:
            self.worker = threading.Thread(
                target=self._work,
                args=work_args,
                daemon=True,
            )
            self.worker.start()

    def _request_stop(self) -> None:
        if not self.running:
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "¿Seguro que quieres detener el proceso en curso?",
            icon=messagebox.WARNING,
        ):
            return
        self.cancel_event.set()
        self.stop_btn.state(["disabled"])
        self.status_var.set("Cancelando…")

    def _work(
        self,
        plan_,
        reports,
        fronts_only: bool = False,
        cut_line_color: str = "#000000",
        cut_line_style: str = "ticks",
        cut_line_width: float = 1.0,
        cut_line_over_cards: bool = False,
        cut_line_over_fronts: bool = True,
        cut_line_over_backs: bool = True,
        cancel_event: threading.Event | None = None,
        ygo_decks: tuple = (),
    ) -> None:
        effective_cancel = cancel_event or self.cancel_event
        run_dir = None
        wd = None
        max_pdf_bytes = self._settings.max_pdf_bytes
        try:
            out = self._effective_output_dir()
            wd = work_dir()

            bled_dir = wd / "bled"
            if bled_dir.exists():
                cached = [f for f in bled_dir.iterdir() if f.is_file()]
                if cached:
                    self.events.put(("checkpoint_found", len(cached)))

            run_dir = out / datetime.now().strftime("%d_%m_%Y_%H-%M-%S")
            run_dir.mkdir(parents=True, exist_ok=True)
            extra_backs = self._resolve_extra_backs()
            crop_map = self._build_crop_map()

            _run_start = time.time()
            _phase_first: dict[str, float] = {}
            _phase_done: dict[str, float] = {}

            def _track(stage: str, done: int, total: int) -> None:
                now = time.time()
                if stage not in _phase_first:
                    _phase_first[stage] = now
                if done == total and total > 0:
                    _phase_done[stage] = now

            _pdf_label = [""]

            def cb(stage: str, done: int, total: int) -> None:
                _track(stage, done, total)
                name = _pdf_label[0] if stage == "pdf" else "Todas las imágenes"
                self.events.put(("progress", stage, done, total, name))

            def on_stage_progress(stage: str, done: int, total: int, label: str) -> None:
                _track(stage, done, total)
                self.events.put(("progress", stage, done, total, label))

            def on_job_pdf_start(job_idx: int, total_jobs: int, job_name: str) -> None:
                job = (
                    next((j for j in plan_.jobs if j.base_name == job_name), None)
                    if plan_ is not None
                    else None
                )
                label = job_name + (" (fusión)" if job and job.is_merged else "")
                _pdf_label[0] = label
                self.events.put(("file", job_idx, total_jobs, label))

            def on_xml_download_progress(xml_name: str, done: int, total: int) -> None:
                self.events.put(("xml_download_progress", xml_name, done, total))

            def on_xml_crop_progress(xml_name: str, done: int, total: int) -> None:
                self.events.put(("xml_crop_progress", xml_name, done, total))

            def on_speed_update(speed_mbps: float, eta_sec: float) -> None:
                self.events.put(("download_speed", speed_mbps, eta_sec))

            def on_verify_warning(inaccessible: list[tuple[str, str]]) -> bool:
                confirm_event = threading.Event()
                self.events.put(("verify_warning", inaccessible, confirm_event))
                while not confirm_event.wait(timeout=0.1):
                    if effective_cancel.is_set():
                        return False
                return not effective_cancel.is_set()

            print_runes_flags = [row["print_runes_var"].get() for row in self._rb_deck_rows]
            rb_decks_with_flags = [
                (
                    deck,
                    print_runes_flags[idx] if idx < len(print_runes_flags) else True,
                )
                for idx, deck in enumerate(self._rb_decks)
            ]

            if (
                plan_ is None
                and not self.state.local_fronts
                and not self._op_decks
                and not rb_decks_with_flags
                and not self._lorcana_decks
                and not self.state.mtg_url_decks
                and not ygo_decks
            ):
                raise ValueError("No hay cartas para generar.")

            spec = PrintJobSpec(
                jobs=plan_.jobs if plan_ is not None else [],
                plan=plan_,
                reports=reports,
                extra_fronts=list(self.state.local_fronts),
                extra_backs=list(extra_backs),
                local_backs=list(self.state.local_backs),
                local_cardback=self.state.local_backs[0] if self.state.local_backs else None,
                local_crop_map=crop_map,
                op_decks=list(self._op_decks),
                rb_decks=rb_decks_with_flags,
                lorcana_decks=list(self._lorcana_decks),
                mtg_decks=list(self.state.mtg_url_decks),
                ygo_decks=list(ygo_decks),
                deck_lang=self._settings.scryfall_lang,
                deck_quality=self._settings.scryfall_quality,
                deck_fail_policy=self._settings.scryfall_fail_policy,
                deck_quality_check=self._settings.scryfall_quality_check,
                deck_blur_threshold=self._settings.scryfall_blur_threshold,
                deck_brightness_contrast_check=self._settings.scryfall_brightness_contrast_check,
                deck_minimize_tokens=self._settings.scryfall_minimize_tokens,
                output_dir=run_dir,
                work_dir=wd,
                fronts_only=fronts_only,
                cut_line_color=cut_line_color,
                cut_line_style=cut_line_style,
                cut_line_width=cut_line_width,
                cut_line_over_cards=cut_line_over_cards,
                cut_line_over_fronts=cut_line_over_fronts,
                cut_line_over_backs=cut_line_over_backs,
                max_pdf_bytes=max_pdf_bytes,
                verify_drive_access=plan_ is not None,
                on_verify_warning=on_verify_warning,
                on_stage_progress=on_stage_progress,
                cancel_event=effective_cancel,
                on_job_pdf_start=on_job_pdf_start,
                on_xml_download_progress=on_xml_download_progress,
                on_xml_crop_progress=on_xml_crop_progress,
                on_speed_update=on_speed_update,
                scryfall_downloader=scryfall_download,
                separator_creator=self._create_separator_image,
                mtg_default_back=resources_dir() / "backs" / "mtg" / "back.jpg",
                locals_runner=run_locals_only,
                plan_runner=run_plan,
            )

            result = PrintPipeline.execute(spec, progress_callback=cb)

            if not self.keep_cache.get():
                self._cleanup_workdir(wd)

            def _fmt_dur(sec: float) -> str:
                return f"{int(sec) // 60}m {int(sec) % 60}s" if sec >= 60 else f"{sec:.0f}s"

            timing_parts = []
            for stage in (Stage.VERIFY, Stage.DOWNLOAD, Stage.CROP, Stage.PDF):
                if stage in _phase_first and stage in _phase_done:
                    dur = _phase_done[stage] - _phase_first[stage]
                    label = {
                        Stage.VERIFY: "Verif.",
                        Stage.DOWNLOAD: "Descarga",
                        Stage.CROP: "Recorte",
                        Stage.PDF: "PDF",
                    }.get(stage, stage)
                    timing_parts.append(f"{label}: {_fmt_dur(dur)}")
            total_dur = time.time() - _run_start
            timing_str = "  ".join(timing_parts)
            if timing_str:
                timing_str += f"  Total: {_fmt_dur(total_dur)}"

            self.events.put(
                (
                    "done",
                    result.pdf_paths,
                    result.manifest_path,
                    run_dir,
                    timing_str,
                    result.skipped_tokens_count,
                )
            )
        except DownloadPartialError as e:
            self.events.put(
                (
                    "partial_download_error",
                    e.permission_errors,
                    e.timeout_errors,
                    e.xml_context,
                    run_dir,
                )
            )
        except DownloadRateLimitError:
            self.events.put(("rate_limit_error", run_dir))
        except DownloadPermissionError as e:
            self.events.put(("permission_error", e.card_name, e.xml_name, e.position, run_dir))
        except DownloadTimeoutError as e:
            self.events.put(("timeout_error", e.card_name, e.xml_name, e.position, run_dir))
        except Cancelled:
            self.events.put(("cancelled", run_dir))
        except Exception as e:
            self.events.put(("error", f"{e}\n\n{traceback.format_exc()}", run_dir))

    @staticmethod
    def _cleanup_workdir(wd: Path) -> None:
        for sub in ("raw", "bled", "op_raw", "rb_raw", "lorcana_raw", "scryfall"):
            target = wd / sub
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

    @staticmethod
    def _cleanup_run_dir(run_dir: Path) -> None:
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)

    _create_separator_image = staticmethod(create_separator_image)

    def _drain_events(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                self._handle(ev)
        except queue.Empty:
            pass
        finally:
            if self.winfo_exists():
                self.after(80, self._drain_events)

    def _handle(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "file":
            _, i, n, name = ev
            self.status_var.set(f"[{i}/{n}] {name}")
        elif kind == "progress":
            _, stage, done, total, name = ev
            label = STAGE_LABELS.get(stage, stage)
            pct = (done / total * 100.0) if total else 0
            self.progress["value"] = pct
            speed_info = (
                f" — {self._dl_speed_str}" if stage == "download" and self._dl_speed_str else ""
            )
            self.status_var.set(f"{name} — {label}: {done}/{total}{speed_info}")
        elif kind == "download_speed":
            _, speed_mbps, eta_sec = ev
            mins = int(eta_sec) // 60
            secs = int(eta_sec) % 60
            eta_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"
            self._dl_speed_str = f"{speed_mbps:.1f} MB/s — ETA {eta_str}"
        elif kind == "checkpoint_found":
            _, count = ev
            self.status_var.set(
                f"Se encontraron {count} imagen(es) en caché de una ejecución anterior. "
                "Retomando desde el crop."
            )
        elif kind == "partial_download_error":
            _, perm_errors, timeout_errors, xml_context, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            parts = []
            if perm_errors:
                names = ", ".join(f"«{name}»" for _, name in perm_errors[:3])
                more = f" y {len(perm_errors) - 3} más" if len(perm_errors) > 3 else ""
                parts.append(
                    f"{len(perm_errors)} imagen(es) no se pudieron descargar: {names}{more}.\n"
                    "Posibles causas:\n"
                    "  • El archivo en Google Drive solo permite descarga con cuenta de Google "
                    "(«Cualquiera con el enlace» no basta para descarga anónima).\n"
                    "  • Le han quitado los permisos de acceso público.\n"
                    "Pide al creador del proxy que comparta las imágenes como "
                    "«Público en Internet» en Google Drive."
                )
            if timeout_errors:
                names = ", ".join(f"«{name}»" for _, name in timeout_errors[:3])
                more = f" y {len(timeout_errors) - 3} más" if len(timeout_errors) > 3 else ""
                parts.append(
                    f"{len(timeout_errors)} imagen(es) con tiempo de espera agotado: {names}{more}.\n"
                    "Las imágenes descargadas correctamente están en caché. "
                    "Vuelve a intentarlo en unos minutos."
                )
            total_failed = len(perm_errors) + len(timeout_errors)
            self.status_var.set(f"Error: {total_failed} imagen(es) no se pudieron descargar.")
            self._finish_running()
            self._show_error_dialog("\n\n".join(parts))
        elif kind == "done":
            if len(ev) == 6:
                _, pdfs, manifest, run_dir, timing_str, skipped_tokens_count = ev
            else:
                _, pdfs, manifest, run_dir, timing_str = ev
                skipped_tokens_count = 0
            self.progress["value"] = 100
            extra = f" Resumen en {manifest.name}." if manifest else ""
            status_text = f"Listo. {len(pdfs)} PDF(s) generados en {run_dir.name}.{extra}"
            if skipped_tokens_count > 0:
                status_text += (
                    f" (Aviso: {skipped_tokens_count} token(s) omitidos por layout no válido)"
                )
            self.status_var.set(status_text)
            if timing_str:
                self.timing_var.set(f"Tiempos — {timing_str}")
                _log.info("Phase timings: %s", timing_str)
            self._finish_running()
            notify(APP_TITLE, f"¡Listo! {len(pdfs)} PDF(s) generados correctamente.")
            self._open_output_folder(run_dir)
        elif kind == "xml_download_progress":
            _, xml_name, done, total = ev
            self._show_xml_download_progress(xml_name, done, total)
        elif kind == "xml_crop_progress":
            _, xml_name, done, total = ev
            self._show_xml_crop_progress(xml_name, done, total)
        elif kind == "verify_warning":
            _, cards, confirm_event = ev
            names = "\n".join(f"  • {name}" for _, name in cards[:8])
            more = f"\n  … y {len(cards) - 8} más" if len(cards) > 8 else ""
            msg = (
                f"{len(cards)} carta(s) sin acceso público en Google Drive:\n\n"
                f"{names}{more}\n\n"
                "El proceso fallará al intentar descargar estas cartas.\n"
                "¿Deseas continuar de todos modos?"
            )
            if not messagebox.askyesno(APP_TITLE, msg, icon=messagebox.WARNING):
                self.cancel_event.set()
            confirm_event.set()
        elif kind == "cancelled":
            _, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            self.progress["value"] = 0
            self.status_var.set(
                "Proceso detenido. Las imágenes descargadas se conservan para retomar."
            )
            self._finish_running()
        elif kind == "rate_limit_error":
            _, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            self.status_var.set("Error: demasiadas descargas en poco tiempo.")
            self._finish_running()
            self._show_error_dialog(
                "Se están intentando descargar demasiadas imágenes en poco tiempo, "
                "espera un rato y vuelve a intentar.\n\n"
                "Por favor selecciona «Guardar en el PC las imágenes entre ejecuciones» "
                "así evitamos volver a descargarlas cada vez."
            )
        elif kind == "permission_error":
            _, card_name, xml_name, position, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            parts = [f"Se ha fallado descargando «{card_name}»"]
            if xml_name:
                parts[0] += f" en el {xml_name}"
            if position:
                parts[0] += f" que está en la posición {position}."
            else:
                parts[0] += "."
            parts.append(
                "Esto no es un fallo del programa. Posibles causas:\n"
                "  • El archivo en Google Drive solo permite descarga con cuenta de Google "
                "(«Cualquiera con el enlace» no basta para descarga anónima).\n"
                "  • Le han quitado los permisos de acceso público.\n"
                "Pide al creador del proxy que comparta las imágenes como "
                "«Público en Internet» en Google Drive."
            )
            self.status_var.set("Error de descarga.")
            self._finish_running()
            self._show_error_dialog("\n\n".join(parts))
        elif kind == "timeout_error":
            _, card_name, xml_name, position, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            parts = [f"Se ha agotado el tiempo de espera descargando «{card_name}»"]
            if xml_name:
                parts[0] += f" en el {xml_name}"
            if position:
                parts[0] += f" que está en la posición {position}."
            else:
                parts[0] += "."
            parts.append(
                "La descarga no recibió datos durante 30 segundos y se canceló.\n"
                "Puede ser un problema temporal de Google Drive o de tu conexión.\n"
                "Vuelve a intentarlo en unos minutos."
            )
            self.status_var.set("Error de descarga (tiempo agotado).")
            self._finish_running()
            self._show_error_dialog("\n\n".join(parts))
        elif kind == "op_deck_loaded":
            _, deck = ev
            if not any(d.slug == deck.slug for d in self._op_decks):
                self._op_decks.append(deck)
                self._op_refresh_rows()
                self._refresh_generate_state()
            self._op_url_var.set("")
            self._op_status_var.set(f"Añadido: {deck.name}")
            self._op_load_btn.state(["!disabled"])
        elif kind == "op_deck_error":
            _, msg = ev
            self._op_status_var.set("Error al cargar el mazo.")
            self._op_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "rb_deck_loaded":
            _, deck = ev
            if not any(d.deck_id == deck.deck_id for d in self._rb_decks):
                self._rb_decks.append(deck)
                self._rb_refresh_rows()
                self._refresh_generate_state()
            self._rb_url_var.set("")
            self._rb_status_var.set(f"Añadido: {deck.name}")
            self._rb_load_btn.state(["!disabled"])
        elif kind == "rb_deck_error":
            _, msg = ev
            self._rb_status_var.set("Error al cargar el mazo.")
            self._rb_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "lorcana_deck_loaded":
            _, deck = ev
            if not any(d.deck_id == deck.deck_id for d in self._lorcana_decks):
                self._lorcana_decks.append(deck)
                self._lorcana_refresh_rows()
                self._refresh_generate_state()
            self._lorcana_url_var.set("")
            self._lorcana_status_var.set(f"Añadido: {deck.name}")
            self._lorcana_load_btn.state(["!disabled"])
        elif kind == "lorcana_deck_error":
            _, msg = ev
            self._lorcana_status_var.set("Error al cargar el mazo.")
            self._lorcana_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "ygo_deck_loaded":
            _, deck = ev
            if not any(d.deck_id == deck.deck_id for d in self._ygo_decks):
                self._ygo_decks.append(deck)
                self._ygo_refresh_rows()
                self._refresh_generate_state()
            self._ygo_url_var.set("")
            self._ygo_status_var.set(f"Añadido: {deck.name}")
            self._ygo_load_btn.state(["!disabled"])
        elif kind == "ygo_deck_error":
            _, msg = ev
            self._ygo_status_var.set("Error al cargar el mazo.")
            self._ygo_load_btn.state(["!disabled"])
            self._show_error_dialog(msg)
        elif kind == "mtg_url_loaded":
            _, url, cards, include_side, deck_name, deck_format = ev
            self.state.mtg_url_decks.append(
                MtgUrlDeck(
                    url=url,
                    cards=cards,
                    include_side=self._settings.scryfall_download_sideboard,
                    include_tokens=self._settings.scryfall_download_tokens,
                    name=deck_name,
                    format=deck_format,
                    include_separator=self._settings.scryfall_deck_separators,
                )
            )
            dlg = getattr(self, "_mtg_url_dialog", None)
            if dlg and dlg.winfo_exists():
                dlg.destroy()
            self._mtg_url_dialog = None
            self._refresh_xml_rows()
            self._refresh_generate_state()
        elif kind == "mtg_url_error":
            _, msg = ev
            status_var = getattr(self, "_mtg_url_status_var", None)
            import_btn = getattr(self, "_mtg_import_btn", None)
            if status_var:
                status_var.set(f"✗ {msg}")
            if import_btn:
                try:
                    import_btn.state(["!disabled"])
                except Exception:
                    pass
        elif kind == "error":
            _, msg, run_dir = ev
            if run_dir is not None:
                self._cleanup_run_dir(run_dir)
            self.status_var.set("Error durante la generación. Las imágenes en caché se conservan.")
            self._finish_running()
            self._show_error_dialog(msg)

    def _show_error_dialog(self, message: str) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{APP_TITLE} — Error")
        dlg.geometry("600x340")
        dlg.resizable(True, True)
        dlg.grab_set()

        ttk.Label(dlg, text="Error", font=("Segoe UI", 11, "bold"), foreground="#cc0000").pack(
            anchor="w", padx=12, pady=(10, 4)
        )

        frame = ttk.Frame(dlg)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        txt = tk.Text(
            frame,
            wrap=tk.WORD,
            relief=tk.SUNKEN,
            borderwidth=1,
            font=("Consolas", 9),
            state=tk.NORMAL,
        )
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        txt.insert("1.0", message)
        txt.configure(state=tk.DISABLED)
        attach_context_menu(txt)

        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 10))

        def _copy():
            dlg.clipboard_clear()
            dlg.clipboard_append(message)
            copy_btn.configure(text="✓ Copiado")
            dlg.after(2000, lambda: copy_btn.configure(text="Copiar al portapapeles"))

        copy_btn = ttk.Button(btn_row, text="Copiar al portapapeles", command=_copy)
        copy_btn.pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Cerrar", command=dlg.destroy).pack(side=tk.RIGHT)

    def _finish_running(self) -> None:
        self.running = False
        self.worker = None
        self.stop_btn.pack_forget()
        self.stop_btn.state(["!disabled"])
        self._refresh_generate_state()

    def _open_output_folder(self, path: Path) -> None:
        try:
            os.startfile(str(path))
        except AttributeError:
            import subprocess

            opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)])
