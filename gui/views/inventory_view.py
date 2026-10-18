"""Card Inventory and Deck Binder view component for MPCFillToPDF."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gui.components.card_preview_hover import CardHoverPreview, CardPreviewInfo
from gui.paths import output_dir, work_dir
from gui.views.inventory_dialogs import (
    AddCardDialog,
    BatchAssignDeckDialog,
    BatchEditCardsDialog,
    CardDecksUsageDialog,
    DeckImportOptionDialog,
    ManaBoxImportDialog,
)
from src.deck_importer import DeckCard, MtgUrlDeck, fetch_deck
from src.engine.task_manager import JobResultEvent, JobStatus, TaskManager
from src.inventory.allocation_engine import card_usage_sort_key
from src.inventory.models import DeckCardEntry, DeckCardMatchDetail, DeckRecord, InventoryCard
from src.inventory.service import InventoryService
from src.pipeline import PrintJobSpec, PrintPipeline
from src.scraper_utils import sanitize_filename

_log = logging.getLogger(__name__)


class InventoryView(ttk.Frame):
    """Main Inventory & Deck Binder view with interactive card preview tooltips."""

    def __init__(
        self,
        master: tk.Misc,
        inventory_service: InventoryService,
        task_manager: TaskManager,
        on_navigate: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.inventory_service = inventory_service
        self.task_manager = task_manager
        self.on_navigate = on_navigate

        self._preview_hover = CardHoverPreview(self)
        self._job_callbacks: dict[str, Callable[[JobResultEvent], None]] = {}
        self._poll_after_id: str | None = None

        # Data caches
        self._inv_item_to_card: dict[str, InventoryCard] = {}
        self._inv_item_to_usage: dict[str, tuple[int, int]] = {}
        self._deck_item_to_info: dict[str, CardPreviewInfo] = {}
        self._deck_row_data: dict[str, DeckCardMatchDetail] = {}
        self._active_cell_editor: tk.Widget | None = None
        self._registered_decks: list[DeckRecord] = []
        self._current_deck_id: int | None = None
        self._current_deck_cards: list[DeckCardEntry] = []

        # UI State variables
        self._search_var = tk.StringVar()
        self._filter_type_var = tk.StringVar(value="Todas")
        self._filter_usage_var = tk.StringVar(value="Todas")
        self._filter_set_var = tk.StringVar(value="Todas las ediciones")
        self._filter_rarity_var = tk.StringVar(value="Todas")
        self._deck_url_var = tk.StringVar()
        self._deck_include_proxies_var = tk.BooleanVar(value=True)
        self._deck_allocate_mode_var = tk.StringVar(value="exclusive")

        # Sorting state
        self._inv_sort_col: str | None = None
        self._inv_sort_descending: bool = False
        self._inv_col_headings: dict[str, str] = {
            "name": "Nombre",
            "set_code": "Edición",
            "collector_number": "Número",
            "quantity": "Total",
            "available": "Disponibles",
            "in_decks": "En Mazos",
            "foil": "Foil",
            "proxy": "Proxy",
            "condition": "Condición",
            "language": "Idioma",
        }

        # Advanced filters visibility
        self._adv_filters_visible: bool = False

        self._build_ui()
        self._schedule_poll()
        self.refresh_data()

    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Tab 1: Collection
        self.tab_collection = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_collection, text="📦 Mi Colección (Inventario)")
        self._build_collection_tab(self.tab_collection)

        # Tab 2: Decks & Moxfield
        self.tab_decks = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_decks, text="🎴 Mazos & Moxfield")
        self._build_decks_tab(self.tab_decks)

    # ---------------------------------------------------------------------------
    # Tab 1: Collection (Mi Colección)
    # ---------------------------------------------------------------------------

    def _build_collection_tab(self, parent: ttk.Frame) -> None:
        # Top toolbar
        self.toolbar_frame = ttk.Frame(parent, padding=(4, 6))
        self.toolbar_frame.pack(fill=tk.X)

        # Search & Quick Filter
        ttk.Label(self.toolbar_frame, text="Buscar:").pack(side=tk.LEFT, padx=(2, 4))
        self.ent_search = ttk.Entry(self.toolbar_frame, textvariable=self._search_var, width=20)
        self.ent_search.pack(side=tk.LEFT, padx=(0, 6))
        self._search_var.trace_add("write", lambda *_: self._filter_collection_table())

        ttk.Label(self.toolbar_frame, text="Filtrar:").pack(side=tk.LEFT, padx=(4, 4))
        self.cb_filter = ttk.Combobox(
            self.toolbar_frame,
            textvariable=self._filter_type_var,
            values=["Todas", "Solo Reales", "Solo Proxies", "Solo Foils"],
            state="readonly",
            width=13,
        )
        self.cb_filter.pack(side=tk.LEFT, padx=(0, 6))
        self.cb_filter.bind("<<ComboboxSelected>>", lambda _: self._filter_collection_table())

        self.btn_toggle_filters = ttk.Button(
            self.toolbar_frame,
            text="🔽 Filtros Avanzados",
            command=self._toggle_advanced_filters,
        )
        self.btn_toggle_filters.pack(side=tk.LEFT, padx=(0, 6))

        btn_refresh = ttk.Button(self.toolbar_frame, text="🔄 Refrescar", command=self.refresh_data)
        btn_refresh.pack(side=tk.LEFT, padx=(0, 10))

        # Action Buttons (Right aligned)
        btn_import_csv = ttk.Button(
            self.toolbar_frame,
            text="📥 Importar CSV",
            command=self._on_import_csv_click,
        )
        btn_import_csv.pack(side=tk.RIGHT, padx=2)

        btn_view_decks = ttk.Button(
            self.toolbar_frame,
            text="🔍 Ver en Mazos",
            command=self._on_view_card_decks_click,
        )
        btn_view_decks.pack(side=tk.RIGHT, padx=2)

        btn_add_card = ttk.Button(
            self.toolbar_frame,
            text="➕ Añadir Carta",
            command=self._on_add_card_click,
        )
        btn_add_card.pack(side=tk.RIGHT, padx=2)

        btn_delete_card = ttk.Button(
            self.toolbar_frame,
            text="🗑️ Eliminar",
            command=self._on_delete_card_click,
        )
        btn_delete_card.pack(side=tk.RIGHT, padx=2)

        # Collapsible Advanced Filters Frame
        self.adv_filters_frame = ttk.LabelFrame(parent, text="Filtros Avanzados", padding=(8, 6))

        ttk.Label(self.adv_filters_frame, text="Estado de Uso:").pack(side=tk.LEFT, padx=(2, 4))
        self.cb_filter_usage = ttk.Combobox(
            self.adv_filters_frame,
            textvariable=self._filter_usage_var,
            values=["Todas", "Solo Disponibles (libres)", "Solo Asignadas a Mazos"],
            state="readonly",
            width=22,
        )
        self.cb_filter_usage.pack(side=tk.LEFT, padx=(0, 10))
        self.cb_filter_usage.bind("<<ComboboxSelected>>", lambda _: self._filter_collection_table())

        ttk.Label(self.adv_filters_frame, text="Edición:").pack(side=tk.LEFT, padx=(4, 4))
        self.cb_filter_set = ttk.Combobox(
            self.adv_filters_frame,
            textvariable=self._filter_set_var,
            values=["Todas las ediciones"],
            state="readonly",
            width=18,
        )
        self.cb_filter_set.pack(side=tk.LEFT, padx=(0, 10))
        self.cb_filter_set.bind("<<ComboboxSelected>>", lambda _: self._filter_collection_table())

        ttk.Label(self.adv_filters_frame, text="Rareza:").pack(side=tk.LEFT, padx=(4, 4))
        self.cb_filter_rarity = ttk.Combobox(
            self.adv_filters_frame,
            textvariable=self._filter_rarity_var,
            values=["Todas", "Mítica", "Rara", "Infrecuente", "Común"],
            state="readonly",
            width=12,
        )
        self.cb_filter_rarity.pack(side=tk.LEFT, padx=(0, 10))
        self.cb_filter_rarity.bind(
            "<<ComboboxSelected>>", lambda _: self._filter_collection_table()
        )

        btn_reset = ttk.Button(
            self.adv_filters_frame,
            text="🧹 Limpiar Filtros",
            command=self._on_reset_filters_click,
        )
        btn_reset.pack(side=tk.RIGHT, padx=4)

        # Bottom Container: Docked contextual batch bar + stats banner
        self.bottom_container = ttk.Frame(parent)
        self.bottom_container.pack(fill=tk.X, side=tk.BOTTOM)

        # Contextual Batch Action Bar (docked above stats, visible when 1 or more cards selected)
        self.batch_bar_frame = ttk.LabelFrame(
            self.bottom_container, text="⚡ Operaciones en Lote", padding=(6, 4)
        )

        self.lbl_batch_count = ttk.Label(
            self.batch_bar_frame,
            text="📌 0 cartas seleccionadas",
            font=("Segoe UI", 9, "bold"),
            foreground="#1a5276",
        )
        self.lbl_batch_count.pack(side=tk.LEFT, padx=(4, 10))

        self.btn_batch_proxy = ttk.Button(
            self.batch_bar_frame,
            text="🏷️ Proxy / Real",
            command=self._on_batch_toggle_proxy_click,
        )
        self.btn_batch_proxy.pack(side=tk.LEFT, padx=2)

        self.btn_batch_foil = ttk.Button(
            self.batch_bar_frame,
            text="✨ Foil / No Foil",
            command=self._on_batch_toggle_foil_click,
        )
        self.btn_batch_foil.pack(side=tk.LEFT, padx=2)

        self.btn_batch_edit = ttk.Button(
            self.batch_bar_frame,
            text="✏️ Edición en Lote...",
            command=self._on_batch_edit_click,
        )
        self.btn_batch_edit.pack(side=tk.LEFT, padx=2)

        self.btn_batch_deck = ttk.Button(
            self.batch_bar_frame,
            text="🎴 Asignar a Mazo...",
            command=self._on_batch_assign_deck_click,
        )
        self.btn_batch_deck.pack(side=tk.LEFT, padx=2)

        self.btn_batch_delete = ttk.Button(
            self.batch_bar_frame,
            text="🗑️ Eliminar",
            command=self._on_batch_delete_click,
        )
        self.btn_batch_delete.pack(side=tk.LEFT, padx=2)

        self.btn_batch_clear = ttk.Button(
            self.batch_bar_frame,
            text="✖️ Deseleccionar",
            command=self._on_deselect_all_cards,
        )
        self.btn_batch_clear.pack(side=tk.RIGHT, padx=4)

        # Bottom stats banner
        stats_frame = ttk.Frame(self.bottom_container, padding=(6, 4))
        stats_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.lbl_stats = ttk.Label(
            stats_frame,
            text="Colección: 0 cartas | Físicas: 0 | Proxies: 0 | Mazos: 0",
            font=("Segoe UI", 9),
            foreground="#444444",
        )
        self.lbl_stats.pack(side=tk.LEFT)

        # Collection Treeview Table
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        columns = (
            "name",
            "set_code",
            "collector_number",
            "quantity",
            "available",
            "in_decks",
            "foil",
            "proxy",
            "condition",
            "language",
        )
        self.inv_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="extended"
        )

        self._update_inv_column_headings()

        self.inv_tree.column("name", width=240, minwidth=130, anchor=tk.W)
        self.inv_tree.column("set_code", width=70, minwidth=50, anchor=tk.CENTER)
        self.inv_tree.column("collector_number", width=65, minwidth=45, anchor=tk.CENTER)
        self.inv_tree.column("quantity", width=60, minwidth=40, anchor=tk.CENTER)
        self.inv_tree.column("available", width=75, minwidth=50, anchor=tk.CENTER)
        self.inv_tree.column("in_decks", width=75, minwidth=50, anchor=tk.CENTER)
        self.inv_tree.column("foil", width=55, minwidth=40, anchor=tk.CENTER)
        self.inv_tree.column("proxy", width=60, minwidth=40, anchor=tk.CENTER)
        self.inv_tree.column("condition", width=70, minwidth=50, anchor=tk.CENTER)
        self.inv_tree.column("language", width=55, minwidth=40, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.inv_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.inv_tree.xview)
        self.inv_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.inv_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # Tags styling
        self.inv_tree.tag_configure("proxy_tag", foreground="#666666")
        self.inv_tree.tag_configure("foil_tag", foreground="#996515")

        # Double-click opens card deck usages dialog
        self.inv_tree.bind("<Double-1>", self._on_card_double_click)

        # Treeview selection changes update the batch action bar
        self.inv_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_batch_bar())

        # Keyboard shortcuts: Select All (Cmd/Ctrl+A), Deselect (Esc)
        self.inv_tree.bind("<Control-a>", self._on_select_all_cards)
        self.inv_tree.bind("<Command-a>", self._on_select_all_cards)
        self.inv_tree.bind("<Escape>", self._on_deselect_all_cards)

        # Attach Floating Preview Tooltip
        self._preview_hover.attach_to_treeview(self.inv_tree, self._resolve_inv_card_preview)

    def _toggle_advanced_filters(self) -> None:
        if self._adv_filters_visible:
            self.adv_filters_frame.pack_forget()
            self.btn_toggle_filters.configure(text="🔽 Filtros Avanzados")
            self._adv_filters_visible = False
        else:
            self.adv_filters_frame.pack(fill=tk.X, after=self.toolbar_frame, padx=4, pady=(0, 4))
            self.btn_toggle_filters.configure(text="🔼 Ocultar Filtros")
            self._adv_filters_visible = True

    def _on_reset_filters_click(self) -> None:
        self._search_var.set("")
        self._filter_type_var.set("Todas")
        self._filter_usage_var.set("Todas")
        self._filter_set_var.set("Todas las ediciones")
        self._filter_rarity_var.set("Todas")
        self._filter_collection_table()

    def _update_inv_column_headings(self) -> None:
        for col, title in self._inv_col_headings.items():
            anchor = tk.W if col == "name" else tk.CENTER
            if col == self._inv_sort_col:
                arrow = " ▼" if self._inv_sort_descending else " ▲"
                heading_text = f"{title}{arrow}"
            else:
                heading_text = title
            self.inv_tree.heading(
                col,
                text=heading_text,
                anchor=anchor,
                command=lambda c=col: self._sort_collection_by_column(c),
            )

    def _sort_collection_by_column(self, col: str) -> None:
        if self._inv_sort_col == col:
            self._inv_sort_descending = not self._inv_sort_descending
        else:
            self._inv_sort_col = col
            self._inv_sort_descending = False

        self._update_inv_column_headings()
        self._filter_collection_table()

    def _get_card_sort_key(self, item: tuple[InventoryCard, int, int], col: str):
        return card_usage_sort_key(item, col)

    def _resolve_inv_card_preview(self, item_id: str) -> CardPreviewInfo | None:
        card = self._inv_item_to_card.get(item_id)
        if not card:
            return None
        return CardPreviewInfo(
            name=card.name,
            set_code=card.set_code,
            collector_number=card.collector_number,
            lang=card.language,
        )

    def _filter_collection_table(self) -> None:
        query = self._search_var.get().strip()
        filter_mode = self._filter_type_var.get()

        filter_proxy: bool | None = None
        filter_foil: bool | None = None
        if filter_mode == "Solo Reales":
            filter_proxy = False
        elif filter_mode == "Solo Proxies":
            filter_proxy = True
        elif filter_mode == "Solo Foils":
            filter_foil = True

        card_tuples = self.inventory_service.get_inventory_cards_with_usage(
            query=query,
            filter_proxy=filter_proxy,
            filter_foil=filter_foil,
            filter_usage=self._filter_usage_var.get().strip(),
            filter_set=self._filter_set_var.get().strip(),
            filter_rarity=self._filter_rarity_var.get().strip(),
            include_proxies=self._deck_include_proxies_var.get(),
            sort_by=self._inv_sort_col,
            sort_descending=self._inv_sort_descending,
        )

        self.inv_tree.delete(*self.inv_tree.get_children())
        self._inv_item_to_card.clear()
        self._inv_item_to_usage.clear()

        for card, in_decks_qty, available_qty in card_tuples:
            foil_str = "✨ Sí" if card.is_foil else "No"
            proxy_str = "Proxy" if card.is_proxy else "Física"
            tags = ()
            if card.is_proxy:
                tags = ("proxy_tag",)
            elif card.is_foil:
                tags = ("foil_tag",)

            item_id = self.inv_tree.insert(
                "",
                tk.END,
                values=(
                    card.name,
                    card.set_code.upper() if card.set_code else "--",
                    card.collector_number or "--",
                    card.quantity,
                    available_qty,
                    in_decks_qty,
                    foil_str,
                    proxy_str,
                    card.condition,
                    card.language.upper(),
                ),
                tags=tags,
            )
            self._inv_item_to_card[item_id] = card
            self._inv_item_to_usage[item_id] = (in_decks_qty, available_qty)

        self._update_batch_bar()

    def _on_card_double_click(self, _event=None) -> None:
        self._open_selected_card_usage_dialog()

    def _on_view_card_decks_click(self) -> None:
        self._open_selected_card_usage_dialog()

    def _open_selected_card_usage_dialog(self) -> None:
        selected = self.inv_tree.selection()
        if not selected:
            messagebox.showwarning(
                "Aviso", "Selecciona una carta del inventario primero.", parent=self
            )
            return

        item_id = selected[0]
        card = self._inv_item_to_card.get(item_id)
        if not card:
            return

        in_decks, available = self._inv_item_to_usage.get(item_id, (0, 0))
        dlg = CardDecksUsageDialog(
            self, card, self.inventory_service, in_decks=in_decks, available=available
        )
        self.wait_window(dlg)

    def _on_import_csv_click(self) -> None:
        csv_path_str = filedialog.askopenfilename(
            parent=self,
            title="Seleccionar exportación CSV de ManaBox",
            filetypes=[("Archivos CSV", "*.csv"), ("Todos los archivos", "*.*")],
        )
        if not csv_path_str:
            return

        csv_path = Path(csv_path_str)
        dlg = ManaBoxImportDialog(self)
        self.wait_window(dlg)

        mode = dlg.result
        if not mode:
            return

        job = self.task_manager.submit_job(
            f"Importar CSV ManaBox ({mode})",
            self._task_import_manabox,
            csv_path,
            mode,
        )

        def _on_import_done(ev: JobResultEvent) -> None:
            if ev.status == JobStatus.COMPLETED:
                count = ev.result or 0
                messagebox.showinfo(
                    "Importación Exitosa",
                    f"Se han procesado e importado {count} cartas correctamente.",
                    parent=self,
                )
                self.refresh_data()
            elif ev.status == JobStatus.FAILED:
                messagebox.showerror(
                    "Error de Importación",
                    f"No se pudo importar el archivo CSV:\n{ev.error}",
                    parent=self,
                )

        self._job_callbacks[job.id] = _on_import_done

    def _task_import_manabox(self, csv_path: Path, mode: str) -> int:
        return self.inventory_service.import_manabox_csv(csv_path, mode=mode)

    def _on_add_card_click(self) -> None:
        dlg = AddCardDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.inventory_service.add_or_update_card(dlg.result)
            self.refresh_data()

    def _on_select_all_cards(self, _event=None) -> str:
        all_items = self.inv_tree.get_children()
        if all_items:
            self.inv_tree.selection_set(all_items)
            self._update_batch_bar()
        return "break"

    def _on_deselect_all_cards(self, _event=None) -> str:
        self.inv_tree.selection_set(())
        self._update_batch_bar()
        return "break"

    def _update_batch_bar(self) -> None:
        selected_items = self.inv_tree.selection()
        if not selected_items:
            if hasattr(self, "batch_bar_frame") and self.batch_bar_frame.winfo_manager() == "pack":
                self.batch_bar_frame.pack_forget()
            return

        count = len(selected_items)
        total_copies = 0
        for item_id in selected_items:
            card = self._inv_item_to_card.get(item_id)
            if card:
                total_copies += card.quantity

        plural_cards = "cartas" if count != 1 else "carta"
        plural_copies = "copias" if total_copies != 1 else "copia"
        self.lbl_batch_count.configure(
            text=f"📌 {count} {plural_cards} seleccionadas ({total_copies} {plural_copies} en total)"
        )

        if hasattr(self, "batch_bar_frame") and self.batch_bar_frame.winfo_manager() != "pack":
            self.batch_bar_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(2, 4))

    def _on_batch_toggle_proxy_click(self) -> None:
        selected_items = self.inv_tree.selection()
        if not selected_items:
            return
        cards = [self._inv_item_to_card[i] for i in selected_items if i in self._inv_item_to_card]
        if not cards:
            return

        all_real = all(not c.is_proxy for c in cards)
        all_proxy = all(c.is_proxy for c in cards)

        if all_real:
            target_proxy = True
        elif all_proxy:
            target_proxy = False
        else:
            choice = messagebox.askyesnocancel(
                "Marcar Proxy / Físicas",
                "Las cartas seleccionadas tienen estados mixtos (físicas y proxies).\n\n"
                "• Pulsa Sí para marcar todas como PROXY.\n"
                "• Pulsa No para marcar todas como FÍSICAS (Reales).\n"
                "• Pulsa Cancelar para no realizar cambios.",
                parent=self,
            )
            if choice is None:
                return
            target_proxy = choice

        card_ids = [c.id for c in cards if c.id is not None]
        self.inventory_service.batch_update_cards(card_ids, is_proxy=target_proxy)
        self.refresh_data()

    def _on_batch_toggle_foil_click(self) -> None:
        selected_items = self.inv_tree.selection()
        if not selected_items:
            return
        cards = [self._inv_item_to_card[i] for i in selected_items if i in self._inv_item_to_card]
        if not cards:
            return

        all_normal = all(not c.is_foil for c in cards)
        all_foil = all(c.is_foil for c in cards)

        if all_normal:
            target_foil = True
        elif all_foil:
            target_foil = False
        else:
            choice = messagebox.askyesnocancel(
                "Marcar Foil / Normal",
                "Las cartas seleccionadas tienen acabados mixtos (normales y foil).\n\n"
                "• Pulsa Sí para marcar todas como FOIL.\n"
                "• Pulsa No para marcar todas como NORMAL (No Foil).\n"
                "• Pulsa Cancelar para no realizar cambios.",
                parent=self,
            )
            if choice is None:
                return
            target_foil = choice

        card_ids = [c.id for c in cards if c.id is not None]
        self.inventory_service.batch_update_cards(card_ids, is_foil=target_foil)
        self.refresh_data()

    def _on_batch_edit_click(self) -> None:
        selected_items = self.inv_tree.selection()
        if not selected_items:
            return
        cards = [self._inv_item_to_card[i] for i in selected_items if i in self._inv_item_to_card]
        if not cards:
            return

        dlg = BatchEditCardsDialog(self, count=len(cards))
        self.wait_window(dlg)
        if not dlg.result:
            return

        card_ids = [c.id for c in cards if c.id is not None]
        self.inventory_service.batch_update_cards(
            card_ids,
            is_proxy=dlg.result["is_proxy"],
            is_foil=dlg.result["is_foil"],
            condition=dlg.result["condition"],
            language=dlg.result["language"],
            set_quantity=dlg.result["set_quantity"],
            quantity_delta=dlg.result["quantity_delta"],
        )
        self.refresh_data()

    def _on_batch_assign_deck_click(self) -> None:
        selected_items = self.inv_tree.selection()
        if not selected_items:
            return
        cards = [self._inv_item_to_card[i] for i in selected_items if i in self._inv_item_to_card]
        if not cards:
            return

        decks = self.inventory_service.get_all_decks()
        dlg = BatchAssignDeckDialog(self, cards=cards, decks=decks)
        self.wait_window(dlg)
        if not dlg.result:
            return

        deck_id = dlg.result["deck_id"]
        if deck_id is None:
            new_name = dlg.result.get("new_name") or "Nuevo Mazo"
            new_fmt = dlg.result.get("new_format") or "Commander"
            deck_id = self.inventory_service.register_deck(
                DeckRecord(name=new_name, format=new_fmt),
                cards=[],
            )
            deck_display = new_name
        else:
            match_deck = next((d for d in decks if d.id == deck_id), None)
            deck_display = match_deck.name if match_deck else f"Mazo #{deck_id}"

        card_ids = [c.id for c in cards if c.id is not None]
        assigned = self.inventory_service.batch_assign_to_deck(
            deck_id=deck_id,
            card_ids=card_ids,
            copies_mode=dlg.result.get("copies_mode", "one"),
        )
        self.refresh_data()
        messagebox.showinfo(
            "Cartas Asignadas",
            f"Se han asignado/actualizado {assigned} carta(s) en el mazo '{deck_display}'.",
            parent=self,
        )

    def _on_delete_card_click(self) -> None:
        self._on_batch_delete_click()

    def _on_batch_delete_click(self) -> None:
        selected = self.inv_tree.selection()
        if not selected:
            messagebox.showwarning(
                "Aviso", "Selecciona una o más cartas del inventario para eliminar.", parent=self
            )
            return

        cards = [self._inv_item_to_card[i] for i in selected if i in self._inv_item_to_card]
        if not cards:
            return

        count = len(cards)
        total_copies = sum(c.quantity for c in cards)
        card_ids = [c.id for c in cards if c.id is not None]

        # Check usages in registered decks
        deck_usages = self.inventory_service.check_cards_in_decks(card_ids)
        cards_in_decks = {cid: dnames for cid, dnames in deck_usages.items() if dnames}

        if count == 1:
            c = cards[0]
            msg = f"¿Seguro que deseas eliminar '{c.name}' ({c.set_code.upper()} #{c.collector_number}) con {c.quantity} copia(s)?"
        else:
            msg = f"¿Estás seguro de que deseas eliminar {count} cartas ({total_copies} copias en total) de tu inventario?"

        if cards_in_decks:
            msg += "\n\n⚠️ ADVERTENCIA: Las siguientes cartas están asignadas a mazos registrados:\n"
            card_by_id = {c.id: c for c in cards}
            for cid, dnames in list(cards_in_decks.items())[:8]:
                card_obj = card_by_id.get(cid)
                c_title = card_obj.name if card_obj else f"ID {cid}"
                msg += f" • {c_title} (en: {', '.join(dnames)})\n"
            if len(cards_in_decks) > 8:
                msg += f" ... y {len(cards_in_decks) - 8} carta(s) más.\n"
            msg += "\nSi las eliminas, aparecerán como faltantes (proxies a imprimir) en dichos mazos.\n"

        msg += "\nEsta acción no se puede deshacer."

        if messagebox.askyesno("Confirmar Eliminación", msg, parent=self):
            self.inventory_service.batch_delete_cards(card_ids)
            self.refresh_data()

    # ---------------------------------------------------------------------------
    # Tab 2: Decks & Moxfield (Mazos & Moxfield)
    # ---------------------------------------------------------------------------

    def _build_decks_tab(self, parent: ttk.Frame) -> None:
        # Top deck importer URL bar
        url_frame = ttk.LabelFrame(parent, text="Importar Nuevo Mazo desde URL", padding=(8, 6))
        url_frame.pack(fill=tk.X, padx=6, pady=(4, 6))

        ttk.Label(url_frame, text="URL del Mazo:").pack(side=tk.LEFT, padx=(2, 6))
        self.ent_deck_url = ttk.Entry(url_frame, textvariable=self._deck_url_var)
        self.ent_deck_url.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_load_deck = ttk.Button(
            url_frame, text="📥 Cargar Mazo", command=self._on_load_deck_click
        )
        btn_load_deck.pack(side=tk.RIGHT, padx=2)

        # Deck selector & header frame
        sel_frame = ttk.Frame(parent, padding=(4, 4))
        sel_frame.pack(fill=tk.X, padx=6, pady=2)

        ttk.Label(sel_frame, text="Mazo Seleccionado:", font=("Segoe UI", 9, "bold")).pack(
            side=tk.LEFT, padx=(2, 6)
        )
        self.cb_decks = ttk.Combobox(sel_frame, state="readonly", width=28)
        self.cb_decks.pack(side=tk.LEFT, padx=(0, 6))
        self.cb_decks.bind("<<ComboboxSelected>>", self._on_deck_combobox_changed)

        btn_delete_deck = ttk.Button(
            sel_frame, text="🗑️ Eliminar Mazo", command=self._on_delete_deck_click
        )
        btn_delete_deck.pack(side=tk.LEFT, padx=4)

        # Stock evaluation options
        self._chk_include_proxies = ttk.Checkbutton(
            sel_frame,
            text="Contar proxies",
            variable=self._deck_include_proxies_var,
            command=self._on_deck_options_changed,
        )
        self._chk_include_proxies.pack(side=tk.LEFT, padx=(8, 4))

        ttk.Label(sel_frame, text="Asignación:").pack(side=tk.LEFT, padx=(6, 2))
        self.cb_allocate_mode = ttk.Combobox(
            sel_frame,
            values=["Exclusiva", "Compartida"],
            state="readonly",
            width=11,
        )
        self.cb_allocate_mode.set("Exclusiva")
        self.cb_allocate_mode.pack(side=tk.LEFT, padx=(0, 6))
        self.cb_allocate_mode.bind("<<ComboboxSelected>>", self._on_deck_options_changed)

        self.lbl_deck_info = ttk.Label(
            sel_frame, text="", font=("Segoe UI", 9), foreground="#555555"
        )
        self.lbl_deck_info.pack(side=tk.LEFT, padx=(6, 0))

        # Deck breakdown table
        table_frame = ttk.Frame(parent)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        deck_cols = (
            "name",
            "set_code",
            "collector_number",
            "qty_required",
            "assigned_version",
            "physical_qty",
            "proxy_qty",
            "status",
            "is_proxy",
        )
        self.deck_tree = ttk.Treeview(
            table_frame, columns=deck_cols, show="headings", selectmode="browse"
        )

        self.deck_tree.heading("name", text="Carta", anchor=tk.W)
        self.deck_tree.heading("set_code", text="Edición Mazo", anchor=tk.CENTER)
        self.deck_tree.heading("collector_number", text="Número", anchor=tk.CENTER)
        self.deck_tree.heading("qty_required", text="Req.", anchor=tk.CENTER)
        self.deck_tree.heading(
            "assigned_version", text="Versión Asignada (Inventario)", anchor=tk.W
        )
        self.deck_tree.heading("physical_qty", text="Físicas", anchor=tk.CENTER)
        self.deck_tree.heading("proxy_qty", text="Proxies", anchor=tk.CENTER)
        self.deck_tree.heading("status", text="Estado", anchor=tk.CENTER)
        self.deck_tree.heading("is_proxy", text="Proxy?", anchor=tk.CENTER)

        self.deck_tree.column("name", width=190, minwidth=110, anchor=tk.W)
        self.deck_tree.column("set_code", width=80, minwidth=50, anchor=tk.CENTER)
        self.deck_tree.column("collector_number", width=55, minwidth=40, anchor=tk.CENTER)
        self.deck_tree.column("qty_required", width=50, minwidth=35, anchor=tk.CENTER)
        self.deck_tree.column("assigned_version", width=220, minwidth=130, anchor=tk.W)
        self.deck_tree.column("physical_qty", width=60, minwidth=45, anchor=tk.CENTER)
        self.deck_tree.column("proxy_qty", width=60, minwidth=45, anchor=tk.CENTER)
        self.deck_tree.column("status", width=135, minwidth=90, anchor=tk.CENTER)
        self.deck_tree.column("is_proxy", width=55, minwidth=45, anchor=tk.CENTER)

        self.deck_tree.bind("<Button-1>", self._on_deck_tree_click)

        vsb_deck = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.deck_tree.yview)
        hsb_deck = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.deck_tree.xview)
        self.deck_tree.configure(yscrollcommand=vsb_deck.set, xscrollcommand=hsb_deck.set)

        self.deck_tree.grid(row=0, column=0, sticky="nsew")
        vsb_deck.grid(row=0, column=1, sticky="ns")
        hsb_deck.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # Tags for Deck Treeview
        self.deck_tree.tag_configure("owned", foreground="#2e7d32")
        self.deck_tree.tag_configure("alt_stock", foreground="#1565c0")
        self.deck_tree.tag_configure("missing", foreground="#c62828", font=("Segoe UI", 9, "bold"))
        self.deck_tree.tag_configure("partial", foreground="#ef6c00")

        # Attach Floating Preview Tooltip to Deck Treeview
        self._preview_hover.attach_to_treeview(self.deck_tree, self._resolve_deck_card_preview)

        # Bottom Deck Actions & Summary
        bottom_frame = ttk.Frame(parent, padding=(6, 6))
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.lbl_plan_summary = ttk.Label(
            bottom_frame,
            text="Selecciona un mazo para ver el desglose de stock y proxies.",
            font=("Segoe UI", 9, "bold"),
            foreground="#333333",
        )
        self.lbl_plan_summary.pack(side=tk.LEFT)

        btn_volcar = ttk.Button(
            bottom_frame,
            text="📥 Volcar cartas del mazo al inventario",
            command=self._on_import_deck_to_inventory_click,
        )
        btn_volcar.pack(side=tk.RIGHT, padx=4)

        btn_gen_pdf = ttk.Button(
            bottom_frame,
            text="📄 Generar PDF de Cartas Faltantes",
            command=self._on_generate_missing_pdf_click,
        )
        btn_gen_pdf.pack(side=tk.RIGHT, padx=4)

    def _resolve_deck_card_preview(self, item_id: str) -> CardPreviewInfo | None:
        return self._deck_item_to_info.get(item_id)

    def _on_load_deck_click(self) -> None:
        url = self._deck_url_var.get().strip()
        if not url:
            messagebox.showwarning(
                "Aviso",
                "Introduce la URL de un mazo (Moxfield, Archidekt, Manabox, etc.).",
                parent=self,
            )
            return

        job = self.task_manager.submit_job(
            "Cargar Mazo desde URL",
            self._task_fetch_and_register_deck,
            url,
        )

        def _on_deck_loaded(ev: JobResultEvent) -> None:
            if ev.status == JobStatus.COMPLETED:
                deck_id = ev.result
                messagebox.showinfo(
                    "Mazo Cargado", "El mazo se ha importado y registrado con éxito.", parent=self
                )
                self._deck_url_var.set("")
                self.refresh_data()
                self._select_deck_by_id(deck_id)
            elif ev.status == JobStatus.FAILED:
                messagebox.showerror(
                    "Error al Cargar Mazo",
                    f"No se pudo obtener el mazo desde la URL indicada:\n{ev.error}",
                    parent=self,
                )

        self._job_callbacks[job.id] = _on_deck_loaded

    def _task_fetch_and_register_deck(self, url: str) -> int:
        fetched = fetch_deck(url)
        deck_rec = DeckRecord(
            name=fetched.name or "Mazo Importado",
            source_url=url,
            format=fetched.format or "Commander",
        )
        deck_cards = [
            DeckCardEntry(
                deck_id=0,
                oracle_id="",
                name=c.name,
                set_code=c.set_code,
                collector_number=c.collector_number,
                quantity_required=c.quantity,
                is_proxy=False,
            )
            for c in fetched.cards
            if c.zone not in ("side", "sideboard", "maybe", "maybeboard", "token")
        ]
        return self.inventory_service.register_deck(deck_rec, deck_cards)

    def _on_deck_options_changed(self, _event=None) -> None:
        mode_val = self.cb_allocate_mode.get().strip().lower()
        self._deck_allocate_mode_var.set("shared" if "compartida" in mode_val else "exclusive")
        self._filter_collection_table()
        if self._current_deck_id is not None:
            deck_data = self.inventory_service.get_deck_by_id(self._current_deck_id)
            if deck_data:
                self._select_deck(deck_data[0])

    def _on_deck_combobox_changed(self, _event=None) -> None:
        idx = self.cb_decks.current()
        if idx >= 0 and idx < len(self._registered_decks):
            self._select_deck(self._registered_decks[idx])
        else:
            self._clear_deck_details()

    def _select_deck_by_id(self, deck_id: int) -> None:
        for idx, d in enumerate(self._registered_decks):
            if d.id == deck_id:
                self.cb_decks.current(idx)
                self._select_deck(d)
                break

    def _destroy_active_cell_editor(self) -> None:
        if self._active_cell_editor is not None:
            try:
                self._active_cell_editor.destroy()
            except Exception:
                pass
            self._active_cell_editor = None

    @staticmethod
    def _format_version_option_label(version: dict) -> str:
        """Format a version dict from DeckCardMatchDetail.available_versions for GUI display."""
        foil_str = " (Foil)" if version.get("is_foil") else ""
        proxy_str = " [Proxy]" if version.get("is_proxy") else ""
        return (
            f"{version['set_code']} #{version['collector_number']} - "
            f"{version['quantity_available']} disp.{foil_str}{proxy_str}"
        )

    def _apply_deck_card_allocation(
        self,
        deck_card_id: int,
        physical_quantity: int,
        proxy_quantity: int,
        fulfilled_card_id: int | None,
    ) -> None:
        self.inventory_service.update_deck_card_allocation(
            deck_card_id,
            physical_quantity=physical_quantity,
            proxy_quantity=proxy_quantity,
            fulfilled_card_id=fulfilled_card_id,
        )
        if self._current_deck_id is not None:
            deck_data = self.inventory_service.get_deck_by_id(self._current_deck_id)
            if deck_data:
                self._select_deck(deck_data[0])

    def _on_deck_tree_click(self, event: tk.Event) -> None:
        self._destroy_active_cell_editor()
        region = self.deck_tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        row_id = self.deck_tree.identify_row(event.y)
        col_id = self.deck_tree.identify_column(event.x)
        if not row_id or not col_id:
            return

        detail = self._deck_row_data.get(row_id)
        if not detail or detail.deck_card_id is None:
            return

        # Column #9 is is_proxy toggle
        if col_id == "#9":
            new_phys, new_proxy, new_fulfilled = detail.resolve_proxy_toggle()
            self._apply_deck_card_allocation(
                detail.deck_card_id, new_phys, new_proxy, new_fulfilled
            )
            return

        # Column #5 is assigned_version dropdown
        elif col_id == "#5":
            if not detail.available_versions:
                return

            bbox = self.deck_tree.bbox(row_id, "assigned_version")
            if not bbox:
                return
            x, y, w, h = bbox

            options = ["Auto: Mejor combinación (Stock disponible)"]
            option_to_card_id: dict[str, int | None] = {
                "Auto: Mejor combinación (Stock disponible)": None
            }

            for v in detail.available_versions:
                lbl = self._format_version_option_label(v)
                options.append(lbl)
                option_to_card_id[lbl] = v["card_id"]

            options.append("Ninguna (Solo Proxy)")
            option_to_card_id["Ninguna (Solo Proxy)"] = -1

            cb = ttk.Combobox(self.deck_tree, values=options, state="readonly")
            cb.place(x=x, y=y, width=max(w, 230), height=h)
            self._active_cell_editor = cb

            if detail.physical_quantity == 0 and detail.proxy_quantity > 0:
                cb.set("Ninguna (Solo Proxy)")
            elif detail.assigned_version_id is not None:
                match_lbl = next(
                    (
                        self._format_version_option_label(v)
                        for v in detail.available_versions
                        if v["card_id"] == detail.assigned_version_id
                    ),
                    None,
                )
                cb.set(match_lbl or options[0])
            else:
                cb.set(options[0])

            def _on_version_selected(_ev=None) -> None:
                chosen = cb.get()
                self._destroy_active_cell_editor()
                card_id_target = option_to_card_id.get(chosen)
                new_phys, new_proxy, new_fulfilled = detail.resolve_version_selection(
                    card_id_target
                )
                self._apply_deck_card_allocation(
                    detail.deck_card_id, new_phys, new_proxy, new_fulfilled
                )

            cb.bind("<<ComboboxSelected>>", _on_version_selected)
            cb.bind("<FocusOut>", lambda _e: self._destroy_active_cell_editor())
            cb.focus_set()
            return

        # Column #6 is physical_qty spinbox
        elif col_id == "#6":
            bbox = self.deck_tree.bbox(row_id, "physical_qty")
            if not bbox:
                return
            x, y, w, h = bbox

            max_limit = detail.max_physical_for_version(detail.assigned_version_id)

            sp = ttk.Spinbox(self.deck_tree, from_=0, to=max(0, max_limit), width=5)
            sp.set(detail.physical_quantity)
            sp.place(x=x, y=y, width=w, height=h)
            self._active_cell_editor = sp

            def _on_spin_commit(_ev=None) -> None:
                val_str = sp.get().strip()
                self._destroy_active_cell_editor()
                try:
                    val = int(val_str)
                except ValueError:
                    return
                new_phys, new_proxy, new_fulfilled = detail.resolve_physical_quantity(val)
                self._apply_deck_card_allocation(
                    detail.deck_card_id, new_phys, new_proxy, new_fulfilled
                )

            sp.bind("<Return>", _on_spin_commit)
            sp.bind("<FocusOut>", lambda _e: _on_spin_commit())
            sp.focus_set()
            return

    def _select_deck(self, deck: DeckRecord) -> None:
        if deck.id is None:
            return

        self._current_deck_id = deck.id
        deck_data = self.inventory_service.get_deck_by_id(deck.id)
        if not deck_data:
            self._clear_deck_details()
            return

        deck_rec, deck_cards = deck_data
        self._current_deck_cards = deck_cards

        # Update info header
        self.lbl_deck_info.configure(
            text=f"Formato: {deck_rec.format} | Total cartas: {sum(c.quantity_required for c in deck_cards)}"
        )

        details_list, total_exact, total_alt, total_missing = (
            self.inventory_service.analyze_deck_card_matches(
                deck_cards,
                current_deck_id=deck.id,
                allocate_mode=self._deck_allocate_mode_var.get(),
                include_proxies=self._deck_include_proxies_var.get(),
            )
        )

        self._destroy_active_cell_editor()
        self.deck_tree.delete(*self.deck_tree.get_children())
        self._deck_item_to_info.clear()
        self._deck_row_data.clear()

        for detail in details_list:
            if detail.status == "exact":
                status_str = "🟢 Exacta"
                tag = "owned"
            elif detail.status == "alternative":
                status_str = "🔵 Alternativa"
                tag = "alt_stock"
            elif detail.status == "partial":
                status_str = f"⚠️ Parcial ({detail.physical_quantity}/{detail.quantity_required})"
                tag = "partial"
            else:
                status_str = "🔴 Faltante (Proxy)"
                tag = "missing"

            deck_print_str = detail.set_code.upper() if detail.set_code else "--"
            coll_num_str = detail.collector_number or "--"

            if detail.available_versions:
                if detail.physical_quantity == 0 and detail.proxy_quantity > 0:
                    assigned_ver_str = "Ninguna (Solo Proxy)"
                elif detail.assigned_version_id is not None:
                    assigned_v = next(
                        (
                            v
                            for v in detail.available_versions
                            if v["card_id"] == detail.assigned_version_id
                        ),
                        None,
                    )
                    if assigned_v:
                        foil_txt = " (Foil)" if assigned_v["is_foil"] else ""
                        assigned_ver_str = (
                            f"{assigned_v['set_code']} #{assigned_v['collector_number']}{foil_txt}"
                        )
                    else:
                        assigned_ver_str = "Auto (Combinación)"
                else:
                    assigned_ver_str = "Auto (Combinación)"
            else:
                assigned_ver_str = "Sin stock (Solo Proxy)"

            proxy_check_str = (
                "[x]" if (detail.physical_quantity == 0 and detail.quantity_required > 0) else "[ ]"
            )

            item_id = self.deck_tree.insert(
                "",
                tk.END,
                values=(
                    detail.name,
                    deck_print_str,
                    coll_num_str,
                    detail.quantity_required,
                    assigned_ver_str,
                    detail.physical_quantity,
                    detail.proxy_quantity,
                    status_str,
                    proxy_check_str,
                ),
                tags=(tag,),
            )
            self._deck_row_data[item_id] = detail

            # Preview: deck print by default, or assigned version if specified
            preview_set = detail.set_code
            preview_num = detail.collector_number
            if detail.assigned_version_id is not None:
                match_v = next(
                    (
                        v
                        for v in detail.available_versions
                        if v["card_id"] == detail.assigned_version_id
                    ),
                    None,
                )
                if match_v:
                    preview_set = match_v["set_code"]
                    preview_num = match_v["collector_number"]

            self._deck_item_to_info[item_id] = CardPreviewInfo(
                name=detail.name,
                set_code=preview_set,
                collector_number=preview_num,
            )

        total_required = sum(c.quantity_required for c in deck_cards)
        self.lbl_plan_summary.configure(
            text=(
                f"Requeridas: {total_required} | "
                f"Stock Exacto: {total_exact} | "
                f"Stock Alternativo: {total_alt} | "
                f"Faltantes (Proxies a Imprimir): {total_missing}"
            )
        )

    def _clear_deck_details(self) -> None:
        self._destroy_active_cell_editor()
        self._current_deck_id = None
        self._current_deck_cards = []
        self._deck_row_data.clear()
        self.deck_tree.delete(*self.deck_tree.get_children())
        self._deck_item_to_info.clear()
        self.lbl_deck_info.configure(text="")
        self.lbl_plan_summary.configure(
            text="Selecciona un mazo para ver el desglose de stock y proxies."
        )

    def _on_delete_deck_click(self) -> None:
        if self._current_deck_id is None:
            messagebox.showwarning("Aviso", "Selecciona un mazo para eliminar.", parent=self)
            return

        deck_data = self.inventory_service.get_deck_by_id(self._current_deck_id)
        if not deck_data:
            return

        deck_rec, _ = deck_data
        if messagebox.askyesno(
            "Confirmar Eliminación",
            f"¿Seguro que deseas eliminar el mazo '{deck_rec.name}'?",
            parent=self,
        ):
            self.inventory_service.delete_deck(self._current_deck_id)
            self._current_deck_id = None
            self.refresh_data()

    def _on_import_deck_to_inventory_click(self) -> None:
        if self._current_deck_id is None:
            messagebox.showwarning("Aviso", "Selecciona un mazo primero.", parent=self)
            return

        dlg = DeckImportOptionDialog(self)
        self.wait_window(dlg)

        if dlg.result is not None:
            as_proxy = dlg.result
            count = self.inventory_service.import_deck_cards_to_inventory(
                self._current_deck_id, as_proxy=as_proxy
            )
            tipo = "cartas proxy" if as_proxy else "cartas físicas reales"
            messagebox.showinfo(
                "Volcado Completado",
                f"Se han volcado {count} {tipo} a tu inventario.",
                parent=self,
            )
            self.refresh_data()

    def _on_generate_missing_pdf_click(self) -> None:
        if self._current_deck_id is None or not self._current_deck_cards:
            messagebox.showwarning("Aviso", "Selecciona un mazo primero.", parent=self)
            return

        deck_data = self.inventory_service.get_deck_by_id(self._current_deck_id)
        if not deck_data:
            return
        deck_rec, deck_cards = deck_data

        plan = self.inventory_service.calculate_missing_proxies(
            deck_cards,
            allocate_mode=self._deck_allocate_mode_var.get(),
            current_deck_id=self._current_deck_id,
            include_proxies=self._deck_include_proxies_var.get(),
        )

        if not plan.missing_cards:
            messagebox.showinfo(
                "Colección Completa",
                "¡Tienes todas las cartas necesarias en tu colección! No hay proxies faltantes que generar.",
                parent=self,
            )
            return

        job = self.task_manager.submit_job(
            f"Generar PDF Proxies: {deck_rec.name}",
            self._task_generate_missing_proxies_pdf,
            deck_rec,
            plan.missing_cards,
        )

        def _on_pdf_completed(ev: JobResultEvent) -> None:
            if ev.status == JobStatus.COMPLETED:
                result_paths = ev.result
                paths_str = (
                    "\n".join(str(p) for p in result_paths)
                    if isinstance(result_paths, list)
                    else str(result_paths)
                )
                messagebox.showinfo(
                    "PDF Generado con Éxito",
                    f"Se ha generado el PDF con las cartas faltantes:\n\n{paths_str}",
                    parent=self,
                )
            elif ev.status == JobStatus.FAILED:
                messagebox.showerror(
                    "Error al Generar PDF",
                    f"Ocurrió un error al generar el PDF de proxies:\n{ev.error}",
                    parent=self,
                )

        self._job_callbacks[job.id] = _on_pdf_completed

    def _task_generate_missing_proxies_pdf(
        self,
        deck: DeckRecord,
        missing_cards: list[DeckCardEntry],
        cancel_event=None,
    ) -> list[Path]:
        cards = [
            DeckCard(
                name=c.name,
                set_code=c.set_code,
                collector_number=c.collector_number,
                quantity=c.quantity_required,
                zone="main",
            )
            for c in missing_cards
        ]
        mtg_deck = MtgUrlDeck(
            url=deck.source_url or "",
            cards=cards,
            name=f"{deck.name}_proxies",
            format=deck.format or "commander",
        )
        spec = PrintJobSpec(
            mtg_decks=[mtg_deck],
            output_dir=output_dir(),
            work_dir=work_dir(),
            base_name=sanitize_filename(f"{deck.name}_proxies"),
            cancel_event=cancel_event,
        )
        return PrintPipeline.execute(spec).pdf_paths

    # ---------------------------------------------------------------------------
    # Global Refresh & Event Polling Lifecycle
    # ---------------------------------------------------------------------------

    def refresh_data(self) -> None:
        """Re-query inventory database and update all UI views and tables."""
        # 1. Update stats banner
        stats = self.inventory_service.get_inventory_stats()
        self.lbl_stats.configure(
            text=(
                f"Colección: {stats['total_cards']} cartas  |  "
                f"Físicas: {stats['physical_count']}  |  "
                f"Proxies: {stats['proxy_count']}  |  "
                f"Mazos: {stats['deck_count']}"
            )
        )

        # 2. Update set codes in advanced filter combobox
        unique_sets = ["Todas las ediciones"] + self.inventory_service.get_unique_set_codes()
        self.cb_filter_set.configure(values=unique_sets)
        if self._filter_set_var.get() not in unique_sets:
            self._filter_set_var.set("Todas las ediciones")

        # 3. Update collection table
        self._filter_collection_table()

        # 4. Update registered decks selector
        self._registered_decks = self.inventory_service.get_all_decks()
        deck_names = [f"{d.name} ({d.format})" for d in self._registered_decks]
        self.cb_decks.configure(values=deck_names)

        if self._current_deck_id is not None:
            self._select_deck_by_id(self._current_deck_id)
        elif self._registered_decks:
            self.cb_decks.current(0)
            self._select_deck(self._registered_decks[0])
        else:
            self.cb_decks.set("")
            self._clear_deck_details()

    def _schedule_poll(self) -> None:
        self._poll_after_id = self.after(100, self._poll_background_tasks)

    def _poll_background_tasks(self) -> None:
        if not self.winfo_exists():
            self._poll_after_id = None
            return

        events = self.task_manager.poll_events()
        for ev in events:
            if isinstance(ev, JobResultEvent):
                cb = self._job_callbacks.pop(ev.job_id, None)
                if cb:
                    try:
                        cb(ev)
                    except Exception as e:
                        _log.error("Error in inventory view job callback: %s", e)

        self._schedule_poll()

    def destroy(self) -> None:
        """Clean up preview hover tooltips and background timers."""
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None

        self._preview_hover.destroy()
        super().destroy()
