"""Modal dialogs for Card Inventory and Deck Binder operations."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from src.inventory.models import DeckRecord, InventoryCard
from src.inventory.service import InventoryService


def _center_dialog_window(
    dialog: tk.Toplevel,
    parent: tk.Misc,
    min_width: int = 0,
    min_height: int = 0,
) -> None:
    """Center a Toplevel dialog relative to its parent window."""
    dialog.update_idletasks()
    w = max(min_width, dialog.winfo_reqwidth())
    h = max(min_height, dialog.winfo_reqheight())
    try:
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
    except Exception:
        x, y = 200, 200
    dialog.geometry(f"{w}x{h}+{x}+{y}")


class CardDecksUsageDialog(tk.Toplevel):
    """Modal dialog displaying all decks using a specific inventory card."""

    def __init__(
        self,
        parent: tk.Misc,
        card: InventoryCard,
        inventory_service: InventoryService,
        in_decks: int | None = None,
        available: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(f"Uso en Mazos — {card.name}")
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.grab_set()

        self.card = card
        self.inventory_service = inventory_service
        self.in_decks = in_decks
        self.available = available

        self.lbl_empty: ttk.Label | None = None
        self.usage_tree: ttk.Treeview | None = None

        self._build_ui()
        self._center_window(parent)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        # Header Info frame
        header_frame = ttk.Frame(container)
        header_frame.pack(fill=tk.X, pady=(0, 8))

        set_disp = self.card.set_code.upper() if self.card.set_code else "--"
        num_disp = self.card.collector_number if self.card.collector_number else "--"

        title_lbl = ttk.Label(
            header_frame,
            text=f"{self.card.name} ({set_disp} #{num_disp})",
            font=("Segoe UI", 11, "bold"),
        )
        title_lbl.pack(anchor=tk.W, pady=(0, 4))

        # Query deck usages
        usages = self.inventory_service.get_card_deck_usages(
            oracle_id=self.card.oracle_id,
            card_name=self.card.name,
        )

        in_decks_count = (
            self.in_decks
            if self.in_decks is not None
            else sum(u["quantity_required"] for u in usages)
        )
        avail_count = (
            self.available
            if self.available is not None
            else max(0, self.card.quantity - in_decks_count)
        )

        stats_lbl = ttk.Label(
            header_frame,
            text=(
                f"Total Copias: {self.card.quantity}  |  "
                f"Disponibles: {avail_count}  |  "
                f"En Mazos: {in_decks_count}"
            ),
            font=("Segoe UI", 9),
            foreground="#444444",
        )
        stats_lbl.pack(anchor=tk.W)

        ttk.Separator(container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(4, 8))

        if not usages:
            self.lbl_empty = ttk.Label(
                container,
                text="Esta carta no está asignada a ningún mazo registrado actualmente.",
                font=("Segoe UI", 9, "italic"),
                foreground="#666666",
            )
            self.lbl_empty.pack(expand=True, pady=30)
        else:
            table_frame = ttk.Frame(container)
            table_frame.pack(fill=tk.BOTH, expand=True)

            usage_cols = ("deck_name", "format", "quantity", "version", "is_proxy")
            self.usage_tree = ttk.Treeview(
                table_frame,
                columns=usage_cols,
                show="headings",
                selectmode="browse",
            )

            self.usage_tree.heading("deck_name", text="Mazo", anchor=tk.W)
            self.usage_tree.heading("format", text="Formato", anchor=tk.CENTER)
            self.usage_tree.heading("quantity", text="Cantidad", anchor=tk.CENTER)
            self.usage_tree.heading("version", text="Versión", anchor=tk.CENTER)
            self.usage_tree.heading("is_proxy", text="Tipo", anchor=tk.CENTER)

            self.usage_tree.column("deck_name", width=180, minwidth=100, anchor=tk.W)
            self.usage_tree.column("format", width=100, minwidth=70, anchor=tk.CENTER)
            self.usage_tree.column("quantity", width=70, minwidth=50, anchor=tk.CENTER)
            self.usage_tree.column("version", width=110, minwidth=80, anchor=tk.CENTER)
            self.usage_tree.column("is_proxy", width=70, minwidth=50, anchor=tk.CENTER)

            vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.usage_tree.yview)
            self.usage_tree.configure(yscrollcommand=vsb.set)

            self.usage_tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            table_frame.rowconfigure(0, weight=1)
            table_frame.columnconfigure(0, weight=1)

            for u in usages:
                sc = (u.get("set_code") or "").strip().upper()
                cn = (u.get("collector_number") or "").strip()
                if sc and cn:
                    version_str = f"{sc} #{cn}"
                elif sc:
                    version_str = sc
                else:
                    version_str = "--"

                tipo_str = "Proxy" if u.get("is_proxy") else "Física"

                self.usage_tree.insert(
                    "",
                    tk.END,
                    values=(
                        u.get("deck_name", ""),
                        u.get("format", ""),
                        u.get("quantity_required", 1),
                        version_str,
                        tipo_str,
                    ),
                )

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X, pady=(8, 0))

        btn_close = ttk.Button(btn_box, text="Cerrar", command=self.destroy)
        btn_close.pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self.destroy())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _center_window(self, parent: tk.Misc) -> None:
        _center_dialog_window(self, parent, min_width=540, min_height=320)


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
        _center_dialog_window(self, parent)

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
        ttk.Entry(grid, textvariable=self._set_var, width=12).grid(
            row=1, column=1, sticky=tk.W, pady=4
        )

        ttk.Label(grid, text="Número:").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self._num_var, width=12).grid(
            row=2, column=1, sticky=tk.W, pady=4
        )

        ttk.Label(grid, text="Cantidad:").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Spinbox(grid, from_=1, to=999, textvariable=self._qty_var, width=8).grid(
            row=3, column=1, sticky=tk.W, pady=4
        )

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
        ttk.Checkbutton(chk_frame, text="Foil", variable=self._foil_var).pack(
            side=tk.LEFT, padx=(0, 14)
        )
        ttk.Checkbutton(chk_frame, text="Proxy", variable=self._proxy_var).pack(side=tk.LEFT)

        ttk.Separator(container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X)
        ttk.Button(btn_box, text="Cancelar", command=self._on_cancel).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        ttk.Button(btn_box, text="Añadir", command=self._on_accept).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._on_accept())
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _center_window(self, parent: tk.Misc) -> None:
        _center_dialog_window(self, parent)

    def _on_accept(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            messagebox.showwarning(
                "Campo Requerido", "Introduce el nombre de la carta.", parent=self
            )
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
        ttk.Button(btn_box, text="Cancelar", command=self._on_cancel).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        ttk.Button(btn_box, text="Aceptar", command=self._on_accept).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._on_accept())
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _center_window(self, parent: tk.Misc) -> None:
        _center_dialog_window(self, parent)

    def _on_accept(self) -> None:
        self.result = self._choice_var.get() == "proxy"
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class BatchEditCardsDialog(tk.Toplevel):
    """Modal dialog to modify attributes of multiple selected inventory cards."""

    def __init__(self, parent: tk.Misc, count: int) -> None:
        super().__init__(parent)
        self.title(f"Edición en Lote ({count} cartas)")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.grab_set()

        self.count = count
        self.result: dict | None = None

        self._cond_var = tk.StringVar(value="[Sin cambios]")
        self._lang_var = tk.StringVar(value="[Sin cambios]")
        self._foil_var = tk.StringVar(value="[Sin cambios]")
        self._proxy_var = tk.StringVar(value="[Sin cambios]")
        self._qty_mode_var = tk.StringVar(value="[Sin cambios]")
        self._qty_val_var = tk.IntVar(value=1)

        self._build_ui()
        self._center_window(parent)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Label(
            container,
            text=f"Modificar {self.count} cartas seleccionadas:",
            font=("Segoe UI", 10, "bold"),
        )
        header.pack(anchor=tk.W, pady=(0, 10))

        grid = ttk.Frame(container)
        grid.pack(fill=tk.BOTH, expand=True)

        # Condición
        ttk.Label(grid, text="Condición:").grid(row=0, column=0, sticky=tk.W, pady=4)
        cb_cond = ttk.Combobox(
            grid,
            textvariable=self._cond_var,
            values=["[Sin cambios]", "NM", "LP", "MP", "HP", "DMG"],
            state="readonly",
            width=16,
        )
        cb_cond.grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)

        # Idioma
        ttk.Label(grid, text="Idioma:").grid(row=1, column=0, sticky=tk.W, pady=4)
        cb_lang = ttk.Combobox(
            grid,
            textvariable=self._lang_var,
            values=["[Sin cambios]", "en", "es", "ja", "fr", "de", "it", "pt"],
            state="readonly",
            width=16,
        )
        cb_lang.grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)

        # Foil
        ttk.Label(grid, text="Acabado Foil:").grid(row=2, column=0, sticky=tk.W, pady=4)
        cb_foil = ttk.Combobox(
            grid,
            textvariable=self._foil_var,
            values=["[Sin cambios]", "Solo Foil", "Solo Normal (No Foil)"],
            state="readonly",
            width=16,
        )
        cb_foil.grid(row=2, column=1, sticky=tk.W, padx=6, pady=4)

        # Proxy
        ttk.Label(grid, text="Estado Proxy:").grid(row=3, column=0, sticky=tk.W, pady=4)
        cb_proxy = ttk.Combobox(
            grid,
            textvariable=self._proxy_var,
            values=["[Sin cambios]", "Solo Proxies", "Solo Físicas (Reales)"],
            state="readonly",
            width=16,
        )
        cb_proxy.grid(row=3, column=1, sticky=tk.W, padx=6, pady=4)

        # Cantidad
        ttk.Label(grid, text="Ajuste de Cantidad:").grid(row=4, column=0, sticky=tk.W, pady=4)
        cb_qty_mode = ttk.Combobox(
            grid,
            textvariable=self._qty_mode_var,
            values=[
                "[Sin cambios]",
                "Establecer cantidad exacta",
                "Sumar copias (+)",
                "Restar copias (-)",
            ],
            state="readonly",
            width=20,
        )
        cb_qty_mode.grid(row=4, column=1, sticky=tk.W, padx=6, pady=4)

        self.sp_qty = ttk.Spinbox(grid, from_=1, to=999, textvariable=self._qty_val_var, width=6)
        self.sp_qty.grid(row=4, column=2, sticky=tk.W, padx=4, pady=4)

        ttk.Separator(container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X)
        ttk.Button(btn_box, text="Cancelar", command=self._on_cancel).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        ttk.Button(btn_box, text="Aplicar Cambios", command=self._on_accept).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _e: self._on_accept())
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _center_window(self, parent: tk.Misc) -> None:
        _center_dialog_window(self, parent)

    def _on_accept(self) -> None:
        foil_val = self._foil_var.get()
        is_foil: bool | None = None
        if foil_val == "Solo Foil":
            is_foil = True
        elif foil_val == "Solo Normal (No Foil)":
            is_foil = False

        proxy_val = self._proxy_var.get()
        is_proxy: bool | None = None
        if proxy_val == "Solo Proxies":
            is_proxy = True
        elif proxy_val == "Solo Físicas (Reales)":
            is_proxy = False

        cond_val = self._cond_var.get()
        condition = None if cond_val == "[Sin cambios]" else cond_val

        lang_val = self._lang_var.get()
        language = None if lang_val == "[Sin cambios]" else lang_val

        qty_mode = self._qty_mode_var.get()
        set_quantity: int | None = None
        quantity_delta: int | None = None
        if qty_mode == "Establecer cantidad exacta":
            set_quantity = max(1, self._qty_val_var.get())
        elif qty_mode == "Sumar copias (+)":
            quantity_delta = max(1, self._qty_val_var.get())
        elif qty_mode == "Restar copias (-)":
            quantity_delta = -max(1, self._qty_val_var.get())

        if (
            is_foil is None
            and is_proxy is None
            and condition is None
            and language is None
            and set_quantity is None
            and quantity_delta is None
        ):
            messagebox.showinfo(
                "Sin cambios", "No se seleccionó ninguna modificación para aplicar.", parent=self
            )
            return

        self.result = {
            "is_foil": is_foil,
            "is_proxy": is_proxy,
            "condition": condition,
            "language": language,
            "set_quantity": set_quantity,
            "quantity_delta": quantity_delta,
        }
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class BatchAssignDeckDialog(tk.Toplevel):
    """Modal dialog to assign multiple selected inventory cards to a deck."""

    def __init__(
        self,
        parent: tk.Misc,
        cards: list[InventoryCard],
        decks: list[DeckRecord],
    ) -> None:
        super().__init__(parent)
        self.title(f"Asignar {len(cards)} cartas a un Mazo")
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.grab_set()

        self.cards = cards
        self.decks = decks
        self.result: dict | None = None

        self._deck_choice_var = tk.StringVar()
        self._new_name_var = tk.StringVar()
        self._new_format_var = tk.StringVar(value="Commander")
        self._copies_mode_var = tk.StringVar(value="one")

        self._build_ui()
        self._center_window(parent)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Label(
            container,
            text=f"Asignar {len(self.cards)} cartas de tu colección a un mazo:",
            font=("Segoe UI", 10, "bold"),
        )
        header.pack(anchor=tk.W, pady=(0, 8))

        deck_frame = ttk.LabelFrame(container, text="Mazo de Destino", padding=(8, 6))
        deck_frame.pack(fill=tk.X, pady=(0, 8))

        deck_options = [f"{d.name} ({d.format})" for d in self.decks]
        deck_options.append("➕ Crear nuevo mazo...")

        ttk.Label(deck_frame, text="Mazo:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.cb_deck = ttk.Combobox(
            deck_frame,
            textvariable=self._deck_choice_var,
            values=deck_options,
            state="readonly",
            width=32,
        )
        self.cb_deck.grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)
        self.cb_deck.current(0)

        self.cb_deck.bind("<<ComboboxSelected>>", self._on_deck_selection_changed)

        # New deck fields
        self.new_deck_frame = ttk.Frame(deck_frame)
        self.new_deck_frame.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(4, 0))

        ttk.Label(self.new_deck_frame, text="Nombre del nuevo mazo:").grid(
            row=0, column=0, sticky=tk.W, pady=2
        )
        self.ent_new_name = ttk.Entry(
            self.new_deck_frame, textvariable=self._new_name_var, width=28
        )
        self.ent_new_name.grid(row=0, column=1, sticky=tk.W, padx=6, pady=2)

        ttk.Label(self.new_deck_frame, text="Formato:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.cb_new_format = ttk.Combobox(
            self.new_deck_frame,
            textvariable=self._new_format_var,
            values=["Commander", "Modern", "Standard", "Pioneer", "Legacy", "Pauper", "Otro"],
            state="readonly",
            width=16,
        )
        self.cb_new_format.grid(row=1, column=1, sticky=tk.W, padx=6, pady=2)

        self._on_deck_selection_changed()

        # Copies Frame
        copies_frame = ttk.LabelFrame(container, text="Copias a Asignar", padding=(8, 6))
        copies_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Radiobutton(
            copies_frame,
            text="1 copia por carta (Recomendado para Commander)",
            value="one",
            variable=self._copies_mode_var,
        ).pack(anchor=tk.W, pady=2)

        ttk.Radiobutton(
            copies_frame,
            text="Todas las copias disponibles en colección",
            value="available",
            variable=self._copies_mode_var,
        ).pack(anchor=tk.W, pady=2)

        # Cards summary table preview
        cards_frame = ttk.LabelFrame(
            container, text="Resumen de cartas seleccionadas", padding=(6, 4)
        )
        cards_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        tree = ttk.Treeview(
            cards_frame,
            columns=("name", "set", "num", "qty", "proxy"),
            show="headings",
            height=6,
        )
        tree.heading("name", text="Carta", anchor=tk.W)
        tree.heading("set", text="Set", anchor=tk.CENTER)
        tree.heading("num", text="Núm", anchor=tk.CENTER)
        tree.heading("qty", text="Total", anchor=tk.CENTER)
        tree.heading("proxy", text="Tipo", anchor=tk.CENTER)

        tree.column("name", width=180, anchor=tk.W)
        tree.column("set", width=50, anchor=tk.CENTER)
        tree.column("num", width=45, anchor=tk.CENTER)
        tree.column("qty", width=45, anchor=tk.CENTER)
        tree.column("proxy", width=65, anchor=tk.CENTER)

        sb = ttk.Scrollbar(cards_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        for c in self.cards:
            tree.insert(
                "",
                tk.END,
                values=(
                    c.name,
                    c.set_code.upper() if c.set_code else "--",
                    c.collector_number or "--",
                    c.quantity,
                    "Proxy" if c.is_proxy else "Real",
                ),
            )

        btn_box = ttk.Frame(container)
        btn_box.pack(fill=tk.X)
        ttk.Button(btn_box, text="Cancelar", command=self._on_cancel).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        ttk.Button(btn_box, text="Asignar al Mazo", command=self._on_accept).pack(side=tk.RIGHT)

        self.bind("<Escape>", lambda _e: self._on_cancel())

    def _on_deck_selection_changed(self, _e=None) -> None:
        sel = self._deck_choice_var.get()
        if sel == "➕ Crear nuevo mazo...":
            for child in self.new_deck_frame.winfo_children():
                child.configure(state=tk.NORMAL)
        else:
            for child in self.new_deck_frame.winfo_children():
                child.configure(state=tk.DISABLED)

    def _center_window(self, parent: tk.Misc) -> None:
        _center_dialog_window(self, parent, min_width=480, min_height=420)

    def _on_accept(self) -> None:
        sel = self._deck_choice_var.get()
        deck_id: int | None = None
        new_name: str | None = None
        new_format: str | None = None

        if sel == "➕ Crear nuevo mazo...":
            new_name = self._new_name_var.get().strip()
            if not new_name:
                messagebox.showwarning(
                    "Campo Requerido", "Introduce el nombre del nuevo mazo.", parent=self
                )
                return
            new_format = self._new_format_var.get().strip() or "Commander"
        else:
            idx = self.cb_deck.current()
            if idx < 0 or idx >= len(self.decks):
                messagebox.showwarning("Aviso", "Selecciona un mazo de la lista.", parent=self)
                return
            deck_id = self.decks[idx].id

        self.result = {
            "deck_id": deck_id,
            "new_name": new_name,
            "new_format": new_format,
            "copies_mode": self._copies_mode_var.get(),
        }
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()
