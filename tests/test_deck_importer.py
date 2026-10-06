from unittest.mock import MagicMock, patch

import pytest

from src.deck_importer import (
    DeckCard,
    DeckImportError,
    FetchedDeck,
    _astro_find,
    _astro_val,
    _manabox_extract_props,
    fetch_deck,
)

_MOXFIELD_FIXTURE = {
    "name": "Test Deck",
    "format": "modern",
    "boards": {
        "mainboard": {
            "cards": {
                "a1": {
                    "quantity": 4,
                    "card": {
                        "name": "Lightning Bolt",
                        "set": "ltr",
                        "cn": "152",
                        "type_line": "Instant",
                    },
                },
                "a2": {
                    "quantity": 1,
                    "card": {
                        "name": "Forest",
                        "set": "m21",
                        "cn": "295",
                        "type_line": "Basic Land — Forest",
                    },
                },
            }
        },
        "commanders": {
            "cards": {
                "c1": {
                    "quantity": 1,
                    "card": {
                        "name": "Urza, Lord High Artificer",
                        "set": "mh1",
                        "cn": "75",
                        "type_line": "Legendary Creature — Human Artificer",
                    },
                }
            }
        },
        "companions": {"cards": {}},
        "sideboard": {
            "cards": {
                "b1": {
                    "quantity": 2,
                    "card": {
                        "name": "Naturalize",
                        "set": "m21",
                        "cn": "196",
                        "type_line": "Instant",
                    },
                }
            }
        },
    },
    "tokens": [
        {
            "name": "Thopter",
            "set": "tmh1",
            "cn": "19",
            "quantity": 3,
            "type_line": "Token Creature — Thopter",
        }
    ],
}

_MOXFIELD_FIXTURE_WITH_COMPANION = {
    "name": "Companion Deck",
    "boards": {
        "mainboard": {"cards": {}},
        "commanders": {"cards": {}},
        "companions": {
            "cards": {
                "c1": {
                    "quantity": 1,
                    "card": {"name": "Lurrus of the Dream-Den", "set": "iko", "cn": "226"},
                }
            }
        },
        "sideboard": {"cards": {}},
    },
}

_MOXFIELD_FIXTURE_WITH_TOKENS = {
    **_MOXFIELD_FIXTURE,
    "tokens": [
        {"name": "Treasure", "set": "tneo", "cn": "12"},
        {"name": "Treasure", "set": "tneo", "cn": "12"},
        {"card": {"name": "Goblin", "set": "tmom", "cn": "7"}},
    ],
}

_ARCHIDEKT_FIXTURE = {
    "name": "Archidekt Test",
    "cards": [
        {
            "quantity": 4,
            "categories": ["Mainboard"],
            "card": {
                "oracleCard": {"name": "Counterspell"},
                "edition": {"editioncode": "ice"},
                "collectorNumber": "57",
            },
        },
        {
            "quantity": 2,
            "categories": ["Sideboard"],
            "card": {
                "oracleCard": {"name": "Negate"},
                "edition": {"editioncode": "m21"},
                "collectorNumber": "60",
            },
        },
        {
            "quantity": 1,
            "categories": ["Maybeboard"],
            "card": {
                "oracleCard": {"name": "Skipped"},
                "edition": {"editioncode": "m21"},
                "collectorNumber": "1",
            },
        },
        {
            "quantity": 1,
            "categories": ["Commander"],
            "card": {
                "oracleCard": {"name": "Kenrith, the Returned King"},
                "edition": {"editioncode": "eld"},
                "collectorNumber": "303",
            },
        },
    ],
}


_ARCHIDEKT_FIXTURE_WITH_TOKENS = {
    **_ARCHIDEKT_FIXTURE,
    "tokens": [{"name": "Servo", "set": "tneo", "cn": "5"}],
}


