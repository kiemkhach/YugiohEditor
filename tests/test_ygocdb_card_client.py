from __future__ import annotations

import json
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from yugioh_editor.common.card_errors import (
    CardReferenceAmbiguityError,
    JapaneseReadingCrawlError,
    JapaneseReadingNotFoundError,
)
from yugioh_editor.common.constants import LANGUAGE_PREFIXES
from yugioh_editor.infrastructure.ygocdb_card_client import (
    _LOCALIZED_NAME_FIELDS,
    YgocdbCardClient,
    _compact_api_match_name,
    _normalize_api_match_name,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._data


class _RawResponse(_Response):
    def __init__(self, data: bytes) -> None:
        self._data = data


class TestYgocdbCardClient(unittest.TestCase):
    def test_provider_language_fields_are_canonical(self):
        self.assertLessEqual(set(_LOCALIZED_NAME_FIELDS), set(LANGUAGE_PREFIXES))

    def test_constructor_rejects_invalid_retry_and_timeout_values(self):
        for timeout in (0, -1, float("nan"), float("inf"), True, "invalid"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                YgocdbCardClient(timeout_seconds=timeout)
        for retries in (-1, 1.5, True, "2"):
            with self.subTest(retries=retries), self.assertRaises(ValueError):
                YgocdbCardClient(max_retries=retries)

    def test_request_uses_encoded_original_query_and_headers(self):
        client = YgocdbCardClient(max_retries=0)
        payload = {"result": [{"cid": 1, "jp_name": "青眼の白龍", "jp_ruby": "ルビ"}]}
        with patch(
            "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
            return_value=_Response(payload),
        ) as opener:
            self.assertEqual(client.fetch_japanese_reading("青眼の白龍"), "ルビ")
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            request.full_url,
            "https://ygocdb.com/api/v0/?search=%E9%9D%92%E7%9C%BC%E3%81%AE%E7%99%BD%E9%BE%8D",
        )
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("Accept-language"), "ja")

    def test_fetch_card_password_returns_exact_uppercase_width_and_zfills_integer(self):
        fixtures = (
            ("Leading Zero", 1_234_567, "01234567"),
            ("Hex Password", "00abcdef", "00ABCDEF"),
        )
        for card_name, provider_id, expected in fixtures:
            with (
                self.subTest(card_name=card_name),
                patch(
                    "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
                    return_value=_Response(
                        {
                            "result": [
                                {
                                    "id": provider_id,
                                    "en_name": card_name,
                                }
                            ]
                        }
                    ),
                ) as opener,
            ):
                password = YgocdbCardClient(max_retries=0).fetch_card_password(
                    card_name, "eng"
                )

            self.assertEqual(password, expected)
            self.assertEqual(len(password), 8)
            self.assertEqual(password, password.upper())
            self.assertEqual(opener.call_count, 1)
            self.assertIn("?search=", opener.call_args.args[0].full_url)

    def test_fetch_card_password_deduplicates_same_id_and_selects_first_distinct_id(
        self,
    ):
        client = YgocdbCardClient(max_retries=0)
        duplicate = {
            "result": [
                {"id": 12_345_678, "en_name": "Exact Name"},
                {"id": "12345678", "en_name": "Exact Name"},
            ]
        }
        with patch(
            "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
            return_value=_Response(duplicate),
        ) as opener:
            self.assertEqual(
                client.fetch_card_password("Exact Name", "eng"),
                "12345678",
            )
        self.assertEqual(opener.call_count, 1)

        distinct = {
            "result": [
                {"id": 12_345_678, "en_name": "Exact Name"},
                {"id": 87_654_321, "en_name": "Exact Name"},
            ]
        }
        with patch(
            "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
            return_value=_Response(distinct),
        ) as opener:
            self.assertEqual(
                client.fetch_card_password("Exact Name", "eng"),
                "12345678",
            )
        self.assertEqual(opener.call_count, 1)

    def test_english_raw_match_selection_preserves_provider_order(self):
        select = YgocdbCardClient._select_reference_candidate
        first = {"en_name": "Exact Name", "id": 12_345_678}
        second = {"en_name": "Exact Name", "id": 87_654_321}

        selected, confidence = select("Exact Name", "eng", [first, second])
        self.assertIs(selected, first)
        self.assertEqual(confidence, "raw_exact")

        selected, confidence = select("Exact Name", "eng", [second, first])
        self.assertIs(selected, second)
        self.assertEqual(confidence, "raw_exact")

    def test_english_normalized_match_selection_preserves_provider_order(self):
        select = YgocdbCardClient._select_reference_candidate
        first = {"en_name": "Exact Name", "id": 12_345_678}
        second = {"en_name": "EXACT NAME", "id": 87_654_321}

        selected, confidence = select("exact name", "eng", [first, second])
        self.assertIs(selected, first)
        self.assertEqual(confidence, "normalized_exact")

        selected, confidence = select("exact name", "eng", [second, first])
        self.assertIs(selected, second)
        self.assertEqual(confidence, "normalized_exact")

    def test_fetch_card_reference_fetches_detail_for_first_english_match(self):
        search = {
            "result": [
                {"id": 12_345_678, "en_name": "Exact Name"},
                {"id": 87_654_321, "en_name": "Exact Name"},
            ]
        }
        detail = {"result": {"id": 12_345_678}}
        with patch(
            "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
            side_effect=[_Response(search), _Response(detail)],
        ) as opener:
            reference = YgocdbCardClient(max_retries=0).fetch_card_reference(
                "Exact Name",
                "eng",
            )

        self.assertIsNotNone(reference)
        self.assertEqual(reference.password, "12345678")
        self.assertEqual(reference.confidence, "raw_exact")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(
            opener.call_args_list[1].args[0].full_url,
            "https://ygocdb.com/api/v0/card/12345678?show=all",
        )

    def test_non_english_reference_ambiguity_is_unchanged(self):
        select = YgocdbCardClient._select_reference_candidate
        with self.assertRaisesRegex(
            CardReferenceAmbiguityError,
            "multiple raw jpn matches",
        ):
            select(
                "Same",
                "jpn",
                [
                    {"jp_name": "Same", "id": 1},
                    {"jp_name": "Same", "id": 2},
                ],
            )
        with self.assertRaisesRegex(
            CardReferenceAmbiguityError,
            "multiple normalized fra matches",
        ):
            select(
                "\uff2e\uff4f\uff4d",
                "fra",
                [
                    {"fr_name": "Nom", "id": 1},
                    {"fr_name": "Nom", "id": 2},
                ],
            )

    def test_selection_order_exact_normalized_compact_and_not_found(self):
        select = YgocdbCardClient._select_reading_from_results
        self.assertEqual(
            select(
                "名前",
                [
                    {"cid": 1, "jp_name": "名前", "jp_ruby": "FIRST"},
                    {"cid": 2, "jp_name": "名前", "jp_ruby": "SECOND"},
                ],
            ),
            "FIRST",
        )
        self.assertEqual(
            select("Ａ－Ｂ", [{"cid": 1, "jp_name": "A―B", "jp_ruby": "NORMAL"}]),
            "NORMAL",
        )
        self.assertEqual(
            select("A B", [{"cid": 1, "jp_name": "ＡＢ", "jp_ruby": "COMPACT"}]),
            "COMPACT",
        )
        with self.assertRaisesRegex(JapaneseReadingNotFoundError, "名前"):
            select("名前", [{"cid": 1, "jp_name": "近似", "jp_ruby": "WRONG"}])

    def test_selection_rejects_ambiguity_invalid_shape_and_empty_ruby(self):
        select = YgocdbCardClient._select_reading_from_results
        for query, candidates in (
            (
                "Ａ",
                [
                    {"cid": 1, "jp_name": "A", "jp_ruby": "ONE"},
                    {"cid": 2, "jp_name": "Ａ ", "jp_ruby": "TWO"},
                ],
            ),
            (
                "A BC",
                [
                    {"cid": 1, "jp_name": "ABC", "jp_ruby": "ONE"},
                    {"cid": 2, "jp_name": "AB C", "jp_ruby": "TWO"},
                ],
            ),
        ):
            with (
                self.subTest(query=query),
                self.assertRaisesRegex(
                    JapaneseReadingCrawlError,
                    "ambiguous",
                ),
            ):
                select(query, candidates)
        with self.assertRaises(JapaneseReadingCrawlError):
            select("名前", ["invalid"])
        for invalid_cid in (True, False, 1.0, 1.5, "not-an-integer"):
            with (
                self.subTest(invalid_cid=invalid_cid),
                self.assertRaisesRegex(JapaneseReadingCrawlError, "invalid cid"),
            ):
                select(
                    "名前",
                    [{"cid": invalid_cid, "jp_name": "別名", "jp_ruby": "ルビ"}],
                )
        self.assertEqual(
            select(
                "名前",
                [{"cid": "1", "jp_name": "名前", "jp_ruby": "ルビ"}],
            ),
            "ルビ",
        )
        with self.assertRaisesRegex(JapaneseReadingCrawlError, "jp_ruby"):
            select("名前", [{"jp_name": "名前", "jp_ruby": ""}])

    def test_response_shape_empty_and_invalid_json(self):
        client = YgocdbCardClient(max_retries=0)
        for payload, error in (
            ([], JapaneseReadingCrawlError),
            ({"result": {}}, JapaneseReadingCrawlError),
            ({}, JapaneseReadingNotFoundError),
            ({"result": []}, JapaneseReadingNotFoundError),
        ):
            with (
                self.subTest(payload=payload),
                patch(
                    "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
                    return_value=_Response(payload),
                ),
                self.assertRaises(error),
            ):
                client.fetch_japanese_reading("未登録")
        with (
            patch(
                "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
                return_value=_RawResponse(b"not json"),
            ),
            self.assertRaisesRegex(JapaneseReadingCrawlError, "invalid JSON"),
        ):
            client.fetch_japanese_reading("未登録")

    def test_transport_retry_policy_uses_the_same_url(self):
        client = YgocdbCardClient(timeout_seconds=0.01, max_retries=2)
        headers = Message()
        headers["Retry-After"] = "0"
        payload = {"result": [{"cid": 1, "jp_name": "名前", "jp_ruby": "ナマエ"}]}
        with (
            patch(
                "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
                side_effect=[
                    TimeoutError("slow"),
                    HTTPError("url", 503, "busy", headers, None),
                    _Response(payload),
                ],
            ) as opener,
            patch("yugioh_editor.infrastructure.ygocdb_card_client.time.sleep"),
        ):
            self.assertEqual(client.fetch_japanese_reading("名前"), "ナマエ")
        self.assertEqual(opener.call_count, 3)
        self.assertEqual(
            len({call.args[0].full_url for call in opener.call_args_list}),
            1,
        )

        non_retryable = HTTPError("url", 400, "bad", Message(), None)
        with (
            patch(
                "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
                side_effect=non_retryable,
            ) as opener,
            self.assertRaises(JapaneseReadingCrawlError),
        ):
            client.fetch_japanese_reading("名前")
        self.assertEqual(opener.call_count, 1)

    def test_name_match_normalizers(self):
        self.assertEqual(_normalize_api_match_name(" Ａ　B―･’ "), "A B-・'")
        self.assertEqual(_compact_api_match_name(" Ａ　B "), "AB")

    def test_card_reference_search_detail_and_domain_mapping(self):
        search = {
            "result": [
                {
                    "cid": 4007,
                    "id": 89631139,
                    "jp_name": "Blue Eyes Japanese",
                    "en_name": "Blue-Eyes White Dragon",
                    "weight": 100,
                    "data": {
                        "type": 0x11,
                        "atk": 3000,
                        "def": 2500,
                        "level": 8,
                        "race": 0x2000,
                        "attribute": 0x10,
                    },
                    "text": {"desc": "Language is not identified"},
                }
            ]
        }
        detail = {
            "result": {
                "id": 89631139,
                "data": search["result"][0]["data"],
            }
        }
        with patch(
            "yugioh_editor.infrastructure.ygocdb_card_client.urlopen",
            side_effect=[_Response(search), _Response(detail)],
        ) as opener:
            reference = YgocdbCardClient(max_retries=0).fetch_card_reference(
                "Blue-Eyes White Dragon",
                "eng",
            )
        self.assertIsNotNone(reference)
        self.assertEqual(reference.password, "89631139")
        self.assertEqual(reference.attack, 3000)
        self.assertEqual(reference.defense, 2500)
        self.assertEqual(reference.level, 8)
        self.assertEqual(reference.attribute, "light")
        self.assertEqual(reference.card_type, "dragon")
        self.assertEqual(reference.card_category, "normal")
        self.assertEqual(dict(reference.localized_descriptions), {})
        self.assertEqual(
            opener.call_args_list[0].args[0].full_url,
            "https://ygocdb.com/api/v0/?search=Blue-Eyes+White+Dragon",
        )
        self.assertEqual(
            opener.call_args_list[1].args[0].full_url,
            "https://ygocdb.com/api/v0/card/89631139?show=all",
        )

    def test_reference_candidate_language_rules_and_ambiguity(self):
        select = YgocdbCardClient._select_reference_candidate
        first = {"en_name": "NAME", "id": 1}
        duplicate = {"en_name": "NAME", "id": "1"}
        second = {"en_name": "NAME", "id": 2}
        self.assertIs(select("NAME", "eng", [first, duplicate])[0], first)
        self.assertIs(select("NAME", "eng", [first, second])[0], first)
        missing_id_first = {"en_name": "NAME"}
        self.assertIs(
            select("NAME", "eng", [missing_id_first, {"en_name": "NAME"}])[0],
            missing_id_first,
        )
        normalized = {"en_name": "Name", "id": 3}
        self.assertIs(select("name", "eng", [normalized])[0], normalized)
        weighted = {"weight": 100, "en_name": "Known"}
        self.assertIs(select("Nom", "fra", [weighted])[0], weighted)
        with self.assertRaises(CardReferenceAmbiguityError):
            select(
                "Nom",
                "fra",
                [{"weight": 100}, {"weight": "100"}],
            )
        self.assertIsNone(select("Near", "eng", [{"en_name": "Nearby"}]))

    def test_reference_description_metadata_and_unsupported_bits(self):
        candidate = {
            "en_name": "Known",
            "data": {
                "type": 0x4000,
                "race": 0x03,
                "attribute": 0x03,
                "level": 99,
                "atk": -1,
                "def": 99999,
            },
            "texts": {
                "eng": {"desc": "English description"},
                "jpn": {"desc": "Japanese description"},
            },
        }
        reference = YgocdbCardClient._build_card_reference(
            "Known",
            "eng",
            candidate,
            {},
            "raw_exact",
        )
        self.assertEqual(
            dict(reference.localized_descriptions),
            {
                "eng": "English description",
                "jpn": "Japanese description",
            },
        )
        self.assertIsNone(reference.attribute)
        self.assertIsNone(reference.card_type)
        self.assertIsNone(reference.card_category)
        self.assertIsNone(reference.level)
        self.assertIsNone(reference.attack)
        self.assertIsNone(reference.defense)

    def test_reference_localized_text_uses_shared_rename_normalization(self):
        candidate = {
            "fr_name": (
                "Guardien Celtique Recyclé (Actualisé de : Gardien Celtique Recyclé)"
            ),
            "texts": {
                "fra": {
                    "desc": (
                        "Non destructible au combat. Nom de carte actualisé de "
                        '"Gardien Celtique Recyclé" le 22-07-2005.'
                    )
                }
            },
        }
        reference = YgocdbCardClient._build_card_reference(
            "Guardien Celtique Recyclé",
            "fra",
            candidate,
            {},
            "raw_exact",
        )
        self.assertEqual(
            dict(reference.localized_names),
            {"fra": "Guardien Celtique Recyclé"},
        )
        self.assertEqual(
            dict(reference.localized_descriptions),
            {"fra": "Non destructible au combat."},
        )


if __name__ == "__main__":
    unittest.main()
