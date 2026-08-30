"""Interactive floating card preview tooltip for Treeview and general Tkinter widgets."""

from __future__ import annotations

import logging
import re
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from gui.paths import work_dir

_log = logging.getLogger(__name__)

_PREVIEW_W = 146
_PREVIEW_H = 204
_DEBOUNCE_MS = 250
_SCRYFALL_HEADERS = {"User-Agent": "MPCFillToPDF/2.0", "Accept": "application/json"}


def sanitize_filename(name: str) -> str:
    """Sanitize card name or identifier for safe filesystem cache storage."""
    cleaned = re.sub(r'[^\w\-.]+', "_", name.strip())
    return re.sub(r"_+", "_", cleaned).strip("_")


def get_preview_cache_dir() -> Path:
    """Return the previews directory under MPCFillToPDF/procesamiento/previews."""
    p_dir = work_dir() / "previews"
    p_dir.mkdir(parents=True, exist_ok=True)
    return p_dir


def resolve_preview_cache_path(
    set_code: str | None = None,
    collector_number: str | None = None,
    name: str | None = None,
    scryfall_id: str | None = None,
    suffix: str = "front",
) -> Path:
    """Resolve standard disk cache path for a card preview image."""
    p_dir = get_preview_cache_dir()
    if scryfall_id:
        clean_id = sanitize_filename(scryfall_id.lower())
        return p_dir / f"preview_id_{clean_id}_{suffix}.jpg"
    if set_code and collector_number:
        s_clean = sanitize_filename(set_code.lower())
        c_clean = sanitize_filename(str(collector_number).lower())
        return p_dir / f"preview_{s_clean}_{c_clean}_{suffix}.jpg"
    if name:
        n_clean = sanitize_filename(name.lower())
        return p_dir / f"preview_name_{n_clean}_{suffix}.jpg"
    return p_dir / f"preview_unknown_{suffix}.jpg"