class TestMoxfieldFetch:
    def _mock_resp(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        return resp

    def test_extracts_mainboard_cards(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_MOXFIELD_FIXTURE)
        ):
            result = fetch_deck("https://www.moxfield.com/decks/ABC123")
        main = [c for c in result.cards if c.zone == "main" and not c.is_commander]
        assert len(main) == 2
        bolt = next(c for c in main if c.name == "Lightning Bolt")
        assert bolt.set_code == "ltr"
        assert bolt.collector_number == "152"
        assert bolt.quantity == 4
        assert bolt.type_line == "Instant"

    def test_extracts_commander(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_MOXFIELD_FIXTURE)
        ):
            result = fetch_deck("https://www.moxfield.com/decks/ABC123")
        main = [c for c in result.cards if c.zone == "main"]
        cmd = next(c for c in main if c.is_commander)
        assert cmd.name == "Urza, Lord High Artificer"
        assert cmd.set_code == "mh1"
        assert cmd.collector_number == "75"
        assert cmd.quantity == 1

    def test_extracts_tokens(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_MOXFIELD_FIXTURE)
        ):
            result = fetch_deck("https://www.moxfield.com/decks/ABC123")
        tokens = [c for c in result.cards if c.zone == "token"]
        assert len(tokens) == 1
        tok = tokens[0]
        assert tok.name == "Thopter"
        assert tok.set_code == "tmh1"
        assert tok.collector_number == "19"
        assert tok.quantity == 3
        assert tok.type_line == "Token Creature — Thopter"

    def test_extracts_sideboard_cards(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_MOXFIELD_FIXTURE)
        ):
            result = fetch_deck("https://www.moxfield.com/decks/ABC123")
        side = [c for c in result.cards if c.zone == "side"]
        assert len(side) == 1
        assert side[0].name == "Naturalize"
        assert side[0].quantity == 2

    def test_extracts_deck_name(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_MOXFIELD_FIXTURE)
        ):
            result = fetch_deck("https://www.moxfield.com/decks/ABC123")
        assert result.name == "Test Deck"
        assert result.format == "modern"

    def test_extracts_companion_as_main(self):
        with patch(
            "src.deck_importer.requests.get",
            return_value=self._mock_resp(_MOXFIELD_FIXTURE_WITH_COMPANION),
        ):
            result = fetch_deck("https://www.moxfield.com/decks/ABC123")
        assert len(result.cards) == 1
        assert result.cards[0].name == "Lurrus of the Dream-Den"
        assert result.cards[0].zone == "main"
        assert result.cards[0].collector_number == "226"

    def test_raises_on_http_error(self):
        import requests as req

        with patch("src.deck_importer.requests.get", side_effect=req.RequestException("404")):
            with pytest.raises(DeckImportError, match="Moxfield"):
                fetch_deck("https://www.moxfield.com/decks/BAD")

    def test_extracts_each_distinct_token_once(self):
        with patch(
            "src.deck_importer.requests.get",
            return_value=self._mock_resp(_MOXFIELD_FIXTURE_WITH_TOKENS),
        ):
            result = fetch_deck("https://www.moxfield.com/decks/ABC123")
        tokens = [c for c in result.cards if c.zone == "token"]
        assert [(c.name, c.quantity) for c in tokens] == [("Treasure", 1), ("Goblin", 1)]


class TestArchidektFetch:
    def _mock_resp(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        return resp

    def test_extracts_mainboard_cards(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_ARCHIDEKT_FIXTURE)
        ):
            result = fetch_deck("https://archidekt.com/decks/12345/my-deck")
        main = [c for c in result.cards if c.zone == "main" and not c.is_commander]
        assert len(main) == 1
        assert main[0].name == "Counterspell"
        assert main[0].set_code == "ice"
        assert main[0].collector_number == "57"
        assert main[0].quantity == 4

        cmd = next(c for c in result.cards if c.is_commander)
        assert cmd.name == "Kenrith, the Returned King"
        assert cmd.set_code == "eld"
        assert cmd.collector_number == "303"
        assert cmd.quantity == 1

    def test_extracts_sideboard_cards(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_ARCHIDEKT_FIXTURE)
        ):
            result = fetch_deck("https://archidekt.com/decks/12345/my-deck")
        side = [c for c in result.cards if c.zone == "side"]
        assert len(side) == 1
        assert side[0].name == "Negate"

    def test_extracts_deck_name(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_ARCHIDEKT_FIXTURE)
        ):
            result = fetch_deck("https://archidekt.com/decks/12345/my-deck")
        assert result.name == "Archidekt Test"

    def test_skips_maybeboard(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_ARCHIDEKT_FIXTURE)
        ):
            result = fetch_deck("https://archidekt.com/decks/12345/my-deck")
        names = [c.name for c in result.cards]
        assert "Skipped" not in names


