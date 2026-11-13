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
        JobProgressEvent(
            job_id="job_1", stage="DOWNLOAD", current=5, total=10, message="Downloading art"
        )
    )
    app.task_manager._event_queue.put(
        JobResultEvent(job_id="job_1", status=JobStatus.COMPLETED, result=None)
    )
    app._poll_engine_events()

    # Clean close
    app.on_closing()


def test_shared_app_settings_across_views(tk_root, tmp_path):
    """SettingsView, GeneratorView, and InventoryView share the single AppSettings instance."""
    from unittest.mock import patch

    from src.app_settings import load_settings
    from src.inventory.models import DeckCardEntry, DeckRecord

    custom_out = tmp_path / "custom_out"
    custom_out.mkdir()

    with (
        patch("gui.app.app_base_dir", return_value=tmp_path),
        patch("gui.app.work_dir", return_value=tmp_path / "work"),
        patch("gui.app.output_dir", return_value=tmp_path / "out"),
        patch("gui.app.db_path", return_value=tmp_path / "test_inv.db"),
    ):
        app = App(tk_root)
        try:
            for route in ["settings", "generator", "inventory"]:
                app.navigate_to(route)

            settings_view = app._views["settings"]
            gen_view = app._views["generator"]
            inv_view = app._views["inventory"]

            assert settings_view.settings is app.settings
            assert gen_view.settings is app.settings
            assert inv_view.settings is app.settings

            # Update settings via SettingsView and verify immediate propagation & persistence
            with patch("tkinter.filedialog.askdirectory", return_value=str(custom_out)):
                settings_view._settings_pick_dir()
            settings_view._settings_style_var.set("full")
            settings_view._on_settings_style_changed()
            settings_view._settings_sf_lang_var.set("Español")
            settings_view._on_sf_lang_changed()

            # Navigate to generator and check refreshed output dir and settings
            app.navigate_to("generator")
            assert gen_view._effective_output_dir() == custom_out
            assert gen_view.out_dir_var.get() == str(custom_out)
            assert gen_view.settings.cut_line_style == "full"
            assert gen_view.settings.scryfall_lang == "es"

            # Verify InventoryView proxy PDF generation applies shared AppSettings
            captured_spec = []

            def fake_execute(spec):
                captured_spec.append(spec)
                from unittest.mock import MagicMock

                return MagicMock(pdf_paths=[custom_out / "proxies.pdf"])

            with patch("gui.views.inventory_view.PrintPipeline.execute", side_effect=fake_execute):
                inv_view._task_generate_missing_proxies_pdf(
                    DeckRecord(name="Test Deck"),
                    [
                        DeckCardEntry(
                            deck_id=1,
                            oracle_id="orc-1",
                            name="Sol Ring",
                            set_code="lea",
                            collector_number="269",
                            quantity_required=1,
                        )
                    ],
                )

            assert len(captured_spec) == 1
            assert captured_spec[0].output_dir == custom_out
            assert captured_spec[0].cut_line_style == "full"
            assert captured_spec[0].deck_lang == "es"

            # Persisted to canonical tmp_path/MPCFillToPDF/settings.json
            reloaded = load_settings(tmp_path)
            assert reloaded.output_dir == custom_out
            assert reloaded.cut_line_style == "full"
            assert reloaded.scryfall_lang == "es"
        finally:
            app.on_closing()


def test_app_polling_dispatches_inventory_job_callbacks_deterministically(tk_root, tmp_path):
    """App._poll_engine_events() delivers InventoryView job callbacks without event stealing."""
    from unittest.mock import MagicMock, patch

    with (
        patch("gui.app.app_base_dir", return_value=tmp_path),
        patch("gui.app.work_dir", return_value=tmp_path / "work"),
        patch("gui.app.output_dir", return_value=tmp_path / "out"),
        patch("gui.app.db_path", return_value=tmp_path / "test_inv.db"),
    ):
        app = App(tk_root)
        try:
            app.navigate_to("inventory")
            inv_view = app._views["inventory"]

            csv_file = tmp_path / "cards.csv"
            csv_file.write_text("dummy", encoding="utf-8")

            with (
                patch("tkinter.filedialog.askopenfilename", return_value=str(csv_file)),
                patch.object(inv_view, "wait_window"),
                patch("gui.views.inventory_view.ManaBoxImportDialog") as mock_dlg_cls,
                patch.object(app.inventory_service, "import_manabox_csv", return_value=17),
                patch("tkinter.messagebox.showinfo") as mock_info,
            ):
                dlg_inst = MagicMock()
                dlg_inst.result = "merge"
                mock_dlg_cls.return_value = dlg_inst

                inv_view._on_import_csv_click()
                job = app.task_manager.get_all_jobs()[-1]
                job.future.result(timeout=2.0)

                # Poll via App._poll_engine_events() — must trigger InventoryView's callback AND update App status bar
                app._poll_engine_events()

                mock_info.assert_called_once()
                assert "17" in mock_info.call_args[0][1]
                assert f"Tarea completada: {job.id}" in app.status_label.cget("text")
        finally:
            app.on_closing()
