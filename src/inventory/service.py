"""Inventory Service orchestrating database operations, imports, and proxy planning."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from src.inventory.allocation_engine import (
    AllocationPolicy,
    StockAllocationEngine,
    card_usage_sort_key,
    cards_match,
    names_match,
    normalize_card_name,
    normalize_rarity_filter,
    normalize_set_filter,
    normalize_usage_filter,
)
from src.inventory.db import DEFAULT_DB_PATH, get_db, init_db
from src.inventory.manabox_csv import parse_manabox_csv
from src.inventory.models import (
    DeckCardEntry,
    DeckCardMatchDetail,
    DeckRecord,
    InventoryCard,
    MissingProxyPlan,
)


def _row_to_inventory_card(row: sqlite3.Row) -> InventoryCard:
    """Hydrate an InventoryCard from a database row."""
    return InventoryCard(
        id=row["id"],
        oracle_id=row["oracle_id"],
        name=row["name"],
        set_code=row["set_code"],
        collector_number=row["collector_number"],
        quantity=int(row["quantity"]),
        is_foil=bool(row["is_foil"]),
        is_proxy=bool(row["is_proxy"]),
        condition=row["condition"],
        language=row["language"],
        metadata_json=row["metadata_json"],
    )


def _row_to_deck_record(row: sqlite3.Row) -> DeckRecord:
    """Hydrate a DeckRecord from a database row."""
    return DeckRecord(
        id=row["id"],
        name=row["name"],
        source_url=row["source_url"],
        format=row["format"],
        created_at=row["created_at"],
        notes=row["notes"],
    )


def _row_to_deck_card_entry(row: sqlite3.Row) -> DeckCardEntry:
    """Hydrate a DeckCardEntry from a database row."""
    return DeckCardEntry(
        id=row["id"],
        deck_id=row["deck_id"],
        oracle_id=row["oracle_id"],
        name=row["name"],
        set_code=row["set_code"],
        collector_number=row["collector_number"],
        quantity_required=int(row["quantity_required"]),
        is_proxy=bool(row["is_proxy"]),
        physical_quantity=int(row["physical_quantity"] or 0),
        proxy_quantity=int(row["proxy_quantity"] or 0),
        fulfilled_card_id=row["fulfilled_card_id"],
    )


def _insert_card_in_tx(conn: sqlite3.Connection, card: InventoryCard) -> int:
    """Insert a new InventoryCard row inside an active transaction."""
    cursor = conn.execute(
        """
        INSERT INTO inventory_cards (
            oracle_id, name, set_code, collector_number, quantity,
            is_foil, is_proxy, condition, language, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card.oracle_id,
            card.name,
            card.set_code,
            card.collector_number,
            card.quantity,
            1 if card.is_foil else 0,
            1 if card.is_proxy else 0,
            card.condition,
            card.language,
            card.metadata_json,
        ),
    )
    return int(cursor.lastrowid)


def _upsert_card_in_tx(
    conn: sqlite3.Connection,
    card: InventoryCard,
    *,
    accumulate_quantity: bool = True,
) -> int:
    """Insert or update an InventoryCard row inside an active transaction."""
    if card.id is not None:
        existing = conn.execute(
            "SELECT id FROM inventory_cards WHERE id = ?", (card.id,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE inventory_cards SET
                    oracle_id = ?,
                    name = ?,
                    set_code = ?,
                    collector_number = ?,
                    quantity = ?,
                    is_foil = ?,
                    is_proxy = ?,
                    condition = ?,
                    language = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    card.oracle_id,
                    card.name,
                    card.set_code,
                    card.collector_number,
                    card.quantity,
                    1 if card.is_foil else 0,
                    1 if card.is_proxy else 0,
                    card.condition,
                    card.language,
                    card.metadata_json,
                    card.id,
                ),
            )
            return card.id

    row = conn.execute(
        """
        SELECT id, quantity FROM inventory_cards
        WHERE set_code = ? AND collector_number = ? AND is_foil = ? AND is_proxy = ?
        """,
        (
            card.set_code,
            card.collector_number,
            1 if card.is_foil else 0,
            1 if card.is_proxy else 0,
        ),
    ).fetchone()

    if row:
        new_qty = (int(row["quantity"]) + card.quantity) if accumulate_quantity else card.quantity
        conn.execute(
            """
            UPDATE inventory_cards SET
                oracle_id = ?,
                name = ?,
                quantity = ?,
                condition = ?,
                language = ?,
                metadata_json = ?
            WHERE id = ?
            """,
            (
                card.oracle_id,
                card.name,
                new_qty,
                card.condition,
                card.language,
                card.metadata_json,
                row["id"],
            ),
        )
        return int(row["id"])

    return _insert_card_in_tx(conn, card)