class TestArchidektTokens:
    def test_extracts_api_tokens(self):
        resp = MagicMock()
        resp.json.return_value = _ARCHIDEKT_FIXTURE_WITH_TOKENS
        resp.raise_for_status.return_value = None
        with patch("src.deck_importer.requests.get", return_value=resp):
            result = fetch_deck("https://archidekt.com/decks/12345/my-deck")
        assert [(c.name, c.zone) for c in result.cards if c.zone == "token"] == [("Servo", "token")]

    def test_keeps_commander_when_archidekt_omits_printing_data(self):
        data = {
            "name": "Terra",
            "cards": [
                {
                    "quantity": 1,
                    "categories": ["Commander"],
                    "card": {"oracleCard": {"name": "Terra, Herald of Hope"}},
                }
            ],
        }
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        with patch("src.deck_importer.requests.get", return_value=resp):
            result = fetch_deck("https://archidekt.com/decks/15820256/terra")
        assert [(c.name, c.zone) for c in result.cards] == [("Terra, Herald of Hope", "main")]

    def test_does_not_treat_a_category_named_tokens_as_a_token_card(self):
        data = {
            "name": "Terra",
            "cards": [
                {
                    "quantity": 1,
                    "categories": ["Tokens"],
                    "card": {
                        "oracleCard": {"name": "Desecrated Tomb"},
                        "edition": {"editioncode": "grn"},
                        "collectorNumber": "230",
                    },
                }
            ],
        }
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        with patch("src.deck_importer.requests.get", return_value=resp):
            result = fetch_deck("https://archidekt.com/decks/15820256/terra")
        assert [(c.name, c.zone) for c in result.cards] == [("Desecrated Tomb", "main")]


class TestUrlDetection:
    def test_invalid_url_raises(self):
        with pytest.raises(DeckImportError, match="no reconocida"):
            fetch_deck("https://deckstats.net/decks/123")

    def test_moxfield_url_detected(self):
        with patch("src.deck_importer._fetch_moxfield", return_value=FetchedDeck("", [])) as mock:
            fetch_deck("https://www.moxfield.com/decks/XYZ-abc")
            mock.assert_called_once_with("XYZ-abc")

    def test_archidekt_url_detected(self):
        with patch("src.deck_importer._fetch_archidekt", return_value=FetchedDeck("", [])) as mock:
            fetch_deck("https://archidekt.com/decks/99999/name")
            mock.assert_called_once_with("99999")

    def test_deckstats_url_detected(self):
        with patch("src.deck_importer._fetch_deckstats", return_value=FetchedDeck("", [])) as mock:
            fetch_deck("https://deckstats.net/decks/12345/67890-burn")
            mock.assert_called_once_with("12345", "67890")

    def test_tappedout_url_detected(self):
        with patch("src.deck_importer._fetch_tappedout", return_value=FetchedDeck("", [])) as mock:
            fetch_deck("https://tappedout.net/mtg-decks/my-deck/")
            mock.assert_called_once_with("my-deck")

    def test_manabox_url_detected(self):
        with patch("src.deck_importer._fetch_manabox", return_value=FetchedDeck("", [])) as mock:
            fetch_deck("https://manabox.app/decks/abc123")
            mock.assert_called_once_with("abc123")


