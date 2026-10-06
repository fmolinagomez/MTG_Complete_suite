"""Settings view — General and Scryfall application configuration."""

from __future__ import annotations

import logging
import shutil
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from gui.paths import app_base_dir, output_dir, work_dir
from src.app_settings import (
    DEFAULT_CUT_LINE_COLOR,
    DEFAULT_CUT_LINE_OVER_BACKS,
    DEFAULT_CUT_LINE_OVER_CARDS,
    DEFAULT_CUT_LINE_OVER_FRONTS,
    DEFAULT_CUT_LINE_STYLE,
    DEFAULT_CUT_LINE_WIDTH,
    DEFAULT_MAX_PDF_SIZE_MB,
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
    MAX_MAX_PDF_SIZE_MB,
    MIN_MAX_PDF_SIZE_MB,
    AppSettings,
    load_settings,
    save_settings,
)

_log = logging.getLogger(__name__)

_WIDTH_MIN = 0.1
_WIDTH_MAX = 10.0


class SettingsView(ttk.Frame):
    """Standalone settings view allowing configuration of export directories, cut lines, and Scryfall rules."""

    def __init__(
        self,
        master: tk.Misc,
        on_navigate: Callable[[str], None] | None = None,
        settings: AppSettings | None = None,
        base_dir: Path | None = None,
        **kw,
    ) -> None:
        super().__init__(master, **kw)
        self._on_navigate = on_navigate
        self._base_dir = Path(base_dir) if base_dir is not None else app_base_dir()
        self._settings = settings if settings is not None else load_settings(self._base_dir)

        self._setup_styles()
        self._build_ui()

    @property
    def settings(self) -> AppSettings:
        """Return the current active AppSettings."""
        return self._settings

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.configure(
                "SettingsTitle.TLabel", font=("Segoe UI", 16, "bold"), foreground="#1a1a1a"
            )
            style.configure("SettingsSub.TLabel", font=("Segoe UI", 9), foreground="#555555")
            style.configure(
                "SettingsSection.TLabel", font=("Segoe UI", 10, "bold"), foreground="#222222"
            )
            style.configure("SettingsHint.TLabel", font=("Segoe UI", 8), foreground="#777777")
        except Exception as e:
            _log.debug("Could not configure ttk style for SettingsView: %s", e)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── Header Bar ──────────────────────────────────────────────────────
        header_frame = ttk.Frame(outer)
        header_frame.pack(fill=tk.X, pady=(0, 12))

        title_box = ttk.Frame(header_frame)
        title_box.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(
            title_box,
            text="⚙️ Configuración",
            style="SettingsTitle.TLabel",
        ).pack(anchor=tk.W)

        ttk.Label(
            title_box,
            text="Ajusta las preferencias de impresión, líneas de corte, directorios y Scryfall.",
            style="SettingsSub.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        if self._on_navigate:
            ttk.Button(
                header_frame,
                text="← Volver al Inicio",
                command=lambda: self._on_navigate("hub"),
            ).pack(side=tk.RIGHT, padx=(8, 0))

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 12))

        # ── Export Directory Section ────────────────────────────────────────
        dir_section = ttk.LabelFrame(outer, text=" Carpeta de exportación de PDFs ", padding=10)
        dir_section.pack(fill=tk.X, pady=(0, 12))

        self._settings_out_var = tk.StringVar(value=str(self._settings.output_dir or output_dir()))
        dir_row = ttk.Frame(dir_section)
        dir_row.pack(fill=tk.X)

        self._dir_lbl = ttk.Label(
            dir_row,
            textvariable=self._settings_out_var,
            foreground="#333333",
            anchor=tk.W,
        )
        self._dir_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._dir_btn = ttk.Button(
            dir_row,
            text="Examinar…",
            command=self._settings_pick_dir,
            width=12,
        )
        self._dir_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # ── Tamaño máximo de PDF ─────────────────────────────────────────────
        pdf_size_section = ttk.LabelFrame(outer, text=" Tamaño máximo de PDF ", padding=10)
        pdf_size_section.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(
            pdf_size_section,
            text="(Seteado en 200Mb para que se puedan subir a la web, puedes editarlo para utilizar WeTransfer)",
            foreground="#555",
            wraplength=600,
        ).pack(anchor=tk.W, pady=(0, 6))
        pdf_size_row = ttk.Frame(pdf_size_section)
        pdf_size_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(pdf_size_row, text="Máximo por archivo (MB):").pack(side=tk.LEFT)
        self._settings_max_pdf_size_var = tk.StringVar(value=str(self._settings.max_pdf_size_mb))
        self._settings_max_pdf_size_var.trace_add(
            "write", lambda *_: self._save_max_pdf_size_input()
        )
        ttk.Entry(
            pdf_size_row,
            textvariable=self._settings_max_pdf_size_var,
            width=6,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(
            pdf_size_section,
            text="Se crea un PDF nuevo al alcanzar este tamaño estimado.",
            foreground="#555",
        ).pack(anchor=tk.W)

        # ── 2-Column Split: Cut Lines & Scryfall Options ────────────────────
        cols_frame = ttk.Frame(outer)
        cols_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        cols_frame.columnconfigure(0, weight=1, uniform="settings_cols")
        cols_frame.columnconfigure(1, weight=1, uniform="settings_cols")

        # ── Left Column: Cut Lines & Storage ────────────────────────────────
        left_col = ttk.Frame(cols_frame)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self._build_cut_lines_section(left_col)
        self._build_cache_section(left_col)

        # ── Right Column: Scryfall Settings ─────────────────────────────────
        right_col = ttk.Frame(cols_frame)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_scryfall_section(right_col)

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(4, 12))

        # ── Bottom Controls ─────────────────────────────────────────────────
        bottom_bar = ttk.Frame(outer)
        bottom_bar.pack(fill=tk.X)

        ttk.Button(
            bottom_bar,
            text="Volver a valores predeterminados",
            command=self._settings_reset,
        ).pack(side=tk.LEFT)

    def _build_cut_lines_section(self, parent: ttk.Frame) -> None:
        cut_frame = ttk.LabelFrame(parent, text=" Líneas de corte ", padding=10)
        cut_frame.pack(fill=tk.X, pady=(0, 10))

        # Color
        color_row = ttk.Frame(cut_frame)
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

        # Grosor
        width_row = ttk.Frame(cut_frame)
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

        # Estilo
        ttk.Label(cut_frame, text="Estilo:").pack(anchor=tk.W, pady=(4, 2))
        self._settings_style_var = tk.StringVar(value=self._settings.cut_line_style)
        self._style_frame = ttk.Frame(cut_frame)
        self._style_frame.pack(anchor=tk.W, pady=(0, 0))

        ttk.Radiobutton(
            self._style_frame,
            text="Marcas de margen  (para copistería)",
            variable=self._settings_style_var,
            value="ticks",
            command=self._on_settings_style_changed,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            self._style_frame,
            text="Líneas completas de lado a lado  (para autocorte)",
            variable=self._settings_style_var,
            value="full",
            command=self._on_settings_style_changed,
        ).pack(anchor=tk.W)

        self._over_cards_sub_frame = ttk.Frame(cut_frame)
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

    def _build_cache_section(self, parent: ttk.Frame) -> None:
        cache_frame = ttk.LabelFrame(parent, text=" Almacenamiento y Caché ", padding=10)
        cache_frame.pack(fill=tk.X)

        cache_dir = work_dir()
        self._cache_dir_var = tk.StringVar(value=str(cache_dir))
        ttk.Label(cache_frame, text="Carpeta de trabajo y descargas:").pack(anchor=tk.W)
        ttk.Label(
            cache_frame,
            textvariable=self._cache_dir_var,
            foreground="#666666",
            wraplength=280,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 6))

        ttk.Button(
            cache_frame,
            text="Limpiar caché de procesamiento",
            command=self._clear_cache,
        ).pack(anchor=tk.W)

    def _build_scryfall_section(self, parent: ttk.Frame) -> None:
        sf_frame = ttk.LabelFrame(parent, text=" Configuración de Scryfall ", padding=10)
        sf_frame.pack(fill=tk.BOTH, expand=True)

        # Idioma preferido
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

        # Calidad de imagen
        quality_row = ttk.Frame(sf_frame)
        quality_row.pack(fill=tk.X, pady=4)
        ttk.Label(quality_row, text="Calidad de imagen:").pack(side=tk.LEFT)

        self._scryfall_quality_options = ["Large (Equilibrada)", "PNG (Alta calidad)"]
        self._scryfall_quality_map = {"Large (Equilibrada)": "large", "PNG (Alta calidad)": "png"}
        self._scryfall_quality_inv_map = {
            "large": "Large (Equilibrada)",
            "png": "PNG (Alta calidad)",
        }

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

        # Política de error
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

        # Control de calidad
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

        # Controlar brillo y contraste
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

        # Umbral de enfoque (blur threshold)
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

        # Separadores de mazos
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

        # Minimizar fichas
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

    def _settings_pick_dir(self) -> None:
        current = self._settings.output_dir or output_dir()
        chosen = filedialog.askdirectory(
            title="Selecciona la carpeta de exportación de PDFs",
            initialdir=str(current),
        )
        if chosen:
            p = Path(chosen)
            self._settings.output_dir = p
            self._settings_out_var.set(str(p))
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

    def _save_max_pdf_size_input(self) -> None:
        try:
            value = int(self._settings_max_pdf_size_var.get())
        except ValueError:
            self._settings_max_pdf_size_var.set(str(self._settings.max_pdf_size_mb))
            return
        value = max(MIN_MAX_PDF_SIZE_MB, min(MAX_MAX_PDF_SIZE_MB, value))
        self._settings.max_pdf_size_mb = value
        self._settings_max_pdf_size_var.set(str(value))
        self._persist_settings()

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

    def _clear_cache(self) -> None:
        """Clear cached previews and intermediate processing files."""
        if not messagebox.askyesno(
            "Limpiar Caché",
            "¿Deseas eliminar las imágenes en caché y archivos temporales de procesamiento?",
            icon=messagebox.QUESTION,
        ):
            return

        c_dir = work_dir()
        if c_dir.exists():
            try:
                for item in c_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    elif item.is_file():
                        item.unlink(missing_ok=True)
                messagebox.showinfo(
                    "Caché limpiada", "Se ha vaciado correctamente la caché de procesamiento."
                )
            except Exception as exc:
                messagebox.showwarning(
                    "Aviso", f"No se pudieron eliminar todos los archivos de caché: {exc}"
                )

    def _settings_reset(self) -> None:
        """Reset all settings to default values and persist."""
        self._settings.output_dir = None
        self._settings.cut_line_color = DEFAULT_CUT_LINE_COLOR
        self._settings.cut_line_style = DEFAULT_CUT_LINE_STYLE
        self._settings.cut_line_width = DEFAULT_CUT_LINE_WIDTH
        self._settings.cut_line_over_cards = DEFAULT_CUT_LINE_OVER_CARDS
        self._settings.cut_line_over_fronts = DEFAULT_CUT_LINE_OVER_FRONTS
        self._settings.cut_line_over_backs = DEFAULT_CUT_LINE_OVER_BACKS
        self._settings.max_pdf_size_mb = DEFAULT_MAX_PDF_SIZE_MB

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

        self._settings_out_var.set(str(output_dir()))
        self._settings_color_var.set(DEFAULT_CUT_LINE_COLOR)
        self._color_swatch.configure(bg=DEFAULT_CUT_LINE_COLOR)
        self._settings_style_var.set(DEFAULT_CUT_LINE_STYLE)
        self._settings_width_var.set(f"{DEFAULT_CUT_LINE_WIDTH:.1f}")
        self._settings_over_fronts_var.set(DEFAULT_CUT_LINE_OVER_FRONTS)
        self._settings_over_backs_var.set(DEFAULT_CUT_LINE_OVER_BACKS)
        self._settings_max_pdf_size_var.set(str(DEFAULT_MAX_PDF_SIZE_MB))
        self._over_cards_sub_frame.pack_forget()

        self._settings_sf_lang_var.set(
            self._scryfall_lang_inv_map.get(DEFAULT_SCRYFALL_LANG, "Inglés")
        )
        self._settings_sf_quality_var.set(
            self._scryfall_quality_inv_map.get(DEFAULT_SCRYFALL_QUALITY, "Large (Equilibrada)")
        )
        self._settings_sf_policy_var.set(
            self._scryfall_policy_inv_map.get(DEFAULT_SCRYFALL_FAIL_POLICY, "Descargar en inglés")
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
        save_settings(self._settings, self._base_dir)
