"""ValidatorView — Reusable technical deck and Scryfall validator view frame."""

from __future__ import annotations

import csv
import json
import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageTk

from gui.components.card_preview_hover import CardHoverPreview, CardPreviewInfo
from gui.paths import work_dir
from gui.widgets import attach_context_menu
from src.deck_importer import DeckCard, DeckImportError, fetch_deck
from src.scryfall import _fetch_raw_card_data, fetch_card_by_name

if TYPE_CHECKING:
    from src.engine.task_manager import TaskManager

_log = logging.getLogger(__name__)


def create_placeholder_image(text: str, width: int = 146, height: int = 204) -> ImageTk.PhotoImage:
    """Create a default placeholder image with custom text."""
    img = Image.new("RGBA", (width, height), "#f0f0f0")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline="#d0d0d0", width=1)
    draw.line([0, 0, width - 1, height - 1], fill="#e0e0e0", width=1)
    draw.line([0, height - 1, width - 1, 0], fill="#e0e0e0", width=1)
    draw.text((width // 2, height // 2), text, fill="#777777", anchor="mm")
    return ImageTk.PhotoImage(img)


@dataclass
class CardValidationResult:
    """Represents technical validation status for a single card."""

    card: DeckCard
    status: str  # 🟢 Válido | ⚠️ Advertencia | 🔴 Error
    details: str
    set_code: str
    collector_number: str
    front_url: str | None
    back_url: str | None
    type_line: str
    rarity: str


class ValidatorView(ttk.Frame):
    """Embeddable view for validating deck URLs, card editions, and Scryfall image resolutions."""

    def __init__(
        self,
        master: tk.Misc,
        task_manager: TaskManager | None = None,
        on_navigate: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.master = master
        self.root = self.winfo_toplevel()
        self.task_manager = task_manager
        self.on_navigate = on_navigate

        self.work_directory = work_dir()
        self.work_directory.mkdir(parents=True, exist_ok=True)

        self.empty_preview = create_placeholder_image("Sin Selección")
        self.loading_preview = create_placeholder_image("Cargando...")
        self.error_preview = create_placeholder_image("Sin Imagen")

        self.image_refs: dict[str, ImageTk.PhotoImage] = {}
        self.current_deck_cards: list[CardValidationResult] = []
        self.event_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._setup_style()

        # Attach interactive hover card preview tooltip
        self.hover_preview = CardHoverPreview(self)
        self.hover_preview.attach_to_treeview(self.tree, self._resolve_tree_card)

        self.after(100, self._process_queue)

    def _setup_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)

    def _build_ui(self) -> None:
        main_pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # --- LEFT PANEL ---
        left_frame = ttk.Frame(main_pane)
        main_pane.add(left_frame, weight=3)

        # URL Input Row
        input_frame = ttk.LabelFrame(left_frame, text=" Cargar Mazo ", padding=8)
        input_frame.pack(fill=tk.X, pady=(0, 8))

        url_lbl = ttk.Label(input_frame, text="URL del mazo (Moxfield, etc.):")
        url_lbl.pack(side=tk.LEFT, padx=(0, 6))

        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(input_frame, textvariable=self.url_var)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        attach_context_menu(self.url_entry)

        self.load_btn = ttk.Button(
            input_frame, text="Cargar y Validar", command=self._on_load_clicked
        )
        self.load_btn.pack(side=tk.LEFT)
        self.url_entry.bind("<Return>", lambda e: self._on_load_clicked())

        # Status and Progress Bar Row
        self.status_var = tk.StringVar(value="Listo.")
        status_row = ttk.Frame(left_frame)
        status_row.pack(fill=tk.X, pady=(0, 6))

        status_lbl = ttk.Label(
            status_row, textvariable=self.status_var, font=("Segoe UI", 9, "bold")
        )
        status_lbl.pack(side=tk.LEFT)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            status_row, variable=self.progress_var, maximum=100, length=200
        )
        self.progress_bar.pack(side=tk.RIGHT, padx=5)
        self.progress_bar.pack_forget()

        # Notebook for Left Frame (Tabs: Validation, JSON)
        self.notebook = ttk.Notebook(left_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Validation Frame
        self.validation_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.validation_tab, text="Validación")

        # Table Frame (Treeview + Scrollbars)
        table_frame = ttk.Frame(self.validation_tab)
        table_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("name", "qty", "zone", "set", "number", "type", "rarity", "status", "details")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.heading("name", text="Nombre")
        self.tree.heading("qty", text="Cant.")
        self.tree.heading("zone", text="Zona")
        self.tree.heading("set", text="Edición")
        self.tree.heading("number", text="Nº")
        self.tree.heading("type", text="Tipo")
        self.tree.heading("rarity", text="Rareza")
        self.tree.heading("status", text="Estado")
        self.tree.heading("details", text="Detalles de Validación")

        self.tree.column("name", width=180, minwidth=120)
        self.tree.column("qty", width=45, anchor=tk.CENTER, minwidth=35)
        self.tree.column("zone", width=80, anchor=tk.CENTER, minwidth=60)
        self.tree.column("set", width=60, anchor=tk.CENTER, minwidth=50)
        self.tree.column("number", width=50, anchor=tk.CENTER, minwidth=40)
        self.tree.column("type", width=140, minwidth=100)
        self.tree.column("rarity", width=80, anchor=tk.CENTER, minwidth=65)
        self.tree.column("status", width=95, anchor=tk.CENTER, minwidth=85)
        self.tree.column("details", width=220, minwidth=150)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_row_selected)

        # Bottom Summary Row
        summary_frame = ttk.Frame(self.validation_tab, padding=4)
        summary_frame.pack(fill=tk.X, pady=(6, 0))

        self.summary_var = tk.StringVar(
            value="Mazo: Sin cargar | Total: 0 | Válidas: 0 | Advertencias: 0 | Errores: 0"
        )
        summary_lbl = ttk.Label(
            summary_frame, textvariable=self.summary_var, font=("Segoe UI", 9)
        )
        summary_lbl.pack(side=tk.LEFT)

        self.export_btn = ttk.Button(
            summary_frame, text="Exportar CSV", command=self._on_export_csv_click
        )
        self.export_btn.pack(side=tk.RIGHT, padx=4)

        # Tab 2: Dynamic JSON Frame (not added to Notebook by default)
        self.json_tab = ttk.Frame(self.notebook)

        self.json_text = tk.Text(self.json_tab, wrap=tk.NONE, font=("Consolas", 9), bg="#fafafa")
        json_vsb = ttk.Scrollbar(self.json_tab, orient=tk.VERTICAL, command=self.json_text.yview)
        json_hsb = ttk.Scrollbar(self.json_tab, orient=tk.HORIZONTAL, command=self.json_text.xview)
        self.json_text.configure(yscrollcommand=json_vsb.set, xscrollcommand=json_hsb.set)

        self.json_text.grid(row=0, column=0, sticky="nsew")
        json_vsb.grid(row=0, column=1, sticky="ns")
        json_hsb.grid(row=1, column=0, sticky="ew")

        self.json_tab.columnconfigure(0, weight=1)
        self.json_tab.rowconfigure(0, weight=1)

        attach_context_menu(self.json_text)

        # --- RIGHT PANEL (Preview Sidebar) ---
        right_frame = ttk.LabelFrame(main_pane, text=" Previsualización de Carta ", padding=8)
        main_pane.add(right_frame, weight=1)

        self.preview_title = ttk.Label(
            right_frame,
            text="Selecciona una carta",
            font=("Segoe UI", 10, "bold"),
            anchor=tk.CENTER,
        )
        self.preview_title.pack(fill=tk.X, pady=(0, 10))

        self.images_frame = ttk.Frame(right_frame)
        self.images_frame.pack(fill=tk.BOTH, expand=True)

        self.front_lbl = ttk.Label(self.images_frame, image=self.empty_preview)
        self.front_lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

        self.back_lbl = ttk.Label(self.images_frame, image=self.empty_preview)
        self.back_lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
        self.back_lbl.pack_forget()

    def _resolve_tree_card(self, item_id: str) -> CardPreviewInfo | None:
        """Resolve CardPreviewInfo for CardHoverPreview from treeview row item id."""
        try:
            children = self.tree.get_children()
            if item_id in children:
                row_idx = children.index(item_id)
                if 0 <= row_idx < len(self.current_deck_cards):
                    res = self.current_deck_cards[row_idx]
                    return CardPreviewInfo(
                        name=res.card.name,
                        set_code=res.set_code,
                        collector_number=res.collector_number,
                        front_url=res.front_url,
                        back_url=res.back_url,
                    )
        except Exception:
            pass
        return None

    def _on_load_clicked(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Advertencia", "Por favor ingresa una URL válida.")
            return

        self.load_btn.state(["disabled"])
        self.url_entry.state(["disabled"])
        self.status_var.set("Accediendo a Moxfield...")
        self.progress_bar.pack(side=tk.RIGHT, padx=5)
        self.progress_var.set(0)

        try:
            self.notebook.forget(self.json_tab)
        except tk.TclError:
            pass

        for item in self.tree.get_children():
            self.tree.delete(item)
        self.current_deck_cards.clear()
        self.image_refs.clear()
        self.front_lbl.configure(image=self.empty_preview)
        self.back_lbl.pack_forget()
        self.preview_title.configure(text="Cargando mazo...")

        if self.task_manager is not None:
            self.task_manager.submit_job("Validate Deck", self._async_load_and_validate, url)
        else:
            threading.Thread(target=self._async_load_and_validate, args=(url,), daemon=True).start()

    def _async_load_and_validate(self, url: str) -> None:
        try:
            deck = fetch_deck(url)
            self.event_queue.put(("deck_loaded", (deck.name, deck.cards, deck.raw_json)))

            total_cards = len(deck.cards)

            def _validate_card_job(card: DeckCard, idx: int):
                res = self._validate_single_card(card)
                self.event_queue.put(("card_validated", (res, idx, total_cards)))

            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=5) as executor:
                for idx, card in enumerate(deck.cards):
                    executor.submit(_validate_card_job, card, idx)

            self.event_queue.put(("validation_complete", None))

        except DeckImportError as exc:
            self.event_queue.put(("load_error", f"Error al importar el mazo: {exc}"))
        except Exception as exc:
            self.event_queue.put(("load_error", f"Error inesperado: {exc}"))

    def _validate_single_card(self, card: DeckCard) -> CardValidationResult:
        set_code = card.set_code
        collector_number = card.collector_number
        resolved_by_name = False

        if not set_code or not collector_number:
            try:
                set_code, collector_number = fetch_card_by_name(card.name)
                resolved_by_name = True
            except Exception as e:
                return CardValidationResult(
                    card=card,
                    status="🔴 Error",
                    details=f"Falta Edición/Nº y no se encontró por nombre: {e}",
                    set_code="",
                    collector_number="",
                    front_url=None,
                    back_url=None,
                    type_line=card.type_line,
                    rarity="unknown",
                )

        try:
            data = _fetch_raw_card_data(set_code, collector_number)

            front_url = None
            back_url = None

            if "image_uris" in data:
                front_url = data["image_uris"].get("small")
            elif "card_faces" in data:
                faces = data["card_faces"]
                if len(faces) > 0:
                    front_url = faces[0].get("image_uris", {}).get("small")
                if len(faces) > 1:
                    back_url = faces[1].get("image_uris", {}).get("small")

            if not front_url:
                return CardValidationResult(
                    card=card,
                    status="🔴 Error",
                    details="No se encontraron imágenes en Scryfall",
                    set_code=set_code,
                    collector_number=collector_number,
                    front_url=None,
                    back_url=None,
                    type_line=data.get("type_line", card.type_line),
                    rarity=data.get("rarity", "unknown"),
                )

            highres = data.get("highres_image", True)
            digital = data.get("digital", False)
            type_line = data.get("type_line", card.type_line)
            rarity = data.get("rarity", "unknown")
            layout = data.get("layout", "")

            status = "🟢 Válido"
            details_list = []
            if card.zone == "token" and layout not in ("token", "emblem", "double_faced_token"):
                status = "⚠️ Advertencia"
                details_list.append(
                    f"Se omitirá al descargar (layout '{layout}' no es token/emblem/double_faced_token)"
                )
            if resolved_by_name:
                status = "⚠️ Advertencia"
                details_list.append("Edición/Nº faltante (resuelto por nombre)")
            if not highres:
                status = "⚠️ Advertencia"
                details_list.append("Imagen de baja resolución en Scryfall")
            if digital:
                status = "⚠️ Advertencia"
                details_list.append("Carta digital")
            if back_url:
                details_list.append("Doble Cara (MDFC)")

            details = ", ".join(details_list) if details_list else "Todo correcto"

            return CardValidationResult(
                card=card,
                status=status,
                details=details,
                set_code=set_code,
                collector_number=collector_number,
                front_url=front_url,
                back_url=back_url,
                type_line=type_line,
                rarity=rarity,
            )

        except Exception as e:
            return CardValidationResult(
                card=card,
                status="🔴 Error",
                details=f"Error en Scryfall ({set_code}/{collector_number}): {e}",
                set_code=set_code,
                collector_number=collector_number,
                front_url=None,
                back_url=None,
                type_line=card.type_line,
                rarity="unknown",
            )

    def _process_queue(self) -> None:
        try:
            while True:
                msg_type, data = self.event_queue.get_nowait()

                if msg_type == "deck_loaded":
                    deck_name, cards, raw_json = data
                    self.status_var.set(f"Mazo cargado: {deck_name}. Validando cartas...")
                    self.preview_title.configure(text=deck_name)
                    for _ in cards:
                        self.tree.insert(
                            "",
                            tk.END,
                            values=("...", "...", "...", "...", "...", "...", "...", "...", "..."),
                        )

                    if raw_json is not None:
                        try:
                            formatted_json = json.dumps(raw_json, indent=2, ensure_ascii=False)
                            self.json_text.configure(state=tk.NORMAL)
                            self.json_text.delete("1.0", tk.END)
                            self.json_text.insert(tk.END, formatted_json)
                            self.json_text.configure(state=tk.DISABLED)
                            self.notebook.add(self.json_tab, text="JSON Moxfield")
                        except Exception as e:
                            _log.error("Error displaying raw JSON: %s", e)

                elif msg_type == "card_validated":
                    res, idx, total = data
                    self.current_deck_cards.append(res)
                    item_id = self.tree.get_children()[idx]

                    zone_map = {"main": "Principal", "side": "Sideboard", "token": "Token"}
                    zone_disp = zone_map.get(res.card.zone, res.card.zone)

                    self.tree.item(
                        item_id,
                        values=(
                            res.card.name,
                            res.card.quantity,
                            zone_disp,
                            res.set_code.upper(),
                            res.collector_number,
                            res.type_line,
                            res.rarity.capitalize(),
                            res.status,
                            res.details,
                        ),
                    )

                    pct = ((idx + 1) / total) * 100
                    self.progress_var.set(pct)
                    self.status_var.set(f"Validando cartas: {idx + 1}/{total}...")
                    self._update_summary()

                elif msg_type == "validation_complete":
                    self.status_var.set("Validación técnica finalizada.")
                    self.progress_bar.pack_forget()
                    self.load_btn.state(["!disabled"])
                    self.url_entry.state(["!disabled"])

                elif msg_type == "load_error":
                    self.status_var.set("Error al cargar.")
                    self.progress_bar.pack_forget()
                    self.load_btn.state(["!disabled"])
                    self.url_entry.state(["!disabled"])
                    self.preview_title.configure(text="Error de Carga")
                    messagebox.showerror("Error", data)

        except queue.Empty:
            pass
        finally:
            if self.winfo_exists():
                self.after(100, self._process_queue)

    def _update_summary(self) -> None:
        total = len(self.current_deck_cards)
        valid = sum(1 for c in self.current_deck_cards if c.status == "🟢 Válido")
        warning = sum(1 for c in self.current_deck_cards if c.status == "⚠️ Advertencia")
        error = sum(1 for c in self.current_deck_cards if c.status == "🔴 Error")

        deck_name = self.preview_title.cget("text")
        self.summary_var.set(
            f"Mazo: {deck_name} | Total: {total} | Válidas: {valid} | Advertencias: {warning} | Errores: {error}"
        )

    def _on_export_csv_click(self) -> None:
        if not self.current_deck_cards:
            messagebox.showwarning("Aviso", "No hay cartas validadas para exportar.", parent=self)
            return

        file_path = filedialog.asksaveasfilename(
            parent=self,
            title="Exportar resultados de validación a CSV",
            defaultextension=".csv",
            filetypes=[("Archivos CSV", "*.csv"), ("Todos los archivos", "*.*")],
        )
        if not file_path:
            return

        self.export_to_csv(file_path)

    def export_to_csv(self, file_path: str | Path) -> None:
        if not self.current_deck_cards:
            return

        path = Path(file_path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["Nombre", "Cantidad", "Zona", "Edición", "Nº", "Tipo", "Rareza", "Estado", "Detalles"]
            )
            zone_map = {"main": "Principal", "side": "Sideboard", "token": "Token"}
            for res in self.current_deck_cards:
                zone_disp = zone_map.get(res.card.zone, res.card.zone)
                writer.writerow(
                    [
                        res.card.name,
                        res.card.quantity,
                        zone_disp,
                        res.set_code.upper() if res.set_code else "",
                        res.collector_number,
                        res.type_line,
                        res.rarity.capitalize() if res.rarity else "",
                        res.status,
                        res.details,
                    ]
                )

    def _on_row_selected(self, event) -> None:
        selected = self.tree.selection()
        if not selected:
            return

        row_idx = self.tree.index(selected[0])
        if row_idx >= len(self.current_deck_cards):
            return

        res = self.current_deck_cards[row_idx]
        self.preview_title.configure(text=res.card.name)

        if not res.front_url:
            self.front_lbl.configure(image=self.error_preview)
            self.back_lbl.pack_forget()
            return

        self.front_lbl.configure(image=self.loading_preview)
        self._load_async_preview(
            res.front_url, res.set_code, res.collector_number, "front", self.front_lbl
        )

        if res.back_url:
            self.back_lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
            self.back_lbl.configure(image=self.loading_preview)
            self._load_async_preview(
                res.back_url, res.set_code, res.collector_number, "back", self.back_lbl
            )
        else:
            self.back_lbl.pack_forget()

    def _load_async_preview(
        self, url: str, set_code: str, cn: str, suffix: str, label_widget: ttk.Label
    ) -> None:
        previews_dir = self.work_directory / "previews"
        previews_dir.mkdir(parents=True, exist_ok=True)

        filename = f"preview_{set_code.lower()}_{cn.lower()}_{suffix}.jpg"
        local_path = previews_dir / filename

        if local_path.exists():
            try:
                img = Image.open(local_path).convert("RGBA")
                photo = ImageTk.PhotoImage(img)
                label_widget.configure(image=photo)
                self.image_refs[f"{set_code}_{cn}_{suffix}"] = photo
                return
            except Exception:
                pass

        def _download():
            try:
                import requests

                headers = {"User-Agent": "MPCFillToPDF/2.0"}
                resp = requests.get(url, headers=headers, timeout=12)
                resp.raise_for_status()
                local_path.write_bytes(resp.content)

                def _update():
                    try:
                        if label_widget.winfo_exists():
                            img = Image.open(local_path).convert("RGBA")
                            photo = ImageTk.PhotoImage(img)
                            label_widget.configure(image=photo)
                            self.image_refs[f"{set_code}_{cn}_{suffix}"] = photo
                    except Exception:
                        pass

                self.after(0, _update)
            except Exception as e:
                _log.error("Error downloading preview image: %s", e)

                def _set_error():
                    try:
                        if label_widget.winfo_exists():
                            label_widget.configure(image=self.error_preview)
                    except Exception:
                        pass

                self.after(0, _set_error)

        threading.Thread(target=_download, daemon=True).start()
