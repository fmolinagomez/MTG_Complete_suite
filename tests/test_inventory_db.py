"""Tests for SQLite database initialization, configuration, and constraints."""

from pathlib import Path

import pytest

from src.inventory.db import get_db, get_db_connection, init_db


class TestInventoryDB:
    def test_init_db_creates_tables_and_indices(self, tmp_path: Path):
        db_file = tmp_path / "test_inventory.db"
        result_path = init_db(db_file)
        assert result_path == db_file
        assert db_file.exists()

        with get_db(db_file) as conn:
            # Verify tables exist
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            assert "inventory_cards" in tables
            assert "decks" in tables
            assert "deck_cards" in tables

            # Verify indices exist
            indices = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
                ).fetchall()
            ]
            assert "idx_inventory_oracle_id" in indices
            assert "idx_inventory_name" in indices
            assert "idx_inventory_set_code" in indices
            assert "idx_inventory_composite" in indices
            assert "idx_decks_source_url" in indices
            assert "idx_decks_name" in indices
            assert "idx_deck_cards_deck_id" in indices
            assert "idx_deck_cards_oracle_id" in indices
            assert "idx_deck_cards_name" in indices

    def test_connection_pragmas(self, tmp_path: Path):
        db_file = tmp_path / "nested" / "dir" / "test.db"
        init_db(db_file)

        conn = get_db_connection(db_file)
        try:
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            assert fk == 1

            journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert journal.lower() == "wal"
        finally:
            conn.close()

    def test_transaction_commit_and_rollback(self, tmp_path: Path):
        db_file = tmp_path / "test_tx.db"
        init_db(db_file)

        # Successful commit
        with get_db(db_file) as conn:
            conn.execute(
                """
                INSERT INTO inventory_cards (oracle_id, name, set_code, collector_number, quantity)
                VALUES ('orc-1', 'Sol Ring', 'c21', '263', 2)
                """
            )

        with get_db(db_file) as conn:
            count = conn.execute("SELECT COUNT(*) FROM inventory_cards").fetchone()[0]
            assert count == 1

        # Rollback on exception
        with pytest.raises(RuntimeError):
            with get_db(db_file) as conn:
                conn.execute(
                    """
                    INSERT INTO inventory_cards (oracle_id, name, set_code, collector_number, quantity)
                    VALUES ('orc-2', 'Mana Crypt', '2xm', '270', 1)
                    """
                )
                raise RuntimeError("Simulated crash")

        with get_db(db_file) as conn:
            count = conn.execute("SELECT COUNT(*) FROM inventory_cards").fetchone()[0]
            assert count == 1  # Mana Crypt rolled back

    def test_foreign_key_cascade_delete(self, tmp_path: Path):
        db_file = tmp_path / "test_cascade.db"
        init_db(db_file)

        with get_db(db_file) as conn:
            cursor = conn.execute(
                "INSERT INTO decks (name, created_at) VALUES ('Urza Commander', '2026-01-01T00:00:00Z')"
            )
            deck_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO deck_cards (deck_id, oracle_id, name, set_code, collector_number, quantity_required)
                VALUES (?, 'orc-1', 'Sol Ring', 'c21', '263', 1)
                """,
                (deck_id,),
            )

        # Confirm insertion
        with get_db(db_file) as conn:
            deck_card_count = conn.execute("SELECT COUNT(*) FROM deck_cards WHERE deck_id = ?", (deck_id,)).fetchone()[0]
            assert deck_card_count == 1

            # Delete deck
            conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))

        # Confirm cascade deletion of deck_cards
        with get_db(db_file) as conn:
            remaining_cards = conn.execute("SELECT COUNT(*) FROM deck_cards WHERE deck_id = ?", (deck_id,)).fetchone()[0]
            assert remaining_cards == 0