_DECKSTATS_FIXTURE = {
    "name": "Deckstats Test",
    "sections": [
        {
            "name": "Creatures",
            "cards": [
                {"amount": 4, "name": "Goblin Guide"},
                {"amount": 2, "name": "Monastery Swiftspear"},
            ],
        },
        {
            "name": "Spells",
            "cards": [
                {"amount": 4, "name": "Lightning Bolt"},
            ],
        },
    ],
    "sideboard": [
        {"amount": 2, "name": "Eidolon of the Great Revel"},
    ],
}

_TAPPEDOUT_TEXT = """\
4 Lightning Bolt
2 Goblin Guide
// sideboard
SB: 2 Pyroclasm
SB: 1 Smash to Smithereens
"""

_TAPPEDOUT_PAGE = "<div>Tokens | Clue, Dinosaur 3/1 R, Treasure</div>"

_MANABOX_PROPS_JSON = (
    '{"deck":[0,{"name":[0,"Manabox Test"],'
    '"cards":[1,[[0,{"name":[0,"Whirlpool Warrior"],"setId":[0,"apc"],'
    '"collectorNumber":[0,"36"],"quantity":[0,2],"boardCategory":[0,3]}],'
    '[0,{"name":[0,"Negate"],"setId":[0,"m21"],'
    '"collectorNumber":[0,"60"],"quantity":[0,1],"boardCategory":[0,1]}]]]}]}'
)
_MANABOX_HTML = (
    "<html><body>"
    '<astro-island props="' + _MANABOX_PROPS_JSON.replace('"', "&quot;") + '"></astro-island>'
    "</body></html>"
)


class TestDeckstatsFetch:
    def _mock_resp(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        return resp

    def test_extracts_mainboard_from_sections(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_DECKSTATS_FIXTURE)
        ):
            result = fetch_deck("https://deckstats.net/decks/12345/67890-burn")
        main = [c for c in result.cards if c.zone == "main"]
        assert len(main) == 3
        bolt = next(c for c in main if c.name == "Lightning Bolt")
        assert bolt.quantity == 4
        assert bolt.set_code == ""

    def test_extracts_sideboard(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_DECKSTATS_FIXTURE)
        ):
            result = fetch_deck("https://deckstats.net/decks/12345/67890-burn")
        side = [c for c in result.cards if c.zone == "side"]
        assert len(side) == 1
        assert side[0].name == "Eidolon of the Great Revel"

    def test_extracts_deck_name(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp(_DECKSTATS_FIXTURE)
        ):
            result = fetch_deck("https://deckstats.net/decks/12345/67890-burn")
        assert result.name == "Deckstats Test"

    def test_raises_on_http_error(self):
        import requests as req

        with patch("src.deck_importer.requests.get", side_effect=req.RequestException("timeout")):
            with pytest.raises(DeckImportError, match="Deckstats"):
                fetch_deck("https://deckstats.net/decks/12345/67890-burn")


class TestTappedoutFetch:
    def _mock_resp(self, text):
        resp = MagicMock()
        resp.text = text
        resp.raise_for_status.return_value = None
        return resp

    def test_extracts_main_cards(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp(_TAPPEDOUT_TEXT)):
            result = fetch_deck("https://tappedout.net/mtg-decks/burn-deck/")
        main = [c for c in result.cards if c.zone == "main"]
        assert len(main) == 2
        bolt = next(c for c in main if c.name == "Lightning Bolt")
        assert bolt.quantity == 4
        assert bolt.set_code == ""

    def test_extracts_sideboard_lines(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp(_TAPPEDOUT_TEXT)):
            result = fetch_deck("https://tappedout.net/mtg-decks/burn-deck/")
        side = [c for c in result.cards if c.zone == "side"]
        assert len(side) == 2
        assert any(c.name == "Pyroclasm" for c in side)
        assert any(c.name == "Smash to Smithereens" for c in side)

    def test_deck_name_from_slug(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp(_TAPPEDOUT_TEXT)):
            result = fetch_deck("https://tappedout.net/mtg-decks/burn-deck/")
        assert "Burn" in result.name

    def test_skips_comment_lines(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp(_TAPPEDOUT_TEXT)):
            result = fetch_deck("https://tappedout.net/mtg-decks/burn-deck/")
        assert all(c.name != "sideboard" for c in result.cards)

    def test_raises_on_empty_response(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp("")):
            with pytest.raises(DeckImportError):
                fetch_deck("https://tappedout.net/mtg-decks/empty-deck/")


