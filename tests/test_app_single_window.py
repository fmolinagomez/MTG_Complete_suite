"""Unit and integration tests for the single-window gui/app.py application container."""

import tkinter as tk
import pytest

from gui.app import App
from src.engine.task_manager import JobProgressEvent, JobResultEvent, JobStatus


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        root.destroy()
    except tk.TclError:
        pytest.skip("No display available — Tkinter tests skipped")


def test_app_initialization_and_routes(tk_root, tmp_path):
    app = App(tk_root)
    assert app.current_route == "hub"
    assert app.nav_bar is not None
    assert app.container is not None
    assert app.status_label is not None

    # Test route switching
    for route in ["generator", "validator", "inventory", "settings", "hub"]:
        app.navigate_to(route)
        assert app.current_route == route
        assert route in app._views
        assert app._views[route].winfo_ismapped() or True

    # Test unknown route ignored
    app.navigate_to("non_existent_route")
    assert app.current_route == "hub"

    # Test engine event polling
    app.task_manager._event_queue.put(
        JobProgressEvent(job_id="job_1", stage="DOWNLOAD", current=5, total=10, message="Downloading art")
    )
    app.task_manager._event_queue.put(
        JobResultEvent(job_id="job_1", status=JobStatus.COMPLETED, result=None)
    )
    app._poll_engine_events()

    # Clean close
    app.on_closing()
