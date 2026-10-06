"""Database initialization and connection management for Card Inventory."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path("MPCFillToPDF/inventario.db")


def get_db_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create and configure a SQLite connection."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def get_db(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager for thread-safe database access with auto-commit and rollback."""
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> Path:
    """Initialize SQLite database schema and indices if they do not exist."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with get_db(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS inventory_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                oracle_id TEXT NOT NULL,
                name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                collector_number TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                is_foil BOOLEAN NOT NULL DEFAULT 0,
                is_proxy BOOLEAN NOT NULL DEFAULT 0,
                condition TEXT NOT NULL DEFAULT 'NM',
                language TEXT NOT NULL DEFAULT 'en',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_inventory_oracle_id ON inventory_cards(oracle_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory_cards(name);
            CREATE INDEX IF NOT EXISTS idx_inventory_set_code ON inventory_cards(set_code);
            CREATE INDEX IF NOT EXISTS idx_inventory_composite ON inventory_cards(oracle_id, set_code, collector_number, is_foil, is_proxy);

            CREATE TABLE IF NOT EXISTS decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                format TEXT NOT NULL DEFAULT 'Commander',
                created_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_decks_source_url ON decks(source_url);
            CREATE INDEX IF NOT EXISTS idx_decks_name ON decks(name);

            CREATE TABLE IF NOT EXISTS deck_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
                oracle_id TEXT NOT NULL,
                name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                collector_number TEXT NOT NULL,
                quantity_required INTEGER NOT NULL DEFAULT 1,
                is_proxy BOOLEAN NOT NULL DEFAULT 0,
                physical_quantity INTEGER NOT NULL DEFAULT 0,
                proxy_quantity INTEGER NOT NULL DEFAULT 0,
                fulfilled_card_id INTEGER REFERENCES inventory_cards(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_deck_cards_deck_id ON deck_cards(deck_id);
            CREATE INDEX IF NOT EXISTS idx_deck_cards_oracle_id ON deck_cards(oracle_id);
            CREATE INDEX IF NOT EXISTS idx_deck_cards_name ON deck_cards(name);
            """
        )

        # Automated schema migration for existing databases
        cursor = conn.execute("PRAGMA table_info(deck_cards)")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if "physical_quantity" not in existing_cols:
            conn.execute(
                "ALTER TABLE deck_cards ADD COLUMN physical_quantity INTEGER NOT NULL DEFAULT 0"
            )
        if "proxy_quantity" not in existing_cols:
            conn.execute(
                "ALTER TABLE deck_cards ADD COLUMN proxy_quantity INTEGER NOT NULL DEFAULT 0"
            )
        if "fulfilled_card_id" not in existing_cols:
            conn.execute(
                "ALTER TABLE deck_cards ADD COLUMN fulfilled_card_id INTEGER REFERENCES inventory_cards(id) ON DELETE SET NULL"
            )

        # Backfill existing rows that have default 0 for both physical and proxy
        conn.execute(
            """
            UPDATE deck_cards
            SET proxy_quantity = quantity_required, physical_quantity = 0
            WHERE is_proxy = 1 AND physical_quantity = 0 AND proxy_quantity = 0
            """
        )
        conn.execute(
            """
            UPDATE deck_cards
            SET physical_quantity = quantity_required, proxy_quantity = 0
            WHERE is_proxy = 0 AND physical_quantity = 0 AND proxy_quantity = 0
            """
        )
    return path