class TestTappedoutTokens:
    def test_extracts_generated_tokens_from_deck_page(self):
        def response(text):
            result = MagicMock()
            result.text = text
            result.raise_for_status.return_value = None
            return result

        with patch(
            "src.deck_importer.requests.get",
            side_effect=[response(_TAPPEDOUT_TEXT), response(_TAPPEDOUT_PAGE)],
        ):
            result = fetch_deck("https://tappedout.net/mtg-decks/burn-deck/")
        assert [c.name for c in result.cards if c.zone == "token"] == [
            "Clue",
            "Dinosaur",
            "Treasure",
        ]


class TestManaboxFetch:
    def _mock_resp(self, text):
        resp = MagicMock()
        resp.text = text
        resp.raise_for_status.return_value = None
        return resp

    def test_extracts_main_cards(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp(_MANABOX_HTML)):
            result = fetch_deck("https://manabox.app/decks/abc123")
        main = [c for c in result.cards if c.zone == "main"]
        assert len(main) == 1
        assert main[0].name == "Whirlpool Warrior"
        assert main[0].set_code == "apc"
        assert main[0].collector_number == "36"
        assert main[0].quantity == 2

    def test_extracts_sideboard_cards(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp(_MANABOX_HTML)):
            result = fetch_deck("https://manabox.app/decks/abc123")
        side = [c for c in result.cards if c.zone == "side"]
        assert len(side) == 1
        assert side[0].name == "Negate"
        assert side[0].set_code == "m21"

    def test_extracts_deck_name(self):
        with patch("src.deck_importer.requests.get", return_value=self._mock_resp(_MANABOX_HTML)):
            result = fetch_deck("https://manabox.app/decks/abc123")
        assert result.name == "Manabox Test"

    def test_raises_on_missing_data(self):
        with patch(
            "src.deck_importer.requests.get", return_value=self._mock_resp("<html>no data</html>")
        ):
            with pytest.raises(DeckImportError):
                fetch_deck("https://manabox.app/decks/abc123")


class TestManaboxHelpers:
    def test_astro_val_unwraps_type0(self):
        assert _astro_val([0, "hello"]) == "hello"
        assert _astro_val([0, 42]) == 42
        assert _astro_val("plain") == "plain"

    def test_astro_val_unwraps_type1_array(self):
        result = _astro_val([1, [[0, "a"], [0, "b"]]])
        assert result == ["a", "b"]

    def test_astro_val_type1_nested_dicts(self):
        result = _astro_val([1, [[0, {"x": [0, 1]}], [0, {"x": [0, 2]}]]])
        assert result == [{"x": [0, 1]}, {"x": [0, 2]}]

    def test_astro_find_nested(self):
        data = {"a": [0, {"b": [0, {"cards": [0, [1, 2, 3]]}]}]}
        result = _astro_find(data, "cards")
        assert result == [1, 2, 3]

    def test_astro_find_not_found(self):
        assert _astro_find({"x": [0, 1]}, "missing") is None

    def test_manabox_extract_props_returns_encoded_value(self):
        html = '<astro-island props="{&quot;cards&quot;:[0,[]]}"></astro-island>'
        result = _manabox_extract_props(html)
        assert result == "{&quot;cards&quot;:[0,[]]}"

    def test_manabox_extract_props_missing_returns_empty(self):
        assert _manabox_extract_props("<html>no data</html>") == ""


