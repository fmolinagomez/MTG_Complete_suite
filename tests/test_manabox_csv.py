"""Tests for ManaBox CSV parsing and import modes."""

import io
from pathlib import Path

import pytest

from src.inventory.db import get_db, init_db
from src.inventory.manabox_csv import import_parsed_cards, parse_manabox_csv

SAMPLE_MANABOX_CSV = """Binder Name,Binder Type,Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,Purchase price currency
Commander Binder,General,Sol Ring,c21,Commander 2021,263,normal,uncommon,2,1001,894b9f29-1941-4545-9854-c917d096c4a8,1.50,false,false,NM,en,USD
Commander Binder,General,Demonic Tutor,uma,Ultimate Masters,93,foil,rare,1,1002,3bdbc031-5316-4edd-a60e-d866409bc673,35.00,false,false,LP,en,USD
Cube,General,Black Lotus,lea,Limited Edition Alpha,232,normal,rare,1,1003,d46fa010-4303-4713-a117-0da630ec5544,10000.00,false,false,MP,en,USD
"""


class TestManaBoxCSV:
    def test_parse_standard_csv(self):
        cards = parse_manabox_csv(SAMPLE_MANABOX_CSV)
        assert len(cards) == 3

        sol_ring = cards[0]
        assert sol_ring.name == "Sol Ring"
        assert sol_ring.set_code == "c21"
        assert sol_ring.collector_number == "263"
        assert sol_ring.quantity == 2
        assert sol_ring.is_foil is False
        assert sol_ring.is_proxy is False
        assert sol_ring.condition == "NM"
        assert sol_ring.language == "en"
        assert sol_ring.oracle_id == "894b9f29-1941-4545-9854-c917d096c4a8"
        assert sol_ring.metadata["binder_name"] == "Commander Binder"
        assert sol_ring.metadata["rarity"] == "uncommon"
        assert sol_ring.metadata["purchase_price"] == "1.50"

        tutor = cards[1]
        assert tutor.name == "Demonic Tutor"
        assert tutor.set_code == "uma"
        assert tutor.collector_number == "93"
        assert tutor.quantity == 1
        assert tutor.is_foil is True
        assert tutor.condition == "LP"

    def test_parse_csv_file_with_bom(self, tmp_path: Path):
        csv_file = tmp_path / "manabox_bom.csv"
        csv_file.write_bytes("\ufeff".encode("utf-8") + SAMPLE_MANABOX_CSV.encode("utf-8"))

        cards = parse_manabox_csv(csv_file)
        assert len(cards) == 3
        assert cards[0].name == "Sol Ring"

    def test_parse_empty_or_whitespace_csv(self):
        assert parse_manabox_csv("") == []
        assert parse_manabox_csv("   \n\n  ") == []
        assert parse_manabox_csv(io.StringIO("")) == []

    def test_import_replace_mode(self, tmp_path: Path):
        db_file = tmp_path / "test_replace.db"
        init_db(db_file)

        # Pre-populate database with an existing physical card and a proxy
        with get_db(db_file) as conn:
            conn.execute(
                """
                INSERT INTO inventory_cards (oracle_id, name, set_code, collector_number, quantity, is_proxy)
                VALUES ('orc-old', 'Old Physical Card', 'm20', '1', 4, 0)
                """
            )
            conn.execute(
                """
                INSERT INTO inventory_cards (oracle_id, name, set_code, collector_number, quantity, is_proxy)
                VALUES ('orc-proxy', 'Proxy Card', 'proxy_set', '99', 1, 1)
                """
            )

        parsed_cards = parse_manabox_csv(SAMPLE_MANABOX_CSV)
        total_imported = import_parsed_cards(parsed_cards, db_path=db_file, mode="replace")

        # Total quantity = 2 (Sol Ring) + 1 (Tutor) + 1 (Lotus) = 4
        assert total_imported == 4

        with get_db(db_file) as conn:
            # Physical old card should be wiped, proxy preserved
            old_card = conn.execute("SELECT * FROM inventory_cards WHERE name = 'Old Physical Card'").fetchone()
            assert old_card is None

            proxy_card = conn.execute("SELECT * FROM inventory_cards WHERE name = 'Proxy Card'").fetchone()
            assert proxy_card is not None
            assert proxy_card["is_proxy"] == 1

            # New cards exist
            sol_ring = conn.execute("SELECT * FROM inventory_cards WHERE name = 'Sol Ring'").fetchone()
            assert sol_ring is not None
            assert sol_ring["quantity"] == 2

    def test_import_merge_mode(self, tmp_path: Path):
        db_file = tmp_path / "test_merge.db"
        init_db(db_file)

        # Pre-populate with 1 copy of Sol Ring and an old unrelated card
        with get_db(db_file) as conn:
            conn.execute(
                """
                INSERT INTO inventory_cards (oracle_id, name, set_code, collector_number, quantity, is_proxy)
                VALUES ('old-orc', 'Sol Ring', 'c21', '263', 1, 0)
                """
            )
            conn.execute(
                """
                INSERT INTO inventory_cards (oracle_id, name, set_code, collector_number, quantity, is_proxy)
                VALUES ('orc-old2', 'Llanowar Elves', 'dom', '168', 4, 0)
                """
            )

        parsed_cards = parse_manabox_csv(SAMPLE_MANABOX_CSV)
        total_imported = import_parsed_cards(parsed_cards, db_path=db_file, mode="merge")
        assert total_imported == 4

        with get_db(db_file) as conn:
            # Sol Ring should be updated to CSV quantity (2)
            sol_ring = conn.execute("SELECT * FROM inventory_cards WHERE name = 'Sol Ring'").fetchone()
            assert sol_ring is not None
            assert sol_ring["quantity"] == 2

            # Llanowar Elves should remain intact
            elves = conn.execute("SELECT * FROM inventory_cards WHERE name = 'Llanowar Elves'").fetchone()
            assert elves is not None
            assert elves["quantity"] == 4

    def test_invalid_import_mode_raises(self, tmp_path: Path):
        db_file = tmp_path / "test_invalid_mode.db"
        init_db(db_file)

        with pytest.raises(ValueError, match="Modo de importación no válido"):
            import_parsed_cards([], db_path=db_file, mode="invalid_mode")