class InventoryService:
    """Service layer for card inventory, deck management, and proxy calculations."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        allocation_engine: StockAllocationEngine | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        init_db(self.db_path)
        self.allocation_engine = allocation_engine or StockAllocationEngine()

    def import_cards(
        self,
        cards: Sequence[InventoryCard],
        mode: str = "merge",
    ) -> int:
        """Import a collection of InventoryCard items using 'merge' or 'replace' mode."""
        mode_clean = mode.strip().lower()
        if mode_clean not in ("replace", "merge"):
            raise ValueError("Modo de importación no válido: debe ser 'merge' o 'replace'")

        with get_db(self.db_path) as conn:
            if mode_clean == "replace":
                conn.execute("DELETE FROM inventory_cards WHERE is_proxy = 0")
                for card in cards:
                    _insert_card_in_tx(conn, card)
            else:
                for card in cards:
                    _upsert_card_in_tx(conn, card, accumulate_quantity=False)

        return sum(c.quantity for c in cards)

    def import_manabox_csv(self, csv_path: Path | str, mode: str = "merge") -> int:
        """Parse and import a ManaBox CSV export into the collection."""
        cards = parse_manabox_csv(csv_path)
        return self.import_cards(cards, mode=mode)

    def get_inventory_cards(
        self,
        query: str = "",
        filter_proxy: bool | None = None,
    ) -> list[InventoryCard]:
        """Fetch inventory cards with optional search query and proxy filter."""
        sql = """
            SELECT id, oracle_id, name, set_code, collector_number, quantity,
                   is_foil, is_proxy, condition, language, metadata_json
            FROM inventory_cards
        """
        clauses: list[str] = []
        params: list[object] = []

        if filter_proxy is not None:
            clauses.append("is_proxy = ?")
            params.append(1 if filter_proxy else 0)

        clean_query = query.strip()
        if clean_query:
            clauses.append(
                "(name LIKE ? OR set_code LIKE ? OR oracle_id LIKE ? OR collector_number LIKE ?)"
            )
            param_str = f"%{clean_query}%"
            params.extend([param_str, param_str, param_str, param_str])

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        sql += " ORDER BY name ASC, set_code ASC, collector_number ASC, is_foil ASC"

        with get_db(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()

        return [_row_to_inventory_card(row) for row in rows]

    def get_card_by_id(self, card_id: int) -> InventoryCard | None:
        """Fetch a single inventory card by its primary key ID."""
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, oracle_id, name, set_code, collector_number, quantity,
                       is_foil, is_proxy, condition, language, metadata_json
                FROM inventory_cards WHERE id = ?
                """,
                (card_id,),
            ).fetchone()

        return _row_to_inventory_card(row) if row else None

    def add_or_update_card(self, card: InventoryCard) -> int:
        """Insert or update a card in the inventory database."""
        with get_db(self.db_path) as conn:
            return _upsert_card_in_tx(conn, card, accumulate_quantity=True)

    def delete_card(self, card_id: int) -> bool:
        """Delete an inventory card by ID."""
        with get_db(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM inventory_cards WHERE id = ?", (card_id,))
            return cursor.rowcount > 0

    def batch_delete_cards(self, card_ids: list[int]) -> int:
        """Delete multiple inventory cards by ID in a single transaction."""
        if not card_ids:
            return 0
        with get_db(self.db_path) as conn:
            placeholders = ",".join("?" for _ in card_ids)
            cursor = conn.execute(
                f"DELETE FROM inventory_cards WHERE id IN ({placeholders})",
                card_ids,
            )
            return cursor.rowcount

    def check_cards_in_decks(self, card_ids: list[int]) -> dict[int, list[str]]:
        """Check which cards are currently in use in registered decks.

        Uses canonical card identity matching (oracle_id, DFC names, apostrophes, and fulfilled_card_id).
        """
        if not card_ids:
            return {}

        result: dict[int, list[str]] = {cid: [] for cid in card_ids}
        with get_db(self.db_path) as conn:
            placeholders = ",".join("?" for _ in card_ids)
            cards = conn.execute(
                f"SELECT id, oracle_id, name FROM inventory_cards WHERE id IN ({placeholders})",
                card_ids,
            ).fetchall()

            if not cards:
                return result

            deck_cards = conn.execute(
                """
                SELECT d.name AS deck_name, dc.oracle_id, dc.name, dc.fulfilled_card_id
                FROM deck_cards dc
                JOIN decks d ON d.id = dc.deck_id
                """
            ).fetchall()

            for c in cards:
                cid = c["id"]
                coid = (c["oracle_id"] or "").strip()
                cname = (c["name"] or "").strip()
                matching_decks = set()
                for dc in deck_cards:
                    if dc["fulfilled_card_id"] == cid or cards_match(
                        coid, cname, dc["oracle_id"], dc["name"]
                    ):
                        matching_decks.add(dc["deck_name"])
                result[cid] = sorted(matching_decks)

        return result

    def batch_update_cards(
        self,
        card_ids: list[int],
        *,
        is_proxy: bool | None = None,
        is_foil: bool | None = None,
        condition: str | None = None,
        language: str | None = None,
        quantity_delta: int | None = None,
        set_quantity: int | None = None,
    ) -> int:
        """Batch update attributes across multiple inventory cards.

        If updating is_proxy or is_foil causes a card to match an existing card
        with identical (set_code, collector_number, is_foil, is_proxy), quantities
        are merged into the existing card and the duplicate card row is deleted.
        """
        if not card_ids:
            return 0

        updated_count = 0
        with get_db(self.db_path) as conn:
            for cid in card_ids:
                row = conn.execute(
                    """
                    SELECT id, oracle_id, name, set_code, collector_number,
                           quantity, is_foil, is_proxy, condition, language, metadata_json
                    FROM inventory_cards
                    WHERE id = ?
                    """,
                    (cid,),
                ).fetchone()
                if not row:
                    continue

                cur_proxy = bool(row["is_proxy"])
                cur_foil = bool(row["is_foil"])
                cur_qty = int(row["quantity"])
                cur_cond = str(row["condition"])
                cur_lang = str(row["language"])
                set_code = str(row["set_code"])
                collector_num = str(row["collector_number"])

                new_proxy = is_proxy if is_proxy is not None else cur_proxy
                new_foil = is_foil if is_foil is not None else cur_foil
                new_cond = condition if condition is not None else cur_cond
                new_lang = language if language is not None else cur_lang

                if set_quantity is not None:
                    new_qty = max(1, set_quantity)
                elif quantity_delta is not None:
                    new_qty = max(1, cur_qty + quantity_delta)
                else:
                    new_qty = cur_qty

                existing = conn.execute(
                    """
                    SELECT id, quantity FROM inventory_cards
                    WHERE set_code = ? AND collector_number = ? AND is_foil = ? AND is_proxy = ? AND id != ?
                    """,
                    (
                        set_code,
                        collector_num,
                        1 if new_foil else 0,
                        1 if new_proxy else 0,
                        cid,
                    ),
                ).fetchone()

                if existing:
                    merged_qty = int(existing["quantity"]) + new_qty
                    conn.execute(
                        "UPDATE inventory_cards SET quantity = ? WHERE id = ?",
                        (merged_qty, existing["id"]),
                    )
                    conn.execute("DELETE FROM inventory_cards WHERE id = ?", (cid,))
                else:
                    conn.execute(
                        """
                        UPDATE inventory_cards SET
                            is_foil = ?,
                            is_proxy = ?,
                            condition = ?,
                            language = ?,
                            quantity = ?
                        WHERE id = ?
                        """,
                        (
                            1 if new_foil else 0,
                            1 if new_proxy else 0,
                            new_cond,
                            new_lang,
                            new_qty,
                            cid,
                        ),
                    )
                updated_count += 1

        return updated_count

    def batch_assign_to_deck(
        self,
        deck_id: int,
        card_ids: list[int],
        copies_mode: str | int = "one",
    ) -> int:
        """Batch assign inventory cards to a registered deck while enforcing ADR-0003 split invariants."""
        if not card_ids:
            return 0

        assigned_count = 0
        with get_db(self.db_path) as conn:
            deck = conn.execute("SELECT id FROM decks WHERE id = ?", (deck_id,)).fetchone()
            if not deck:
                raise ValueError(f"Deck with id {deck_id} does not exist.")

            placeholders = ",".join("?" for _ in card_ids)
            card_rows = conn.execute(
                f"""
                SELECT id, oracle_id, name, set_code, collector_number,
                       quantity, is_foil, is_proxy, condition, language, metadata_json
                FROM inventory_cards
                WHERE id IN ({placeholders})
                """,
                card_ids,
            ).fetchall()

            avail_by_card_id: dict[int, int] = {}
            if copies_mode == "available" and card_rows:
                all_inv_rows = conn.execute(
                    """
                    SELECT id, oracle_id, name, set_code, collector_number,
                           quantity, is_foil, is_proxy, condition, language, metadata_json
                    FROM inventory_cards
                    """
                ).fetchall()
                all_dc_rows = conn.execute(
                    """
                    SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                           quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id
                    FROM deck_cards
                    ORDER BY id ASC
                    """
                ).fetchall()
                _, avail_by_card_id = self.allocation_engine.calculate_inventory_usage(
                    [_row_to_inventory_card(r) for r in all_inv_rows],
                    [_row_to_deck_card_entry(r) for r in all_dc_rows],
                )

            for card in card_rows:
                cid = int(card["id"])
                card_qty = int(card["quantity"])
                card_is_proxy = bool(card["is_proxy"])

                if copies_mode == "available":
                    avail = avail_by_card_id.get(cid, card_qty)
                    copies = max(1, avail)
                elif isinstance(copies_mode, int):
                    copies = max(1, copies_mode)
                else:
                    copies = 1

                existing_dc = conn.execute(
                    """
                    SELECT id, quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id
                    FROM deck_cards
                    WHERE deck_id = ? AND set_code = ? AND collector_number = ?
                    """,
                    (deck_id, card["set_code"], card["collector_number"]),
                ).fetchone()

                if existing_dc:
                    new_req = int(existing_dc["quantity_required"]) + copies
                    cur_phys = int(existing_dc["physical_quantity"] or 0)
                    cur_proxy = int(existing_dc["proxy_quantity"] or 0)
                    cur_fulfilled = existing_dc["fulfilled_card_id"]

                    if card_is_proxy:
                        new_phys = cur_phys
                        new_proxy = (
                            cur_proxy + copies if (cur_phys > 0 or cur_proxy > 0) else new_req
                        )
                        new_is_proxy = 1 if new_phys <= 0 else 0
                        new_fulfilled = cur_fulfilled
                    else:
                        base_phys = (
                            cur_phys
                            if (cur_phys > 0 or cur_proxy > 0 or existing_dc["is_proxy"])
                            else int(existing_dc["quantity_required"])
                        )
                        new_phys = min(new_req, base_phys + copies)
                        new_proxy = max(0, new_req - new_phys)
                        new_is_proxy = 1 if new_phys <= 0 else 0
                        new_fulfilled = cur_fulfilled if cur_fulfilled is not None else cid

                    conn.execute(
                        """
                        UPDATE deck_cards SET
                            quantity_required = ?,
                            is_proxy = ?,
                            physical_quantity = ?,
                            proxy_quantity = ?,
                            fulfilled_card_id = ?
                        WHERE id = ?
                        """,
                        (
                            new_req,
                            new_is_proxy,
                            new_phys,
                            new_proxy,
                            new_fulfilled,
                            existing_dc["id"],
                        ),
                    )
                else:
                    if card_is_proxy:
                        phys_qty = 0
                        proxy_qty = copies
                        is_proxy_val = 1
                        fulfilled_id = None
                    else:
                        phys_qty = min(copies, card_qty)
                        proxy_qty = max(0, copies - phys_qty)
                        is_proxy_val = 1 if phys_qty <= 0 else 0
                        fulfilled_id = cid

                    conn.execute(
                        """
                        INSERT INTO deck_cards (
                            deck_id, oracle_id, name, set_code, collector_number,
                            quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            deck_id,
                            card["oracle_id"],
                            card["name"],
                            card["set_code"],
                            card["collector_number"],
                            copies,
                            is_proxy_val,
                            phys_qty,
                            proxy_qty,
                            fulfilled_id,
                        ),
                    )
                assigned_count += 1

        return assigned_count

    def get_all_decks(self) -> list[DeckRecord]:
        """Fetch all registered decks."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, name, source_url, format, created_at, notes
                FROM decks
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()

        return [_row_to_deck_record(row) for row in rows]

    def get_deck_by_id(self, deck_id: int) -> tuple[DeckRecord, list[DeckCardEntry]] | None:
        """Fetch a deck record and its associated card entries."""
        with get_db(self.db_path) as conn:
            deck_row = conn.execute(
                """
                SELECT id, name, source_url, format, created_at, notes
                FROM decks WHERE id = ?
                """,
                (deck_id,),
            ).fetchone()

            if not deck_row:
                return None

            deck = _row_to_deck_record(deck_row)

            card_rows = conn.execute(
                """
                SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                       quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id
                FROM deck_cards
                WHERE deck_id = ?
                ORDER BY name ASC, set_code ASC
                """,
                (deck_id,),
            ).fetchall()

            cards = [_row_to_deck_card_entry(row) for row in card_rows]

        return deck, cards

    def register_deck(self, deck: DeckRecord, cards: list[DeckCardEntry]) -> int:
        """Register or update a deck with its card requirements."""
        created_at = deck.created_at or datetime.now(timezone.utc).isoformat()

        with get_db(self.db_path) as conn:
            if deck.id is not None:
                existing = conn.execute("SELECT id FROM decks WHERE id = ?", (deck.id,)).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE decks SET name = ?, source_url = ?, format = ?, created_at = ?, notes = ?
                        WHERE id = ?
                        """,
                        (deck.name, deck.source_url, deck.format, created_at, deck.notes, deck.id),
                    )
                    deck_id = deck.id
                    conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO decks (name, source_url, format, created_at, notes)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (deck.name, deck.source_url, deck.format, created_at, deck.notes),
                    )
                    deck_id = int(cursor.lastrowid)
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO decks (name, source_url, format, created_at, notes)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (deck.name, deck.source_url, deck.format, created_at, deck.notes),
                )
                deck_id = int(cursor.lastrowid)

            for entry in cards:
                conn.execute(
                    """
                    INSERT INTO deck_cards (
                        deck_id, oracle_id, name, set_code, collector_number,
                        quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deck_id,
                        entry.oracle_id,
                        entry.name,
                        entry.set_code,
                        entry.collector_number,
                        entry.quantity_required,
                        1 if entry.is_proxy else 0,
                        entry.physical_quantity,
                        entry.proxy_quantity,
                        entry.fulfilled_card_id,
                    ),
                )

        return deck_id

    def update_deck_card_allocation(
        self,
        deck_card_id: int,
        physical_quantity: int,
        proxy_quantity: int,
        fulfilled_card_id: int | None = None,
    ) -> bool:
        """Update granular physical vs proxy allocation and fulfilled version for a deck card."""
        with get_db(self.db_path) as conn:
            is_proxy = 1 if physical_quantity <= 0 else 0
            cursor = conn.execute(
                """
                UPDATE deck_cards SET
                    physical_quantity = ?,
                    proxy_quantity = ?,
                    fulfilled_card_id = ?,
                    is_proxy = ?
                WHERE id = ?
                """,
                (physical_quantity, proxy_quantity, fulfilled_card_id, is_proxy, deck_card_id),
            )
            return cursor.rowcount > 0

    def delete_deck(self, deck_id: int) -> bool:
        """Delete a deck and all its card associations (cascade)."""
        with get_db(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
            return cursor.rowcount > 0

    def _load_allocation_context(
        self,
        include_proxies: bool,
        current_deck_id: int | None,
        allocate_mode: AllocationPolicy | str,
    ) -> tuple[list[InventoryCard], list[DeckCardEntry]]:
        """Load inventory cards and competing deck cards for stock allocation."""
        policy = AllocationPolicy.from_str(allocate_mode)
        with get_db(self.db_path) as conn:
            where_sql = (
                "WHERE quantity > 0" if include_proxies else "WHERE is_proxy = 0 AND quantity > 0"
            )
            rows = conn.execute(
                f"""
                SELECT id, oracle_id, name, set_code, collector_number, quantity,
                       is_foil, is_proxy, condition, language, metadata_json
                FROM inventory_cards
                {where_sql}
                """
            ).fetchall()
            inventory_cards = [_row_to_inventory_card(row) for row in rows]

            other_deck_cards: list[DeckCardEntry] = []
            if policy == AllocationPolicy.EXCLUSIVE:
                deck_filter_clauses: list[str] = []
                deck_params: list[object] = []
                if not include_proxies:
                    deck_filter_clauses.append("is_proxy = 0")
                if current_deck_id is not None:
                    deck_filter_clauses.append("deck_id != ?")
                    deck_params.append(current_deck_id)

                deck_filter_sql = (
                    f"WHERE {' AND '.join(deck_filter_clauses)}" if deck_filter_clauses else ""
                )
                assigned_rows = conn.execute(
                    f"""
                    SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                           quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id
                    FROM deck_cards
                    {deck_filter_sql}
                    ORDER BY id ASC
                    """,
                    deck_params,
                ).fetchall()
                other_deck_cards = [_row_to_deck_card_entry(r) for r in assigned_rows]

        return inventory_cards, other_deck_cards

    def calculate_missing_proxies(
        self,
        deck_cards: list[DeckCardEntry],
        allocate_mode: str = "exclusive",
        current_deck_id: int | None = None,
        include_proxies: bool = True,
    ) -> MissingProxyPlan:
        """Evaluate physical card and proxy availability vs deck requirements (Option C Stock Allocation)."""
        if not deck_cards:
            return MissingProxyPlan()

        inventory_cards, other_deck_cards = self._load_allocation_context(
            include_proxies=include_proxies,
            current_deck_id=current_deck_id,
            allocate_mode=allocate_mode,
        )

        return self.allocation_engine.calculate_missing_proxies(
            deck_cards=deck_cards,
            inventory_cards=inventory_cards,
            other_deck_cards=other_deck_cards,
            allocate_mode=allocate_mode,
            current_deck_id=current_deck_id,
            include_proxies=include_proxies,
        )

    def import_deck_cards_to_inventory(self, deck_id: int, as_proxy: bool = False) -> int:
        """Bulk import all cards from a registered deck directly into the inventory."""
        deck_data = self.get_deck_by_id(deck_id)
        if not deck_data:
            return 0

        _, deck_cards = deck_data
        if not deck_cards:
            return 0

        with get_db(self.db_path) as conn:
            for entry in deck_cards:
                _upsert_card_in_tx(
                    conn,
                    InventoryCard(
                        oracle_id=entry.oracle_id,
                        name=entry.name,
                        set_code=entry.set_code,
                        collector_number=entry.collector_number,
                        quantity=entry.quantity_required,
                        is_foil=False,
                        is_proxy=as_proxy,
                    ),
                    accumulate_quantity=True,
                )

        return sum(c.quantity_required for c in deck_cards)

    def get_inventory_stats(self) -> dict[str, int]:
        """Fetch high-level collection metrics."""
        with get_db(self.db_path) as conn:
            total_cards = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM inventory_cards"
            ).fetchone()[0]
            unique_oracle_ids = conn.execute(
                "SELECT COUNT(DISTINCT oracle_id) FROM inventory_cards WHERE oracle_id != ''"
            ).fetchone()[0]
            proxy_count = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM inventory_cards WHERE is_proxy = 1"
            ).fetchone()[0]
            physical_count = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM inventory_cards WHERE is_proxy = 0"
            ).fetchone()[0]
            deck_count = conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]

        return {
            "total_cards": int(total_cards),
            "unique_oracle_ids": int(unique_oracle_ids),
            "proxy_count": int(proxy_count),
            "physical_count": int(physical_count),
            "deck_count": int(deck_count),
        }

    def get_unique_set_codes(self) -> list[str]:
        """Fetch a sorted list of distinct non-empty set codes in uppercase from the inventory."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT UPPER(set_code) AS sc
                FROM inventory_cards
                WHERE set_code IS NOT NULL AND TRIM(set_code) != ''
                ORDER BY sc ASC
                """
            ).fetchall()
        return [row["sc"] for row in rows if row["sc"]]

    def get_card_deck_usages(self, oracle_id: str = "", card_name: str = "") -> list[dict]:
        """Find all deck usages for a card matching oracle_id or DFC-tolerant card_name."""
        oid = oracle_id.strip()
        cname = card_name.strip()
        if not oid and not cname:
            return []

        sql = """
            SELECT d.id AS deck_id, d.name AS deck_name, d.format AS format,
                   dc.oracle_id, dc.name AS card_name,
                   dc.quantity_required, dc.set_code, dc.collector_number, dc.is_proxy
            FROM deck_cards dc
            JOIN decks d ON d.id = dc.deck_id
            ORDER BY d.name ASC, dc.id ASC
        """

        with get_db(self.db_path) as conn:
            rows = conn.execute(sql).fetchall()

        usages: list[dict] = []
        for row in rows:
            row_oid = (row["oracle_id"] or "").strip()
            row_name = (row["card_name"] or "").strip()
            if oid and cname:
                matched = cards_match(oid, cname, row_oid, row_name)
            elif oid:
                matched = bool(row_oid and row_oid == oid)
            else:
                matched = names_match(cname, row_name)

            if matched:
                usages.append(
                    {
                        "deck_id": row["deck_id"],
                        "deck_name": row["deck_name"],
                        "format": row["format"],
                        "quantity_required": int(row["quantity_required"]),
                        "set_code": row["set_code"],
                        "collector_number": row["collector_number"],
                        "is_proxy": bool(row["is_proxy"]),
                    }
                )
        return usages

    def get_inventory_cards_with_usage(
        self,
        query: str = "",
        filter_proxy: bool | None = None,
        filter_foil: bool | None = None,
        filter_usage: str = "all",
        filter_set: str = "",
        filter_rarity: str = "",
        include_proxies: bool = True,
        sort_by: str | None = None,
        sort_descending: bool = False,
    ) -> list[tuple[InventoryCard, int, int]]:
        """Fetch inventory cards along with dynamic in-deck usage, filtering, and natural sorting."""
        all_cards = self.get_inventory_cards()

        with get_db(self.db_path) as conn:
            deck_card_rows = conn.execute(
                """
                SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                       quantity_required, is_proxy, physical_quantity, proxy_quantity, fulfilled_card_id
                FROM deck_cards
                ORDER BY id ASC
                """
            ).fetchall()

        all_deck_cards = [_row_to_deck_card_entry(dc) for dc in deck_card_rows]

        usage_by_card_id, _ = self.allocation_engine.calculate_inventory_usage(
            all_cards, all_deck_cards, include_proxies=include_proxies
        )

        results: list[tuple[InventoryCard, int, int]] = []
        clean_query = query.strip().lower()
        norm_query = normalize_card_name(query)
        clean_usage = normalize_usage_filter(filter_usage)
        clean_set = normalize_set_filter(filter_set)
        clean_rarity = normalize_rarity_filter(filter_rarity)

        for card in all_cards:
            card_id = card.id
            in_decks_qty = usage_by_card_id.get(card_id, 0) if card_id is not None else 0
            available_qty = max(0, card.quantity - in_decks_qty)

            if filter_proxy is not None and card.is_proxy != filter_proxy:
                continue

            if filter_foil is not None and card.is_foil != filter_foil:
                continue

            if clean_query:
                q_matches = (
                    clean_query in card.name.lower()
                    or (norm_query and norm_query in normalize_card_name(card.name))
                    or clean_query in card.set_code.lower()
                    or clean_query in card.oracle_id.lower()
                    or clean_query in card.collector_number.lower()
                )
                if not q_matches:
                    continue

            if clean_set and card.set_code.strip().lower() != clean_set:
                continue

            if clean_rarity:
                card_rarity = normalize_rarity_filter(str(card.metadata.get("rarity", "")))
                if card_rarity != clean_rarity:
                    continue

            if clean_usage == "available" and available_qty <= 0:
                continue
            if clean_usage == "in_decks" and in_decks_qty <= 0:
                continue

            results.append((card, in_decks_qty, available_qty))

        if sort_by:
            results.sort(
                key=lambda item: card_usage_sort_key(item, sort_by),
                reverse=sort_descending,
            )

        return results

    def analyze_deck_card_matches(
        self,
        deck_cards: list[DeckCardEntry],
        current_deck_id: int | None = None,
        allocate_mode: str = "exclusive",
        include_proxies: bool = True,
    ) -> tuple[list[DeckCardMatchDetail], int, int, int]:
        """Analyze physical and proxy card matches for deck cards respecting stock allocation."""
        if not deck_cards:
            return [], 0, 0, 0

        inventory_cards, other_deck_cards = self._load_allocation_context(
            include_proxies=include_proxies,
            current_deck_id=current_deck_id,
            allocate_mode=allocate_mode,
        )

        return self.allocation_engine.analyze_deck_card_matches(
            deck_cards=deck_cards,
            inventory_cards=inventory_cards,
            other_deck_cards=other_deck_cards,
            allocate_mode=allocate_mode,
            current_deck_id=current_deck_id,
            include_proxies=include_proxies,
        )
