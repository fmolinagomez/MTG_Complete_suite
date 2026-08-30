"""Moxfield and general deck validator GUI script.

Fetches deck metadata and cards (main, sideboard, tokens) and performs technical 
validation via Scryfall. Displays card list and downloads 'small' image previews.
"""

from __future__ import annotations

import logging
import tkinter as tk

from gui.views import validator_view
from gui.views.validator_view import (
    CardValidationResult,
    ValidatorView,
)
from src.deck_importer import DeckCard
from src.scryfall import _fetch_raw_card_data, fetch_card_by_name

_log = logging.getLogger(__name__)


class ValidatorApp:
    """Standalone window wrapper for ValidatorView."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Validador Técnico de Mazos")
        self.root.geometry("1100x650")
        self.root.minsize(900, 500)

        self.view = ValidatorView(root)
        self.view.pack(fill=tk.BOTH, expand=True)

    @property
    def work_directory(self):
        return self.view.work_directory

    @property
    def empty_preview(self):
        return self.view.empty_preview

    @property
    def loading_preview(self):
        return self.view.loading_preview

    @property
    def error_preview(self):
        return self.view.error_preview

    @property
    def image_refs(self):
        return self.view.image_refs

    @property
    def current_deck_cards(self):
        return self.view.current_deck_cards

    @property
    def event_queue(self):
        return self.view.event_queue

    @property
    def tree(self):
        return self.view.tree

    @property
    def url_var(self):
        return self.view.url_var

    @property
    def url_entry(self):
        return self.view.url_entry

    @property
    def load_btn(self):
        return self.view.load_btn

    @property
    def status_var(self):
        return self.view.status_var

    @property
    def progress_var(self):
        return self.view.progress_var

    @property
    def progress_bar(self):
        return self.view.progress_bar

    @property
    def notebook(self):
        return self.view.notebook

    @property
    def validation_tab(self):
        return self.view.validation_tab

    @property
    def json_tab(self):
        return self.view.json_tab

    @property
    def json_text(self):
        return self.view.json_text

    @property
    def preview_title(self):
        return self.view.preview_title

    @property
    def images_frame(self):
        return self.view.images_frame

    @property
    def front_lbl(self):
        return self.view.front_lbl

    @property
    def back_lbl(self):
        return self.view.back_lbl

    def _validate_single_card(self, card: DeckCard) -> CardValidationResult:
        orig_fetch = validator_view._fetch_raw_card_data
        orig_name = validator_view.fetch_card_by_name
        try:
            validator_view._fetch_raw_card_data = _fetch_raw_card_data
            validator_view.fetch_card_by_name = fetch_card_by_name
            return self.view._validate_single_card(card)
        finally:
            validator_view._fetch_raw_card_data = orig_fetch
            validator_view.fetch_card_by_name = orig_name

    def _on_load_clicked(self) -> None:
        self.view._on_load_clicked()

    def _async_load_and_validate(self, url: str) -> None:
        self.view._async_load_and_validate(url)

    def _process_queue(self) -> None:
        self.view._process_queue()

    def _update_summary(self) -> None:
        self.view._update_summary()

    def _on_row_selected(self, event) -> None:
        self.view._on_row_selected(event)

    def _load_async_preview(self, url: str, set_code: str, cn: str, suffix: str, label_widget: tk.Widget) -> None:
        self.view._load_async_preview(url, set_code, cn, suffix, label_widget)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    root = tk.Tk()
    ValidatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