class TestSideboardFiltering:
    def test_main_and_side_zones_returned(self):
        with patch(
            "src.deck_importer.requests.get",
            return_value=MagicMock(json=lambda: _MOXFIELD_FIXTURE, raise_for_status=lambda: None),
        ):
            result = fetch_deck("https://www.moxfield.com/decks/X")
        zones = {c.zone for c in result.cards}
        assert "main" in zones
        assert "side" in zones

    def test_deck_card_dataclass_fields(self):
        card = DeckCard("Test", "m21", "1", 3, "main")
        assert card.name == "Test"
        assert card.set_code == "m21"
        assert card.collector_number == "1"
        assert card.quantity == 3
        assert card.zone == "main"


class TestGameDeckAdapterAndRegistry:
    def test_registered_adapters(self):
        from src.deck_importer import _REGISTRY, get_adapter

        assert get_adapter("mtg") is not None
        assert get_adapter("lorcana") is not None
        assert get_adapter("onepiece") is not None
        assert get_adapter("riftbound") is not None
        assert len(_REGISTRY.all_adapters()) >= 4

    def test_custom_adapter_registration(self):
        from src.deck_importer import FetchedDeck, GameDeckAdapter, get_adapter, register_adapter

        class CustomAdapter(GameDeckAdapter):
            @property
            def game_name(self) -> str:
                return "custom_game"

            @property
            def display_name(self) -> str:
                return "Custom Game"

            def can_handle_url(self, url: str) -> bool:
                return "customgame.com" in url

            def can_handle_text(self, text: str) -> bool:
                return "custom_tag" in text

            def fetch_deck(self, url: str) -> FetchedDeck:
                return FetchedDeck(name="Custom Deck", cards=[])

            def parse_deck_list(self, text: str) -> FetchedDeck:
                return FetchedDeck(name="Parsed Custom", cards=[])

        adapter = CustomAdapter()
        register_adapter(adapter)
        assert get_adapter("custom_game") is adapter
        assert adapter.can_handle_url("https://customgame.com/deck/1")
        assert adapter.can_handle_text("custom_tag card")


class TestDetectGame:
    def test_detect_game_from_urls(self):
        from src.deck_importer import detect_game

        # MTG
        assert detect_game("https://www.moxfield.com/decks/123") == "mtg"
        assert detect_game("https://archidekt.com/decks/456") == "mtg"
        assert (
            detect_game("https://scryfall.com/@user/decks/00000000-0000-0000-0000-000000000001")
            == "mtg"
        )
        assert detect_game("https://deckstats.net/decks/1/2") == "mtg"
        assert detect_game("https://tappedout.net/mtg-decks/slug") == "mtg"
        assert detect_game("https://manabox.app/decks/abc") == "mtg"

        # Lorcana
        assert detect_game("https://lorcana.gg/decks/slug/") == "lorcana"
        assert detect_game("https://inkdecks.com/lorcana-metagame/deck-123") == "lorcana"
        assert detect_game("https://dreamborn.ink/decks/abc123") == "lorcana"
        assert detect_game("https://inktable.net/lorcana/deck/xyz") == "lorcana"

        # One Piece
        assert detect_game("https://onepiece.gg/decks/slug/") == "onepiece"
        assert detect_game("https://deckbuilder.egmanevents.com/?deck=OP01-001:1") == "onepiece"
        assert detect_game("https://deckbuilder.cardkaizoku.com/?deck=1xOP01-001") == "onepiece"
        assert detect_game("https://onepiecetopdecks.com/deck-list/op01") == "onepiece"
        assert detect_game("https://gumgum.gg/decks/123") == "onepiece"
        assert detect_game("https://play.limitlesstcg.com/decks/onepiece/123") == "onepiece"

        # Riftbound
        assert (
            detect_game(
                "https://piltoverarchive.com/decks/view/00000000-0000-0000-0000-000000000001"
            )
            == "riftbound"
        )
        assert detect_game("https://riftmana.com/decks/slug") == "riftbound"
        assert detect_game("https://riftbinder.com/decks/123") == "riftbound"
        assert (
            detect_game("https://riftdex.com/deck/00000000-0000-0000-0000-000000000001")
            == "riftbound"
        )
        assert detect_game("https://riftbound.gg/decks/slug/") == "riftbound"

    def test_detect_game_from_text(self):
        from src.deck_importer import detect_game

        assert detect_game("4x OP01-001\n1 ST01-001 (Leader)") == "onepiece"
        assert detect_game("4 Ariel - On Human Legs\n// Lorcana") == "lorcana"
        assert detect_game("1 Legend (Legend)\n4 Unit (Maindeck)\n// Riftbound") == "riftbound"
        assert (
            detect_game(
                "1 Urza, Lord High Artificer *CMDR*\n4 Lightning Bolt (LTR) 152\nSB: 2 Pyroclasm"
            )
            == "mtg"
        )


