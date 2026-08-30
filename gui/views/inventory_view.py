"""Card Inventory and Deck Binder view component for MPCFillToPDF."""

from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gui.components.card_preview_hover import CardHoverPreview, CardPreviewInfo
from gui.paths import output_dir, work_dir
from src.deck_importer import DeckCard, fetch_deck
from src.engine.task_manager import JobResultEvent, JobStatus, TaskManager
from src.inventory.models import DeckCardEntry, DeckRecord, InventoryCard
from src.inventory.service import InventoryService
from src.pipeline import run_locals_only
from src.scraper_utils import resources_dir, sanitize_filename
from src.scryfall import download_deck_images

_log = logging.getLogger(__name__)


class ManaBoxImportDialog(tk.Toplevel):
    """Modal dialog asking the user whether to merge or replace the inventory when importing ManaBox CSV."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Modo de Importación ManaBox")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.grab_set()

        self.result: str | None = None
        self._mode_var = tk.StringVar(value="merge")

        self._build_ui()
        self._center_window(parent)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        lbl_title = ttk.Label(
            container,
            text="¿Cómo deseas importar el archivo CSV de ManaBox?",
            font=("Segoe UI", 10, "bold"),
            wraplength=380,
        )
        lbl_title.pack(anchor=tk.W, pady=(0, 10))

        # Option 1: Merge
        opt_merge = ttk.Radiobutton(
            container,
            text="Combinar (Merge)",
            value="merge",
            variable=self._mode_var,
        )
        opt_merge.pack(anchor=tk.W)
        lbl_merge_desc = ttk.Label(
            container,
            text="Actualiza cantidades y versiones sin borrar mazos ni cartas existentes en el inventario.",
            foreground="#555555",
            wraplength=360,
        )
        lbl_merge_desc.pack(anchor=tk.W, padx=(20, 0), pady=(0, 8))

        # Option 2: Replace
        opt_replace = ttk.Radiobutton(
            container,
            text="Sustituir (Replace)",
            value="replace",
            variable=self._mode_var,
        )
        opt_replace.pack(anchor=tk.W)
        lbl_replace_desc = ttk.Label(
            container,
            text="Borra la colección física no asignada a mazos y carga el nuevo archivo desde cero.",
            foreground="#555555",
            wraplength=360,
        )
        lbl_replace_desc.pack(anchor=tk.W, padx=(20, 0), pady=(0, 12))

        ttk.Separator(container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X, pady=(6, 0))

        btn_cancel = ttk.Button(btn_box, text="Cancelar", command=self._on_cancel)
        btn_cancel.pack(side=tk.RIGHT, padx=(4, 0))

        btn_ok = ttk.Button(btn_box, text="Importar", command=self._on_accept)
        btn_ok.pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._on_accept())
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _center_window(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
        except Exception:
            x, y = 200, 200
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_accept(self) -> None:
        self.result = self._mode_var.get()
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class AddCardDialog(tk.Toplevel):
    """Modal dialog to manually enter a new card into the inventory."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Añadir Carta al Inventario")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.grab_set()

        self.result: InventoryCard | None = None

        self._name_var = tk.StringVar()
        self._set_var = tk.StringVar()
        self._num_var = tk.StringVar()
        self._qty_var = tk.IntVar(value=1)
        self._foil_var = tk.BooleanVar(value=False)
        self._proxy_var = tk.BooleanVar(value=False)
        self._cond_var = tk.StringVar(value="NM")
        self._lang_var = tk.StringVar(value="en")

        self._build_ui()
        self._center_window(parent)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        grid = ttk.Frame(container)
        grid.pack(fill=tk.BOTH, expand=True)

        ttk.Label(grid, text="Nombre:").grid(row=0, column=0, sticky=tk.W, pady=4)
        ent_name = ttk.Entry(grid, textvariable=self._name_var, width=28)
        ent_name.grid(row=0, column=1, sticky=tk.EW, pady=4)
        ent_name.focus_set()

        ttk.Label(grid, text="Edición (Set):").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self._set_var, width=12).grid(row=1, column=1, sticky=tk.W, pady=4)

        ttk.Label(grid, text="Número:").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self._num_var, width=12).grid(row=2, column=1, sticky=tk.W, pady=4)

        ttk.Label(grid, text="Cantidad:").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Spinbox(grid, from_=1, to=999, textvariable=self._qty_var, width=8).grid(row=3, column=1, sticky=tk.W, pady=4)

        ttk.Label(grid, text="Condición:").grid(row=4, column=0, sticky=tk.W, pady=4)
        cb_cond = ttk.Combobox(
            grid,
            textvariable=self._cond_var,
            values=["NM", "LP", "MP", "HP", "DMG"],
            state="readonly",
            width=8,
        )
        cb_cond.grid(row=4, column=1, sticky=tk.W, pady=4)

        ttk.Label(grid, text="Idioma:").grid(row=5, column=0, sticky=tk.W, pady=4)
        cb_lang = ttk.Combobox(
            grid,
            textvariable=self._lang_var,
            values=["en", "es", "ja", "fr", "de", "it", "pt"],
            state="readonly",
            width=8,
        )
        cb_lang.grid(row=5, column=1, sticky=tk.W, pady=4)

        chk_frame = ttk.Frame(grid)
        chk_frame.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(6, 4))
        ttk.Checkbutton(chk_frame, text="Foil", variable=self._foil_var).pack(side=tk.LEFT, padx=(0, 14))
        ttk.Checkbutton(chk_frame, text="Proxy", variable=self._proxy_var).pack(side=tk.LEFT)

        ttk.Separator(container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X)
        ttk.Button(btn_box, text="Cancelar", command=self._on_cancel).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_box, text="Añadir", command=self._on_accept).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._on_accept())
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _center_window(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
        except Exception:
            x, y = 200, 200
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_accept(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            messagebox.showwarning("Campo Requerido", "Introduce el nombre de la carta.", parent=self)
            return

        qty = max(1, self._qty_var.get())
        self.result = InventoryCard(
            oracle_id="",
            name=name,
            set_code=self._set_var.get().strip().lower(),
            collector_number=self._num_var.get().strip(),
            quantity=qty,
            is_foil=self._foil_var.get(),
            is_proxy=self._proxy_var.get(),
            condition=self._cond_var.get(),
            language=self._lang_var.get(),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class DeckImportOptionDialog(tk.Toplevel):
    """Modal dialog asking whether to import deck cards as physical copies or proxies."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Volcar cartas al inventario")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.grab_set()

        self.result: bool | None = None
        self._choice_var = tk.StringVar(value="physical")

        self._build_ui()
        self._center_window(parent)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        lbl_title = ttk.Label(
            container,
            text="¿Cómo deseas añadir las cartas del mazo a tu inventario?",
            font=("Segoe UI", 10, "bold"),
            wraplength=360,
        )
        lbl_title.pack(anchor=tk.W, pady=(0, 10))

        ttk.Radiobutton(
            container,
            text="Cartas Físicas (Reales)",
            value="physical",
            variable=self._choice_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(
            container,
            text="Se registrarán como copias físicas reales en tu colección.",
            foreground="#555555",
            wraplength=340,
        ).pack(anchor=tk.W, padx=(20, 0), pady=(0, 6))

        ttk.Radiobutton(
            container,
            text="Cartas Proxy",
            value="proxy",
            variable=self._choice_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Label(
            container,
            text="Se registrarán marcadas como copias proxy (no físicas).",
            foreground="#555555",
            wraplength=340,
        ).pack(anchor=tk.W, padx=(20, 0), pady=(0, 10))

        ttk.Separator(container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_box, text="Cancelar", command=self._on_cancel).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_box, text="Aceptar", command=self._on_accept).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._on_accept())
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _center_window(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
        except Exception:
            x, y = 200, 200
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_accept(self) -> None:
        self.result = (self._choice_var.get() == "proxy")
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


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
        self._deck_item_to_info: dict[str, CardPreviewInfo] = {}
        self._registered_decks: list[DeckRecord] = []
        self._current_deck_id: int | None = None
        self._current_deck_cards: list[DeckCardEntry] = []

        # UI State variables
        self._search_var = tk.StringVar()
        self._filter_type_var = tk.StringVar(value="Todas")
        self._deck_url_var = tk.StringVar()

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
        toolbar = ttk.Frame(parent, padding=(4, 6))
        toolbar.pack(fill=tk.X)

        # Search & Filter
        ttk.Label(toolbar, text="Buscar:").pack(side=tk.LEFT, padx=(2, 4))
        self.ent_search = ttk.Entry(toolbar, textvariable=self._search_var, width=22)
        self.ent_search.pack(side=tk.LEFT, padx=(0, 8))
        self._search_var.trace_add("write", lambda *_: self._filter_collection_table())

        ttk.Label(toolbar, text="Filtrar:").pack(side=tk.LEFT, padx=(4, 4))
        self.cb_filter = ttk.Combobox(
            toolbar,
            textvariable=self._filter_type_var,
            values=["Todas", "Solo Reales", "Solo Proxies", "Solo Foils"],
            state="readonly",
            width=14,
        )
        self.cb_filter.pack(side=tk.LEFT, padx=(0, 8))
        self.cb_filter.bind("<<ComboboxSelected>>", lambda _: self._filter_collection_table())

        btn_refresh = ttk.Button(toolbar, text="🔄 Refrescar", command=self.refresh_data)
        btn_refresh.pack(side=tk.LEFT, padx=(0, 12))

        # Action Buttons (Right aligned)
        btn_import_csv = ttk.Button(
            toolbar,
            text="📥 Importar CSV de ManaBox",
            command=self._on_import_csv_click,
        )
        btn_import_csv.pack(side=tk.RIGHT, padx=3)

        btn_add_card = ttk.Button(
            toolbar,
            text="➕ Añadir Carta",
            command=self._on_add_card_click,
        )
        btn_add_card.pack(side=tk.RIGHT, padx=3)

        btn_delete_card = ttk.Button(
            toolbar,
            text="🗑️ Eliminar Carta",
            command=self._on_delete_card_click,
        )
        btn_delete_card.pack(side=tk.RIGHT, padx=3)

        # Collection Treeview Table
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        columns = ("name", "set_code", "collector_number", "quantity", "foil", "proxy", "condition", "language")
        self.inv_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")

        self.inv_tree.heading("name", text="Nombre", anchor=tk.W)
        self.inv_tree.heading("set_code", text="Edición", anchor=tk.CENTER)
        self.inv_tree.heading("collector_number", text="Número", anchor=tk.CENTER)
        self.inv_tree.heading("quantity", text="Cantidad", anchor=tk.CENTER)
        self.inv_tree.heading("foil", text="Foil", anchor=tk.CENTER)
        self.inv_tree.heading("proxy", text="Proxy", anchor=tk.CENTER)
        self.inv_tree.heading("condition", text="Condición", anchor=tk.CENTER)
        self.inv_tree.heading("language", text="Idioma", anchor=tk.CENTER)

        self.inv_tree.column("name", width=260, minwidth=140, anchor=tk.W)
        self.inv_tree.column("set_code", width=80, minwidth=60, anchor=tk.CENTER)
        self.inv_tree.column("collector_number", width=70, minwidth=50, anchor=tk.CENTER)
        self.inv_tree.column("quantity", width=70, minwidth=50, anchor=tk.CENTER)
        self.inv_tree.column("foil", width=60, minwidth=40, anchor=tk.CENTER)
        self.inv_tree.column("proxy", width=60, minwidth=40, anchor=tk.CENTER)
        self.inv_tree.column("condition", width=80, minwidth=60, anchor=tk.CENTER)
        self.inv_tree.column("language", width=60, minwidth=40, anchor=tk.CENTER)

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

        # Attach Floating Preview Tooltip
        self._preview_hover.attach_to_treeview(self.inv_tree, self._resolve_inv_card_preview)

        # Bottom stats banner
        stats_frame = ttk.Frame(parent, padding=(6, 4))
        stats_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.lbl_stats = ttk.Label(
            stats_frame,
            text="Colección: 0 cartas | Físicas: 0 | Proxies: 0 | Mazos: 0",
            font=("Segoe UI", 9),
            foreground="#444444",
        )
        self.lbl_stats.pack(side=tk.LEFT)

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
        if filter_mode == "Solo Reales":
            filter_proxy = False
        elif filter_mode == "Solo Proxies":
            filter_proxy = True

        cards = self.inventory_service.get_inventory_cards(query=query, filter_proxy=filter_proxy)

        if filter_mode == "Solo Foils":
            cards = [c for c in cards if c.is_foil]

        self.inv_tree.delete(*self.inv_tree.get_children())
        self._inv_item_to_card.clear()

        for card in cards:
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
                    card.set_code.upper(),
                    card.collector_number,
                    card.quantity,
                    foil_str,
                    proxy_str,
                    card.condition,
                    card.language.upper(),
                ),
                tags=tags,
            )
            self._inv_item_to_card[item_id] = card

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

    def _on_delete_card_click(self) -> None:
        selected = self.inv_tree.selection()
        if not selected:
            messagebox.showwarning("Aviso", "Selecciona una carta del inventario para eliminar.", parent=self)
            return

        item_id = selected[0]
        card = self._inv_item_to_card.get(item_id)
        if not card or card.id is None:
            return

        if messagebox.askyesno(
            "Confirmar Eliminación",
            f"¿Seguro que deseas eliminar '{card.name}' ({card.set_code.upper()} #{card.collector_number})?",
            parent=self,
        ):
            self.inventory_service.delete_card(card.id)
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

        btn_load_deck = ttk.Button(url_frame, text="📥 Cargar Mazo", command=self._on_load_deck_click)
        btn_load_deck.pack(side=tk.RIGHT, padx=2)

        # Deck selector & header frame
        sel_frame = ttk.Frame(parent, padding=(4, 4))
        sel_frame.pack(fill=tk.X, padx=6, pady=2)

        ttk.Label(sel_frame, text="Mazo Seleccionado:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(2, 6))
        self.cb_decks = ttk.Combobox(sel_frame, state="readonly", width=35)
        self.cb_decks.pack(side=tk.LEFT, padx=(0, 8))
        self.cb_decks.bind("<<ComboboxSelected>>", self._on_deck_combobox_changed)

        btn_delete_deck = ttk.Button(sel_frame, text="🗑️ Eliminar Mazo", command=self._on_delete_deck_click)
        btn_delete_deck.pack(side=tk.LEFT, padx=4)

        self.lbl_deck_info = ttk.Label(sel_frame, text="", font=("Segoe UI", 9), foreground="#555555")
        self.lbl_deck_info.pack(side=tk.LEFT, padx=(10, 0))

        # Deck breakdown table
        table_frame = ttk.Frame(parent)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        deck_cols = ("name", "set_code", "collector_number", "qty_required", "stock", "status")
        self.deck_tree = ttk.Treeview(table_frame, columns=deck_cols, show="headings", selectmode="browse")

        self.deck_tree.heading("name", text="Carta", anchor=tk.W)
        self.deck_tree.heading("set_code", text="Edición", anchor=tk.CENTER)
        self.deck_tree.heading("collector_number", text="Número", anchor=tk.CENTER)
        self.deck_tree.heading("qty_required", text="Cantidad Requerida", anchor=tk.CENTER)
        self.deck_tree.heading("stock", text="Stock en Colección", anchor=tk.CENTER)
        self.deck_tree.heading("status", text="Estado", anchor=tk.CENTER)

        self.deck_tree.column("name", width=260, minwidth=140, anchor=tk.W)
        self.deck_tree.column("set_code", width=80, minwidth=60, anchor=tk.CENTER)
        self.deck_tree.column("collector_number", width=70, minwidth=50, anchor=tk.CENTER)
        self.deck_tree.column("qty_required", width=130, minwidth=80, anchor=tk.CENTER)
        self.deck_tree.column("stock", width=130, minwidth=80, anchor=tk.CENTER)
        self.deck_tree.column("status", width=150, minwidth=100, anchor=tk.CENTER)

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
            messagebox.showwarning("Aviso", "Introduce la URL de un mazo (Moxfield, Archidekt, Manabox, etc.).", parent=self)
            return

        job = self.task_manager.submit_job(
            "Cargar Mazo desde URL",
            self._task_fetch_and_register_deck,
            url,
        )

        def _on_deck_loaded(ev: JobResultEvent) -> None:
            if ev.status == JobStatus.COMPLETED:
                deck_id = ev.result
                messagebox.showinfo("Mazo Cargado", "El mazo se ha importado y registrado con éxito.", parent=self)
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
            if c.zone == "main"
        ]
        return self.inventory_service.register_deck(deck_rec, deck_cards)

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
        self.lbl_deck_info.configure(text=f"Formato: {deck_rec.format} | Total cartas: {sum(c.quantity_required for c in deck_cards)}")

        # Calculate missing proxy plan (Stock Allocation Option C)
        plan = self.inventory_service.calculate_missing_proxies(
            deck_cards,
            allocate_mode="exclusive",
            current_deck_id=deck.id,
        )

        self.deck_tree.delete(*self.deck_tree.get_children())
        self._deck_item_to_info.clear()

        # Build owned vs missing counts per (oracle_id, name)
        owned_map: dict[str, int] = {}
        for oc in plan.owned_cards:
            key = oc.name.lower()
            owned_map[key] = owned_map.get(key, 0) + oc.quantity_required

        for card in deck_cards:
            needed = card.quantity_required
            c_key = card.name.lower()
            owned_for_card = min(needed, owned_map.get(c_key, 0))
            owned_map[c_key] = max(0, owned_map.get(c_key, 0) - owned_for_card)

            if owned_for_card >= needed:
                status_str = "🟢 Poseída"
                tag = "owned"
            elif owned_for_card == 0:
                status_str = "🔴 Faltante (Proxy)"
                tag = "missing"
            else:
                status_str = f"⚠️ Parcial ({owned_for_card}/{needed})"
                tag = "partial"

            item_id = self.deck_tree.insert(
                "",
                tk.END,
                values=(
                    card.name,
                    card.set_code.upper() if card.set_code else "--",
                    card.collector_number or "--",
                    needed,
                    owned_for_card,
                    status_str,
                ),
                tags=(tag,),
            )
            self._deck_item_to_info[item_id] = CardPreviewInfo(
                name=card.name,
                set_code=card.set_code,
                collector_number=card.collector_number,
            )

        self.lbl_plan_summary.configure(
            text=(
                f"Requeridas: {plan.total_required}  |  "
                f"Poseídas en Stock: {plan.total_owned}  |  "
                f"Faltantes (Proxies a Imprimir): {plan.total_missing}"
            )
        )

    def _clear_deck_details(self) -> None:
        self._current_deck_id = None
        self._current_deck_cards = []
        self.deck_tree.delete(*self.deck_tree.get_children())
        self._deck_item_to_info.clear()
        self.lbl_deck_info.configure(text="")
        self.lbl_plan_summary.configure(text="Selecciona un mazo para ver el desglose de stock y proxies.")

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
            count = self.inventory_service.import_deck_cards_to_inventory(self._current_deck_id, as_proxy=as_proxy)
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
            allocate_mode="exclusive",
            current_deck_id=self._current_deck_id,
        )

        if not plan.missing_cards:
            messagebox.showinfo(
                "Colección Completa",
                "¡Tienes todas las cartas necesarias en tu colección física! No hay proxies faltantes que generar.",
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
                paths_str = "\n".join(str(p) for p in result_paths) if isinstance(result_paths, list) else str(result_paths)
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
        out_dir = output_dir()
        w_dir = work_dir()
        scryfall_dir = w_dir / "scryfall"
        dl_results = download_deck_images(
            cards,
            scryfall_dir,
            cancel_event=cancel_event,
        )
        mtg_back = resources_dir() / "backs" / "mtg" / "back.jpg"
        extra_fronts: list[Path] = []
        extra_backs: list[Path | None] = []
        local_crop_map: dict[Path, bool] = {}

        for card, front_path, back_path in dl_results:
            local_crop_map[front_path] = False
            if back_path:
                local_crop_map[back_path] = False
            for _ in range(card.quantity):
                extra_fronts.append(front_path)
                extra_backs.append(back_path)

        base_name = sanitize_filename(f"{deck.name}_proxies")
        return run_locals_only(
            extra_fronts=extra_fronts,
            local_cardback=mtg_back,
            output_dir=out_dir,
            base_name=base_name,
            work_dir=w_dir,
            cancel_event=cancel_event,
            extra_backs=extra_backs,
            local_crop_map=local_crop_map,
        )

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

        # 2. Update collection table
        self._filter_collection_table()

        # 3. Update registered decks selector
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
