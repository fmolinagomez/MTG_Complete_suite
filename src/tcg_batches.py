"""Adapters that turn imported TCG decks into normalized printable batches."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Protocol

from src.cancellation import Cancelled
from src.deck_importer import DeckCard
from src.lorcana_scraper import LocanaDeck, get_lorcana_back
from src.lorcana_scraper import download_images as lorcana_download
from src.lorcana_scraper import expand_deck as lorcana_expand
from src.op_scraper import OPDeck, get_op_backs
from src.op_scraper import download_images as op_download
from src.op_scraper import expand_deck as op_expand
from src.pdf_generator import YUGIOH_LAYOUT
from src.print_batch import PrintBatch
from src.rb_scraper import RBDeck, get_rb_backs
from src.rb_scraper import download_images as rb_download
from src.rb_scraper import expand_deck as rb_expand
from src.scryfall import download_deck_images as scryfall_download
from src.ygo_scraper import YGODeck, get_ygo_back
from src.ygo_scraper import download_images as ygo_download
from src.ygo_scraper import expand_deck as ygo_expand

_log = logging.getLogger(__name__)
ProgressReporter = Callable[[str, int, int, str], None]


def _check_cancel(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()


class MtgDeck(Protocol):
    """Magic deck interface consumed by the Magic adapter."""

    cards: list[DeckCard]
    back_path: Path | None

    def includes(self, card: DeckCard) -> bool: ...


class TcgBatchAdapter(Protocol):
    """Adapter interface for a TCG that can join a print run."""

    def prepare(self) -> PrintBatch: ...


def create_separator_image(
    deck_name: str, deck_format: str, cards: list, output_path: Path
) -> None:
    """Render a decorative separator card with deck title, format, commanders, and card-type counts."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = 745, 1040
    img = Image.new("RGB", (w, h))
    draw_g = ImageDraw.Draw(img)
    for y in range(h):
        r = int(46 - (46 - 19) * (y / h))
        g = int(40 - (40 - 17) * (y / h))
        b = int(34 - (34 - 16) * (y / h))
        draw_g.line([(0, y), (w, y)], fill=(r, g, b))

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_ol = ImageDraw.Draw(overlay)

    y0 = 60
    y1 = y0 + 170
    y_center = (y0 + y1) // 2
    x0 = 80
    x1 = w - 80
    cx = w // 2

    r_plaque = int(w * 0.027)
    r_inner = int(w * 0.020)

    shadow_offset = int(w * 0.008)
    shadow_rect = [
        x0 - 4 + shadow_offset,
        y0 - 4 + shadow_offset,
        x1 + 4 + shadow_offset,
        y1 + 4 + shadow_offset,
    ]
    draw_ol.rounded_rectangle(shadow_rect, radius=r_plaque, fill=(0, 0, 0, 180))

    stone_color = (60, 55, 50, 255)
    inner_stone_color = (74, 69, 64, 255)
    draw_ol.rounded_rectangle([x0, y0, x1, y1], radius=r_plaque, fill=stone_color)
    draw_ol.rounded_rectangle(
        [x0 + 8, y0 + 8, x1 - 8, y1 - 8], radius=r_inner, fill=inner_stone_color
    )

    bronze_color = (42, 34, 21, 255)
    gold_color = (197, 165, 96, 255)
    light_gold = (234, 210, 149, 255)

    w_outer_bronze = max(2, int(w * 0.008))
    w_outer_gold = max(1, int(w * 0.0027))
    w_inner_bronze = max(1, int(w * 0.0054))

    draw_ol.rounded_rectangle(
        [x0, y0, x1, y1], radius=r_plaque, outline=bronze_color, width=w_outer_bronze
    )
    draw_ol.rounded_rectangle(
        [x0 + 2, y0 + 2, x1 - 2, y1 - 2],
        radius=r_plaque - 2,
        outline=gold_color,
        width=w_outer_gold,
    )
    draw_ol.rounded_rectangle(
        [x0 + 8, y0 + 8, x1 - 8, y1 - 8],
        radius=r_inner,
        outline=bronze_color,
        width=w_inner_bronze,
    )

    corner_r = int(w * 0.033)
    draw_ol.arc(
        [x0 - corner_r, y0 - corner_r, x0 + corner_r, y0 + corner_r],
        90,
        270,
        fill=gold_color,
        width=w_outer_gold,
    )
    draw_ol.arc(
        [x0 - corner_r + 4, y0 - corner_r + 4, x0 + corner_r - 4, y0 + corner_r - 4],
        90,
        270,
        fill=bronze_color,
        width=1,
    )
    draw_ol.arc(
        [x0 - corner_r, y1 - corner_r, x0 + corner_r, y1 + corner_r],
        180,
        360,
        fill=gold_color,
        width=w_outer_gold,
    )
    draw_ol.arc(
        [x1 - corner_r, y0 - corner_r, x1 + corner_r, y0 + corner_r],
        270,
        90,
        fill=gold_color,
        width=w_outer_gold,
    )
    draw_ol.arc(
        [x1 - corner_r, y1 - corner_r, x1 + corner_r, y1 + corner_r],
        0,
        180,
        fill=gold_color,
        width=w_outer_gold,
    )

    draw_ol.line([(x0 + 40, y0), (cx - 40, y0)], fill=bronze_color, width=w_inner_bronze)
    draw_ol.line([(cx + 40, y0), (x1 - 40, y0)], fill=bronze_color, width=w_inner_bronze)
    draw_ol.line([(x0 + 40, y1), (cx - 40, y1)], fill=bronze_color, width=w_inner_bronze)
    draw_ol.line([(cx + 40, y1), (x1 - 40, y1)], fill=bronze_color, width=w_inner_bronze)

    c_white = (243, 230, 199, 255)
    c_blue = (80, 115, 169, 255)
    c_black = (50, 48, 47, 255)
    c_red = (192, 92, 70, 255)
    c_green = (62, 111, 83, 255)

    r1 = int(w * 0.040)
    r2 = int(w * 0.037)
    r3 = int(w * 0.029)

    medallions = [
        (x0 - 6, y_center, r1, c_red),
        (x1 + 6, y_center, r1, c_green),
        (cx, y0 - 6, r2, c_blue),
        (cx, y1 + 6, r2, c_black),
        (x0 + (cx - x0) // 2, y0 - 3, r3, c_white),
        (cx + (x1 - cx) // 2, y0 - 3, r3, c_black),
        (x0 + (cx - x0) // 2, y1 + 3, r3, c_blue),
        (cx + (x1 - cx) // 2, y1 + 3, r3, c_red),
    ]

    for mx, my, mr, mcolor in medallions:
        draw_ol.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=bronze_color)
        draw_ol.ellipse(
            [mx - mr + 3, my - mr + 3, mx + mr - 3, my + mr - 3], outline=gold_color, width=2
        )
        draw_ol.ellipse(
            [mx - mr + 6, my - mr + 6, mx + mr - 6, my + mr - 6], fill=(20, 15, 10, 255)
        )
        gem_r = mr - 9
        draw_ol.ellipse([mx - gem_r, my - gem_r, mx + gem_r, my + gem_r], fill=mcolor)
        glint_r = gem_r // 3
        if glint_r > 0:
            draw_ol.ellipse(
                [mx - glint_r - 2, my - glint_r - 2, mx + glint_r - 2, my + glint_r - 2],
                fill=(255, 255, 255, 150),
            )

    img_composite = Image.alpha_composite(img.convert("RGBA"), overlay)
    img = img_composite.convert("RGB")
    draw = ImageDraw.Draw(img)

    font_title = None
    font_sub = None
    font_stats_header = None
    font_stats_val = None

    title_size = 38
    sub_size = 20
    stats_header_size = 22
    stats_val_size = 26

    title_font_names = [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "Georgia Bold",
        "Arial Bold",
    ]
    sub_font_names = [
        "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
        "Georgia Italic",
        "Arial Italic",
    ]
    stats_header_names = [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "Georgia Bold",
        "Arial Bold",
    ]
    stats_val_names = ["/System/Library/Fonts/Supplemental/Georgia.ttf", "Georgia", "Arial"]

    for f_name in title_font_names:
        try:
            font_title = ImageFont.truetype(f_name, title_size)
            break
        except OSError:
            continue
    if font_title is None:
        font_title = ImageFont.load_default()

    for f_name in sub_font_names:
        try:
            font_sub = ImageFont.truetype(f_name, sub_size)
            break
        except OSError:
            continue
    if font_sub is None:
        font_sub = ImageFont.load_default()

    for f_name in stats_header_names:
        try:
            font_stats_header = ImageFont.truetype(f_name, stats_header_size)
            break
        except OSError:
            continue
    if font_stats_header is None:
        font_stats_header = ImageFont.load_default()

    for f_name in stats_val_names:
        try:
            font_stats_val = ImageFont.truetype(f_name, stats_val_size)
            break
        except OSError:
            continue
    if font_stats_val is None:
        font_stats_val = ImageFont.load_default()

    title_text = deck_name.upper()
    max_title_w = (x1 - x0) - 40
    while title_size > 14:
        try:
            bbox = draw.textbbox((0, 0), title_text, font=font_title)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            y_offset_t = bbox[1]
        except AttributeError:
            tw, th = draw.textsize(title_text, font=font_title)
            y_offset_t = 0
        if tw <= max_title_w:
            break
        title_size -= 2
        for f_name in title_font_names:
            try:
                font_title = ImageFont.truetype(f_name, title_size)
                break
            except OSError:
                continue

    try:
        bbox_t = draw.textbbox((0, 0), title_text, font=font_title)
        tw = bbox_t[2] - bbox_t[0]
        th = bbox_t[3] - bbox_t[1]
        y_offset_t = bbox_t[1]
    except AttributeError:
        tw, th = draw.textsize(title_text, font=font_title)
        y_offset_t = 0

    format_translation = {
        "commander": "Commander",
        "standard": "Standard",
        "modern": "Modern",
        "pauper": "Pauper",
        "legacy": "Legacy",
        "vintage": "Vintage",
        "pioneer": "Pioneer",
        "cedh": "cEDH",
    }
    fmt_pretty = format_translation.get(deck_format.lower(), deck_format.title())
    sub_text = f"Mazo de {fmt_pretty}"

    try:
        bbox_s = draw.textbbox((0, 0), sub_text, font=font_sub)
        sw = bbox_s[2] - bbox_s[0]
        sh = bbox_s[3] - bbox_s[1]
        y_offset_s = bbox_s[1]
    except AttributeError:
        sw, sh = draw.textsize(sub_text, font=font_sub)
        y_offset_s = 0

    tx = (w - tw) // 2
    ty = y_center - th - 5 - y_offset_t
    sx = (w - sw) // 2
    sy = y_center + 10 - y_offset_s

    draw.text((tx + 2, ty + 2), title_text, fill=(0, 0, 0), font=font_title)
    draw.text((tx - 1, ty - 1), title_text, fill=(0, 0, 0), font=font_title)
    draw.text((tx + 1, ty - 1), title_text, fill=(0, 0, 0), font=font_title)
    draw.text((tx - 1, ty + 1), title_text, fill=(0, 0, 0), font=font_title)
    draw.text((tx + 1, ty + 1), title_text, fill=(0, 0, 0), font=font_title)
    draw.text((tx, ty), title_text, fill=light_gold[:3], font=font_title)

    draw.text((sx + 1, sy + 1), sub_text, fill=(0, 0, 0), font=font_sub)
    draw.text((sx, sy), sub_text, fill=(218, 203, 169), font=font_sub)

    mainboard_cards = [c for c in cards if c.zone == "main"]
    commanders = [c for c in cards if getattr(c, "is_commander", False)]

    num_creatures = sum(
        c.quantity for c in mainboard_cards if "creature" in getattr(c, "type_line", "").lower()
    )
    num_lands = sum(
        c.quantity for c in mainboard_cards if "land" in getattr(c, "type_line", "").lower()
    )
    num_instants = sum(
        c.quantity for c in mainboard_cards if "instant" in getattr(c, "type_line", "").lower()
    )
    num_sorceries = sum(
        c.quantity for c in mainboard_cards if "sorcery" in getattr(c, "type_line", "").lower()
    )
    num_enchantments = sum(
        c.quantity for c in mainboard_cards if "enchantment" in getattr(c, "type_line", "").lower()
    )

    y_cursor = y1 + 90

    is_commander_format = (deck_format.lower() in ["commander", "cedh"]) or len(commanders) > 0
    if is_commander_format and commanders:
        header_text = "COMANDANTE(S)" if len(commanders) > 1 else "COMANDANTE"
        try:
            bbox_h = draw.textbbox((0, 0), header_text, font=font_stats_header)
            hw = bbox_h[2] - bbox_h[0]
            hh = bbox_h[3] - bbox_h[1]
        except AttributeError:
            hw, hh = draw.textsize(header_text, font=font_stats_header)

        hx = (w - hw) // 2
        draw.text((hx + 1, y_cursor + 1), header_text, fill=(0, 0, 0), font=font_stats_header)
        draw.text((hx, y_cursor), header_text, fill=light_gold[:3], font=font_stats_header)
        y_cursor += hh + 15

        for comm in commanders:
            c_name = comm.name
            try:
                bbox_n = draw.textbbox((0, 0), c_name, font=font_sub)
                nw = bbox_n[2] - bbox_n[0]
                nh = bbox_n[3] - bbox_n[1]
            except AttributeError:
                nw, nh = draw.textsize(c_name, font=font_sub)

            nx = (w - nw) // 2
            draw.text((nx + 1, y_cursor + 1), c_name, fill=(0, 0, 0), font=font_sub)
            draw.text((nx, y_cursor), c_name, fill=(255, 255, 255), font=font_sub)
            y_cursor += nh + 10

        y_cursor += 15
        sep_w = 260
        draw.line(
            [(cx - sep_w // 2, y_cursor), (cx + sep_w // 2, y_cursor)],
            fill=gold_color[:3],
            width=2,
        )
        draw.polygon(
            [(cx - 6, y_cursor), (cx, y_cursor - 6), (cx + 6, y_cursor), (cx, y_cursor + 6)],
            fill=gold_color[:3],
        )
        y_cursor += 35

    stats_header = "ESTADÍSTICAS DEL MAZO"
    try:
        bbox_sh = draw.textbbox((0, 0), stats_header, font=font_stats_header)
        shw = bbox_sh[2] - bbox_sh[0]
        shh = bbox_sh[3] - bbox_sh[1]
    except AttributeError:
        shw, shh = draw.textsize(stats_header, font=font_stats_header)

    shx = (w - shw) // 2
    draw.text((shx + 1, y_cursor + 1), stats_header, fill=(0, 0, 0), font=font_stats_header)
    draw.text((shx, y_cursor), stats_header, fill=light_gold[:3], font=font_stats_header)
    y_cursor += shh + 30

    stats = [
        ("Criaturas", num_creatures),
        ("Tierras", num_lands),
        ("Instantáneos", num_instants),
        ("Conjuros", num_sorceries),
        ("Encantamientos", num_enchantments),
    ]

    for label, val in stats:
        stat_text = f"{label}: {val}"
        try:
            bbox_v = draw.textbbox((0, 0), stat_text, font=font_stats_val)
            vw = bbox_v[2] - bbox_v[0]
            vh = bbox_v[3] - bbox_v[1]
        except AttributeError:
            vw, vh = draw.textsize(stat_text, font=font_stats_val)

        vx = (w - vw) // 2
        draw.text((vx + 1, y_cursor + 1), stat_text, fill=(0, 0, 0), font=font_stats_val)
        draw.text((vx, y_cursor), stat_text, fill=(240, 235, 225), font=font_stats_val)
        y_cursor += vh + 15

    y_cursor += 20
    sep_w = 160
    draw.line(
        [(cx - sep_w // 2, y_cursor), (cx + sep_w // 2, y_cursor)],
        fill=bronze_color[:3],
        width=2,
    )
    draw.ellipse([cx - 4, y_cursor - 4, cx + 4, y_cursor + 4], fill=gold_color[:3])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "JPEG", quality=95)


def count_skipped_tokens(decks: Sequence[MtgDeck]) -> int:
    """Count token copies excluded because their Scryfall layout is not a printable token/emblem."""
    skipped = 0
    for deck in decks:
        if getattr(deck, "include_tokens", False):
            skipped += sum(
                c.quantity
                for c in deck.cards
                if c.zone == "token" and c.layout not in ("token", "emblem", "double_faced_token")
            )
    return skipped


def write_tokens_markdown(decks: Sequence[MtgDeck], output_dir: Path) -> Path | None:
    """Write tokens.md into output_dir if any imported Magic deck contains token cards."""
    if not decks or not any(any(c.zone == "token" for c in d.cards) for d in decks):
        return None

    md_path = Path(output_dir) / "tokens.md"
    md_lines = ["# Fichas Necesarias por Mazo", ""]
    for deck in decks:
        display_name = getattr(deck, "display_name", getattr(deck, "name", "Mazo"))
        md_lines.append(f"## Mazo: {display_name}")
        tokens = [c for c in deck.cards if c.zone == "token"]
        if not tokens:
            md_lines.append("No se necesitan fichas.")
            md_lines.append("")
            continue

        token_groups: dict[tuple[str, str, str, str], int] = {}
        for c in tokens:
            key = (
                c.name,
                (c.set_code or "").lower(),
                c.collector_number or "",
                c.layout,
            )
            token_groups[key] = token_groups.get(key, 0) + c.quantity

        include_tokens = getattr(deck, "include_tokens", False)
        for (name, set_code, cn, layout), qty in sorted(
            token_groups.items(), key=lambda x: x[0][0].casefold()
        ):
            is_valid = layout in ("token", "emblem", "double_faced_token")
            is_included = include_tokens and is_valid
            inc_str = "Incluida en el PDF" if is_included else "No incluida en el PDF"
            details = []
            if set_code:
                details.append(set_code.upper())
            if cn:
                details.append(cn)
            details_str = f" ({' '.join(details)})" if details else ""
            md_lines.append(f"- {qty}x {name}{details_str} — *{inc_str}*")
        md_lines.append("")

    try:
        md_path.write_text("\n".join(md_lines), encoding="utf-8")
        _log.info("Archivo tokens.md generado en: %s", md_path)
        return md_path
    except Exception as e:
        _log.error("No se pudo escribir tokens.md: %s", e)
        return None


@dataclass(frozen=True)
class YugiohBatchAdapter:
    """Normalize Yu-Gi-Oh! decks while preserving their separate print run."""

    decks: tuple[tuple[YGODeck, bool], ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("yugioh_proxies")
        total = sum(len({card.card_id for card in deck.cards}) for deck, _ in self.decks)
        self.report_progress("download", 0, total, "Yu-Gi-Oh!")
        fronts: list[Path] = []
        downloaded = 0
        for deck, include_side in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.card_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, "Yu-Gi-Oh!")

            image_map = ygo_download(
                deck,
                self.work_dir / "ygo_raw",
                cancel_event=self.cancel_event,
                progress_cb=_progress,
            )
            _check_cancel(self.cancel_event)
            fronts.extend(ygo_expand(deck, image_map, include_side))
            downloaded += len({card.card_id for card in deck.cards})
        back = get_ygo_back()
        return PrintBatch(
            "yugioh_proxies",
            fronts,
            [back] * len(fronts),
            {path: False for path in {*fronts, back}},
            back,
            YUGIOH_LAYOUT,
            True,
        )


@dataclass(frozen=True)
class OnePieceBatchAdapter:
    decks: tuple[OPDeck, ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("One Piece")
        label = " + ".join(deck.name for deck in self.decks)
        total = sum(len({card.card_id for card in deck.cards}) for deck in self.decks)
        self.report_progress("download", 0, total, f"One Piece – {label}")
        image_map: dict[str, Path] = {}
        downloaded = 0
        for deck in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.card_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, f"One Piece – {label}")

            image_map.update(
                op_download(
                    deck,
                    self.work_dir / "op_raw",
                    cancel_event=self.cancel_event,
                    progress_cb=_progress,
                )
            )
            _check_cancel(self.cancel_event)
            downloaded += len({card.card_id for card in deck.cards})
        standard_back, leader_back = get_op_backs()
        leader_backs = {
            deck.leader.card_id: leader_back for deck in self.decks if deck.leader is not None
        }
        fronts: list[Path] = []
        backs: list[Path | None] = []
        for deck in self.decks:
            leader = deck.leader
            resolved_leader_back = leader_backs.get(leader.card_id) if leader else None
            deck_fronts, deck_backs = op_expand(
                deck, image_map, resolved_leader_back, standard_back
            )
            fronts.extend(deck_fronts)
            backs.extend(standard_back if back is None else back for back in deck_backs)
        crop_map = {path: False for path in set(fronts) | {standard_back, *leader_backs.values()}}
        return PrintBatch("One Piece", fronts, backs, crop_map, standard_back)


@dataclass(frozen=True)
class RiftboundBatchAdapter:
    decks: tuple[tuple[RBDeck, bool], ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("Riftbound")
        label = " + ".join(deck.name for deck, _ in self.decks)
        total = sum(len({card.variant_id for card in deck.cards}) for deck, _ in self.decks)
        self.report_progress("download", 0, total, f"Riftbound – {label}")
        image_map: dict[str, Path] = {}
        downloaded = 0
        for deck, _ in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.variant_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, f"Riftbound – {label}")

            image_map.update(
                rb_download(
                    deck,
                    self.work_dir / "rb_raw",
                    cancel_event=self.cancel_event,
                    progress_cb=_progress,
                )
            )
            _check_cancel(self.cancel_event)
            downloaded += len({card.variant_id for card in deck.cards})
        back_by_section = get_rb_backs()
        default_back = back_by_section.get("maindeck") or next(iter(back_by_section.values()))
        fronts: list[Path] = []
        backs: list[Path | None] = []
        for deck, include_runes in self.decks:
            deck_fronts, deck_backs = rb_expand(
                deck, image_map, back_by_section, include_runes=include_runes
            )
            fronts.extend(deck_fronts)
            backs.extend(default_back if back is None else back for back in deck_backs)
        crop_map = {path: False for path in set(fronts) | set(back_by_section.values())}
        return PrintBatch("Riftbound", fronts, backs, crop_map, default_back)


@dataclass(frozen=True)
class LorcanaBatchAdapter:
    decks: tuple[LocanaDeck, ...]
    work_dir: Path
    cancel_event: Event | None
    report_progress: ProgressReporter

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("Lorcana")
        label = " + ".join(deck.name for deck in self.decks)
        total = sum(len({card.card_id for card in deck.cards}) for deck in self.decks)
        self.report_progress("download", 0, total, f"Lorcana – {label}")
        image_map: dict[str, Path] = {}
        downloaded = 0
        for deck in self.decks:
            selected = getattr(deck, "selected_card_ids", None)
            if selected is not None:
                deck = replace(
                    deck, cards=[card for card in deck.cards if card.card_id in selected]
                )
            offset = downloaded

            def _progress(done: int, _total: int, *, _offset=offset) -> None:
                self.report_progress("download", _offset + done, total, f"Lorcana – {label}")

            image_map.update(
                lorcana_download(
                    deck,
                    self.work_dir / "lorcana_raw",
                    cancel_event=self.cancel_event,
                    progress_cb=_progress,
                )
            )
            _check_cancel(self.cancel_event)
            downloaded += len({card.card_id for card in deck.cards})
        default_back = get_lorcana_back()
        fronts: list[Path] = []
        backs: list[Path | None] = []
        for deck in self.decks:
            deck_fronts, deck_backs = lorcana_expand(deck, image_map)
            fronts.extend(deck_fronts)
            backs.extend(default_back if back is None else back for back in deck_backs)
        crop_map = {path: False for path in set(fronts) | {default_back}}
        return PrintBatch("Lorcana", fronts, backs, crop_map, default_back)


@dataclass(frozen=True)
class MagicBatchAdapter:
    decks: tuple[MtgDeck, ...]
    work_dir: Path
    default_back: Path
    local_backs: tuple[Path, ...]
    cancel_event: Event | None
    report_progress: ProgressReporter
    scryfall_downloader: Callable = scryfall_download
    separator_creator: Callable | None = create_separator_image
    scryfall_lang: str = "en"
    scryfall_quality: str = "large"
    scryfall_fail_policy: str = "english"
    scryfall_quality_check: bool = True
    scryfall_blur_threshold: float = 100.0
    scryfall_brightness_contrast_check: bool = True
    scryfall_minimize_tokens: bool = False

    def prepare(self) -> PrintBatch:
        if not self.decks:
            return PrintBatch("magic_url")

        # 1. Identify active cards and commanders for each deck
        decks_with_cards = []
        for _deck in self.decks:
            active_cards = []
            seen_tokens = set()
            for c in _deck.cards:
                if not _deck.includes(c) or c.quantity <= 0:
                    continue
                if c.zone == "token":
                    key = ((c.set_code or "").lower(), c.collector_number or "")
                    if key in seen_tokens:
                        continue
                    seen_tokens.add(key)
                    active_cards.append(replace(c, quantity=1))
                else:
                    active_cards.append(c)

            zone_order = {"main": 0, "token": 1, "side": 2}
            active_cards.sort(key=lambda x: (zone_order.get(x.zone, 0), x.name.casefold()))

            commander_card = None
            for c in _deck.cards:
                if getattr(c, "is_commander", False):
                    commander_card = c
                    break
            if not commander_card:
                for c in _deck.cards:
                    if c.zone == "main":
                        commander_card = c
                        break

            decks_with_cards.append(
                {
                    "deck": _deck,
                    "active_cards": active_cards,
                    "commander": commander_card,
                }
            )

        # 2. Collect all cards to download
        mtg_cards_all = []
        for item in decks_with_cards:
            mtg_cards_all.extend(item["active_cards"])
            if item["commander"] and not any(
                c.name == item["commander"].name
                and c.set_code == item["commander"].set_code
                and c.collector_number == item["commander"].collector_number
                for c in item["active_cards"]
            ):
                mtg_cards_all.append(replace(item["commander"], quantity=0))

        label = f"Magic – {len(self.decks)} mazo(s)"
        self.report_progress("download", 0, len(mtg_cards_all), label)

        def _mtg_prog(done: int, total: int) -> None:
            self.report_progress("download", done, total, label)

        scryfall_dir = self.work_dir / "scryfall"
        try:
            dl_results = self.scryfall_downloader(
                mtg_cards_all,
                scryfall_dir,
                _mtg_prog,
                cancel_event=self.cancel_event,
                lang=self.scryfall_lang,
                quality=self.scryfall_quality,
                fail_policy=self.scryfall_fail_policy,
                quality_check=self.scryfall_quality_check,
                blur_threshold=self.scryfall_blur_threshold,
                brightness_contrast_check=self.scryfall_brightness_contrast_check,
            )
        except TypeError:
            dl_results = self.scryfall_downloader(
                mtg_cards_all,
                scryfall_dir,
                _mtg_prog,
                cancel_event=self.cancel_event,
            )

        _check_cancel(self.cancel_event)

        image_map = {}
        for card, front_path, back_path in dl_results:
            if card.scryfall_id:
                image_map[card.scryfall_id] = (front_path, back_path)
            image_map[(card.set_code.lower(), card.collector_number)] = (front_path, back_path)

        local_back_set = set(self.local_backs)
        all_mtg_back_paths: set[Path] = {self.default_back}
        mtg_fronts: list[Path] = []
        mtg_backs_resolved: list[Path | None] = []

        printed_tokens_global = set()
        for idx, item in enumerate(decks_with_cards):
            _deck = item["deck"]

            if getattr(_deck, "include_separator", False) and self.separator_creator:
                sep_front_path = scryfall_dir / f"separator_{idx}.jpg"
                sep_back_path = self.default_back
                if _deck.back_path in local_back_set:
                    sep_back_path = _deck.back_path

                try:
                    display_name = getattr(_deck, "display_name", getattr(_deck, "name", "Mazo"))
                    deck_format = getattr(_deck, "format", "commander")
                    self.separator_creator(display_name, deck_format, _deck.cards, sep_front_path)
                except Exception as e:
                    _log.error("Error al crear la carta separadora: %s", e)
                    sep_front_path = None

                if sep_front_path and sep_front_path.exists():
                    mtg_fronts.append(sep_front_path)
                    mtg_backs_resolved.append(sep_back_path)
                    if sep_back_path:
                        all_mtg_back_paths.add(sep_back_path)

            for card in item["active_cards"]:
                comp_key = (card.set_code.lower(), card.collector_number)
                front_path, back_path = image_map.get(comp_key, (None, None))
                if front_path is None and card.scryfall_id:
                    front_path, back_path = image_map.get(card.scryfall_id, (None, None))
                if not front_path or not front_path.exists():
                    continue

                if card.zone == "token" and self.scryfall_minimize_tokens:
                    if comp_key in printed_tokens_global:
                        continue
                    printed_tokens_global.add(comp_key)

                if back_path is not None:
                    resolved_back = back_path
                elif _deck.back_path in local_back_set:
                    resolved_back = _deck.back_path
                else:
                    resolved_back = self.default_back

                all_mtg_back_paths.add(resolved_back)

                for _ in range(card.quantity):
                    mtg_fronts.append(front_path)
                    mtg_backs_resolved.append(resolved_back)

        crop_map = {
            p: False for p in set(mtg_fronts) | all_mtg_back_paths if p not in local_back_set
        }
        return PrintBatch("magic_url", mtg_fronts, mtg_backs_resolved, crop_map, self.default_back)
