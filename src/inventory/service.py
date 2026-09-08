"""Inventory Service orchestrating database operations, imports, and proxy planning."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.inventory.allocation_engine import StockAllocationEngine
from src.inventory.db import DEFAULT_DB_PATH, get_db, init_db
from src.inventory.manabox_csv import import_parsed_cards, parse_manabox_csv
from src.inventory.models import (
    DeckCardEntry,
    DeckCardMatchDetail,
    DeckRecord,
    InventoryCard,
    MissingProxyPlan,
)


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

    def import_manabox_csv(self, csv_path: Path | str, mode: str = "merge") -> int:
        """Parse and import a ManaBox CSV export into the collection."""
        cards = parse_manabox_csv(csv_path)
        return import_parsed_cards(cards, db_path=self.db_path, mode=mode)

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
            clauses.append("(name LIKE ? OR set_code LIKE ? OR oracle_id LIKE ? OR collector_number LIKE ?)")
            param_str = f"%{clean_query}%"
            params.extend([param_str, param_str, param_str, param_str])

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        sql += " ORDER BY name ASC, set_code ASC, collector_number ASC, is_foil ASC"

        with get_db(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            InventoryCard(
                id=row["id"],
                oracle_id=row["oracle_id"],
                name=row["name"],
                set_code=row["set_code"],
                collector_number=row["collector_number"],
                quantity=row["quantity"],
                is_foil=bool(row["is_foil"]),
                is_proxy=bool(row["is_proxy"]),
                condition=row["condition"],
                language=row["language"],
                metadata_json=row["metadata_json"],
            )
            for row in rows
        ]

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

        if not row:
            return None

        return InventoryCard(
            id=row["id"],
            oracle_id=row["oracle_id"],
            name=row["name"],
            set_code=row["set_code"],
            collector_number=row["collector_number"],
            quantity=row["quantity"],
            is_foil=bool(row["is_foil"]),
            is_proxy=bool(row["is_proxy"]),
            condition=row["condition"],
            language=row["language"],
            metadata_json=row["metadata_json"],
        )

    def add_or_update_card(self, card: InventoryCard) -> int:
        """Insert or update a card in the inventory database."""
        with get_db(self.db_path) as conn:
            if card.id is not None:
                existing = conn.execute("SELECT id FROM inventory_cards WHERE id = ?", (card.id,)).fetchone()
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

            # If no ID provided, check for existing identical print and finish
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
                new_qty = row["quantity"] + card.quantity
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

    def delete_card(self, card_id: int) -> bool:
        """Delete an inventory card by ID."""
        with get_db(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM inventory_cards WHERE id = ?", (card_id,))
            return cursor.rowcount > 0

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

        return [
            DeckRecord(
                id=row["id"],
                name=row["name"],
                source_url=row["source_url"],
                format=row["format"],
                created_at=row["created_at"],
                notes=row["notes"],
            )
            for row in rows
        ]

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

            deck = DeckRecord(
                id=deck_row["id"],
                name=deck_row["name"],
                source_url=deck_row["source_url"],
                format=deck_row["format"],
                created_at=deck_row["created_at"],
                notes=deck_row["notes"],
            )

            card_rows = conn.execute(
                """
                SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                       quantity_required, is_proxy
                FROM deck_cards
                WHERE deck_id = ?
                ORDER BY name ASC, set_code ASC
                """,
                (deck_id,),
            ).fetchall()

            cards = [
                DeckCardEntry(
                    id=row["id"],
                    deck_id=row["deck_id"],
                    oracle_id=row["oracle_id"],
                    name=row["name"],
                    set_code=row["set_code"],
                    collector_number=row["collector_number"],
                    quantity_required=row["quantity_required"],
                    is_proxy=bool(row["is_proxy"]),
                )
                for row in card_rows
            ]

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
                        quantity_required, is_proxy
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deck_id,
                        entry.oracle_id,
                        entry.name,
                        entry.set_code,
                        entry.collector_number,
                        entry.quantity_required,
                        1 if entry.is_proxy else 0,
                    ),
                )

        return deck_id

    def delete_deck(self, deck_id: int) -> bool:
        """Delete a deck and all its card associations (cascade)."""
        with get_db(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
            return cursor.rowcount > 0

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

        with get_db(self.db_path) as conn:
            where_sql = "WHERE quantity > 0" if include_proxies else "WHERE is_proxy = 0 AND quantity > 0"
            rows = conn.execute(
                f"""
                SELECT id, oracle_id, name, set_code, collector_number, quantity,
                       is_foil, is_proxy, condition, language, metadata_json
                FROM inventory_cards
                {where_sql}
                """
            ).fetchall()
            inventory_cards = [
                InventoryCard(
                    id=row["id"],
                    oracle_id=row["oracle_id"],
                    name=row["name"],
                    set_code=row["set_code"],
                    collector_number=row["collector_number"],
                    quantity=row["quantity"],
                    is_foil=bool(row["is_foil"]),
                    is_proxy=bool(row["is_proxy"]),
                    condition=row["condition"],
                    language=row["language"],
                    metadata_json=row["metadata_json"],
                )
                for row in rows
            ]

            other_deck_cards: list[DeckCardEntry] = []
            if allocate_mode == "exclusive":
                deck_filter_clauses: list[str] = []
                deck_params: list[object] = []
                if not include_proxies:
                    deck_filter_clauses.append("is_proxy = 0")
                if current_deck_id is not None:
                    deck_filter_clauses.append("deck_id != ?")
                    deck_params.append(current_deck_id)

                deck_filter_sql = f"WHERE {' AND '.join(deck_filter_clauses)}" if deck_filter_clauses else ""

                assigned_rows = conn.execute(
                    f"""
                    SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                           quantity_required, is_proxy
                    FROM deck_cards
                    {deck_filter_sql}
                    """,
                    deck_params,
                ).fetchall()
                other_deck_cards = [
                    DeckCardEntry(
                        id=r["id"],
                        deck_id=r["deck_id"],
                        oracle_id=r["oracle_id"],
                        name=r["name"],
                        set_code=r["set_code"],
                        collector_number=r["collector_number"],
                        quantity_required=r["quantity_required"],
                        is_proxy=bool(r["is_proxy"]),
                    )
                    for r in assigned_rows
                ]

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
                row = conn.execute(
                    """
                    SELECT id, quantity FROM inventory_cards
                    WHERE set_code = ? AND collector_number = ? AND is_foil = 0 AND is_proxy = ?
                    """,
                    (
                        entry.set_code,
                        entry.collector_number,
                        1 if as_proxy else 0,
                    ),
                ).fetchone()

                if row:
                    conn.execute(
                        "UPDATE inventory_cards SET quantity = quantity + ? WHERE id = ?",
                        (entry.quantity_required, row["id"]),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO inventory_cards (
                            oracle_id, name, set_code, collector_number, quantity,
                            is_foil, is_proxy, condition, language, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, 'NM', 'en', '{}')
                        """,
                        (
                            entry.oracle_id,
                            entry.name,
                            entry.set_code,
                            entry.collector_number,
                            entry.quantity_required,
                            1 if as_proxy else 0,
                        ),
                    )

        return sum(c.quantity_required for c in deck_cards)

    def get_inventory_stats(self) -> dict[str, int]:
        """Fetch high-level collection metrics."""
        with get_db(self.db_path) as conn:
            total_cards = conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM inventory_cards").fetchone()[0]
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
        """Find all deck usages for a card matching oracle_id or card_name."""
        oid = oracle_id.strip()
        cname = card_name.strip()
        if not oid and not cname:
            return []

        clauses: list[str] = []
        params: list[object] = []

        if oid and cname:
            clauses.append("(dc.oracle_id = ? OR LOWER(dc.name) = LOWER(?))")
            params.extend([oid, cname])
        elif oid:
            clauses.append("dc.oracle_id = ?")
            params.append(oid)
        elif cname:
            clauses.append("LOWER(dc.name) = LOWER(?)")
            params.append(cname)

        sql = f"""
            SELECT d.id AS deck_id, d.name AS deck_name, d.format AS format,
                   dc.quantity_required, dc.set_code, dc.collector_number, dc.is_proxy
            FROM deck_cards dc
            JOIN decks d ON d.id = dc.deck_id
            WHERE {" AND ".join(clauses)}
            ORDER BY d.name ASC, dc.id ASC
        """

        with get_db(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            {
                "deck_id": row["deck_id"],
                "deck_name": row["deck_name"],
                "format": row["format"],
                "quantity_required": int(row["quantity_required"]),
                "set_code": row["set_code"],
                "collector_number": row["collector_number"],
                "is_proxy": bool(row["is_proxy"]),
            }
            for row in rows
        ]

    def get_inventory_cards_with_usage(
        self,
        query: str = "",
        filter_proxy: bool | None = None,
        filter_usage: str = "all",
        filter_set: str = "",
        filter_rarity: str = "",
        include_proxies: bool = True,
    ) -> list[tuple[InventoryCard, int, int]]:
        """Fetch inventory cards along with dynamic in-deck usage and available quantity."""
        all_cards = self.get_inventory_cards()

        with get_db(self.db_path) as conn:
            deck_card_rows = conn.execute(
                """
                SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                       quantity_required, is_proxy
                FROM deck_cards
                ORDER BY id ASC
                """
            ).fetchall()

        all_deck_cards = [
            DeckCardEntry(
                id=dc["id"],
                deck_id=dc["deck_id"],
                oracle_id=dc["oracle_id"],
                name=dc["name"],
                set_code=dc["set_code"],
                collector_number=dc["collector_number"],
                quantity_required=dc["quantity_required"],
                is_proxy=bool(dc["is_proxy"]),
            )
            for dc in deck_card_rows
        ]

        usage_by_card_id, avail_by_card_id = self.allocation_engine.calculate_inventory_usage(
            all_cards, all_deck_cards, include_proxies=include_proxies
        )

        results: list[tuple[InventoryCard, int, int]] = []
        clean_query = query.strip().lower()
        clean_set = filter_set.strip().lower()
        clean_rarity = filter_rarity.strip().lower()

        for card in all_cards:
            card_id = card.id
            in_decks_qty = usage_by_card_id.get(card_id, 0) if card_id is not None else 0
            available_qty = max(0, card.quantity - in_decks_qty)

            if filter_proxy is not None and card.is_proxy != filter_proxy:
                continue

            if clean_query:
                q_matches = (
                    clean_query in card.name.lower()
                    or clean_query in card.set_code.lower()
                    or clean_query in card.oracle_id.lower()
                    or clean_query in card.collector_number.lower()
                )
                if not q_matches:
                    continue

            if clean_set and card.set_code.strip().lower() != clean_set:
                continue

            if clean_rarity:
                card_rarity = str(card.metadata.get("rarity", "")).strip().lower()
                if card_rarity != clean_rarity:
                    continue

            if filter_usage == "available" and available_qty <= 0:
                continue
            if filter_usage == "in_decks" and in_decks_qty <= 0:
                continue

            results.append((card, in_decks_qty, available_qty))

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

        with get_db(self.db_path) as conn:
            where_sql = "WHERE quantity > 0" if include_proxies else "WHERE is_proxy = 0 AND quantity > 0"
            rows = conn.execute(
                f"""
                SELECT id, oracle_id, name, set_code, collector_number, quantity,
                       is_foil, is_proxy, condition, language, metadata_json
                FROM inventory_cards
                {where_sql}
                """
            ).fetchall()
            inventory_cards = [
                InventoryCard(
                    id=row["id"],
                    oracle_id=row["oracle_id"],
                    name=row["name"],
                    set_code=row["set_code"],
                    collector_number=row["collector_number"],
                    quantity=row["quantity"],
                    is_foil=bool(row["is_foil"]),
                    is_proxy=bool(row["is_proxy"]),
                    condition=row["condition"],
                    language=row["language"],
                    metadata_json=row["metadata_json"],
                )
                for row in rows
            ]

            # Deduct cards allocated to other registered decks
            deck_filter_clauses: list[str] = []
            deck_params: list[object] = []
            if not include_proxies:
                deck_filter_clauses.append("is_proxy = 0")
            if current_deck_id is not None:
                deck_filter_clauses.append("deck_id != ?")
                deck_params.append(current_deck_id)

            deck_filter_sql = f"WHERE {' AND '.join(deck_filter_clauses)}" if deck_filter_clauses else ""

            assigned_rows = conn.execute(
                f"""
                SELECT id, deck_id, oracle_id, name, set_code, collector_number,
                       quantity_required, is_proxy
                FROM deck_cards
                {deck_filter_sql}
                ORDER BY id ASC
                """,
                deck_params,
            ).fetchall()
            other_deck_cards = [
                DeckCardEntry(
                    id=r["id"],
                    deck_id=r["deck_id"],
                    oracle_id=r["oracle_id"],
                    name=r["name"],
                    set_code=r["set_code"],
                    collector_number=r["collector_number"],
                    quantity_required=r["quantity_required"],
                    is_proxy=bool(r["is_proxy"]),
                )
                for r in assigned_rows
            ]

        return self.allocation_engine.analyze_deck_card_matches(
            deck_cards=deck_cards,
            inventory_cards=inventory_cards,
            other_deck_cards=other_deck_cards,
            allocate_mode=allocate_mode,
            current_deck_id=current_deck_id,
            include_proxies=include_proxies,
        )

