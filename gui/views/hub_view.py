"""Splash Hub view for MPCFillToPDF single-window desktop application."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from src.inventory.service import InventoryService

_log = logging.getLogger(__name__)


class HubView(ttk.Frame):
    """Hub / Splash dashboard with module quick-access cards and collection metrics."""

    def __init__(
        self,
        master: tk.Misc,
        on_navigate: Callable[[str], None] | None = None,
        inventory_service: InventoryService | None = None,
        **kw,
    ) -> None:
        super().__init__(master, **kw)
        self._on_navigate = on_navigate
        self._inventory_service = inventory_service

        # Stat tracking variables
        self._total_cards_var = tk.StringVar(value="0")
        self._unique_cards_var = tk.StringVar(value="0")
        self._physical_cards_var = tk.StringVar(value="0")
        self._proxy_cards_var = tk.StringVar(value="0")
        self._deck_count_var = tk.StringVar(value="0")

        self._setup_styles()
        self._build_ui()
        self.refresh_stats()

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.configure(
                "HubBannerTitle.TLabel", font=("Segoe UI", 18, "bold"), foreground="#1a1a1a"
            )
            style.configure("HubBannerSub.TLabel", font=("Segoe UI", 10), foreground="#555555")
            style.configure(
                "HubCardTitle.TLabel", font=("Segoe UI", 11, "bold"), foreground="#1a1a1a"
            )
            style.configure("HubCardDesc.TLabel", font=("Segoe UI", 9), foreground="#666666")
            style.configure("HubAction.TButton", font=("Segoe UI", 9, "bold"), padding=(10, 4))
            style.configure(
                "HubStatValue.TLabel", font=("Segoe UI", 14, "bold"), foreground="#0066cc"
            )
            style.configure("HubStatTitle.TLabel", font=("Segoe UI", 8), foreground="#666666")
        except Exception as e:
            _log.debug("Could not configure ttk style for HubView: %s", e)

    def _build_ui(self) -> None:
        # Main scrollable or padded container
        main_container = ttk.Frame(self, padding=20)
        main_container.pack(fill=tk.BOTH, expand=True)

        # ── Header Banner ──────────────────────────────────────────────────
        banner_frame = ttk.Frame(main_container)
        banner_frame.pack(fill=tk.X, pady=(0, 20))

        title_lbl = ttk.Label(
            banner_frame,
            text="MPCFillToPDF Suite",
            style="HubBannerTitle.TLabel",
        )
        title_lbl.pack(anchor=tk.W)

        subtitle_lbl = ttk.Label(
            banner_frame,
            text="Centro de control y preparación de cartas para imprenta, validación y gestión de colección.",
            style="HubBannerSub.TLabel",
        )
        subtitle_lbl.pack(anchor=tk.W, pady=(4, 0))

        ttk.Separator(main_container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 16))

        # ── Quick-Access Cards Grid (2x2) ──────────────────────────────────
        cards_container = ttk.Frame(main_container)
        cards_container.pack(fill=tk.BOTH, expand=True, pady=(0, 16))
        cards_container.columnconfigure(0, weight=1, uniform="hub_col")
        cards_container.columnconfigure(1, weight=1, uniform="hub_col")
        cards_container.rowconfigure(0, weight=1, uniform="hub_row")
        cards_container.rowconfigure(1, weight=1, uniform="hub_row")

        cards_data = [
            (
                0,
                0,
                "generator",
                "📄 Generador de PDFs",
                "Genera PDFs listos para imprenta desde XMLs de MPC o enlaces de mazos",
                "Abrir Generador →",
            ),
            (
                0,
                1,
                "validator",
                "🔍 Validador de Cartas",
                "Comprueba resolución, nitidez y legalidad técnica en Scryfall",
                "Abrir Validador →",
            ),
            (
                1,
                0,
                "inventory",
                "📦 Inventario & Mazos",
                "Gestiona tu colección, importa ManaBox CSV y calcula proxies faltantes",
                "Abrir Inventario →",
            ),
            (
                1,
                1,
                "settings",
                "⚙️ Configuración",
                "Ajustes de calidad Scryfall, directorios y opciones de impresión",
                "Abrir Ajustes →",
            ),
        ]

        for row, col, route, title, desc, btn_text in cards_data:
            self._create_quick_card(
                cards_container,
                row=row,
                col=col,
                route=route,
                title=title,
                desc=desc,
                btn_text=btn_text,
            )

        # ── Bottom Metrics Panel ───────────────────────────────────────────
        self._build_stats_panel(main_container)

    def _create_quick_card(
        self,
        parent: ttk.Frame,
        row: int,
        col: int,
        route: str,
        title: str,
        desc: str,
        btn_text: str,
    ) -> ttk.Frame:
        card = ttk.LabelFrame(parent, text=f" {title} ", padding=12)
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)

        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)

        desc_lbl = ttk.Label(
            card,
            text=desc,
            style="HubCardDesc.TLabel",
            wraplength=280,
            justify=tk.LEFT,
        )
        desc_lbl.grid(row=0, column=0, sticky="nw", pady=(0, 10))

        btn = ttk.Button(
            card,
            text=btn_text,
            style="HubAction.TButton",
            command=lambda r=route: self._navigate(r),
        )
        btn.grid(row=1, column=0, sticky="e")

        # Make card labels clickable as well
        desc_lbl.bind("<Button-1>", lambda _e, r=route: self._navigate(r))

        return card

    def _build_stats_panel(self, parent: ttk.Frame) -> None:
        stats_frame = ttk.LabelFrame(
            parent, text=" 📊 Estado de la Colección e Inventario ", padding=10
        )
        stats_frame.pack(fill=tk.X, pady=(0, 4))

        metrics_row = ttk.Frame(stats_frame)
        metrics_row.pack(fill=tk.X)

        metrics = [
            ("TOTAL CARTAS", self._total_cards_var),
            ("CARTAS ÚNICAS", self._unique_cards_var),
            ("FÍSICAS", self._physical_cards_var),
            ("PROXIES", self._proxy_cards_var),
            ("MAZOS REGISTRADOS", self._deck_count_var),
        ]

        for i, (label_text, var) in enumerate(metrics):
            cell = ttk.Frame(metrics_row)
            cell.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)

            ttk.Label(
                cell,
                textvariable=var,
                style="HubStatValue.TLabel",
                anchor="center",
            ).pack(fill=tk.X)

            ttk.Label(
                cell,
                text=label_text,
                style="HubStatTitle.TLabel",
                anchor="center",
            ).pack(fill=tk.X)

        refresh_btn = ttk.Button(
            stats_frame,
            text="🔄 Actualizar",
            width=12,
            command=self.refresh_stats,
        )
        refresh_btn.pack(side=tk.RIGHT, pady=(6, 0))

    def _navigate(self, route: str) -> None:
        if self._on_navigate:
            try:
                self._on_navigate(route)
            except Exception as e:
                _log.error("Navigation error from Hub to '%s': %s", route, e)

    def refresh_stats(self) -> dict[str, int]:
        """Fetch updated collection statistics and refresh UI labels."""
        stats = {
            "total_cards": 0,
            "unique_oracle_ids": 0,
            "proxy_count": 0,
            "physical_count": 0,
            "deck_count": 0,
        }

        try:
            service = self._inventory_service
            if service is None:
                service = InventoryService()
                self._inventory_service = service

            stats = service.get_inventory_stats()
        except Exception as e:
            _log.debug("Could not fetch inventory stats for HubView: %s", e)

        self._total_cards_var.set(str(stats.get("total_cards", 0)))
        self._unique_cards_var.set(str(stats.get("unique_oracle_ids", 0)))
        self._physical_cards_var.set(str(stats.get("physical_count", 0)))
        self._proxy_cards_var.set(str(stats.get("proxy_count", 0)))
        self._deck_count_var.set(str(stats.get("deck_count", 0)))

        return stats
