"""Corner-case test suite for src/deck_importer.py.

Covers:
- All 4 game deck adapters (MTG, Lorcana, One Piece, Riftbound)
- Strategy interface methods: display_name, can_handle_url, can_handle_text, fetch_deck, parse_deck_list
- Platform fetchers (LorcanaGG, Inkdecks, Dreamborn, Inktable, OnePieceGG, Egman, Kaizoku, OnePieceTopDecks, GumGum, Limitless, Scryfall list entries, Manabox Astro dehydrated props)
- Error handling paths and network exceptions wrapped in DeckImportError
- AdapterRegistry lookups, auto-detection heuristics, and legacy fallbacks
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.deck_importer import (
    DeckCard,
    DeckImportError,
    FetchedDeck,
    LorcanaDeckAdapter,
    MtgDeckAdapter,
    OnePieceDeckAdapter,
    RiftboundDeckAdapter,
    _astro_find,
    _astro_val,
    _fetch_archidekt,
    _fetch_deckstats,
    _fetch_dreamborn,
    _fetch_egman_op,
    _fetch_gumgum,
    _fetch_inkdecks,
    _fetch_inktable,
    _fetch_kaizoku_op,
    _fetch_limitless_op,
    _fetch_lorcana_gg,
    _fetch_manabox,
    _fetch_moxfield,
    _fetch_onepiece_gg,
    _fetch_optopdecks,
    _fetch_scryfall,
    _fetch_tappedout,
    _manabox_extract_props,
    _REGISTRY,
    detect_game,
    fetch_deck,
    get_adapter,
    parse_deck_list,
    register_adapter,
)


class TestMtgAdapterCornerCases:
    def test_display_name_and_game_name(self):
        adapter = MtgDeckAdapter()
        assert adapter.game_name == "mtg"
        assert adapter.display_name == "Magic: The Gathering"

    def test_can_handle_text_variations(self):
        adapter = MtgDeckAdapter()
        assert adapter.can_handle_text("1 Sol Ring *cmdr*")
        assert adapter.can_handle_text("// commander\n1 Atraxa")
        assert adapter.can_handle_text("// sideboard\n2 Negate")
        assert adapter.can_handle_text("SB: 2 Pyroclasm")
        assert adapter.can_handle_text("Commander:\n1 Kenrith")
        assert adapter.can_handle_text("Sideboard:\n2 Duress")
        assert adapter.can_handle_text("4x Lightning Bolt (M11) 149")
        assert not adapter.can_handle_text("Unknown text format")

    def test_fetch_deck_unsupported_url_raises(self):
        adapter = MtgDeckAdapter()
        with pytest.raises(DeckImportError, match="URL no reconocida"):
            adapter.fetch_deck("https://unsupported-mtg-site.com/deck/123")

    def test_parse_deck_list_delimiters_and_headers(self):
        adapter = MtgDeckAdapter()
        text = """
        # name: Commander Superfriends
        // commanders:
        1 Atraxa, Praetors' Voice <CM2:1>
        // mainboard
        4 Counterspell [ICE:57]
        2 Lightning Bolt (M11:149)
        1 Birds of Paradise (rav) 153
        1 Sol Ring
        // sideboard:
        SB: 2 Swords to Plowshares
        // comment to ignore
        invalid line without count
        """
        deck = adapter.parse_deck_list(text)
        assert deck.name == "Commander Superfriends"
        assert len(deck.cards) == 6

        atraxa = next(c for c in deck.cards if "Atraxa" in c.name)
        assert atraxa.is_commander is True
        assert atraxa.set_code == "cm2"
        assert atraxa.collector_number == "1"

        cs = next(c for c in deck.cards if "Counterspell" in c.name)
        assert cs.set_code == "ice"
        assert cs.collector_number == "57"

        bolt = next(c for c in deck.cards if "Lightning Bolt" in c.name)
        assert bolt.set_code == "m11"
        assert bolt.collector_number == "149"

        bop = next(c for c in deck.cards if "Birds of Paradise" in c.name)
        assert bop.set_code == "rav"
        assert bop.collector_number == "153"

        sol = next(c for c in deck.cards if "Sol Ring" in c.name)
        assert sol.set_code == ""
        assert sol.collector_number == ""

        swords = next(c for c in deck.cards if "Swords to Plowshares" in c.name)
        assert swords.zone == "side"
        assert swords.quantity == 2


class TestLorcanaAdapterCornerCases:
    def test_display_name_and_game_name(self):
        adapter = LorcanaDeckAdapter()
        assert adapter.game_name == "lorcana"
        assert adapter.display_name == "Disney Lorcana"

    def test_can_handle_url(self):
        adapter = LorcanaDeckAdapter()
        assert adapter.can_handle_url("https://lorcana.gg/decks/amber-amethyst")
        assert adapter.can_handle_url("https://inkdecks.com/lorcana-metagame/deck-12345")
        assert adapter.can_handle_url("https://dreamborn.ink/decks/xyz-123")
        assert adapter.can_handle_url("https://inktable.net/decks/test-deck")
        assert not adapter.can_handle_url("https://moxfield.com/decks/123")

    def test_can_handle_text(self):
        adapter = LorcanaDeckAdapter()
        assert adapter.can_handle_text("// Lorcana deck\n4 Elsa")
        assert adapter.can_handle_text("4x Mickey Mouse - Brave Little Tailor")
        assert not adapter.can_handle_text("just some text")

    def test_fetch_deck_routing_and_unsupported(self):
        adapter = LorcanaDeckAdapter()
        with patch("src.deck_importer._fetch_lorcana_gg", return_value=FetchedDeck("GG", [])) as m_gg:
            adapter.fetch_deck("https://lorcana.gg/decks/my-deck/")
            m_gg.assert_called_once()

        with patch("src.deck_importer._fetch_inkdecks", return_value=FetchedDeck("ID", [])) as m_id:
            adapter.fetch_deck("https://inkdecks.com/lorcana-metagame/deck-98765")
            m_id.assert_called_once()

        with patch("src.deck_importer._fetch_dreamborn", return_value=FetchedDeck("DB", [])) as m_db:
            adapter.fetch_deck("https://dreamborn.ink/decks/db-123")
            m_db.assert_called_once()

        with patch("src.deck_importer._fetch_inktable", return_value=FetchedDeck("IT", [])) as m_it:
            adapter.fetch_deck("https://inktable.net/decks/it-123")
            m_it.assert_called_once()

        with pytest.raises(DeckImportError, match="URL de Lorcana no soportada"):
            adapter.fetch_deck("https://unknown-lorcana.com/deck/1")

    def test_parse_deck_list(self):
        adapter = LorcanaDeckAdapter()
        text = """
        # name: Steel Ruby Aggro
        // comments to ignore
        4 Stitch - Carefree Surfer
        2x Maui - Hero to All
        malformed line without count
        """
        deck = adapter.parse_deck_list(text)
        assert deck.name == "Steel Ruby Aggro"
        assert len(deck.cards) == 2
        assert deck.cards[0].name == "Stitch - Carefree Surfer"
        assert deck.cards[0].quantity == 4
        assert deck.cards[1].name == "Maui - Hero to All"
        assert deck.cards[1].quantity == 2

    def test_lorcana_fetchers_exceptions(self):
        with patch("src.scraper_utils.fetch_json", side_effect=Exception("API Error")):
            with pytest.raises(DeckImportError, match="Dreamborn"):
                _fetch_dreamborn("https://dreamborn.ink/api/decks/bad")

        with patch("src.scraper_utils.fetch_json", side_effect=Exception("API Error")):
            with pytest.raises(DeckImportError, match="Inktable"):
                _fetch_inktable("https://inktable.net/api/lorcana/decks/bad")


class TestOnePieceAdapterCornerCases:
    def test_display_name_and_game_name(self):
        adapter = OnePieceDeckAdapter()
        assert adapter.game_name == "onepiece"
        assert adapter.display_name == "One Piece Card Game"

    def test_can_handle_url(self):
        adapter = OnePieceDeckAdapter()
        assert adapter.can_handle_url("https://onepiece.gg/decks/zoro")
        assert adapter.can_handle_url("https://deckbuilder.egmanevents.com/?deck=1")
        assert adapter.can_handle_url("https://deckbuilder.cardkaizoku.com/?deck=1")
        assert adapter.can_handle_url("https://onepiecetopdecks.com/deck-list/op01")
        assert adapter.can_handle_url("https://gumgum.gg/decks/1")
        assert adapter.can_handle_url("https://gumgumtcg.com/decks/1")
        assert adapter.can_handle_url("https://limitlesstcg.com/decks/1")
        assert not adapter.can_handle_url("https://moxfield.com/decks/1")

    def test_can_handle_text(self):
        adapter = OnePieceDeckAdapter()
        assert adapter.can_handle_text("One Piece Tournament Deck")
        assert adapter.can_handle_text("optcg deck list")
        assert adapter.can_handle_text("4 OP01-001")
        assert adapter.can_handle_text("4 ST01-004")
        assert not adapter.can_handle_text("other game")

    def test_fetch_deck_routing_and_unsupported(self):
        adapter = OnePieceDeckAdapter()
        with patch("src.deck_importer._fetch_onepiece_gg", return_value=FetchedDeck("OPGG", [])) as m_opgg:
            adapter.fetch_deck("https://onepiece.gg/decks/zoro/")
            m_opgg.assert_called_once()

        with patch("src.deck_importer._fetch_egman_op", return_value=FetchedDeck("Egman", [])) as m_eg:
            adapter.fetch_deck("https://deckbuilder.egmanevents.com/?deck=OP01-001:1")
            m_eg.assert_called_once()

        with patch("src.deck_importer._fetch_kaizoku_op", return_value=FetchedDeck("Kaizoku", [])) as m_kz:
            adapter.fetch_deck("https://deckbuilder.cardkaizoku.com/?deck=OP01-001:1")
            m_kz.assert_called_once()

        with patch("src.deck_importer._fetch_optopdecks", return_value=FetchedDeck("TopDecks", [])) as m_td:
            adapter.fetch_deck("https://onepiecetopdecks.com/deck-list/test")
            m_td.assert_called_once()

        with patch("src.deck_importer._fetch_gumgum", return_value=FetchedDeck("GumGum", [])) as m_gg:
            adapter.fetch_deck("https://gumgum.gg/decks/123")
            m_gg.assert_called_once()

        with patch("src.deck_importer._fetch_limitless_op", return_value=FetchedDeck("Limitless", [])) as m_lim:
            adapter.fetch_deck("https://limitlesstcg.com/decks/123")
            m_lim.assert_called_once()

        with pytest.raises(DeckImportError, match="URL de One Piece no soportada"):
            adapter.fetch_deck("https://unknown-onepiece.com/deck/1")

    def test_parse_deck_list_egman_colon_and_leader(self):
        adapter = OnePieceDeckAdapter()
        text = """
        # name: Red Green Law
        1 OP01-002 (Leader)
        4x OP01-006 Otama
        OP01-016:4
        OP01-025:invalid_qty
        invalid_without_colon_or_count
        """
        deck = adapter.parse_deck_list(text)
        assert deck.name == "Red Green Law"
        assert len(deck.cards) == 4

        leader = next(c for c in deck.cards if c.collector_number == "OP01-002")
        assert leader.zone == "leader"
        assert leader.is_commander is True

        otama = next(c for c in deck.cards if "Otama" in c.name)
        assert otama.collector_number == "OP01-006"
        assert otama.quantity == 4

        c16 = next(c for c in deck.cards if c.collector_number == "OP01-016")
        assert c16.quantity == 4

        c25 = next(c for c in deck.cards if c.collector_number == "OP01-025")
        assert c25.quantity == 1

    def test_onepiece_fetchers_exceptions(self):
        with patch("src.scraper_utils.fetch_text", side_effect=Exception("Network Error")):
            with pytest.raises(DeckImportError, match="OnePieceTopDecks"):
                _fetch_optopdecks("https://onepiecetopdecks.com/deck-list/bad")

        with patch("src.scraper_utils.fetch_json", side_effect=Exception("Network Error")):
            with pytest.raises(DeckImportError, match="GumGum"):
                _fetch_gumgum("https://gumgum.gg/decks/bad")

        with patch("src.scraper_utils.fetch_text", side_effect=Exception("Network Error")):
            with pytest.raises(DeckImportError, match="Limitless"):
                _fetch_limitless_op("https://limitlesstcg.com/decks/bad")


class TestRiftboundAdapterCornerCases:
    def test_display_name_and_game_name(self):
        adapter = RiftboundDeckAdapter()
        assert adapter.game_name == "riftbound"
        assert adapter.display_name == "Riftbound"

    def test_can_handle_url(self):
        adapter = RiftboundDeckAdapter()
        assert adapter.can_handle_url("https://piltoverarchive.com/decks/view/00000000-0000-0000-0000-000000000001")
        assert adapter.can_handle_url("https://riftmana.com/decks/test")
        assert adapter.can_handle_url("https://riftbinder.com/decks/test")
        assert adapter.can_handle_url("https://riftdex.com/decks/00000000-0000-0000-0000-000000000001")
        assert adapter.can_handle_url("https://riftbound.gg/decks/test")
        assert not adapter.can_handle_url("https://moxfield.com/decks/1")

    def test_can_handle_text(self):
        adapter = RiftboundDeckAdapter()
        assert adapter.can_handle_text("Riftbound tournament deck")
        assert adapter.can_handle_text("Piltover Archive list")
        assert adapter.can_handle_text("Legend:\n1 Caitlyn")
        assert adapter.can_handle_text("Battlefield:\n2 Piltover")
        assert adapter.can_handle_text("Rune:\n3 Hextech")
        assert adapter.can_handle_text("Maindeck:\n4 Unit")
        assert not adapter.can_handle_text("plain text")

    def test_fetch_deck_mapping_and_error(self):
        adapter = RiftboundDeckAdapter()
        mock_card = MagicMock()
        mock_card.name = "Vi"
        mock_card.card_id = "RB01-001"
        mock_card.quantity = 1
        mock_card.section = "legend"
        mock_card.card_type = "Legend"
        mock_card.image_url = "https://example.com/vi.jpg"

        mock_rb_deck = MagicMock()
        mock_rb_deck.name = "Vi Beatdown"
        mock_rb_deck.cards = [mock_card]

        with patch("src.rb_scraper.scrape_deck", return_value=mock_rb_deck):
            deck = adapter.fetch_deck("https://piltoverarchive.com/decks/view/00000000-0000-0000-0000-000000000001")
            assert deck.name == "Vi Beatdown"
            assert deck.game == "riftbound"
            assert len(deck.cards) == 1
            assert deck.cards[0].name == "Vi"
            assert deck.cards[0].image_url == "https://example.com/vi.jpg"

        with patch("src.rb_scraper.scrape_deck", side_effect=Exception("Scraper Fail")):
            with pytest.raises(DeckImportError, match="Riftbound"):
                adapter.fetch_deck("https://piltoverarchive.com/decks/view/bad")

    def test_parse_deck_list_all_zones(self):
        adapter = RiftboundDeckAdapter()
        text = """
        # name: Piltover Zaun Mixed
        Legend:
        1 Jinx
        Battlefield:
        2 Piltover City
        Rune:
        3 Hextech Rune
        Champion:
        4 Warwick
        Sideboard:
        2 Flashbomb
        Maindeck:
        4 Scrap Golem
        invalid_without_count
        """
        deck = adapter.parse_deck_list(text)
        assert deck.name == "Piltover Zaun Mixed"
        assert len(deck.cards) == 6

        jinx = next(c for c in deck.cards if c.name == "Jinx")
        assert jinx.zone == "legend"

        city = next(c for c in deck.cards if c.name == "Piltover City")
        assert city.zone == "battlefield"

        rune = next(c for c in deck.cards if c.name == "Hextech Rune")
        assert rune.zone == "rune"

        ww = next(c for c in deck.cards if c.name == "Warwick")
        assert ww.zone == "champion"

        fb = next(c for c in deck.cards if c.name == "Flashbomb")
        assert fb.zone == "sideboard"

        golem = next(c for c in deck.cards if c.name == "Scrap Golem")
        assert golem.zone == "maindeck"


class TestAdapterRegistryAndTopLevelCornerCases:
    def test_registry_get_for_text(self):
        assert _REGISTRY.get_for_text("OP01-001") is not None
        assert _REGISTRY.get_for_text("unknown text that has no pattern") is None

    def test_parse_deck_list_auto_detection(self):
        deck_op = parse_deck_list("1 OP01-001 (Leader)\n4 OP01-002", game="auto")
        assert deck_op.game == "onepiece"

        deck_unk = parse_deck_list("4 Unknown Card", game="unknown")
        assert deck_unk.game == "mtg"

    def test_fetch_deck_with_game_parameter(self):
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.json.return_value = {"name": "MTG Deck", "boards": {}}
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            deck = fetch_deck("https://moxfield.com/decks/test", game="mtg")
            assert deck.name == "MTG Deck"

    def test_detect_game_fallbacks(self):
        assert detect_game("   ") == "unknown"
        assert detect_game("4 Lightning Bolt\n2 Island") == "mtg"


class TestMtgPlatformFetchersCornerCases:
    def test_moxfield_skips_cards_without_set_or_cn(self):
        fixture = {
            "name": "Missing Set Deck",
            "boards": {
                "mainboard": {
                    "cards": {
                        "c1": {"quantity": 1, "card": {"name": "Valid", "set": "m11", "cn": "1"}},
                        "c2": {"quantity": 1, "card": {"name": "No Set", "set": "", "cn": "1"}},
                        "c3": {"quantity": 1, "card": {"name": "No CN", "set": "m11", "cn": ""}},
                    }
                }
            },
            "tokens": [
                {"name": "Valid Token", "set": "tm11", "cn": "1", "quantity": 1},
                {"name": "Invalid Token", "set": "", "cn": "1"},
            ],
        }
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.json.return_value = fixture
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            deck = _fetch_moxfield("test123")
            assert len(deck.cards) == 2
            assert deck.cards[0].name == "Valid"
            assert deck.cards[1].name == "Valid Token"

    def test_archidekt_error_and_types_array(self):
        with patch("src.deck_importer.requests.get", side_effect=requests.RequestException("Archidekt 500")):
            with pytest.raises(DeckImportError, match="Archidekt"):
                _fetch_archidekt("bad_id")

        fixture = {
            "name": "Archidekt Types Test",
            "cards": [
                {
                    "quantity": 1,
                    "categories": ["Mainboard"],
                    "card": {
                        "oracleCard": {"name": "Jace", "types": ["Planeswalker", "Jace"]},
                        "edition": {"editioncode": "m11"},
                        "collectorNumber": "58",
                    },
                },
                {
                    "quantity": 1,
                    "categories": ["Mainboard"],
                    "card": {
                        "oracleCard": {"name": "Incomplete"},
                        "edition": {"editioncode": ""},
                        "collectorNumber": "",
                    },
                },
            ],
        }
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.json.return_value = fixture
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            deck = _fetch_archidekt("good_id")
            assert len(deck.cards) == 1
            assert deck.cards[0].name == "Jace"
            assert deck.cards[0].type_line == "Planeswalker Jace"

    def test_deckstats_skips_cards_without_name(self):
        fixture = {
            "name": "Deckstats Empty Name",
            "sections": [{"cards": [{"amount": 1, "name": ""}, {"amount": 1, "name": "Forest"}]}],
            "sideboard": [{"amount": 1, "name": ""}, {"amount": 2, "name": "Dispel"}],
        }
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.json.return_value = fixture
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            deck = _fetch_deckstats("1", "2")
            assert len(deck.cards) == 2
            assert deck.cards[0].name == "Forest"
            assert deck.cards[1].name == "Dispel"

    def test_scryfall_fetch_list_format_entries(self):
        fixture = {
            "name": "Scryfall List Format",
            "entries": [
                {
                    "count": 4,
                    "zone": "main",
                    "card_digest": {"name": "Brainstorm", "set": "ema", "collector_number": "40"},
                },
                {
                    "count": 2,
                    "zone": "sideboard",
                    "card_digest": {"name": "Flusterstorm", "set": "mh1", "collector_number": "255"},
                },
                {"count": 1, "zone": "main", "card_digest": {"name": ""}},
            ],
        }
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.json.return_value = fixture
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            deck = _fetch_scryfall("scryfall-id")
            assert len(deck.cards) == 2
            assert deck.cards[0].name == "Brainstorm"
            assert deck.cards[0].zone == "main"
            assert deck.cards[1].name == "Flusterstorm"
            assert deck.cards[1].zone == "side"

        with patch("src.deck_importer.requests.get", side_effect=requests.RequestException("Scryfall 500")):
            with pytest.raises(DeckImportError, match="Scryfall"):
                _fetch_scryfall("bad-scryfall-id")

    def test_manabox_fetch_malformed_json_or_format(self):
        with patch("src.deck_importer.requests.get", side_effect=requests.RequestException("Manabox 404")):
            with pytest.raises(DeckImportError, match="Manabox"):
                _fetch_manabox("bad_id")

        # Malformed JSON in props
        bad_json_html = '<astro-island props="{&quot;cards&quot;: [not valid json}"></astro-island>'
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.text = bad_json_html
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            with pytest.raises(DeckImportError, match="parsear datos"):
                _fetch_manabox("bad_json")

        # Cards is not a list
        no_cards_list_html = '<astro-island props="{&quot;cards&quot;:123}"></astro-island>'
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.text = no_cards_list_html
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            with pytest.raises(DeckImportError, match="Formato de mazos inesperado"):
                _fetch_manabox("no_cards_list")

    def test_manabox_extract_props_without_props_attribute(self):
        html = '<div>&quot;cards&quot;: [] without props attribute</div>'
        assert _manabox_extract_props(html) == ""

    def test_astro_find_in_list(self):
        data = [[0, {"target": [0, "found_value"]}]]
        assert _astro_find(data, "target") == "found_value"

    def test_fetch_tappedout_network_error_and_cmdr(self):
        with patch("src.deck_importer.requests.get", side_effect=requests.RequestException("TappedOut down")):
            with pytest.raises(DeckImportError, match="TappedOut"):
                _fetch_tappedout("burn-deck")

        text = "1 Urza *CMDR*\n4 Lightning Bolt\nSB: 2 Pyroclasm"
        with patch("src.deck_importer.requests.get") as m_get:
            m_resp = MagicMock()
            m_resp.text = text
            m_resp.raise_for_status.return_value = None
            m_get.return_value = m_resp
            deck = _fetch_tappedout("urza-deck")
            urza = next(c for c in deck.cards if "Urza" in c.name)
            assert urza.is_commander is True

    def test_fetch_lorcana_and_onepiece_scrapers(self):
        # LorcanaGG
        mock_c = MagicMock()
        mock_c.name = "Elsa"
        mock_c.card_id = "001-001"
        mock_c.quantity = 4
        mock_c.image_url = "https://example.com/elsa.jpg"
        mock_deck = MagicMock()
        mock_deck.name = "Elsa Deck"
        mock_deck.cards = [mock_c]

        with patch("src.lorcana_scraper.scrape_deck", return_value=mock_deck):
            deck_gg = _fetch_lorcana_gg("elsa-deck")
            assert deck_gg.name == "Elsa Deck"
            assert deck_gg.source == "lorcana_gg"

            deck_id = _fetch_inkdecks("12345")
            assert deck_id.name == "Elsa Deck"
            assert deck_id.source == "inkdecks"

        # OnePiece scrapers
        mock_op_c = MagicMock()
        mock_op_c.name = "Zoro"
        mock_op_c.card_id = "OP01-001"
        mock_op_c.quantity = 1
        mock_op_c.is_leader = True
        mock_op_deck = MagicMock()
        mock_op_deck.name = "Zoro Leader Deck"
        mock_op_deck.cards = [mock_op_c]

        with patch("src.op_scraper.scrape_deck", return_value=mock_op_deck):
            deck_opgg = _fetch_onepiece_gg("zoro-deck")
            assert deck_opgg.name == "Zoro Leader Deck"
            assert deck_opgg.source == "onepiece_gg"

            deck_eg = _fetch_egman_op("https://deckbuilder.egmanevents.com/?deck=1")
            assert deck_eg.name == "Zoro Leader Deck"
            assert deck_eg.source == "egman"

            deck_kz = _fetch_kaizoku_op("https://deckbuilder.cardkaizoku.com/?deck=1")
            assert deck_kz.name == "Zoro Leader Deck"
            assert deck_kz.source == "kaizoku"

