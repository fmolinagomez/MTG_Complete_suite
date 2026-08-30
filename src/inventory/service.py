"""Inventory Service orchestrating database operations, imports, and proxy planning."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        init_db(self.db_path)

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
    ) -> MissingProxyPlan:
        """Evaluate physical card availability vs deck requirements (Option C Stock Allocation)."""
        plan = MissingProxyPlan()
        if not deck_cards:
            return plan

        with get_db(self.db_path) as conn:
            # Query physical collection totals per oracle_id and card name
            inventory_rows = conn.execute(
                """
                SELECT oracle_id, name, SUM(quantity) as total_physical
                FROM inventory_cards
                WHERE is_proxy = 0
                GROUP BY oracle_id, name
                """
            ).fetchall()

            # Map stock by oracle_id and fallback by normalized name
            stock_by_oracle: dict[str, int] = {}
            stock_by_name: dict[str, int] = {}
            for row in inventory_rows:
                qty = int(row["total_physical"] or 0)
                oid = row["oracle_id"].strip()
                cname = row["name"].strip().lower()
                if oid:
                    stock_by_oracle[oid] = stock_by_oracle.get(oid, 0) + qty
                if cname:
                    stock_by_name[cname] = stock_by_name.get(cname, 0) + qty

            if allocate_mode == "exclusive":
                # Subtract physical cards already assigned to other registered decks
                deck_filter_sql = "WHERE is_proxy = 0"
                deck_params: list[object] = []
                if current_deck_id is not None:
                    deck_filter_sql += " AND deck_id != ?"
                    deck_params.append(current_deck_id)

                assigned_rows = conn.execute(
                    f"""
                    SELECT oracle_id, name, SUM(quantity_required) as total_assigned
                    FROM deck_cards
                    {deck_filter_sql}
                    GROUP BY oracle_id, name
                    """,
                    deck_params,
                ).fetchall()

                for row in assigned_rows:
                    assigned_qty = int(row["total_assigned"] or 0)
                    oid = row["oracle_id"].strip()
                    cname = row["name"].strip().lower()
                    if oid in stock_by_oracle:
                        stock_by_oracle[oid] = max(0, stock_by_oracle[oid] - assigned_qty)
                    if cname in stock_by_name:
                        stock_by_name[cname] = max(0, stock_by_name[cname] - assigned_qty)

        # Allocate stock for the requested deck cards
        owned_list: list[DeckCardEntry] = []
        missing_list: list[DeckCardEntry] = []

        for entry in deck_cards:
            needed = entry.quantity_required
            oid = entry.oracle_id.strip()
            cname = entry.name.strip().lower()

            available = 0
            match_key = ""
            is_oracle_match = False

            if oid and oid in stock_by_oracle:
                available = stock_by_oracle[oid]
                match_key = oid
                is_oracle_match = True
            elif cname and cname in stock_by_name:
                available = stock_by_name[cname]
                match_key = cname
                is_oracle_match = False

            owned_qty = min(needed, available)
            missing_qty = needed - owned_qty

            if is_oracle_match and match_key:
                stock_by_oracle[match_key] -= owned_qty
                if cname in stock_by_name:
                    stock_by_name[cname] = max(0, stock_by_name[cname] - owned_qty)
            elif match_key:
                stock_by_name[match_key] -= owned_qty

            if owned_qty > 0:
                owned_list.append(
                    DeckCardEntry(
                        id=entry.id,
                        deck_id=entry.deck_id,
                        oracle_id=entry.oracle_id,
                        name=entry.name,
                        set_code=entry.set_code,
                        collector_number=entry.collector_number,
                        quantity_required=owned_qty,
                        is_proxy=False,
                    )
                )

            if missing_qty > 0:
                missing_list.append(
                    DeckCardEntry(
                        id=entry.id,
                        deck_id=entry.deck_id,
                        oracle_id=entry.oracle_id,
                        name=entry.name,
                        set_code=entry.set_code,
                        collector_number=entry.collector_number,
                        quantity_required=missing_qty,
                        is_proxy=True,
                    )
                )

        plan.owned_cards = owned_list
        plan.missing_cards = missing_list
        plan.total_required = sum(c.quantity_required for c in deck_cards)
        plan.total_owned = sum(c.quantity_required for c in owned_list)
        plan.total_missing = sum(c.quantity_required for c in missing_list)
        return plan

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

        usage_by_card_id: dict[int, int] = {card.id: 0 for card in all_cards if card.id is not None}
        avail_by_card_id: dict[int, int] = {card.id: card.quantity for card in all_cards if card.id is not None}

        for dc in deck_card_rows:
            dc_qty = int(dc["quantity_required"])
            dc_proxy = bool(dc["is_proxy"])
            dc_sc = (dc["set_code"] or "").strip().lower()
            dc_num = (dc["collector_number"] or "").strip().lower()
            dc_oid = (dc["oracle_id"] or "").strip()
            dc_name = (dc["name"] or "").strip().lower()

            # First pass: exact print match
            for card in all_cards:
                if dc_qty <= 0:
                    break
                if card.id is None or card.is_proxy != dc_proxy:
                    continue
                c_sc = card.set_code.strip().lower()
                c_num = card.collector_number.strip().lower()
                if c_sc == dc_sc and c_num == dc_num:
                    avail = avail_by_card_id[card.id]
                    if avail > 0:
                        take = min(dc_qty, avail)
                        avail_by_card_id[card.id] -= take
                        usage_by_card_id[card.id] += take
                        dc_qty -= take

            # Second pass: alt print match
            if dc_qty > 0:
                for card in all_cards:
                    if dc_qty <= 0:
                        break
                    if card.id is None or card.is_proxy != dc_proxy:
                        continue
                    matches = (dc_oid and card.oracle_id.strip() == dc_oid) or (
                        dc_name and card.name.strip().lower() == dc_name
                    )
                    if matches:
                        avail = avail_by_card_id[card.id]
                        if avail > 0:
                            take = min(dc_qty, avail)
                            avail_by_card_id[card.id] -= take
                            usage_by_card_id[card.id] += take
                            dc_qty -= take

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
    ) -> tuple[list[DeckCardMatchDetail], int, int, int]:
        """Analyze physical card matches for deck cards respecting Option C stock allocation."""
        if not deck_cards:
            return [], 0, 0, 0

        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, oracle_id, name, set_code, collector_number, quantity
                FROM inventory_cards
                WHERE is_proxy = 0 AND quantity > 0
                """
            ).fetchall()

            stock_pool = [
                {
                    "id": row["id"],
                    "oracle_id": (row["oracle_id"] or "").strip(),
                    "name": (row["name"] or "").strip(),
                    "set_code": (row["set_code"] or "").strip().lower(),
                    "collector_number": (row["collector_number"] or "").strip().lower(),
                    "quantity": int(row["quantity"]),
                }
                for row in rows
            ]

            # Deduct physical cards allocated to other registered decks
            deck_filter_sql = "WHERE is_proxy = 0"
            deck_params: list[object] = []
            if current_deck_id is not None:
                deck_filter_sql += " AND deck_id != ?"
                deck_params.append(current_deck_id)

            other_deck_cards = conn.execute(
                f"""
                SELECT oracle_id, name, set_code, collector_number, quantity_required
                FROM deck_cards
                {deck_filter_sql}
                ORDER BY id ASC
                """,
                deck_params,
            ).fetchall()

            for assigned in other_deck_cards:
                qty_to_deduct = int(assigned["quantity_required"])
                assigned_sc = (assigned["set_code"] or "").strip().lower()
                assigned_num = (assigned["collector_number"] or "").strip().lower()
                assigned_oid = (assigned["oracle_id"] or "").strip()
                assigned_name = (assigned["name"] or "").strip().lower()

                # Exact match deduction first
                for item in stock_pool:
                    if qty_to_deduct <= 0:
                        break
                    if item["quantity"] > 0 and item["set_code"] == assigned_sc and item["collector_number"] == assigned_num:
                        take = min(qty_to_deduct, item["quantity"])
                        item["quantity"] -= take
                        qty_to_deduct -= take

                # Alt match deduction
                if qty_to_deduct > 0:
                    for item in stock_pool:
                        if qty_to_deduct <= 0:
                            break
                        if item["quantity"] > 0:
                            matches = (assigned_oid and item["oracle_id"] == assigned_oid) or (
                                assigned_name and item["name"].lower() == assigned_name
                            )
                            if matches:
                                take = min(qty_to_deduct, item["quantity"])
                                item["quantity"] -= take
                                qty_to_deduct -= take

        details_list: list[DeckCardMatchDetail] = []
        total_exact = 0
        total_alt = 0
        total_missing = 0

        for entry in deck_cards:
            needed = entry.quantity_required
            entry_sc = entry.set_code.strip().lower()
            entry_num = entry.collector_number.strip().lower()
            entry_oid = entry.oracle_id.strip()
            entry_name = entry.name.strip().lower()

            exact_stock = 0
            alt_stock = 0

            for item in stock_pool:
                if item["quantity"] <= 0:
                    continue
                matches_card = (entry_oid and item["oracle_id"] == entry_oid) or (
                    entry_name and item["name"].lower() == entry_name
                )
                if not matches_card:
                    continue
                if item["set_code"] == entry_sc and item["collector_number"] == entry_num:
                    exact_stock += item["quantity"]
                else:
                    alt_stock += item["quantity"]

            if exact_stock >= needed:
                status = "exact"
            elif (exact_stock + alt_stock) >= needed and exact_stock == 0:
                status = "alternative"
            elif (exact_stock + alt_stock) > 0:
                status = "partial"
            else:
                status = "missing"

            exact_fulfilled = min(needed, exact_stock)
            rem_needed = needed - exact_fulfilled
            alt_fulfilled = min(rem_needed, alt_stock)
            missing = rem_needed - alt_fulfilled

            # Deduct allocated stock from pool
            deduct_exact = exact_fulfilled
            if deduct_exact > 0:
                for item in stock_pool:
                    if deduct_exact <= 0:
                        break
                    matches_card = (entry_oid and item["oracle_id"] == entry_oid) or (
                        entry_name and item["name"].lower() == entry_name
                    )
                    if (
                        matches_card
                        and item["set_code"] == entry_sc
                        and item["collector_number"] == entry_num
                        and item["quantity"] > 0
                    ):
                        take = min(deduct_exact, item["quantity"])
                        item["quantity"] -= take
                        deduct_exact -= take

            deduct_alt = alt_fulfilled
            if deduct_alt > 0:
                for item in stock_pool:
                    if deduct_alt <= 0:
                        break
                    matches_card = (entry_oid and item["oracle_id"] == entry_oid) or (
                        entry_name and item["name"].lower() == entry_name
                    )
                    is_exact = item["set_code"] == entry_sc and item["collector_number"] == entry_num
                    if matches_card and not is_exact and item["quantity"] > 0:
                        take = min(deduct_alt, item["quantity"])
                        item["quantity"] -= take
                        deduct_alt -= take

            details_list.append(
                DeckCardMatchDetail(
                    name=entry.name,
                    set_code=entry.set_code,
                    collector_number=entry.collector_number,
                    quantity_required=entry.quantity_required,
                    exact_stock=exact_stock,
                    alt_stock=alt_stock,
                    status=status,
                    oracle_id=entry.oracle_id,
                    deck_card_id=entry.id,
                )
            )

            total_exact += exact_fulfilled
            total_alt += alt_fulfilled
            total_missing += missing

        return details_list, total_exact, total_alt, total_missing

