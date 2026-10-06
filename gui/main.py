"""MPCFillToPDF GUI — Application entry point.

Directly launches the single-window suite application container (gui.app.App).
"""

from __future__ import annotations

from gui.app import main
from gui.views.generator_view import MtgUrlDeck

__all__ = ["main", "MtgUrlDeck"]

if __name__ == "__main__":
    main()
