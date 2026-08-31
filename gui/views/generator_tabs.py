"""Modular sub-tab mixins for GeneratorView.

Includes:
- XmlTabMixin: MPCFill XML queue and URL import for Magic.
- LocalsTabMixin: Local front/back image assignment and crop controls.
- OPTabMixin, RBTabMixin, LorcanaTabMixin: Direct scraper sub-tabs leveraging GameScraperTabMixin.
- SettingsTabMixin: Embedded cut-line and Scryfall settings configuration.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from gui.components.generator_scraper_tab import GameScraperTabMixin, ScraperTabSpec
from gui.paths import app_base_dir, output_dir
from gui.widgets import (
    _PB_CROP_COLOR,
    _PB_DOWNLOAD_COLOR,
    APP_TITLE,
    FRONT_NAME_WIDTH,
    IMAGE_FILETYPES,
    WINDND_AVAILABLE,
    ImageTooltip,
    PreviewWindow,
    XmlPb,
    attach_context_menu,
    ellipsize,
)
from src.app_settings import (
    DEFAULT_CUT_LINE_COLOR,
    DEFAULT_CUT_LINE_OVER_BACKS,
    DEFAULT_CUT_LINE_OVER_CARDS,
    DEFAULT_CUT_LINE_OVER_FRONTS,
    DEFAULT_CUT_LINE_STYLE,
    DEFAULT_CUT_LINE_WIDTH,
    DEFAULT_SCRYFALL_BC_CHECK,
    DEFAULT_SCRYFALL_BLUR_THRESHOLD,
    DEFAULT_SCRYFALL_DECK_SEPARATORS,
    DEFAULT_SCRYFALL_DOWNLOAD_SIDEBOARD,
    DEFAULT_SCRYFALL_DOWNLOAD_TOKENS,
    DEFAULT_SCRYFALL_FAIL_POLICY,
    DEFAULT_SCRYFALL_LANG,
    DEFAULT_SCRYFALL_MINIMIZE_TOKENS,
    DEFAULT_SCRYFALL_QUALITY,
    DEFAULT_SCRYFALL_QUALITY_CHECK,
    save_settings,
)
from src.constants import SUPPORTED_IMAGE_EXTS
from src.deck_importer import DeckImportError, fetch_deck
from src.lorcana_scraper import scrape_deck as lorcana_scrape_deck
from src.op_scraper import scrape_deck as op_scrape_deck
from src.parser import parse
from src.precheck import analyze
from src.rb_scraper import scrape_deck as rb_scrape_deck
from src.validator import validate

_log = logging.getLogger(__name__)

_WIDTH_MIN = 0.1
_WIDTH_MAX = 10.0

_RB_SECTION_LABELS = {
    "legend": "Leyenda",
    "champion": "Campeón",
    "battlefield": "Campo de batalla",
    "rune": "Runa",
    "maindeck": "Mazo principal",
    "sideboard": "Sideboard",
}


# ===========================================================================
# 1. XML & MTG URL Tab Mixin
# ===========================================================================


class XmlTabMixin:
    """Methods for the Magic/XML tab and XML file management."""

    def _build_magic_tab(self, parent: ttk.Frame) -> None:
        self._xml_drop_frame = parent
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        xml_list_frame = ttk.Frame(parent)
        xml_list_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 2))
        xml_list_frame.columnconfigure(0, weight=1)
        xml_list_frame.rowconfigure(0, weight=1)

        self.xml_canvas, self.xml_inner, self._xml_window = self._build_scrollable_rows(
            xml_list_frame
        )
        self.xml_canvas.bind("<Enter>", lambda _e: self._bind_mousewheel(self.xml_canvas, True))
        self.xml_canvas.bind("<Leave>", lambda _e: self._bind_mousewheel(self.xml_canvas, False))

        dnd_hint = " o arrastra aquí" if WINDND_AVAILABLE else ""
        self._xml_empty_label = ttk.Label(
            self.xml_inner,
            text=f"(sin XMLs — usa «Seleccionar XMLs…»{dnd_hint})",
            foreground="#777",
            padding=(8, 10),
        )
        self._xml_empty_label.pack(anchor="w")

        xml_btn_row = ttk.Frame(parent)
        xml_btn_row.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        ttk.Button(xml_btn_row, text="Seleccionar XMLs…", command=self._pick_xmls).pack(
            side=tk.LEFT
        )
        ttk.Button(xml_btn_row, text="Añadir desde URL", command=self._open_url_dialog).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(xml_btn_row, text="Vaciar", command=self._clear_xmls).pack(side=tk.LEFT, padx=6)

    def _pick_xmls(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecciona archivos XML de MPCFill",
            filetypes=[("Archivos XML", "*.xml"), ("Todos", "*.*")],
        )
        added = 0
        for p in paths:
            pp = Path(p)
            if pp not in self.state.xml_paths:
                self.state.xml_paths.append(pp)
                added += 1
                if pp not in self._xml_card_counts:
                    try:
                        rpts = analyze([pp])
                        if rpts:
                            self._xml_card_counts[pp] = rpts[0].cards
                    except Exception:
                        pass
                if pp not in self._xml_orders:
                    try:
                        self._xml_orders[pp] = parse(pp)
                    except Exception:
                        pass
                if pp not in self._xml_validations:
                    try:
                        self._xml_validations[pp] = validate(pp)
                    except Exception:
                        self._xml_validations[pp] = []
        if added:
            self._refresh_xml_rows()
            self.status_var.set(f"{len(self.state.xml_paths)} XML(s) en cola.")
        self._refresh_generate_state()

    def _on_drop_xmls(self, files) -> None:
        paths = self._decode_drop(files)
        added = 0
        for pp in paths:
            if pp.suffix.lower() != ".xml":
                continue
            if pp not in self.state.xml_paths:
                self.state.xml_paths.append(pp)
                added += 1
                if pp not in self._xml_card_counts:
                    try:
                        rpts = analyze([pp])
                        if rpts:
                            self._xml_card_counts[pp] = rpts[0].cards
                    except Exception:
                        pass
                if pp not in self._xml_orders:
                    try:
                        self._xml_orders[pp] = parse(pp)
                    except Exception:
                        pass
                if pp not in self._xml_validations:
                    try:
                        self._xml_validations[pp] = validate(pp)
                    except Exception:
                        self._xml_validations[pp] = []
        if added:
            self._refresh_xml_rows()
            self.status_var.set(f"{len(self.state.xml_paths)} XML(s) en cola.")
        self._refresh_generate_state()

    def _remove_xml(self, idx: int) -> None:
        if 0 <= idx < len(self.state.xml_paths):
            p = self.state.xml_paths[idx]
            self._xml_card_counts.pop(p, None)
            self._xml_orders.pop(p, None)
            self._xml_validations.pop(p, None)
            del self.state.xml_paths[idx]
            self._refresh_xml_rows()
            self._refresh_generate_state()

    def _clear_xmls(self) -> None:
        self.state.xml_paths.clear()
        self._xml_card_counts.clear()
        self._xml_orders.clear()
        self._xml_validations.clear()
        self.state.mtg_url_decks.clear()
        self._refresh_xml_rows()
        self._refresh_generate_state()

    def _refresh_xml_rows(self) -> None:
        for row in self._xml_rows:
            row["frame"].destroy()
        self._xml_rows.clear()
        for row in self._mtg_deck_rows:
            row["frame"].destroy()
        self._mtg_deck_rows.clear()

        if not self.state.xml_paths and not self.state.mtg_url_decks:
            self._xml_empty_label.pack(anchor="w")
            return
        self._xml_empty_label.pack_forget()

        for i, xml_path in enumerate(self.state.xml_paths):
            frame = ttk.Frame(self.xml_inner)
            frame.pack(fill=tk.X, pady=1, padx=2)
            frame.columnconfigure(0, weight=3)
            frame.columnconfigure(2, weight=7)

            ttk.Label(
                frame,
                text=ellipsize(xml_path.name, 32),
                anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=(4, 0))

            ttk.Label(frame, text=" - ").grid(row=0, column=1)

            right = ttk.Frame(frame)
            right.grid(row=0, column=2, sticky="ew")

            card_count = self._xml_card_counts.get(xml_path)
            cards_text = f"{card_count} cartas" if card_count is not None else ""
            ttk.Label(right, text=cards_text, foreground="#555", anchor="w").grid(
                row=0, column=0, sticky="w", padx=(0, 6)
            )

            pb = XmlPb(right)
            pb.grid(row=0, column=1, padx=(0, 4))
            pb.grid_remove()

            count_var = tk.StringVar(value="")
            count_lbl = ttk.Label(right, textvariable=count_var, width=9, anchor="w")
            count_lbl.grid(row=0, column=2, padx=(0, 4))
            count_lbl.grid_remove()

            ttk.Button(
                right,
                text="Ver cartas",
                command=lambda p=xml_path: self._show_preview(p),
            ).grid(row=0, column=4, padx=(0, 2))
            ttk.Button(
                right,
                text="✕",
                width=2,
                command=lambda idx=i: self._remove_xml(idx),
            ).grid(row=0, column=3, padx=(0, 1))

            xml_warnings = self._xml_validations.get(xml_path, [])
            warn_btn = ttk.Button(
                right,
                text="⚠",
                width=2,
                command=lambda p=xml_path: self._show_xml_warnings(p),
            )
            warn_btn.grid(row=0, column=5, padx=(0, 2))
            if not xml_warnings:
                warn_btn.grid_remove()

            self._xml_rows.append(
                {
                    "frame": frame,
                    "pb": pb,
                    "count_var": count_var,
                    "count_lbl": count_lbl,
                    "warn_btn": warn_btn,
                }
            )

        numbered = [str(j + 1) for j in range(len(self.state.local_backs))]
        back_combo_values = ["default", *numbered]

        for i, deck in enumerate(self.state.mtg_url_decks):
            frame = ttk.Frame(self.xml_inner)
            frame.pack(fill=tk.X, pady=1, padx=2)
            frame.columnconfigure(0, weight=3)
            frame.columnconfigure(2, weight=7)

            ttk.Label(
                frame,
                text=ellipsize(deck.display_name, 32),
                anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=(4, 0))

            ttk.Label(frame, text=" - ").grid(row=0, column=1)

            right = ttk.Frame(frame)
            right.grid(row=0, column=2, sticky="ew")

            main_count = sum(c.quantity for c in deck.cards if c.zone == "main")
            side_count = sum(c.quantity for c in deck.cards if c.zone == "side")
            token_count = sum(
                c.quantity
                for c in deck.cards
                if c.zone == "token" and c.layout in ("token", "emblem", "double_faced_token")
            )

            count_parts = [f"{main_count} cartas"]
            if side_count:
                count_parts.append(f"+{side_count} side")
            if token_count:
                count_parts.append(f"+{token_count} tokens")
            count_text = " ".join(count_parts)

            ttk.Label(right, text=count_text, foreground="#555", anchor="w").grid(
                row=0, column=0, sticky="w", padx=(0, 6)
            )

            col = 1
            if side_count:
                side_var = tk.BooleanVar(value=deck.include_side)

                def _toggle_side(idx=i, var=side_var):
                    self.state.mtg_url_decks[idx].include_side = var.get()
                    self._refresh_generate_state()

                ttk.Checkbutton(
                    right, text="Incluir sideboard", variable=side_var, command=_toggle_side
                ).grid(row=0, column=col, padx=(0, 4))
                col += 1

            if token_count:
                token_var = tk.BooleanVar(value=deck.include_tokens)

                def _toggle_tokens(idx=i, var=token_var):
                    self.state.mtg_url_decks[idx].include_tokens = var.get()
                    self._refresh_generate_state()

                ttk.Checkbutton(
                    right, text="Incluir tokens", variable=token_var, command=_toggle_tokens
                ).grid(row=0, column=col, padx=(0, 4))
                col += 1

            sep_var = tk.BooleanVar(value=deck.include_separator)

            def _toggle_sep(idx=i, var=sep_var):
                self.state.mtg_url_decks[idx].include_separator = var.get()
                self._refresh_generate_state()

            ttk.Checkbutton(
                right, text="Incluir separador", variable=sep_var, command=_toggle_sep
            ).grid(row=0, column=col, padx=(0, 4))
            col += 1

            if deck.back_path is not None and deck.back_path not in self.state.local_backs:
                deck.back_path = None

            back_var = tk.StringVar()
            if deck.back_path is None:
                back_var.set("default")
            else:
                back_var.set(str(self.state.local_backs.index(deck.back_path) + 1))

            ttk.Label(right, text="Traseras:").grid(row=0, column=col, padx=(0, 2))
            col += 1
            back_combo = ttk.Combobox(
                right,
                values=back_combo_values,
                textvariable=back_var,
                state="readonly" if numbered else "disabled",
                width=4,
            )
            back_combo.bind(
                "<<ComboboxSelected>>",
                lambda _e, idx=i, v=back_var: self._on_mtg_back_change(idx, v),
            )
            back_combo.grid(row=0, column=col, padx=(0, 4))
            col += 1

            ttk.Button(
                right,
                text="✕",
                width=2,
                command=lambda idx=i: self._remove_mtg_deck(idx),
            ).grid(row=0, column=col, padx=(0, 2))

            self._mtg_deck_rows.append({"frame": frame})

        self.xml_inner.update_idletasks()
        self.xml_canvas.configure(scrollregion=self.xml_canvas.bbox("all"))

    def _show_xml_download_progress(self, xml_name: str, done: int, total: int) -> None:
        for xml_path, row in zip(self.state.xml_paths, self._xml_rows):
            if xml_path.name == xml_name:
                pct = (done / total * 100.0) if total else 100.0
                row["pb"].set_progress(pct, "Descargando", color=_PB_DOWNLOAD_COLOR)
                row["count_var"].set(f"{done}/{total}")
                row["pb"].grid()
                row["count_lbl"].grid()
                break

    def _show_xml_crop_progress(self, xml_name: str, done: int, total: int) -> None:
        for xml_path, row in zip(self.state.xml_paths, self._xml_rows):
            if xml_path.name == xml_name:
                pct = (done / total * 100.0) if total else 100.0
                row["pb"].set_progress(pct, "Recortando", color=_PB_CROP_COLOR)
                row["count_var"].set(f"{done}/{total}")
                row["pb"].grid()
                row["count_lbl"].grid()
                break

    def _reset_xml_download_progress(self) -> None:
        for row in self._xml_rows:
            row["pb"].set_progress(0, "")
            row["count_var"].set("")
            row["pb"].grid_remove()
            row["count_lbl"].grid_remove()

    def _show_preview(self, xml_path: Path) -> None:
        order = self._xml_orders.get(xml_path)
        if order is None:
            messagebox.showinfo(APP_TITLE, "No hay datos de cartas para esta XML.")
            return
        PreviewWindow(self.root, xml_path, order)

    def _show_xml_warnings(self, xml_path: Path) -> None:
        warnings = self._xml_validations.get(xml_path, [])
        if not warnings:
            return
        msg = "\n".join(f"• {w.message}" for w in warnings)
        messagebox.showwarning(
            APP_TITLE,
            f"Advertencias en {xml_path.name}:\n\n{msg}",
        )

    def _remove_mtg_deck(self, idx: int) -> None:
        if 0 <= idx < len(self.state.mtg_url_decks):
            del self.state.mtg_url_decks[idx]
            self._refresh_xml_rows()
            self._refresh_generate_state()

    def _on_mtg_back_change(self, deck_idx: int, var: tk.StringVar) -> None:
        if deck_idx >= len(self.state.mtg_url_decks):
            return
        choice = var.get()
        try:
            n = int(choice)
        except (TypeError, ValueError):
            self.state.mtg_url_decks[deck_idx].back_path = None
            return
        if 1 <= n <= len(self.state.local_backs):
            self.state.mtg_url_decks[deck_idx].back_path = self.state.local_backs[n - 1]

    def _open_url_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Añadir mazo desde URL")
        dlg.geometry("480x190")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.columnconfigure(0, weight=1)
        self._mtg_url_dialog = dlg

        ttk.Label(
            dlg,
            text="Ten en cuenta que al usar esta opción puede que las imágenes tengan menos calidad que las de MPCFill",
            foreground="#b86000",
            font=("Segoe UI", 8),
            wraplength=440,
        ).pack(anchor="w", padx=10, pady=(10, 2))

        ttk.Label(
            dlg,
            text="Webs aceptadas: moxfield.com, archidekt.com, deckstats.net, tappedout.net, manabox.app",
            foreground="#999",
            font=("Segoe UI", 8),
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 2))

        url_frame = ttk.Frame(dlg)
        url_frame.pack(fill=tk.X, padx=10)
        url_frame.columnconfigure(1, weight=1)
        ttk.Label(url_frame, text="URL:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        url_var = tk.StringVar()
        url_entry = ttk.Entry(url_frame, textvariable=url_var)
        url_entry.grid(row=0, column=1, sticky="ew")
        attach_context_menu(url_entry)
        url_entry.focus_set()

        status_var = tk.StringVar(value="")
        self._mtg_url_status_var = status_var

        status_lbl = ttk.Label(dlg, textvariable=status_var, foreground="#555")
        status_lbl.pack(anchor="w", padx=10, pady=(6, 0))

        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill=tk.X, padx=10, pady=(6, 10))

        def _do_import():
            url = url_var.get().strip()
            if not url:
                return
            import_btn.state(["disabled"])
            status_var.set("Cargando…")

            def _run():
                try:
                    result = fetch_deck(url)
                    from src.scryfall import fetch_card, fetch_card_by_name

                    for c in result.cards:
                        if c.zone == "token":
                            try:
                                set_code = c.set_code
                                col_num = c.collector_number
                                if not set_code or not col_num:
                                    set_code, col_num = fetch_card_by_name(c.name)
                                    c.set_code = set_code
                                    c.collector_number = col_num
                                sc_card = fetch_card(set_code, col_num)
                                c.layout = getattr(sc_card, "layout", "")
                            except Exception as e:
                                _log.warning(
                                    "No se pudo obtener el layout de Scryfall para el token %s: %s",
                                    c.name,
                                    e,
                                )
                                c.layout = ""
                    self.events.put(
                        ("mtg_url_loaded", url, result.cards, False, result.name, result.format)
                    )
                except DeckImportError as exc:
                    self.events.put(("mtg_url_error", str(exc)))
                except Exception as exc:
                    self.events.put(("mtg_url_error", f"Error inesperado: {exc}"))

            threading.Thread(target=_run, daemon=True).start()

        import_btn = ttk.Button(btn_row, text="Importar", command=_do_import)
        import_btn.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Cancelar", command=dlg.destroy).pack(side=tk.RIGHT)
        url_entry.bind("<Return>", lambda _e: _do_import())
        self._mtg_import_btn = import_btn


# ===========================================================================
# 2. Local Images Pane Mixin
# ===========================================================================


class LocalsTabMixin:
    """Methods for the local images pane (backs + fronts with per-card back assignment)."""

    def _build_locals_pane(self, parent: ttk.Frame) -> None:
        local_frame = ttk.LabelFrame(parent, text="Imágenes locales (opcional)")
        local_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self._locals_pane = local_frame
        self._locals_drop_frame = local_frame
        local_frame.columnconfigure(0, weight=1)
        local_frame.rowconfigure(1, weight=1, uniform="locals")
        local_frame.rowconfigure(3, weight=2, uniform="locals")

        backs_hdr = ttk.Frame(local_frame)
        backs_hdr.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        ttk.Label(backs_hdr, text="Traseras (numeradas 1, 2, …):").pack(side=tk.LEFT)
        self._back_crop_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            backs_hdr,
            text="Recortar todas",
            variable=self._back_crop_all,
            command=self._on_back_crop_all,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        backs_block = ttk.Frame(local_frame)
        backs_block.grid(row=1, column=0, sticky="nsew", padx=6)
        backs_block.columnconfigure(0, weight=1)
        backs_block.rowconfigure(0, weight=1)

        self.backs_canvas, self.backs_inner, self._backs_window = self._build_scrollable_rows(
            backs_block
        )
        self.backs_canvas.bind("<Enter>", lambda _e: self._bind_mousewheel(self.backs_canvas, True))
        self.backs_canvas.bind(
            "<Leave>", lambda _e: self._bind_mousewheel(self.backs_canvas, False)
        )

        dnd_hint = " o arrastra aquí" if WINDND_AVAILABLE else ""
        self._backs_empty_label = ttk.Label(
            self.backs_inner,
            text=f"(sin traseras — usa «Seleccionar imágenes…»{dnd_hint})",
            foreground="#777",
            padding=(8, 10),
        )
        self._backs_empty_label.pack(anchor="w")

        backs_btn_row = ttk.Frame(backs_block)
        backs_btn_row.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        ttk.Button(
            backs_btn_row, text="Seleccionar imágenes…", command=self._pick_local_backs
        ).pack(side=tk.LEFT)
        ttk.Button(backs_btn_row, text="Vaciar", command=self._clear_local_backs).pack(
            side=tk.LEFT, padx=6
        )

        ttk.Separator(local_frame, orient=tk.HORIZONTAL).grid(
            row=2, column=0, sticky="ew", padx=6, pady=(4, 4)
        )

        fronts_block = ttk.Frame(local_frame)
        fronts_block.grid(row=3, column=0, sticky="nsew", padx=6, pady=(0, 6))
        fronts_block.columnconfigure(0, weight=1)
        fronts_block.rowconfigure(1, weight=1)

        fronts_hdr = ttk.Frame(fronts_block)
        fronts_hdr.grid(row=0, column=0, sticky="ew", pady=(2, 4))
        self._fronts_header_var = tk.StringVar(value="Frontales (asignar trasera por carta):")
        ttk.Label(fronts_hdr, textvariable=self._fronts_header_var).pack(side=tk.LEFT)
        self._front_crop_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            fronts_hdr,
            text="Recortar todas",
            variable=self._front_crop_all,
            command=self._on_front_crop_all,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        fronts_holder = ttk.Frame(fronts_block)
        fronts_holder.grid(row=1, column=0, sticky="nsew")
        fronts_holder.columnconfigure(0, weight=1)
        fronts_holder.rowconfigure(0, weight=1)

        self.fronts_canvas, self.fronts_inner, self._fronts_window = self._build_scrollable_rows(
            fronts_holder
        )
        self.fronts_canvas.bind(
            "<Enter>", lambda _e: self._bind_mousewheel(self.fronts_canvas, True)
        )
        self.fronts_canvas.bind(
            "<Leave>", lambda _e: self._bind_mousewheel(self.fronts_canvas, False)
        )

        self._fronts_empty_label = ttk.Label(
            self.fronts_inner,
            text=f"(sin frontales — usa «Seleccionar imágenes…»{dnd_hint})",
            foreground="#777",
            padding=(8, 10),
        )
        self._fronts_empty_label.pack(anchor="w")

        fronts_btn_row = ttk.Frame(fronts_block)
        fronts_btn_row.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(
            fronts_btn_row, text="Seleccionar imágenes…", command=self._pick_local_fronts
        ).pack(side=tk.LEFT)
        ttk.Button(fronts_btn_row, text="Vaciar", command=self._clear_local_fronts).pack(
            side=tk.LEFT, padx=6
        )

    def _pick_local_backs(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecciona imágenes locales (traseras)",
            filetypes=IMAGE_FILETYPES,
        )
        was_empty = not self.state.local_backs
        added = False
        for p in paths:
            pp = Path(p)
            if pp not in self.state.local_backs:
                self.state.local_backs.append(pp)
                self.state.local_back_crop.append(False)
                added = True
        if added:
            if was_empty and self.state.local_backs:
                first = self.state.local_backs[0]
                for i, assigned in enumerate(self.state.front_back_paths):
                    if assigned is None:
                        self.state.front_back_paths[i] = first
            self._refresh_back_rows()
            self._refresh_front_rows()
            self._refresh_generate_state()

    def _remove_back(self, idx: int) -> None:
        if not (0 <= idx < len(self.state.local_backs)):
            return
        removed_path = self.state.local_backs[idx]
        del self.state.local_backs[idx]
        del self.state.local_back_crop[idx]
        for i, assigned in enumerate(self.state.front_back_paths):
            if assigned == removed_path:
                self.state.front_back_paths[i] = None
        self._refresh_back_rows()
        self._refresh_front_rows()
        self._refresh_generate_state()

    def _clear_local_backs(self) -> None:
        if not self.state.local_backs:
            return
        self.state.local_backs.clear()
        self.state.local_back_crop.clear()
        self.state.front_back_paths = [None] * len(self.state.front_back_paths)
        self._refresh_back_rows()
        self._refresh_front_rows()
        self._refresh_generate_state()

    def _on_back_crop_change(self, idx: int, var: tk.BooleanVar) -> None:
        if 0 <= idx < len(self.state.local_back_crop):
            self.state.local_back_crop[idx] = bool(var.get())

    def _refresh_back_rows(self) -> None:
        for row in self._back_rows:
            row["frame"].destroy()
        self._back_rows.clear()

        if not self.state.local_backs:
            self._backs_empty_label.pack(anchor="w")
            return
        self._backs_empty_label.pack_forget()

        for i, back_path in enumerate(self.state.local_backs):
            row = ttk.Frame(self.backs_inner)
            row.pack(fill=tk.X, pady=1, padx=2)

            ttk.Label(row, text=f"{i + 1:>3}.", width=4, anchor="e").pack(side=tk.LEFT)
            name_lbl = ttk.Label(
                row,
                text=ellipsize(back_path.name, FRONT_NAME_WIDTH),
                width=FRONT_NAME_WIDTH + 1,
                anchor="w",
            )
            name_lbl.pack(side=tk.LEFT, padx=(4, 8))
            ImageTooltip(name_lbl, back_path)

            crop_var = tk.BooleanVar(value=self.state.local_back_crop[i])
            ttk.Checkbutton(
                row,
                text="Recortar bordes extra",
                variable=crop_var,
                command=lambda idx=i, v=crop_var: self._on_back_crop_change(idx, v),
            ).pack(side=tk.LEFT, padx=(4, 6))

            ttk.Button(
                row,
                text="✕",
                width=2,
                command=lambda idx=i: self._remove_back(idx),
            ).pack(side=tk.RIGHT)
            ttk.Button(
                row,
                text="▼",
                width=2,
                command=lambda idx=i: self._move_back_down(idx),
            ).pack(side=tk.RIGHT, padx=(0, 1))
            ttk.Button(
                row,
                text="▲",
                width=2,
                command=lambda idx=i: self._move_back_up(idx),
            ).pack(side=tk.RIGHT, padx=(0, 1))

            self._back_rows.append({"frame": row, "crop_var": crop_var})

        self.backs_inner.update_idletasks()
        self.backs_canvas.configure(scrollregion=self.backs_canvas.bbox("all"))
        self._refresh_xml_rows()

    def _pick_local_fronts(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecciona imágenes locales (frontales)",
            filetypes=IMAGE_FILETYPES,
        )
        default_back = self.state.local_backs[0] if self.state.local_backs else None
        added = False
        for p in paths:
            pp = Path(p)
            if pp not in self.state.local_fronts:
                self.state.local_fronts.append(pp)
                self.state.front_back_paths.append(default_back)
                self.state.local_front_crop.append(False)
                added = True
        if added:
            self._refresh_front_rows()
            self._refresh_generate_state()

    def _clear_local_fronts(self) -> None:
        if not self.state.local_fronts:
            return
        self.state.local_fronts.clear()
        self.state.front_back_paths.clear()
        self.state.local_front_crop.clear()
        self._refresh_front_rows()
        self._refresh_generate_state()

    def _remove_front(self, idx: int) -> None:
        if 0 <= idx < len(self.state.local_fronts):
            del self.state.local_fronts[idx]
            del self.state.front_back_paths[idx]
            del self.state.local_front_crop[idx]
            self._refresh_front_rows()
            self._refresh_generate_state()

    def _on_front_back_change(self, idx: int, var: tk.StringVar) -> None:
        choice = var.get()
        try:
            n = int(choice)
        except (TypeError, ValueError):
            self.state.front_back_paths[idx] = None
            return
        if 1 <= n <= len(self.state.local_backs):
            self.state.front_back_paths[idx] = self.state.local_backs[n - 1]

    def _on_front_crop_change(self, idx: int, var: tk.BooleanVar) -> None:
        if 0 <= idx < len(self.state.local_front_crop):
            self.state.local_front_crop[idx] = bool(var.get())

    def _on_front_crop_all(self) -> None:
        val = bool(self._front_crop_all.get())
        for i in range(len(self.state.local_front_crop)):
            self.state.local_front_crop[i] = val
        self._refresh_front_rows()

    def _on_back_crop_all(self) -> None:
        val = bool(self._back_crop_all.get())
        for i in range(len(self.state.local_back_crop)):
            self.state.local_back_crop[i] = val
        self._refresh_back_rows()

    def _refresh_front_rows(self) -> None:
        for row in self._front_rows:
            row["frame"].destroy()
        self._front_rows.clear()

        if not self.state.local_fronts:
            self._fronts_empty_label.pack(anchor="w")
            self._fronts_header_var.set("Frontales (asignar trasera por carta):")
            return
        self._fronts_empty_label.pack_forget()

        numbered = [str(i) for i in range(1, len(self.state.local_backs) + 1)]
        combo_values = ["—", *numbered]
        backs_present = bool(numbered)

        for i, front_path in enumerate(self.state.local_fronts):
            row = ttk.Frame(self.fronts_inner)
            row.pack(fill=tk.X, pady=1, padx=2)

            ttk.Label(row, text=f"{i + 1:>3}.", width=4, anchor="e").pack(side=tk.LEFT)
            front_name_lbl = ttk.Label(
                row,
                text=ellipsize(front_path.name, FRONT_NAME_WIDTH),
                width=FRONT_NAME_WIDTH + 1,
                anchor="w",
            )
            front_name_lbl.pack(side=tk.LEFT, padx=(4, 8))
            ImageTooltip(front_name_lbl, front_path)

            ttk.Label(row, text="Trasera:").pack(side=tk.LEFT)

            assigned = self.state.front_back_paths[i]
            if assigned is not None and assigned not in self.state.local_backs:
                assigned = None
                self.state.front_back_paths[i] = None
            var = tk.StringVar()
            if assigned is None:
                var.set("—")
            else:
                var.set(str(self.state.local_backs.index(assigned) + 1))
            combo = ttk.Combobox(
                row,
                values=combo_values,
                textvariable=var,
                state="readonly" if backs_present else "disabled",
                width=4,
            )
            combo.bind(
                "<<ComboboxSelected>>",
                lambda _e, idx=i, v=var: self._on_front_back_change(idx, v),
            )
            combo.pack(side=tk.LEFT, padx=(4, 6))

            crop_var = tk.BooleanVar(value=self.state.local_front_crop[i])
            ttk.Checkbutton(
                row,
                text="Recortar bordes extra",
                variable=crop_var,
                command=lambda idx=i, v=crop_var: self._on_front_crop_change(idx, v),
            ).pack(side=tk.LEFT, padx=(4, 6))

            ttk.Button(
                row,
                text="✕",
                width=2,
                command=lambda idx=i: self._remove_front(idx),
            ).pack(side=tk.RIGHT)
            ttk.Button(
                row,
                text="▼",
                width=2,
                command=lambda idx=i: self._move_front_down(idx),
            ).pack(side=tk.RIGHT, padx=(0, 1))
            ttk.Button(
                row,
                text="▲",
                width=2,
                command=lambda idx=i: self._move_front_up(idx),
            ).pack(side=tk.RIGHT, padx=(0, 1))

            self._front_rows.append(
                {"frame": row, "var": var, "combo": combo, "crop_var": crop_var},
            )

        self.fronts_inner.update_idletasks()
        self.fronts_canvas.configure(scrollregion=self.fronts_canvas.bbox("all"))

        total = len(self.state.local_fronts)
        self._fronts_header_var.set(
            f"Frontales (asignar trasera por carta):   Actualmente: {total} cartas"
        )

    def _move_back_up(self, idx: int) -> None:
        if idx > 0:
            self.state.local_backs[idx], self.state.local_backs[idx - 1] = (
                self.state.local_backs[idx - 1],
                self.state.local_backs[idx],
            )
            self.state.local_back_crop[idx], self.state.local_back_crop[idx - 1] = (
                self.state.local_back_crop[idx - 1],
                self.state.local_back_crop[idx],
            )
            self._refresh_back_rows()
            self._refresh_front_rows()

    def _move_back_down(self, idx: int) -> None:
        if idx < len(self.state.local_backs) - 1:
            self.state.local_backs[idx], self.state.local_backs[idx + 1] = (
                self.state.local_backs[idx + 1],
                self.state.local_backs[idx],
            )
            self.state.local_back_crop[idx], self.state.local_back_crop[idx + 1] = (
                self.state.local_back_crop[idx + 1],
                self.state.local_back_crop[idx],
            )
            self._refresh_back_rows()
            self._refresh_front_rows()

    def _move_front_up(self, idx: int) -> None:
        if idx > 0:
            self.state.local_fronts[idx], self.state.local_fronts[idx - 1] = (
                self.state.local_fronts[idx - 1],
                self.state.local_fronts[idx],
            )
            self.state.front_back_paths[idx], self.state.front_back_paths[idx - 1] = (
                self.state.front_back_paths[idx - 1],
                self.state.front_back_paths[idx],
            )
            self.state.local_front_crop[idx], self.state.local_front_crop[idx - 1] = (
                self.state.local_front_crop[idx - 1],
                self.state.local_front_crop[idx],
            )
            self._refresh_front_rows()

    def _move_front_down(self, idx: int) -> None:
        if idx < len(self.state.local_fronts) - 1:
            self.state.local_fronts[idx], self.state.local_fronts[idx + 1] = (
                self.state.local_fronts[idx + 1],
                self.state.local_fronts[idx],
            )
            self.state.front_back_paths[idx], self.state.front_back_paths[idx + 1] = (
                self.state.front_back_paths[idx + 1],
                self.state.front_back_paths[idx],
            )
            self.state.local_front_crop[idx], self.state.local_front_crop[idx + 1] = (
                self.state.local_front_crop[idx + 1],
                self.state.local_front_crop[idx],
            )
            self._refresh_front_rows()

    def _on_drop_backs(self, files) -> None:
        paths = self._decode_drop(files)
        was_empty = not self.state.local_backs
        added = False
        for pp in paths:
            if pp.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
                continue
            if pp not in self.state.local_backs:
                self.state.local_backs.append(pp)
                self.state.local_back_crop.append(False)
                added = True
        if added:
            if was_empty and self.state.local_backs:
                first = self.state.local_backs[0]
                for i, assigned in enumerate(self.state.front_back_paths):
                    if assigned is None:
                        self.state.front_back_paths[i] = first
            self._refresh_back_rows()
            self._refresh_front_rows()
            self._refresh_generate_state()

    def _on_drop_fronts(self, files) -> None:
        paths = self._decode_drop(files)
        default_back = self.state.local_backs[0] if self.state.local_backs else None
        added = False
        for pp in paths:
            if pp.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
                continue
            if pp not in self.state.local_fronts:
                self.state.local_fronts.append(pp)
                self.state.front_back_paths.append(default_back)
                self.state.local_front_crop.append(False)
                added = True
        if added:
            self._refresh_front_rows()
            self._refresh_generate_state()


# ===========================================================================
# 3. One Piece Scraper Sub-Tab Mixin
# ===========================================================================


class OPTabMixin(GameScraperTabMixin):
    """Methods for the One Piece scraper tab."""

    def _build_onepiece_tab(self, parent: ttk.Frame) -> None:
        spec = ScraperTabSpec(
            game_id="op",
            game_name="One Piece",
            sites_hint="Webs aceptadas: onepiece.gg, egmanevents.com, cardkaizoku.com",
            empty_hint="(introduce una URL de mazo y haz clic en «Añadir»)",
            scrape_fn=op_scrape_deck,
            loaded_event="op_deck_loaded",
            error_event="op_deck_error",
        )
        self._op_spec = spec
        (
            self._op_url_var,
            self._op_url_entry,
            self._op_load_btn,
            self._op_status_var,
            self._op_canvas,
            self._op_inner,
            self._op_empty_label,
        ) = self._build_generic_scraper_tab(parent, spec)

        self._op_url_entry.bind("<Return>", lambda _e: self._op_load_deck())
        self._op_load_btn.configure(command=self._op_load_deck)

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=2, column=0, sticky="ew", padx=6, pady=(2, 6))
        ttk.Button(btn_row, text="Vaciar todo", command=self._op_clear).pack(side=tk.LEFT)

    def _op_load_deck(self) -> None:
        self._dispatch_scrape_deck(
            self._op_url_var,
            self._op_load_btn,
            self._op_status_var,
            self._op_spec,
        )

    def _op_refresh_rows(self) -> None:
        for row in self._op_deck_rows:
            row["outer"].destroy()
        self._op_deck_rows.clear()

        if not self._op_decks:
            self._op_empty_label.pack(anchor="w")
            return
        self._op_empty_label.pack_forget()

        for idx, deck in enumerate(self._op_decks):
            leader = deck.leader

            outer = ttk.Frame(self._op_inner, relief="groove", borderwidth=1)
            outer.pack(fill=tk.X, pady=3, padx=2)
            outer.columnconfigure(0, weight=1)

            summary = ttk.Frame(outer)
            summary.pack(fill=tk.X, padx=6, pady=4)
            summary.columnconfigure(1, weight=1)

            ttk.Label(
                summary, text=ellipsize(deck.name, 22), font=("Segoe UI", 9, "bold"), anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=(0, 12))

            if leader:
                color_txt = " / ".join(leader.colors)
                leader_txt = f"Líder: {ellipsize(leader.name, 18)}  ({color_txt})"
            else:
                leader_txt = "(sin líder)"
            ttk.Label(summary, text=leader_txt, foreground="#555", anchor="w").grid(
                row=0, column=1, sticky="w"
            )

            ttk.Label(
                summary, text=f"{deck.total_slots} cartas", foreground="#888", anchor="e"
            ).grid(row=0, column=2, sticky="e", padx=(8, 6))

            expanded_var = tk.BooleanVar(value=False)
            toggle_btn = ttk.Button(
                summary,
                text="Detalles ▼",
                width=10,
                command=lambda i=idx: self._op_toggle_details(i),
            )
            toggle_btn.grid(row=0, column=3, padx=(0, 4))

            ttk.Button(
                summary,
                text="✕",
                width=2,
                command=lambda i=idx: self._op_remove_deck(i),
            ).grid(row=0, column=4)

            detail = ttk.Frame(outer)

            for card in deck.cards:
                row_f = ttk.Frame(detail)
                row_f.pack(fill=tk.X, pady=0, padx=(12, 4))

                if card.is_leader:
                    badge_text, badge_fg = "LÍDER", "#1565C0"
                    badge_font = ("Segoe UI", 8, "bold")
                else:
                    badge_text, badge_fg = f"x{card.quantity}", "#444"
                    badge_font = ("Segoe UI", 8)
                ttk.Label(
                    row_f,
                    text=badge_text,
                    foreground=badge_fg,
                    font=badge_font,
                    width=6,
                    anchor="e",
                ).pack(side=tk.LEFT, padx=(0, 6))
                ttk.Label(row_f, text=ellipsize(card.name, 24), anchor="w", width=25).pack(
                    side=tk.LEFT
                )
                ttk.Label(
                    row_f,
                    text=card.card_id,
                    foreground="#888",
                    font=("Segoe UI", 8),
                    anchor="w",
                    width=10,
                ).pack(side=tk.LEFT, padx=(4, 0))
                if card.colors:
                    ttk.Label(
                        row_f, text=" / ".join(card.colors), foreground="#555", font=("Segoe UI", 8)
                    ).pack(side=tk.LEFT, padx=(6, 0))

            self._op_deck_rows.append(
                {
                    "outer": outer,
                    "detail": detail,
                    "toggle_btn": toggle_btn,
                    "expanded": expanded_var,
                    "deck": deck,
                }
            )

        self._op_inner.update_idletasks()
        self._op_canvas.configure(scrollregion=self._op_canvas.bbox("all"))

    def _op_toggle_details(self, idx: int) -> None:
        if idx >= len(self._op_deck_rows):
            return
        row = self._op_deck_rows[idx]
        expanded = row["expanded"]
        if expanded.get():
            row["detail"].pack_forget()
            row["toggle_btn"].configure(text="Detalles ▼")
            expanded.set(False)
        else:
            row["detail"].pack(fill=tk.X, padx=0, pady=(0, 4))
            row["toggle_btn"].configure(text="Detalles ▲")
            expanded.set(True)
        self._op_inner.update_idletasks()
        self._op_canvas.configure(scrollregion=self._op_canvas.bbox("all"))

    def _op_remove_deck(self, idx: int) -> None:
        if 0 <= idx < len(self._op_decks):
            del self._op_decks[idx]
            self._op_refresh_rows()
            self._refresh_generate_state()

    def _op_clear(self) -> None:
        self._op_decks.clear()
        self._op_url_var.set("")
        self._op_status_var.set("")
        self._op_load_btn.state(["!disabled"])
        self._op_refresh_rows()
        self._refresh_generate_state()


# ===========================================================================
# 4. Riftbound Scraper Sub-Tab Mixin
# ===========================================================================


class RBTabMixin(GameScraperTabMixin):
    """Methods for the Riftbound scraper tab."""

    def _build_riftbound_tab(self, parent: ttk.Frame) -> None:
        spec = ScraperTabSpec(
            game_id="rb",
            game_name="Riftbound",
            sites_hint="Webs aceptadas: piltoverarchive.com, riftbound.gg, riftmana.com, riftbinder.com, riftdex.com",
            empty_hint="(introduce una URL de piltoverarchive.com, riftbound.gg, riftmana.com, riftbinder.com o riftdex.com)",
            scrape_fn=rb_scrape_deck,
            loaded_event="rb_deck_loaded",
            error_event="rb_deck_error",
        )
        self._rb_spec = spec
        (
            self._rb_url_var,
            self._rb_url_entry,
            self._rb_load_btn,
            self._rb_status_var,
            self._rb_canvas,
            self._rb_inner,
            self._rb_empty_label,
        ) = self._build_generic_scraper_tab(parent, spec)

        self._rb_url_entry.bind("<Return>", lambda _e: self._rb_load_deck())
        self._rb_load_btn.configure(command=self._rb_load_deck)

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=2, column=0, sticky="ew", padx=6, pady=(2, 6))
        ttk.Button(btn_row, text="Vaciar todo", command=self._rb_clear).pack(side=tk.LEFT)

    def _rb_load_deck(self) -> None:
        self._dispatch_scrape_deck(
            self._rb_url_var,
            self._rb_load_btn,
            self._rb_status_var,
            self._rb_spec,
        )

    def _rb_refresh_rows(self) -> None:
        for row in self._rb_deck_rows:
            row["outer"].destroy()
        self._rb_deck_rows.clear()

        if not self._rb_decks:
            self._rb_empty_label.pack(anchor="w")
            return
        self._rb_empty_label.pack_forget()

        for idx, deck in enumerate(self._rb_decks):
            outer = ttk.Frame(self._rb_inner, relief="groove", borderwidth=1)
            outer.pack(fill=tk.X, pady=3, padx=2)
            outer.columnconfigure(0, weight=1)

            summary = ttk.Frame(outer)
            summary.pack(fill=tk.X, padx=6, pady=4)
            summary.columnconfigure(1, weight=1)

            ttk.Label(
                summary, text=ellipsize(deck.name, 28), font=("Segoe UI", 9, "bold"), anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=(0, 12))

            by_sec = deck.by_section()
            sec_parts = [
                f"{_RB_SECTION_LABELS.get(s, s)}: {sum(c.quantity for c in cards)}"
                for s, cards in by_sec.items()
                if cards
            ]
            ttk.Label(
                summary,
                text="  |  ".join(sec_parts),
                foreground="#555",
                anchor="w",
                font=("Segoe UI", 8),
            ).grid(row=0, column=1, sticky="w")

            ttk.Label(
                summary, text=f"{deck.total_slots} cartas", foreground="#888", anchor="e"
            ).grid(row=0, column=2, sticky="e", padx=(8, 6))

            print_runes_var = tk.BooleanVar(value=True)
            has_runes = bool(by_sec.get("rune"))
            runes_cb = ttk.Checkbutton(
                summary,
                text="imprimir runas",
                variable=print_runes_var,
            )
            if not has_runes:
                runes_cb.state(["disabled"])
            runes_cb.grid(row=0, column=3, padx=(4, 8))

            expanded_var = tk.BooleanVar(value=False)
            toggle_btn = ttk.Button(
                summary,
                text="Detalles ▼",
                width=10,
                command=lambda i=idx: self._rb_toggle_details(i),
            )
            toggle_btn.grid(row=0, column=4, padx=(0, 4))

            ttk.Button(
                summary,
                text="✕",
                width=2,
                command=lambda i=idx: self._rb_remove_deck(i),
            ).grid(row=0, column=5)

            detail = ttk.Frame(outer)

            for section, cards in by_sec.items():
                if not cards:
                    continue
                sec_lbl = _RB_SECTION_LABELS.get(section, section)
                ttk.Label(
                    detail,
                    text=f"── {sec_lbl} ──",
                    foreground="#888",
                    font=("Segoe UI", 8, "italic"),
                    padding=(12, 2, 0, 0),
                ).pack(anchor="w")
                for card in cards:
                    row_f = ttk.Frame(detail)
                    row_f.pack(fill=tk.X, pady=0, padx=(12, 4))
                    ttk.Label(
                        row_f,
                        text=f"x{card.quantity}",
                        foreground="#444",
                        font=("Segoe UI", 8),
                        width=4,
                        anchor="e",
                    ).pack(side=tk.LEFT, padx=(0, 6))
                    ttk.Label(row_f, text=ellipsize(card.name, 26), anchor="w", width=27).pack(
                        side=tk.LEFT
                    )
                    ttk.Label(
                        row_f,
                        text=card.card_type,
                        foreground="#888",
                        font=("Segoe UI", 8),
                        anchor="w",
                        width=12,
                    ).pack(side=tk.LEFT, padx=(4, 0))

            self._rb_deck_rows.append(
                {
                    "outer": outer,
                    "detail": detail,
                    "toggle_btn": toggle_btn,
                    "expanded": expanded_var,
                    "deck": deck,
                    "print_runes_var": print_runes_var,
                }
            )

        self._rb_inner.update_idletasks()
        self._rb_canvas.configure(scrollregion=self._rb_canvas.bbox("all"))

    def _rb_toggle_details(self, idx: int) -> None:
        if idx >= len(self._rb_deck_rows):
            return
        row = self._rb_deck_rows[idx]
        expanded = row["expanded"]
        if expanded.get():
            row["detail"].pack_forget()
            row["toggle_btn"].configure(text="Detalles ▼")
            expanded.set(False)
        else:
            row["detail"].pack(fill=tk.X, padx=0, pady=(0, 4))
            row["toggle_btn"].configure(text="Detalles ▲")
            expanded.set(True)
        self._rb_inner.update_idletasks()
        self._rb_canvas.configure(scrollregion=self._rb_canvas.bbox("all"))

    def _rb_remove_deck(self, idx: int) -> None:
        if 0 <= idx < len(self._rb_decks):
            del self._rb_decks[idx]
            self._rb_refresh_rows()
            self._refresh_generate_state()

    def _rb_clear(self) -> None:
        self._rb_decks.clear()
        self._rb_url_var.set("")
        self._rb_status_var.set("")
        self._rb_load_btn.state(["!disabled"])
        self._rb_refresh_rows()
        self._refresh_generate_state()


# ===========================================================================
# 5. Lorcana Scraper Sub-Tab Mixin
# ===========================================================================


class LorcanaTabMixin(GameScraperTabMixin):
    """Methods for the Lorcana scraper tab."""

    def _build_lorcana_tab(self, parent: ttk.Frame) -> None:
        spec = ScraperTabSpec(
            game_id="lorcana",
            game_name="Lorcana",
            sites_hint="Webs aceptadas: lorcana.gg, inkdecks.com",
            empty_hint="(introduce una URL de lorcana.gg o inkdecks.com)",
            scrape_fn=lorcana_scrape_deck,
            loaded_event="lorcana_deck_loaded",
            error_event="lorcana_deck_error",
        )
        self._lorcana_spec = spec
        (
            self._lorcana_url_var,
            self._lorcana_url_entry,
            self._lorcana_load_btn,
            self._lorcana_status_var,
            self._lorcana_canvas,
            self._lorcana_inner,
            self._lorcana_empty_label,
        ) = self._build_generic_scraper_tab(parent, spec)

        self._lorcana_url_entry.bind("<Return>", lambda _e: self._lorcana_load_deck())
        self._lorcana_load_btn.configure(command=self._lorcana_load_deck)

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=2, column=0, sticky="ew", padx=6, pady=(2, 6))
        ttk.Button(btn_row, text="Vaciar todo", command=self._lorcana_clear).pack(side=tk.LEFT)

    def _lorcana_load_deck(self) -> None:
        self._dispatch_scrape_deck(
            self._lorcana_url_var,
            self._lorcana_load_btn,
            self._lorcana_status_var,
            self._lorcana_spec,
        )

    def _lorcana_refresh_rows(self) -> None:
        for row in self._lorcana_deck_rows:
            row["outer"].destroy()
        self._lorcana_deck_rows.clear()

        if not self._lorcana_decks:
            self._lorcana_empty_label.pack(anchor="w")
            return
        self._lorcana_empty_label.pack_forget()

        source_labels = {
            "lorcana_gg": "lorcana.gg",
            "inkdecks": "inkdecks.com",
        }

        for idx, deck in enumerate(self._lorcana_decks):
            outer = ttk.Frame(self._lorcana_inner, relief="groove", borderwidth=1)
            outer.pack(fill=tk.X, pady=3, padx=2)
            outer.columnconfigure(0, weight=1)

            summary = ttk.Frame(outer)
            summary.pack(fill=tk.X, padx=6, pady=4)
            summary.columnconfigure(1, weight=1)

            ttk.Label(
                summary,
                text=ellipsize(deck.name, 28),
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=(0, 12))

            source_txt = source_labels.get(deck.source, deck.source)
            ttk.Label(
                summary,
                text=source_txt,
                foreground="#555",
                anchor="w",
                font=("Segoe UI", 8),
            ).grid(row=0, column=1, sticky="w")

            ttk.Label(
                summary,
                text=f"{deck.total_slots} cartas",
                foreground="#888",
                anchor="e",
            ).grid(row=0, column=2, sticky="e", padx=(8, 6))

            expanded_var = tk.BooleanVar(value=False)
            toggle_btn = ttk.Button(
                summary,
                text="Detalles ▼",
                width=10,
                command=lambda i=idx: self._lorcana_toggle_details(i),
            )
            toggle_btn.grid(row=0, column=3, padx=(0, 4))

            ttk.Button(
                summary,
                text="✕",
                width=2,
                command=lambda i=idx: self._lorcana_remove_deck(i),
            ).grid(row=0, column=4)

            detail = ttk.Frame(outer)

            for card in deck.cards:
                row_f = ttk.Frame(detail)
                row_f.pack(fill=tk.X, pady=0, padx=(12, 4))
                ttk.Label(
                    row_f,
                    text=f"x{card.quantity}",
                    foreground="#444",
                    font=("Segoe UI", 8),
                    width=4,
                    anchor="e",
                ).pack(side=tk.LEFT, padx=(0, 6))
                ttk.Label(
                    row_f,
                    text=ellipsize(card.name, 30),
                    anchor="w",
                    width=31,
                ).pack(side=tk.LEFT)
                ttk.Label(
                    row_f,
                    text=card.card_id,
                    foreground="#888",
                    font=("Segoe UI", 8),
                    anchor="w",
                    width=10,
                ).pack(side=tk.LEFT, padx=(4, 0))

            self._lorcana_deck_rows.append(
                {
                    "outer": outer,
                    "detail": detail,
                    "toggle_btn": toggle_btn,
                    "expanded": expanded_var,
                    "deck": deck,
                }
            )

        self._lorcana_inner.update_idletasks()
        self._lorcana_canvas.configure(scrollregion=self._lorcana_canvas.bbox("all"))

    def _lorcana_toggle_details(self, idx: int) -> None:
        if idx >= len(self._lorcana_deck_rows):
            return
        row = self._lorcana_deck_rows[idx]
        expanded = row["expanded"]
        if expanded.get():
            row["detail"].pack_forget()
            row["toggle_btn"].configure(text="Detalles ▼")
            expanded.set(False)
        else:
            row["detail"].pack(fill=tk.X, padx=0, pady=(0, 4))
            row["toggle_btn"].configure(text="Detalles ▲")
            expanded.set(True)
        self._lorcana_inner.update_idletasks()
        self._lorcana_canvas.configure(scrollregion=self._lorcana_canvas.bbox("all"))

    def _lorcana_remove_deck(self, idx: int) -> None:
        if 0 <= idx < len(self._lorcana_decks):
            del self._lorcana_decks[idx]
            self._lorcana_refresh_rows()
            self._refresh_generate_state()

    def _lorcana_clear(self) -> None:
        self._lorcana_decks.clear()
        self._lorcana_url_var.set("")
        self._lorcana_status_var.set("")
        self._lorcana_load_btn.state(["!disabled"])
        self._lorcana_refresh_rows()
        self._refresh_generate_state()


# ===========================================================================
# 6. Embedded Settings Sub-Tab Mixin
# ===========================================================================


class SettingsTabMixin:
    """Mixin that builds the Configuración tab and exposes _settings: AppSettings."""

    def _build_settings_tab(self, frame: ttk.Frame) -> None:
        outer = ttk.Frame(frame)
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        ttk.Label(outer, text="Carpeta de exportación de PDFs", font=("Segoe UI", 10, "bold")).pack(
            anchor=tk.W, pady=(0, 2)
        )

        self._settings_out_var = tk.StringVar(value=str(self._settings.output_dir or output_dir()))
        dir_row = ttk.Frame(outer)
        dir_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(
            dir_row, textvariable=self._settings_out_var, foreground="#444", anchor=tk.W
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_row, text="Examinar…", command=self._settings_pick_dir, width=10).pack(
            side=tk.RIGHT, padx=(8, 0)
        )

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 12))

        cols_frame = ttk.Frame(outer)
        cols_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        left_col = ttk.Frame(cols_frame)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 12))

        right_col = ttk.Frame(cols_frame)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        ttk.Label(left_col, text="Líneas de corte", font=("Segoe UI", 10, "bold")).pack(
            anchor=tk.W, pady=(0, 6)
        )

        color_row = ttk.Frame(left_col)
        color_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(color_row, text="Color:").pack(side=tk.LEFT)
        self._color_swatch = tk.Label(
            color_row,
            width=4,
            relief=tk.SOLID,
            borderwidth=1,
            bg=self._settings.cut_line_color,
        )
        self._color_swatch.pack(side=tk.LEFT, padx=(6, 4))
        self._settings_color_var = tk.StringVar(value=self._settings.cut_line_color)
        ttk.Label(color_row, textvariable=self._settings_color_var, foreground="#555").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(color_row, text="Elegir color…", command=self._settings_pick_color).pack(
            side=tk.LEFT
        )

        width_row = ttk.Frame(left_col)
        width_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(width_row, text="Grosor (pt):").pack(side=tk.LEFT)
        self._settings_width_var = tk.StringVar(value=f"{self._settings.cut_line_width:.1f}")
        width_spin = ttk.Spinbox(
            width_row,
            from_=_WIDTH_MIN,
            to=_WIDTH_MAX,
            increment=0.1,
            format="%.1f",
            textvariable=self._settings_width_var,
            width=6,
            command=self._on_settings_width_changed,
        )
        width_spin.pack(side=tk.LEFT, padx=(6, 0))
        self._settings_width_var.trace_add("write", self._on_settings_width_trace)

        ttk.Label(left_col, text="Estilo:").pack(anchor=tk.W, pady=(6, 2))
        self._settings_style_var = tk.StringVar(value=self._settings.cut_line_style)
        self._style_frame = ttk.Frame(left_col)
        style_frame = self._style_frame
        style_frame.pack(anchor=tk.W, pady=(0, 0))
        ttk.Radiobutton(
            style_frame,
            text="Marcas de margen  (para copistería)",
            variable=self._settings_style_var,
            value="ticks",
            command=self._on_settings_style_changed,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            style_frame,
            text="Líneas completas de lado a lado  (para autocorte)",
            variable=self._settings_style_var,
            value="full",
            command=self._on_settings_style_changed,
        ).pack(anchor=tk.W)

        self._over_cards_sub_frame = ttk.Frame(left_col)
        self._settings_over_fronts_var = tk.BooleanVar(value=self._settings.cut_line_over_fronts)
        self._settings_over_backs_var = tk.BooleanVar(value=self._settings.cut_line_over_backs)
        ttk.Checkbutton(
            self._over_cards_sub_frame,
            text="Frontal",
            variable=self._settings_over_fronts_var,
            command=self._on_settings_over_fronts_changed,
        ).pack(anchor=tk.W)
        ttk.Checkbutton(
            self._over_cards_sub_frame,
            text="Trasero",
            variable=self._settings_over_backs_var,
            command=self._on_settings_over_backs_changed,
        ).pack(anchor=tk.W)
        if self._settings.cut_line_style == "full":
            self._over_cards_sub_frame.pack(anchor=tk.W, padx=(20, 0), pady=(4, 0))

        sf_frame = ttk.LabelFrame(right_col, text=" Configuración avanzada de Scryfall ", padding=10)
        sf_frame.pack(fill=tk.BOTH, expand=True)

        lang_row = ttk.Frame(sf_frame)
        lang_row.pack(fill=tk.X, pady=4)
        ttk.Label(lang_row, text="Idioma preferido:").pack(side=tk.LEFT)

        self._scryfall_lang_options = ["Español", "Inglés"]
        self._scryfall_lang_map = {"Español": "es", "Inglés": "en"}
        self._scryfall_lang_inv_map = {"es": "Español", "en": "Inglés"}

        initial_lang_name = self._scryfall_lang_inv_map.get(self._settings.scryfall_lang, "Inglés")
        self._settings_sf_lang_var = tk.StringVar(value=initial_lang_name)

        self._sf_lang_combo = ttk.Combobox(
            lang_row,
            values=self._scryfall_lang_options,
            textvariable=self._settings_sf_lang_var,
            state="readonly",
            width=15,
        )
        self._sf_lang_combo.pack(side=tk.LEFT, padx=(8, 0))
        self._sf_lang_combo.bind("<<ComboboxSelected>>", self._on_sf_lang_changed)

        quality_row = ttk.Frame(sf_frame)
        quality_row.pack(fill=tk.X, pady=4)
        ttk.Label(quality_row, text="Calidad de imagen:").pack(side=tk.LEFT)

        self._scryfall_quality_options = ["Large (Equilibrada)", "PNG (Alta calidad)"]
        self._scryfall_quality_map = {"Large (Equilibrada)": "large", "PNG (Alta calidad)": "png"}
        self._scryfall_quality_inv_map = {"large": "Large (Equilibrada)", "png": "PNG (Alta calidad)"}

        initial_quality_name = self._scryfall_quality_inv_map.get(
            self._settings.scryfall_quality, "Large (Equilibrada)"
        )
        self._settings_sf_quality_var = tk.StringVar(value=initial_quality_name)

        self._sf_quality_combo = ttk.Combobox(
            quality_row,
            values=self._scryfall_quality_options,
            textvariable=self._settings_sf_quality_var,
            state="readonly",
            width=20,
        )
        self._sf_quality_combo.pack(side=tk.LEFT, padx=(8, 0))
        self._sf_quality_combo.bind("<<ComboboxSelected>>", self._on_sf_quality_changed)

        policy_row = ttk.Frame(sf_frame)
        policy_row.pack(fill=tk.X, pady=4)
        self._sf_policy_label = ttk.Label(policy_row, text="En caso de fallo en idioma preferido:")
        self._sf_policy_label.pack(anchor=tk.W)

        self._scryfall_policy_options = [
            "Descargar en inglés",
            "Buscar versión alternativa en idioma preferido y si no descargar en inglés",
        ]
        self._scryfall_policy_map = {
            "Descargar en inglés": "english",
            "Buscar versión alternativa en idioma preferido y si no descargar en inglés": "alternative",
        }
        self._scryfall_policy_inv_map = {
            "english": "Descargar en inglés",
            "alternative": "Buscar versión alternativa en idioma preferido y si no descargar en inglés",
        }

        initial_policy_name = self._scryfall_policy_inv_map.get(
            self._settings.scryfall_fail_policy, "Descargar en inglés"
        )
        self._settings_sf_policy_var = tk.StringVar(value=initial_policy_name)

        self._sf_policy_combo = ttk.Combobox(
            policy_row,
            values=self._scryfall_policy_options,
            textvariable=self._settings_sf_policy_var,
            state="readonly",
        )
        self._sf_policy_combo.pack(fill=tk.X, pady=(2, 0))
        self._sf_policy_combo.bind("<<ComboboxSelected>>", self._on_sf_policy_changed)

        self._update_sf_policy_state()

        qc_row = ttk.Frame(sf_frame)
        qc_row.pack(fill=tk.X, pady=4)
        self._settings_sf_quality_check_var = tk.BooleanVar(
            value=self._settings.scryfall_quality_check
        )
        self._sf_quality_check_cb = ttk.Checkbutton(
            qc_row,
            text="Controlar calidad (evitar borrosas)",
            variable=self._settings_sf_quality_check_var,
            command=self._on_sf_quality_check_changed,
        )
        self._sf_quality_check_cb.pack(anchor=tk.W)

        bc_row = ttk.Frame(sf_frame)
        bc_row.pack(fill=tk.X, pady=4)
        self._settings_sf_bc_check_var = tk.BooleanVar(
            value=self._settings.scryfall_brightness_contrast_check
        )
        self._sf_bc_check_cb = ttk.Checkbutton(
            bc_row,
            text="Controlar brillo y contraste",
            variable=self._settings_sf_bc_check_var,
            command=self._on_sf_bc_check_changed,
        )
        self._sf_bc_check_cb.pack(anchor=tk.W)

        blur_row = ttk.Frame(sf_frame)
        blur_row.pack(fill=tk.X, pady=4)
        self._sf_blur_label = ttk.Label(blur_row, text="Umbral de enfoque (blur):")
        self._sf_blur_label.pack(side=tk.LEFT)
        self._settings_sf_blur_threshold_var = tk.StringVar(
            value=f"{self._settings.scryfall_blur_threshold}"
        )
        self._sf_blur_entry = ttk.Entry(
            blur_row,
            textvariable=self._settings_sf_blur_threshold_var,
            width=8,
        )
        self._sf_blur_entry.pack(side=tk.LEFT, padx=(8, 0))
        self._settings_sf_blur_threshold_var.trace_add("write", self._on_sf_blur_threshold_trace)

        self._update_sf_quality_check_state()

        sep_row = ttk.Frame(sf_frame)
        sep_row.pack(fill=tk.X, pady=4)
        self._settings_sf_deck_separators_var = tk.BooleanVar(
            value=self._settings.scryfall_deck_separators
        )
        self._sf_deck_separators_cb = ttk.Checkbutton(
            sep_row,
            text="Insertar separadores de mazos",
            variable=self._settings_sf_deck_separators_var,
            command=self._on_sf_deck_separators_changed,
        )
        self._sf_deck_separators_cb.pack(anchor=tk.W)

        min_tok_row = ttk.Frame(sf_frame)
        min_tok_row.pack(fill=tk.X, pady=4)
        self._settings_sf_minimize_tokens_var = tk.BooleanVar(
            value=self._settings.scryfall_minimize_tokens
        )
        self._sf_minimize_tokens_cb = ttk.Checkbutton(
            min_tok_row,
            text="Minimizar fichas (unificar entre mazos)",
            variable=self._settings_sf_minimize_tokens_var,
            command=self._on_sf_minimize_tokens_changed,
        )
        self._sf_minimize_tokens_cb.pack(anchor=tk.W)

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 12))

        ttk.Button(
            outer,
            text="Volver a valores predeterminados",
            command=self._settings_reset,
        ).pack(anchor=tk.W)

    def _settings_pick_dir(self) -> None:
        current = self._settings.output_dir or output_dir()
        chosen = filedialog.askdirectory(
            title="Selecciona la carpeta de exportación de PDFs",
            initialdir=str(current),
        )
        if chosen:
            p = Path(chosen)
            self._settings.output_dir = p
            self._custom_output_dir = p
            self._settings_out_var.set(str(p))
            self.out_dir_var.set(str(p))
            self._persist_settings()

    def _settings_pick_color(self) -> None:
        result = colorchooser.askcolor(
            color=self._settings.cut_line_color,
            title="Elige el color de las líneas de corte",
        )
        if result and result[1]:
            hex_color: str = result[1]
            self._settings.cut_line_color = hex_color
            self._settings_color_var.set(hex_color)
            self._color_swatch.configure(bg=hex_color)
            self._persist_settings()

    def _on_settings_width_changed(self) -> None:
        self._apply_width_from_var()

    def _on_settings_width_trace(self, *_) -> None:
        self._apply_width_from_var()

    def _apply_width_from_var(self) -> None:
        try:
            val = float(self._settings_width_var.get().replace(",", "."))
            val = max(_WIDTH_MIN, min(_WIDTH_MAX, val))
            self._settings.cut_line_width = val
            self._persist_settings()
        except ValueError:
            pass

    def _on_settings_style_changed(self) -> None:
        self._settings.cut_line_style = self._settings_style_var.get()
        is_full = self._settings.cut_line_style == "full"
        self._settings.cut_line_over_cards = is_full
        if is_full:
            self._settings_over_fronts_var.set(True)
            self._settings_over_backs_var.set(True)
            self._settings.cut_line_over_fronts = True
            self._settings.cut_line_over_backs = True
            self._over_cards_sub_frame.pack(
                anchor=tk.W, padx=(20, 0), pady=(4, 0), after=self._style_frame
            )
        else:
            self._over_cards_sub_frame.pack_forget()
        self._persist_settings()

    def _on_settings_over_fronts_changed(self) -> None:
        self._settings.cut_line_over_fronts = self._settings_over_fronts_var.get()
        self._persist_settings()

    def _on_settings_over_backs_changed(self) -> None:
        self._settings.cut_line_over_backs = self._settings_over_backs_var.get()
        self._persist_settings()

    def _update_sf_policy_state(self) -> None:
        lang_val = self._scryfall_lang_map.get(self._settings_sf_lang_var.get(), "en")
        if lang_val == "en":
            self._sf_policy_combo.configure(state="disabled")
            self._sf_policy_label.configure(foreground="#888")
        else:
            self._sf_policy_combo.configure(state="readonly")
            self._sf_policy_label.configure(foreground="")

    def _on_sf_lang_changed(self, _event=None) -> None:
        lang_name = self._settings_sf_lang_var.get()
        lang_code = self._scryfall_lang_map.get(lang_name, "en")
        self._settings.scryfall_lang = lang_code
        self._update_sf_policy_state()
        self._persist_settings()

    def _on_sf_quality_changed(self, _event=None) -> None:
        quality_name = self._settings_sf_quality_var.get()
        quality_code = self._scryfall_quality_map.get(quality_name, "large")
        self._settings.scryfall_quality = quality_code
        self._persist_settings()

    def _on_sf_policy_changed(self, _event=None) -> None:
        policy_name = self._settings_sf_policy_var.get()
        policy_code = self._scryfall_policy_map.get(policy_name, "english")
        self._settings.scryfall_fail_policy = policy_code
        self._persist_settings()

    def _update_sf_quality_check_state(self) -> None:
        if self._settings_sf_quality_check_var.get():
            self._sf_blur_entry.configure(state="normal")
            self._sf_blur_label.configure(foreground="")
            self._sf_bc_check_cb.configure(state="normal")
        else:
            self._sf_blur_entry.configure(state="disabled")
            self._sf_blur_label.configure(foreground="#888")
            self._sf_bc_check_cb.configure(state="disabled")

    def _on_sf_quality_check_changed(self) -> None:
        self._settings.scryfall_quality_check = self._settings_sf_quality_check_var.get()
        self._update_sf_quality_check_state()
        self._persist_settings()

    def _on_sf_bc_check_changed(self) -> None:
        self._settings.scryfall_brightness_contrast_check = self._settings_sf_bc_check_var.get()
        self._persist_settings()

    def _on_sf_deck_separators_changed(self) -> None:
        self._settings.scryfall_deck_separators = self._settings_sf_deck_separators_var.get()
        self._persist_settings()

    def _on_sf_minimize_tokens_changed(self) -> None:
        self._settings.scryfall_minimize_tokens = self._settings_sf_minimize_tokens_var.get()
        self._persist_settings()

    def _on_sf_blur_threshold_trace(self, *_) -> None:
        try:
            val = float(self._settings_sf_blur_threshold_var.get().replace(",", "."))
            if val >= 0.0:
                self._settings.scryfall_blur_threshold = val
                self._persist_settings()
        except ValueError:
            pass

    def _settings_reset(self) -> None:
        self._settings.output_dir = None
        self._settings.cut_line_color = DEFAULT_CUT_LINE_COLOR
        self._settings.cut_line_style = DEFAULT_CUT_LINE_STYLE
        self._settings.cut_line_width = DEFAULT_CUT_LINE_WIDTH
        self._settings.cut_line_over_cards = DEFAULT_CUT_LINE_OVER_CARDS
        self._settings.cut_line_over_fronts = DEFAULT_CUT_LINE_OVER_FRONTS
        self._settings.cut_line_over_backs = DEFAULT_CUT_LINE_OVER_BACKS

        self._settings.scryfall_lang = DEFAULT_SCRYFALL_LANG
        self._settings.scryfall_quality = DEFAULT_SCRYFALL_QUALITY
        self._settings.scryfall_fail_policy = DEFAULT_SCRYFALL_FAIL_POLICY
        self._settings.scryfall_quality_check = DEFAULT_SCRYFALL_QUALITY_CHECK
        self._settings.scryfall_blur_threshold = DEFAULT_SCRYFALL_BLUR_THRESHOLD
        self._settings.scryfall_brightness_contrast_check = DEFAULT_SCRYFALL_BC_CHECK
        self._settings.scryfall_download_sideboard = DEFAULT_SCRYFALL_DOWNLOAD_SIDEBOARD
        self._settings.scryfall_download_tokens = DEFAULT_SCRYFALL_DOWNLOAD_TOKENS
        self._settings.scryfall_deck_separators = DEFAULT_SCRYFALL_DECK_SEPARATORS
        self._settings.scryfall_minimize_tokens = DEFAULT_SCRYFALL_MINIMIZE_TOKENS

        self._custom_output_dir = None
        self.out_dir_var.set(str(output_dir()))
        self._settings_out_var.set(str(output_dir()))
        self._settings_color_var.set(DEFAULT_CUT_LINE_COLOR)
        self._color_swatch.configure(bg=DEFAULT_CUT_LINE_COLOR)
        self._settings_style_var.set(DEFAULT_CUT_LINE_STYLE)
        self._settings_width_var.set(f"{DEFAULT_CUT_LINE_WIDTH:.1f}")
        self._settings_over_fronts_var.set(DEFAULT_CUT_LINE_OVER_FRONTS)
        self._settings_over_backs_var.set(DEFAULT_CUT_LINE_OVER_BACKS)
        self._over_cards_sub_frame.pack_forget()

        self._settings_sf_lang_var.set(self._scryfall_lang_inv_map.get(DEFAULT_SCRYFALL_LANG))
        self._settings_sf_quality_var.set(
            self._scryfall_quality_inv_map.get(DEFAULT_SCRYFALL_QUALITY)
        )
        self._settings_sf_policy_var.set(
            self._scryfall_policy_inv_map.get(DEFAULT_SCRYFALL_FAIL_POLICY)
        )
        self._update_sf_policy_state()

        self._settings_sf_quality_check_var.set(DEFAULT_SCRYFALL_QUALITY_CHECK)
        self._settings_sf_blur_threshold_var.set(f"{DEFAULT_SCRYFALL_BLUR_THRESHOLD}")
        self._settings_sf_bc_check_var.set(DEFAULT_SCRYFALL_BC_CHECK)
        self._settings_sf_deck_separators_var.set(DEFAULT_SCRYFALL_DECK_SEPARATORS)
        self._settings_sf_minimize_tokens_var.set(DEFAULT_SCRYFALL_MINIMIZE_TOKENS)
        self._update_sf_quality_check_state()

        self._persist_settings()

    def _persist_settings(self) -> None:
        save_settings(self._settings, app_base_dir())
