"""Single-window application container for MPCFillToPDF Suite.

Manages persistent top navigation bar, frame switching between modular views,
and centralized background task polling.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from gui.components.nav_bar import NavBar
from gui.paths import app_base_dir, db_path, output_dir, work_dir
from gui.views.generator_view import GeneratorView
from gui.views.hub_view import HubView
from gui.views.inventory_view import InventoryView
from gui.views.settings_view import SettingsView
from gui.views.validator_view import ValidatorView
from gui.widgets import APP_TITLE
from src.app_settings import AppSettings, load_settings
from src.engine.task_manager import JobProgressEvent, JobResultEvent, JobStatus, TaskManager
from src.inventory.service import InventoryService

_log = logging.getLogger(__name__)


class App:
    """Main application controller for MPCFillToPDF single-window GUI."""

    def __init__(self, root: tk.Tk | None = None) -> None:
        self.root = root if root is not None else tk.Tk()
        self._owns_root = root is None
        self.root.title(APP_TITLE)
        self.root.geometry("1100x720")
        self.root.minsize(920, 600)

        # Paths and persistence
        self.base_dir = app_base_dir()
        self.work_dir = work_dir()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = output_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = db_path()

        # Services & Core Engine
        self.settings: AppSettings = load_settings(self.work_dir / "settings.json")
        self.inventory_service = InventoryService(self.db_file)
        self.task_manager = TaskManager(max_workers=5)

        # UI State & Views Cache
        self.current_route: str = "hub"
        self._views: dict[str, ttk.Frame] = {}

        self._setup_styles()
        self._build_ui()

        # Start periodic polling for background task events
        self._poll_task_id = self.root.after(50, self._poll_engine_events)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass

        style.configure("Treeview", rowheight=24)
        style.configure("Nav.TButton", font=("Helvetica", 10, "normal"))
        style.configure("NavActive.TButton", font=("Helvetica", 10, "bold"))
        style.configure("Status.TLabel", font=("Helvetica", 9), foreground="#555555")

    def _build_ui(self) -> None:
        # Top Persistent Navigation Bar
        self.nav_bar = NavBar(
            self.root,
            on_navigate=self.navigate_to,
            routes=[
                ("hub", "🏠 Inicio"),
                ("generator", "📄 Generador PDF"),
                ("validator", "🔍 Validador"),
                ("inventory", "📦 Inventario & Mazos"),
                ("settings", "⚙️ Configuración"),
            ],
            default_route="hub",
        )
        self.nav_bar.pack(fill=tk.X, side=tk.TOP)

        # Central Dynamic View Container
        self.container = ttk.Frame(self.root)
        self.container.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # Bottom Global Status Bar
        self.status_bar_frame = ttk.Frame(self.root, padding=(8, 4))
        self.status_bar_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_label = ttk.Label(
            self.status_bar_frame,
            text="Listo",
            style="Status.TLabel",
            anchor="w",
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.version_label = ttk.Label(
            self.status_bar_frame,
            text="MPCFillToPDF Suite v2.1",
            style="Status.TLabel",
            anchor="e",
        )
        self.version_label.pack(side=tk.RIGHT)

        # Navigate to initial route
        self.navigate_to("hub")

    def _get_or_create_view(self, route: str) -> ttk.Frame:
        if route in self._views:
            return self._views[route]

        view: ttk.Frame
        if route == "hub":
            view = HubView(
                self.container,
                on_navigate=self.navigate_to,
                inventory_service=self.inventory_service,
            )
        elif route == "generator":
            view = GeneratorView(
                self.container,
                task_manager=self.task_manager,
                on_navigate=self.navigate_to,
            )
        elif route == "validator":
            view = ValidatorView(
                self.container,
                task_manager=self.task_manager,
                on_navigate=self.navigate_to,
            )
        elif route == "inventory":
            view = InventoryView(
                self.container,
                inventory_service=self.inventory_service,
                task_manager=self.task_manager,
                on_navigate=self.navigate_to,
            )
        elif route == "settings":
            view = SettingsView(
                self.container,
                on_navigate=self.navigate_to,
                settings=self.settings,
                base_dir=self.work_dir,
            )
        else:
            raise ValueError(f"Ruta desconocida: {route}")

        self._views[route] = view
        return view

    def navigate_to(self, route: str) -> None:
        """Switch the currently visible view in the central container."""
        if route not in {"hub", "generator", "validator", "inventory", "settings"}:
            _log.warning("Unknown route requested: %s", route)
            return

        # Hide current view
        for v in self._views.values():
            v.pack_forget()

        # Display target view
        target_view = self._get_or_create_view(route)
        target_view.pack(fill=tk.BOTH, expand=True)
        self.current_route = route
        self.nav_bar.set_active_route(route)

        # Trigger data refreshes on views when visited
        if route == "hub" and hasattr(target_view, "refresh_stats"):
            target_view.refresh_stats()
        elif route == "inventory" and hasattr(target_view, "refresh_data"):
            target_view.refresh_data()

    def _poll_engine_events(self) -> None:
        """Poll events from the TaskManager background queue and update UI elements."""
        try:
            events = self.task_manager.poll_events()
            for event in events:
                if isinstance(event, JobProgressEvent):
                    pct = int(event.current / event.total * 100) if event.total > 0 else 0
                    msg = f"[{event.stage}] {event.message} ({pct}%)"
                    self.status_label.config(text=msg)
                    self.nav_bar.update_activity_text(msg)
                elif isinstance(event, JobResultEvent):
                    if event.status == JobStatus.COMPLETED:
                        self.status_label.config(text=f"Tarea completada: {event.job_id}")
                    elif event.status == JobStatus.FAILED:
                        self.status_label.config(
                            text=f"Error en tarea {event.job_id}: {event.error}"
                        )
                    elif event.status == JobStatus.CANCELLED:
                        self.status_label.config(text=f"Tarea cancelada: {event.job_id}")

            # Check overall activity state for top nav bar spinner
            active_jobs = self.task_manager.get_active_jobs()
            if active_jobs:
                job_names = ", ".join(j.name for j in active_jobs)
                self.nav_bar.set_activity(True, f"En ejecución: {job_names}")
            else:
                self.nav_bar.set_activity(False)

        except Exception as e:
            _log.error("Error polling engine events: %s", e)

        finally:
            self._poll_task_id = self.root.after(50, self._poll_engine_events)

    def on_closing(self) -> None:
        """Handle window closing cleanly by cancelling background workers."""
        try:
            if self._poll_task_id:
                self.root.after_cancel(self._poll_task_id)
        except Exception:
            pass

        try:
            active_jobs = self.task_manager.get_active_jobs()
            if active_jobs:
                self.task_manager.cancel_all()
            self.task_manager.shutdown(wait=False)
        except Exception as e:
            _log.warning("Error shutting down task manager: %s", e)

        # Cleanup view resources
        for view in self._views.values():
            if hasattr(view, "destroy_view"):
                try:
                    view.destroy_view()
                except Exception:
                    pass

        if self._owns_root:
            self.root.destroy()

    def run(self) -> None:
        """Start the Tkinter event loop."""
        self.root.mainloop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    app = App()
    app.run()


if __name__ == "__main__":
    main()