class TestParseDeckList:
    def test_parse_mtg_deck_list(self):
        from src.deck_importer import parse_deck_list

        text = """
// name: Red Blue Spells
// Commander
1 Urza, Lord High Artificer *CMDR*
// Mainboard
4 Lightning Bolt (LTR) 152
2 Counterspell [ICE:57]
// Sideboard
SB: 2 Pyroclasm
1 Hydroblast
"""
        deck = parse_deck_list(text, game="mtg")
        assert deck.name == "Red Blue Spells"
        assert deck.game == "mtg"
        assert len(deck.cards) == 5

        urza = next(c for c in deck.cards if "Urza" in c.name)
        assert urza.is_commander is True

        bolt = next(c for c in deck.cards if "Lightning Bolt" in c.name)
        assert bolt.quantity == 4
        assert bolt.set_code == "ltr"
        assert bolt.collector_number == "152"
        assert bolt.zone == "main"

        cs = next(c for c in deck.cards if "Counterspell" in c.name)
        assert cs.set_code == "ice"
        assert cs.collector_number == "57"

        side = [c for c in deck.cards if c.zone == "side"]
        assert len(side) == 2

    def test_parse_lorcana_deck_list(self):
        from src.deck_importer import parse_deck_list

        text = """
// name: Amber Steel
4 Ariel - On Human Legs
3 Robin Hood - Champion of Sherwood
"""
        deck = parse_deck_list(text, game="lorcana")
        assert deck.name == "Amber Steel"
        assert deck.game == "lorcana"
        assert len(deck.cards) == 2
        assert deck.cards[0].name == "Ariel - On Human Legs"
        assert deck.cards[0].quantity == 4

    def test_parse_onepiece_deck_list(self):
        from src.deck_importer import parse_deck_list

        text = """
// name: Red Luffy
1 ST01-001 (Leader)
4x OP01-004
"""
        deck = parse_deck_list(text, game="onepiece")
        assert deck.name == "Red Luffy"
        assert deck.game == "onepiece"
        assert len(deck.cards) == 2
        leader = next(c for c in deck.cards if c.zone == "leader")
        assert leader.collector_number == "ST01-001"
        assert leader.is_commander is True

    def test_parse_riftbound_deck_list(self):
        from src.deck_importer import parse_deck_list

        text = """
// name: Piltover Deck
Legend:
1 Caitlyn
Maindeck:
4 Enforcer
Sideboard:
2 Flashbomb
"""
        deck = parse_deck_list(text, game="riftbound")
        assert deck.name == "Piltover Deck"
        assert deck.game == "riftbound"
        assert len(deck.cards) == 3
        cait = next(c for c in deck.cards if c.zone == "legend")
        assert cait.name == "Caitlyn"


