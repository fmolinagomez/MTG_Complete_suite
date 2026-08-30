"""Top persistent navigation bar component with route switcher and background activity indicator."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

_log = logging.getLogger(__name__)

DEFAULT_ROUTES: list[tuple[str, str]] = [
    ("hub", "🏠 Inicio"),
    ("generator", "📄 Generador PDF"),
    ("validator", "🔍 Validador"),
    ("inventory", "📦 Inventario & Mazos"),
    ("settings", "⚙️ Configuración"),
]

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class NavBar(ttk.Frame):
    """Persistent top navigation header with active tab styling and background activity indicator."""

    def __init__(
        self,
        parent: tk.Misc,
        routes: list[tuple[str, str]] | None = None,
        on_navigate: Callable[[str], None] | None = None,
        default_route: str | None = None,
        **kw,
    ) -> None:
        super().__init__(parent, **kw)
        self._routes = routes if routes is not None else list(DEFAULT_ROUTES)
        self._on_navigate = on_navigate
        self._active_route: str | None = None
        self._buttons: dict[str, ttk.Button] = {}

        # Activity indicator state
        self._is_busy: bool = False
        self._activity_text: str = ""
        self._spinner_idx: int = 0
        self._spinner_after_id: str | None = None

        self._setup_styles()
        self._build_ui()

        initial_route = default_route or (self._routes[0][0] if self._routes else None)
        if initial_route:
            self.set_active_route(initial_route)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            # Base navigation bar frame styling
            style.configure("NavBar.TFrame", background="#f8f9fa")
            style.configure("NavLeft.TFrame", background="#f8f9fa")
            style.configure("NavRight.TFrame", background="#f8f9fa")

            # Default nav button style
            style.configure(
                "Nav.TButton",
                font=("Segoe UI", 10),
                padding=(12, 6),
                relief="flat",
            )

            # Active nav button style with distinct accent
            style.configure(
                "NavActive.TButton",
                font=("Segoe UI", 10, "bold"),
                padding=(12, 6),
                relief="sunken",
            )

            # Activity labels
            style.configure("NavActivity.TLabel", font=("Segoe UI", 9), foreground="#0066cc", background="#f8f9fa")
            style.configure("NavSpinner.TLabel", font=("Segoe UI", 10, "bold"), foreground="#0066cc", background="#f8f9fa")
        except Exception as e:
            _log.debug("Could not configure ttk style for NavBar: %s", e)

    def _build_ui(self) -> None:
        self.configure(style="NavBar.TFrame")

        # Container for navigation tabs (Left aligned)
        self._left_container = ttk.Frame(self, style="NavLeft.TFrame")
        self._left_container.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)

        for route_key, label in self._routes:
            btn = ttk.Button(
                self._left_container,
                text=label,
                style="Nav.TButton",
                command=lambda r=route_key: self._on_button_clicked(r),
            )
            btn.pack(side=tk.LEFT, padx=3, pady=2)
            self._buttons[route_key] = btn

        # Container for background activity indicator (Right aligned)
        self._right_container = ttk.Frame(self, style="NavRight.TFrame")
        self._right_container.pack(side=tk.RIGHT, fill=tk.Y, padx=10, pady=4)

        self._spinner_label = ttk.Label(
            self._right_container,
            text="",
            style="NavSpinner.TLabel",
            width=2,
            anchor="center",
        )
        self._activity_label = ttk.Label(
            self._right_container,
            text="",
            style="NavActivity.TLabel",
            anchor="e",
        )

        # Bottom subtle separator line
        self._sep = ttk.Separator(self, orient=tk.HORIZONTAL)
        self._sep.pack(side=tk.BOTTOM, fill=tk.X)

        self.bind("<Destroy>", self._on_destroy, add="+")

    @property
    def active_route(self) -> str | None:
        """Return the key of the currently active route."""
        return self._active_route

    @property
    def is_busy(self) -> bool:
        """Return whether background activity indicator is currently active."""
        return self._is_busy

    def set_active_route(self, route: str) -> bool:
        """Update active route styling without triggering navigation callback.
        
        Returns True if route exists and was activated, False otherwise.
        """
        if route not in self._buttons:
            return False

        self._active_route = route
        for r_key, btn in self._buttons.items():
            if r_key == route:
                btn.configure(style="NavActive.TButton")
            else:
                btn.configure(style="Nav.TButton")
        return True

    def _on_button_clicked(self, route: str) -> None:
        if self._active_route == route:
            return
        self.set_active_route(route)
        if self._on_navigate:
            try:
                self._on_navigate(route)
            except Exception as e:
                _log.error("Error executing on_navigate callback for route '%s': %s", route, e)

    def set_activity(self, active: bool, text: str = "") -> None:
        """Update background task activity indicator."""
        self._is_busy = bool(active)
        self._activity_text = text

        if self._is_busy:
            self._spinner_label.pack(side=tk.RIGHT, padx=(2, 0))
            self._activity_label.pack(side=tk.RIGHT, padx=(0, 4))
            self._activity_label.configure(text=self._activity_text)
            if self._spinner_after_id is None:
                self._animate_spinner()
        else:
            self._cancel_spinner()
            self._spinner_label.configure(text="")
            self._activity_label.configure(text="")
            self._spinner_label.pack_forget()
            self._activity_label.pack_forget()

    def update_activity_text(self, text: str) -> None:
        """Update activity text while keeping activity state active."""
        self._activity_text = text
        if self._is_busy:
            self._activity_label.configure(text=text)

    def _animate_spinner(self) -> None:
        if not self.winfo_exists() or not self._is_busy:
            self._spinner_after_id = None
            return

        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self._spinner_label.configure(text=_SPINNER_FRAMES[self._spinner_idx])
        self._spinner_after_id = self.after(120, self._animate_spinner)

    def _cancel_spinner(self) -> None:
        if self._spinner_after_id is not None:
            try:
                self.after_cancel(self._spinner_after_id)
            except Exception:
                pass
            self._spinner_after_id = None

    def _on_destroy(self, _event=None) -> None:
        self._cancel_spinner()

    def destroy(self) -> None:
        self._cancel_spinner()
        super().destroy()
