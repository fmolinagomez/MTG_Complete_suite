"""Modular helper components and mixins for direct game scraper tabs.

Reduces repetitive boilerplates for URL-based deck scrapers (One Piece, Riftbound, Lorcana)
while maintaining full UI capability, progress feedback, and card inspection.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any

from gui.widgets import APP_TITLE, attach_context_menu

_log = logging.getLogger(__name__)


@dataclass
class ScraperTabSpec:
    """Configuration specification for a direct-game scraper sub-tab."""

    game_id: str
    game_name: str
    sites_hint: str
    empty_hint: str
    scrape_fn: Callable[[str], Any]
    loaded_event: str
    error_event: str


class GameScraperTabMixin:
    """Reusable mixin providing common UI builders and logic for game scraper tabs."""

    def _build_generic_scraper_tab(
        self,
        parent: ttk.Frame,
        spec: ScraperTabSpec,
    ) -> tuple[tk.StringVar, ttk.Entry, ttk.Button, tk.StringVar, tk.Canvas, ttk.Frame, ttk.Label]:
        """Build the common layout: URL entry row, status row, scrollable canvas, and clear button."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        url_row = ttk.Frame(parent)
        url_row.grid(row=0, column=0, sticky="ew", padx=6, pady=(10, 4))
        url_row.columnconfigure(1, weight=1)

        ttk.Label(
            url_row,
            text=spec.sites_hint,
            foreground="#999",
            font=("Segoe UI", 8),
            wraplength=450,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        ttk.Label(url_row, text="URL del mazo:").grid(row=1, column=0, sticky="w", padx=(0, 6))

        url_var = tk.StringVar()
        url_entry = ttk.Entry(url_row, textvariable=url_var)
        url_entry.grid(row=1, column=1, sticky="ew")
        attach_context_menu(url_entry)

        load_btn = ttk.Button(url_row, text="Añadir", width=7)
        load_btn.grid(row=1, column=2, padx=(6, 0))

        status_var = tk.StringVar(value="")
        ttk.Label(url_row, textvariable=status_var, foreground="#555", anchor="w").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(2, 0)
        )

        list_frame = ttk.Frame(parent)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 2))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        canvas, inner, _ = self._build_scrollable_rows(list_frame)
        canvas.bind("<Enter>", lambda _e: self._bind_mousewheel(canvas, True))
        canvas.bind("<Leave>", lambda _e: self._bind_mousewheel(canvas, False))

        empty_label = ttk.Label(
            inner,
            text=spec.empty_hint,
            foreground="#777",
            padding=(8, 10),
        )
        empty_label.pack(anchor="w")

        return url_var, url_entry, load_btn, status_var, canvas, inner, empty_label

    def _dispatch_scrape_deck(
        self,
        url_var: tk.StringVar,
        load_btn: ttk.Button,
        status_var: tk.StringVar,
        spec: ScraperTabSpec,
    ) -> None:
        """Validate input URL and spawn a background thread to scrape the deck."""
        url = url_var.get().strip()
        if not url:
            messagebox.showwarning(APP_TITLE, f"Introduce una URL de mazo de {spec.game_name}.")
            return

        load_btn.state(["disabled"])
        status_var.set("Cargando mazo…")

        def _fetch():
            try:
                deck = spec.scrape_fn(url)
                self.events.put((spec.loaded_event, deck))
            except Exception as e:
                self.events.put((spec.error_event, str(e)))

        threading.Thread(target=_fetch, daemon=True).start()