class TestMultiGameFetchDeck:
    def test_scryfall_fetch(self):
        from src.deck_importer import fetch_deck

        mock_data = {
            "name": "Scryfall Test",
            "format": "commander",
            "entries": {
                "commanders": [
                    {
                        "count": 1,
                        "card_digest": {
                            "name": "Atraxa",
                            "set": "cm2",
                            "collector_number": "1",
                            "type_line": "Legendary Creature",
                        },
                    }
                ],
                "mainboard": [
                    {
                        "count": 4,
                        "card_digest": {
                            "name": "Sol Ring",
                            "set": "cmd",
                            "collector_number": "261",
                        },
                    }
                ],
                "sideboard": [],
            },
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status.return_value = None

        with patch("src.deck_importer.requests.get", return_value=mock_resp):
            deck = fetch_deck(
                "https://scryfall.com/@user/decks/11111111-2222-3333-4444-555555555555"
            )

        assert deck.name == "Scryfall Test"
        assert deck.game == "mtg"
        assert len(deck.cards) == 2
        cmd = next(c for c in deck.cards if c.is_commander)
        assert cmd.name == "Atraxa"
        assert cmd.set_code == "cm2"

    def test_dreamborn_fetch(self):
        from src.deck_importer import fetch_deck

        mock_data = {
            "name": "Dreamborn Test",
            "cards": [
                {"id": "001-001", "name": "Ariel", "quantity": 4},
                {"id": "001-002", "name": "Elsa", "quantity": 2},
            ],
        }

        with patch("src.deck_importer.fetch_json", return_value=mock_data):
            deck = fetch_deck("https://dreamborn.ink/decks/abc123xyz")

        assert deck.name == "Dreamborn Test"
        assert deck.game == "lorcana"
        assert len(deck.cards) == 2
        assert deck.cards[0].name == "Ariel"
        assert deck.cards[0].quantity == 4

    def test_inktable_fetch(self):
        from src.deck_importer import fetch_deck

        mock_data = {
            "name": "Inktable Test",
            "cards": [
                {"id": "001-003", "name": "Mickey", "quantity": 3},
            ],
        }

        with patch("src.deck_importer.fetch_json", return_value=mock_data):
            deck = fetch_deck("https://inktable.net/lorcana/deck/ink123")

        assert deck.name == "Inktable Test"
        assert deck.game == "lorcana"
        assert len(deck.cards) == 1
        assert deck.cards[0].name == "Mickey"

    def test_gumgum_fetch(self):
        from src.deck_importer import fetch_deck

        mock_data = {
            "name": "GumGum Test",
            "cards": [
                {"id": "OP01-001", "name": "Zoro", "quantity": 1, "is_leader": True},
                {"id": "OP01-002", "name": "Luffy", "quantity": 4, "is_leader": False},
            ],
        }

        with patch("src.deck_importer.fetch_json", return_value=mock_data):
            deck = fetch_deck("https://gumgum.gg/decks/gg123")

        assert deck.name == "GumGum Test"
        assert deck.game == "onepiece"
        assert len(deck.cards) == 2
        leader = next(c for c in deck.cards if c.is_commander)
        assert leader.collector_number == "OP01-001"

    def test_limitless_fetch(self):
        from src.deck_importer import fetch_deck

        html = """
        <html>
        <h1>Limitless Zoro Deck</h1>
        <div>Card list: OP01-001 OP01-002 OP01-002</div>
        </html>
        """
        with patch("src.deck_importer.fetch_text", return_value=html):
            deck = fetch_deck("https://play.limitlesstcg.com/decks/onepiece/lim123")

        assert deck.name == "Limitless Zoro Deck"
        assert deck.game == "onepiece"
        assert len(deck.cards) == 2
        card_002 = next(c for c in deck.cards if c.collector_number == "OP01-002")
        assert card_002.quantity == 2

    def test_onepiecetopdecks_fetch(self):
        from src.deck_importer import fetch_deck

        html = """
        <html>
        <h1>Top Decks Tournament Winner</h1>
        <div>Cards: OP01-004 OP01-004 OP01-004 OP01-004</div>
        </html>
        """
        with patch("src.deck_importer.fetch_text", return_value=html):
            deck = fetch_deck("https://onepiecetopdecks.com/deck-list/winner-deck")

        assert deck.name == "Top Decks Tournament Winner"
        assert deck.game == "onepiece"
        assert len(deck.cards) == 1
        assert deck.cards[0].collector_number == "OP01-004"
        assert deck.cards[0].quantity == 4