def create_placeholder_image(text: str, width: int = _PREVIEW_W, height: int = _PREVIEW_H) -> ImageTk.PhotoImage:
    """Create a default placeholder PhotoImage with text."""
    img = Image.new("RGBA", (width, height), "#2b2b2b")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline="#444444", width=1)
    draw.line([0, 0, width - 1, height - 1], fill="#383838", width=1)
    draw.line([0, height - 1, width - 1, 0], fill="#383838", width=1)
    draw.text((width // 2, height // 2), text, fill="#cccccc", anchor="mm")
    return ImageTk.PhotoImage(img)


@dataclass
class CardPreviewInfo:
    """Container for card metadata needed to resolve or render preview images."""

    name: str = ""
    set_code: str = ""
    collector_number: str = ""
    scryfall_id: str = ""
    front_url: str | None = None
    back_url: str | None = None
    lang: str = "en"

    @classmethod
    def from_any(cls, obj: CardPreviewInfo | dict | tuple | str | None) -> CardPreviewInfo | None:
        """Coerce various input representations into CardPreviewInfo."""
        if obj is None:
            return None
        if isinstance(obj, CardPreviewInfo):
            return obj
        if isinstance(obj, dict):
            return cls(
                name=str(obj.get("name", "")),
                set_code=str(obj.get("set_code", obj.get("set", ""))),
                collector_number=str(obj.get("collector_number", obj.get("number", ""))),
                scryfall_id=str(obj.get("scryfall_id", obj.get("id", ""))),
                front_url=obj.get("front_url") or obj.get("image_url"),
                back_url=obj.get("back_url"),
                lang=str(obj.get("lang", "en")),
            )
        if isinstance(obj, tuple):
            if len(obj) == 2:
                return cls(name=str(obj[0]), set_code=str(obj[1]))
            if len(obj) >= 3:
                return cls(name=str(obj[0]), set_code=str(obj[1]), collector_number=str(obj[2]))
        if isinstance(obj, str):
            return cls(name=obj)
        # Check for duck typing (e.g. DeckCard or InventoryCard)
        name = getattr(obj, "name", "")
        set_code = getattr(obj, "set_code", "")
        collector_number = getattr(obj, "collector_number", "")
        scryfall_id = getattr(obj, "scryfall_id", "")
        front_url = getattr(obj, "front_url", None)
        back_url = getattr(obj, "back_url", None)
        lang = getattr(obj, "lang", "en")
        if name or set_code or scryfall_id:
            return cls(
                name=name,
                set_code=set_code,
                collector_number=str(collector_number),
                scryfall_id=scryfall_id,
                front_url=front_url,
                back_url=back_url,
                lang=lang,
            )
        return None


class CardHoverPreview:
    """Floating tooltip / popup showing cached or asynchronously fetched Scryfall card images on hover."""

    def __init__(self, root: tk.Misc, debounce_ms: int = _DEBOUNCE_MS) -> None:
        self.root = root
        self._debounce_ms = debounce_ms

        self._tip: tk.Toplevel | None = None
        self._front_label: tk.Label | None = None
        self._back_label: tk.Label | None = None
        self._header_label: tk.Label | None = None

        self._current_info: CardPreviewInfo | None = None
        self._current_key: str | None = None
        self._active_request_id: int = 0
        self._after_id: str | None = None

        # In-memory photo cache & placeholders
        self._photo_cache: dict[str, ImageTk.PhotoImage] = {}
        self._empty_photo: ImageTk.PhotoImage | None = None
        self._loading_photo: ImageTk.PhotoImage | None = None
        self._error_photo: ImageTk.PhotoImage | None = None

        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="CardPreviewHover")

    def _get_loading_photo(self) -> ImageTk.PhotoImage:
        if self._loading_photo is None:
            self._loading_photo = create_placeholder_image("Cargando...")
        return self._loading_photo

    def _get_error_photo(self) -> ImageTk.PhotoImage:
        if self._error_photo is None:
            self._error_photo = create_placeholder_image("Sin Vista Previa")
        return self._error_photo

    def _make_key(self, info: CardPreviewInfo) -> str:
        return f"{info.scryfall_id}|{info.set_code}|{info.collector_number}|{info.name}|{info.lang}"

    def attach_to_treeview(
        self,
        tree: ttk.Treeview,
        card_resolver: Callable[[str], CardPreviewInfo | dict | tuple | str | None],
    ) -> None:
        """Attach hover preview listener to a ttk.Treeview."""
        last_item: list[str | None] = [None]

        def _on_motion(event: tk.Event) -> None:
            region = tree.identify_region(event.x, event.y)
            if region != "cell" and region != "tree":
                last_item[0] = None
                self.hide()
                return

            item_id = tree.identify_row(event.y)
            if not item_id:
                last_item[0] = None
                self.hide()
                return

            if item_id == last_item[0]:
                self._move(event.x_root, event.y_root)
                return

            last_item[0] = item_id
            resolved = card_resolver(item_id)
            info = CardPreviewInfo.from_any(resolved)
            if info:
                self.schedule_show(info, event.x_root, event.y_root)
            else:
                self.hide()

        def _on_leave(_event: tk.Event) -> None:
            last_item[0] = None
            self.hide()

        tree.bind("<Motion>", _on_motion, add="+")
        tree.bind("<Leave>", _on_leave, add="+")
        tree.bind("<Unmap>", _on_leave, add="+")
        tree.bind("<ButtonPress>", _on_leave, add="+")
        tree.bind("<Destroy>", lambda _e: self.hide(), add="+")

    def attach_to_widget(
        self,
        widget: tk.Widget,
        card_info_or_resolver: CardPreviewInfo | dict | str | Callable[[tk.Event], CardPreviewInfo | dict | str | None],
    ) -> None:
        """Attach hover preview listener to a standard Tkinter widget."""
        def _get_info(event: tk.Event) -> CardPreviewInfo | None:
            if callable(card_info_or_resolver):
                raw = card_info_or_resolver(event)
            else:
                raw = card_info_or_resolver
            return CardPreviewInfo.from_any(raw)

        def _on_enter(event: tk.Event) -> None:
            info = _get_info(event)
            if info:
                self.schedule_show(info, event.x_root, event.y_root)

        def _on_motion(event: tk.Event) -> None:
            if self._tip and self._tip.winfo_exists():
                self._move(event.x_root, event.y_root)

        def _on_leave(_event: tk.Event) -> None:
            self.hide()

        widget.bind("<Enter>", _on_enter, add="+")
        widget.bind("<Motion>", _on_motion, add="+")
        widget.bind("<Leave>", _on_leave, add="+")
        widget.bind("<Unmap>", _on_leave, add="+")
        widget.bind("<ButtonPress>", _on_leave, add="+")
        widget.bind("<Destroy>", lambda _e: self.hide(), add="+")

    def schedule_show(self, info: CardPreviewInfo, x_root: int, y_root: int) -> None:
        """Schedule preview popup with debouncing."""
        self._cancel_scheduled()
        self._active_request_id += 1
        req_id = self._active_request_id

        def _do_show() -> None:
            if req_id == self._active_request_id:
                self.show_preview(info, x_root, y_root, req_id=req_id)

        try:
            self._after_id = self.root.after(self._debounce_ms, _do_show)
        except Exception:
            pass

    def _cancel_scheduled(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def hide(self, _event=None) -> None:
        """Hide and destroy current preview tooltip."""
        self._cancel_scheduled()
        self._active_request_id += 1
        self._current_info = None
        self._current_key = None

        if self._tip is not None:
            try:
                if self._tip.winfo_exists():
                    self._tip.destroy()
            except Exception:
                pass
            self._tip = None
            self._front_label = None
            self._back_label = None
            self._header_label = None

    def show_preview(self, info: CardPreviewInfo, x_root: int, y_root: int, req_id: int | None = None) -> None:
        """Display the preview tooltip immediately and initiate image loading."""
        if req_id is None:
            self._cancel_scheduled()
            self._active_request_id += 1
            req_id = self._active_request_id

        self._current_info = info
        key = self._make_key(info)
        self._current_key = key

        self._ensure_tooltip_window()
        if not (self._tip and self._tip.winfo_exists()):
            return

        # Update header title if available
        header_text = info.name
        if info.set_code and info.collector_number:
            header_text += f" ({info.set_code.upper()} #{info.collector_number})"
        if self._header_label:
            self._header_label.configure(text=header_text)

        # Load front image (from memory cache, disk cache, or download)
        self._load_face_image(info, "front", self._front_label, req_id)

        # Handle back face if MDFC/Transform
        if info.back_url or getattr(info, "back_url", None):
            if self._back_label:
                self._back_label.pack(side=tk.LEFT, padx=(4, 0))
                self._load_face_image(info, "back", self._back_label, req_id)
        elif self._back_label:
            self._back_label.pack_forget()

        self._tip.update_idletasks()
        self._move(x_root, y_root)

    def _ensure_tooltip_window(self) -> None:
        if self._tip and self._tip.winfo_exists():
            return

        try:
            parent_win = self.root.winfo_toplevel()
        except Exception:
            parent_win = self.root

        self._tip = tk.Toplevel(parent_win)
        self._tip.overrideredirect(True)
        try:
            self._tip.attributes("-topmost", True)
        except Exception:
            pass

        # Outer border frame
        border = tk.Frame(self._tip, bg="#1e1e1e", padx=3, pady=3, relief="ridge", bd=1)
        border.pack()

        # Card name header
        self._header_label = tk.Label(
            border,
            text="",
            bg="#1e1e1e",
            fg="#f0f0f0",
            font=("Segoe UI", 8, "bold"),
            anchor="w",
            wraplength=300,
        )
        self._header_label.pack(fill=tk.X, padx=2, pady=(1, 4))

        # Images container
        img_container = tk.Frame(border, bg="#1e1e1e")
        img_container.pack()

        self._front_label = tk.Label(img_container, image=self._get_loading_photo(), bg="#1e1e1e")
        self._front_label.pack(side=tk.LEFT)

        self._back_label = tk.Label(img_container, image=self._get_loading_photo(), bg="#1e1e1e")
        # back_label is packed on-demand if card has back face

    def _load_face_image(
        self,
        info: CardPreviewInfo,
        suffix: str,
        target_label: tk.Label | None,
        req_id: int,
    ) -> None:
        if target_label is None:
            return

        cache_path = resolve_preview_cache_path(
            set_code=info.set_code,
            collector_number=info.collector_number,
            name=info.name,
            scryfall_id=info.scryfall_id,
            suffix=suffix,
        )
        mem_key = f"{cache_path.stem}_{suffix}"

        # 1. Check in-memory cache
        if mem_key in self._photo_cache:
            target_label.configure(image=self._photo_cache[mem_key])
            return

        # 2. Check disk cache
        if cache_path.exists():
            try:
                img = Image.open(cache_path).convert("RGBA")
                if img.width != _PREVIEW_W or img.height != _PREVIEW_H:
                    img.thumbnail((_PREVIEW_W, _PREVIEW_H), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self._photo_cache[mem_key] = photo
                target_label.configure(image=photo)
                return
            except Exception as e:
                _log.debug("Failed reading cached preview image %s: %s", cache_path, e)

        # 3. Not cached: show loading placeholder and download asynchronously
        target_label.configure(image=self._get_loading_photo())
        self._executor.submit(self._async_fetch_and_cache, info, suffix, cache_path, mem_key, req_id)

    def _async_fetch_and_cache(
        self,
        info: CardPreviewInfo,
        suffix: str,
        cache_path: Path,
        mem_key: str,
        req_id: int,
    ) -> None:
        try:
            download_url = self._resolve_download_url(info, suffix)
            if not download_url:
                raise ValueError("Could not resolve Scryfall download URL")

            import requests

            resp = requests.get(download_url, headers=_SCRYFALL_HEADERS, timeout=12)
            resp.raise_for_status()
            cache_path.write_bytes(resp.content)

            img = Image.open(cache_path).convert("RGBA")
            if img.width != _PREVIEW_W or img.height != _PREVIEW_H:
                img.thumbnail((_PREVIEW_W, _PREVIEW_H), Image.LANCZOS)

            def _apply_success() -> None:
                if req_id != self._active_request_id or not (self._tip and self._tip.winfo_exists()):
                    return
                try:
                    photo = ImageTk.PhotoImage(img)
                    self._photo_cache[mem_key] = photo
                    target = self._front_label if suffix == "front" else self._back_label
                    if target and target.winfo_exists():
                        target.configure(image=photo)
                except Exception as ex:
                    _log.debug("Error applying preview photo to label: %s", ex)

            try:
                self.root.after(0, _apply_success)
            except Exception:
                pass

        except Exception as e:
            _log.debug("Error fetching Scryfall preview image for %s (%s): %s", info.name, suffix, e)

            def _apply_error() -> None:
                if req_id != self._active_request_id or not (self._tip and self._tip.winfo_exists()):
                    return
                target = self._front_label if suffix == "front" else self._back_label
                if target and target.winfo_exists():
                    target.configure(image=self._get_error_photo())

            try:
                self.root.after(0, _apply_error)
            except Exception:
                pass

    def _resolve_download_url(self, info: CardPreviewInfo, suffix: str) -> str | None:
        """Resolve direct small image URL from Scryfall API or info fields."""
        if suffix == "front" and info.front_url:
            return info.front_url
        if suffix == "back" and info.back_url:
            return info.back_url

        from src.scryfall import _fetch_raw_card_data, fetch_card_by_name

        raw_data: dict | None = None
        s_code = info.set_code
        c_num = info.collector_number

        if not s_code or not c_num:
            if info.name:
                try:
                    s_code, c_num = fetch_card_by_name(info.name)
                except Exception:
                    pass

        if s_code and c_num:
            try:
                raw_data = _fetch_raw_card_data(s_code, c_num, lang=info.lang)
            except Exception:
                if info.lang != "en":
                    try:
                        raw_data = _fetch_raw_card_data(s_code, c_num, lang="en")
                    except Exception:
                        pass

        if raw_data:
            if "image_uris" in raw_data:
                uris = raw_data["image_uris"]
                return uris.get("small") or uris.get("normal") or uris.get("large")
            elif "card_faces" in raw_data:
                faces = raw_data["card_faces"]
                idx = 0 if suffix == "front" else 1
                if idx < len(faces):
                    uris = faces[idx].get("image_uris", {})
                    return uris.get("small") or uris.get("normal") or uris.get("large")

        return None

    def _move(self, x_root: int, y_root: int) -> None:
        if not (self._tip and self._tip.winfo_exists()):
            return
        tw = self._tip.winfo_width()
        th = self._tip.winfo_height()
        sw = self._tip.winfo_screenwidth()
        sh = self._tip.winfo_screenheight()

        x = x_root + 18
        y = y_root + 18
        if x + tw > sw:
            x = x_root - tw - 12
        if y + th > sh:
            y = y_root - th - 12

        self._tip.geometry(f"+{max(0, x)}+{max(0, y)}")

    def destroy(self) -> None:
        """Clean up resources, pending tasks and executor."""
        self.hide()
        self._executor.shutdown(wait=False)
