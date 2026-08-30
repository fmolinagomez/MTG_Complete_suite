"""GUI views package for MPCFillToPDF single-window application."""

from __future__ import annotations

from gui.views.generator_view import GeneratorView
from gui.views.hub_view import HubView
from gui.views.inventory_view import InventoryView
from gui.views.settings_view import SettingsView
from gui.views.validator_view import ValidatorView

__all__ = [
    "GeneratorView",
    "HubView",
    "InventoryView",
    "SettingsView",
    "ValidatorView",
]
