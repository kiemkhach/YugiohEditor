from __future__ import annotations

import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import URLError

from yugioh_editor.common.constants import LANGUAGE_PREFIXES
from yugioh_editor.infrastructure.official_card_client import (
    OFFICIAL_LOCALES,
    OfficialCardClient,
    _DetailParser,
    _SearchParser,
)


class _Response:
    def __init__(
        self, payload: str, content_type: str = "text/html; charset=utf-8"
    ) -> None:
        self.payload = payload.encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


def _detail(name: str, description: str) -> str:
    return f"""
    <html><div class="card_name">{name}</div>
    <div class="item_box_text">{description}</div>
    <span class="atk_power">ATK 3000</span>
    <span class="def_power">DEF 2500</span>
    <span class="level">Level 8</span>
    <span class="attribute">LIGHT</span>
    <span class="species">Dragon / Effect</span></html>
    """


class OfficialCardClientTests(unittest.TestCase):
    def test_current_search_rows_pair_names_with_their_own_hidden_cid(self):
        parser = _SearchParser()
        parser.feed(
            """
            <div class="t_row c_normal open">
              <span class="card_name">Other Card</span>
              <input type="hidden" class="link_value"
                value="/yugiohdb/card_search.action?ope=2&amp;cid=99">
            </div>
            <div class="t_row c_normal open">
              <span class="card_name">仮面魔獣デス・ガーディウス</span>
              <input type="hidden" class="link_value"
                value="/yugiohdb/card_search.action?ope=2&amp;cid=5097">
            </div>
            <a href="?cid=777">Related navigation</a>
            """
        )
        self.assertEqual(
            parser.candidates,
            [("Other Card", "99"), ("仮面魔獣デス・ガーディウス", "5097")],
        )

    def test_current_detail_is_scoped_to_main_card_set(self):
        parser = _DetailParser()
        parser.feed(
            """
            <div id="CardSet">
              <div id="cardname"><h1>Main Card</h1></div>
              <div id="CardImgSet"><img alt="Main Card" src="main.png"></div>
              <div class="item_box"><span class="item_box_title">ATK</span>
                   <span class="item_box_value">2000</span></div>
              <div class="item_box"><span class="item_box_title">DEF</span>
                   <span class="item_box_value">1200</span></div>
              <div class="item_box"><span class="item_box_title">Level</span>
                   <span class="item_box_value">6</span></div>
              <div class="item_box"><span class="item_box_title">Attribute</span>
                   <span class="item_box_value">DARK</span></div>
              <p class="species">Fiend / Effect</p>
              <div class="item_box_text"><span class="text_title">Card Text</span>
                   Main description.</div>
            </div>
            <div id="CardRelation"><div class="card_name">Related Card</div>
              <span class="atk_power">ATK 9999</span>
              <div class="item_box_text">Related description.</div></div>
            """
        )
        result = parser.result()
        self.assertEqual(result.name, "Main Card")
        self.assertEqual(result.description, "Main description.")
        self.assertEqual((result.attack, result.defense, result.level), (2000, 1200, 6))
        self.assertEqual(
            (result.attribute, result.card_type, result.card_category),
            ("dark", "fiend", "effect"),
        )

    def test_current_detail_ignores_separate_rename_info_block(self):
        parser = _DetailParser()
        parser.feed(
            """
            <div id="CardSet">
              <div id="cardname"><h1>
                Guardien Celtique Recyclé
                (Actualisé de : Gardien Celtique Recyclé)
              </h1></div>
              <div class="item_box_text">
                <div class="item_box_title">Texte de Carte</div>
                <div class="item_box_value">
                  Non destructible au combat avec un monstre qui a min. 1900 ATK.
                </div>
              </div>
              <div class="item_box_text">
                <div class="item_box_title">Info</div>
                <div class="item_box_value">
                  Nom de carte actualisé de "Gardien Celtique Recyclé"
                  le 22-07-2005.
                </div>
              </div>
            </div>
            """
        )
        result = parser.result()
        self.assertEqual(result.name, "Guardien Celtique Recyclé")
        self.assertEqual(
            result.description,
            "Non destructible au combat avec un monstre qui a min. 1900 ATK.",
        )

    def test_locale_registry_is_provider_specific_and_canonical(self):
        self.assertEqual(set(OFFICIAL_LOCALES), set(LANGUAGE_PREFIXES))
        self.assertEqual(
            dict(OFFICIAL_LOCALES),
            {
                "eng": "en",
                "fra": "fr",
                "jpn": "ja",
                "spa": "es",
                "ita": "it",
                "ger": "de",
            },
        )

    def test_one_search_then_same_cid_for_every_locale(self):
        search = (
            '<a href="card_search.action?ope=2&amp;cid=4007">Blue-Eyes White Dragon</a>'
        )
        names = {
            "eng": "Blue-Eyes White Dragon",
            "fra": "Dragon Blanc aux Yeux Bleus",
            "jpn": "Blue Eyes Japanese",
            "spa": "Dragon Blanco de Ojos Azules",
            "ita": "Drago Bianco Occhi Blu",
            "ger": "Blauaeugiger w. Drache",
        }
        responses = [_Response(search)] + [
            _Response(_detail(names[code], f"Description {code}"))
            for code in LANGUAGE_PREFIXES
        ]
        with patch(
            "yugioh_editor.infrastructure.official_card_client.urlopen",
            side_effect=responses,
        ) as opener:
            reference = OfficialCardClient(max_retries=0).fetch_card_reference(
                "Blue-Eyes White Dragon", "eng"
            )
        self.assertEqual(reference.canonical_id, "4007")
        self.assertEqual(dict(reference.localized_names), names)
        self.assertEqual(reference.attack, 3000)
        self.assertEqual(reference.defense, 2500)
        self.assertEqual(reference.level, 8)
        self.assertEqual(reference.attribute, "light")
        self.assertEqual(reference.card_type, "dragon")
        self.assertEqual(reference.card_category, "effect")
        self.assertEqual(opener.call_count, 1 + len(LANGUAGE_PREFIXES))
        urls = [call.args[0].full_url for call in opener.call_args_list]
        self.assertIn("keyword=Blue-Eyes+White+Dragon", urls[0])
        self.assertTrue(all("cid=4007" in url for url in urls[1:]))

    def test_unsupported_modern_category_is_not_fabricated(self):
        detail = _detail("Modern", "Text").replace(
            "Dragon / Effect", "Dragon / Synchro / Effect"
        )
        search = '<a href="?ope=2&amp;cid=99">Modern</a>'
        with patch(
            "yugioh_editor.infrastructure.official_card_client.urlopen",
            side_effect=[_Response(search)]
            + [_Response(detail)] * len(LANGUAGE_PREFIXES),
        ):
            reference = OfficialCardClient(max_retries=0).fetch_card_reference(
                "Modern", "eng"
            )
        self.assertIsNone(reference.card_category)

    def test_one_locale_failure_keeps_other_localized_values(self):
        search = '<a href="?ope=2&amp;cid=7">Known</a>'
        responses = [_Response(search)]
        for code in LANGUAGE_PREFIXES:
            responses.append(
                URLError("locale unavailable")
                if code == "fra"
                else _Response(_detail(f"Name {code}", f"Text {code}"))
            )
        with patch(
            "yugioh_editor.infrastructure.official_card_client.urlopen",
            side_effect=responses,
        ):
            reference = OfficialCardClient(max_retries=0).fetch_card_reference(
                "Known", "eng"
            )
        self.assertNotIn("fra", reference.localized_names)
        self.assertEqual(reference.localized_names["eng"], "Name eng")


if __name__ == "__main__":
    unittest.main()
