from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, RLock
from unittest.mock import MagicMock, Mock, call, patch

import pandas as pd

from yugioh_editor.common.card_errors import (
    CardImageError,
    CardReferenceAmbiguityError,
    CardReferenceDataConflictError,
    CardReferenceDataResourceError,
    CardSuggestionError,
    JapaneseReadingCrawlError,
    JapaneseReadingNotFoundError,
)
from yugioh_editor.models.card_editing import CardReferenceData
from yugioh_editor.services.card_reference_data_service import (
    CardReferenceDataService,
)


class _EntryCountingLock:
    def __init__(self, expected_entries: int) -> None:
        self._lock = RLock()
        self._expected_entries = expected_entries
        self._entries = 0
        self.reached = Event()

    def __enter__(self):
        self._lock.acquire()
        self._entries += 1
        if self._entries >= self._expected_entries:
            self.reached.set()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self._lock.release()


class TestCardReferenceDataService(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.resource = self.root / "card_reading_jpn.csv"
        self._write_rows([("青眼の白龍", "ブルーアイズ")])
        self.mocked_ygocdb_card_client = Mock()
        self.mocked_official_card_client = Mock()
        self.mocked_alias_client = Mock()
        self.mocked_image_client = Mock()
        self.mocked_ygocdb_card_client.fetch_card_password.return_value = None

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_rows(self, rows: list[tuple[str, str]]) -> None:
        pd.DataFrame(
            rows,
            columns=["display_name_jpn", "reading_jpn"],
        ).to_csv(
            self.resource,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )

    def _service(self) -> CardReferenceDataService:
        return CardReferenceDataService(
            japanese_reading_resource_path=self.resource,
            ygocdb_client=self.mocked_ygocdb_card_client,
            official_card_client=self.mocked_official_card_client,
            image_client=self.mocked_image_client,
            yugipedia_alias_client=self.mocked_alias_client,
        )

    @staticmethod
    def _reference(name: str) -> CardReferenceData:
        return CardReferenceData(
            matched_name=name,
            matched_language="eng",
            localized_names={"eng": name},
            localized_descriptions={"eng": "Description"},
        )

    def test_get_japanese_reading_from_resource(self):
        service = self._service()
        self.assertEqual(
            service.get_japanese_reading("青眼の白龍"),
            "ブルーアイズ",
        )
        self.mocked_ygocdb_card_client.fetch_japanese_reading.assert_not_called()

    def test_try_get_japanese_reading_returns_none(self):
        service = self._service()
        self.assertIsNone(service.try_get_japanese_reading("未登録"))
        self.mocked_ygocdb_card_client.fetch_japanese_reading.assert_not_called()

    def test_get_japanese_reading_crawls_when_missing_and_caches(self):
        self.mocked_ygocdb_card_client.fetch_japanese_reading.return_value = (
            "ミトウロク"
        )
        service = self._service()
        self.assertEqual(service.get_japanese_reading("未登録"), "ミトウロク")
        self.assertEqual(service.get_japanese_reading("未登録"), "ミトウロク")
        self.mocked_ygocdb_card_client.fetch_japanese_reading.assert_called_once_with(
            "未登録"
        )
        self.assertEqual(
            self._service().try_get_japanese_reading("未登録"),
            "ミトウロク",
        )

    def test_network_lookup_runs_outside_the_resource_lock(self):
        service = self._service()
        lock = MagicMock()
        service._lock = lock

        def fetch(_name):
            self.assertEqual(lock.__exit__.call_count, 1)
            return "ミトウロク"

        self.mocked_ygocdb_card_client.fetch_japanese_reading.side_effect = fetch
        self.assertEqual(service.get_japanese_reading("未登録"), "ミトウロク")
        self.assertEqual(lock.__enter__.call_count, 2)
        self.assertEqual(lock.__exit__.call_count, 2)

    def test_allow_crawl_false_does_not_call_remote_api(self):
        service = self._service()
        with self.assertRaisesRegex(JapaneseReadingNotFoundError, "未登録"):
            service.get_japanese_reading("未登録", allow_crawl=False)
        self.mocked_ygocdb_card_client.fetch_japanese_reading.assert_not_called()

    def test_crawl_japanese_reading_delegates_to_ygocdb_client(self):
        self.mocked_ygocdb_card_client.fetch_japanese_reading.return_value = "ルビ"
        service = self._service()
        self.assertEqual(service.crawl_japanese_reading("名前"), "ルビ")
        self.mocked_ygocdb_card_client.fetch_japanese_reading.assert_called_once_with(
            "名前"
        )

    def test_failed_crawl_does_not_modify_resource_or_cache(self):
        service = self._service()
        original = self.resource.read_bytes()
        for error in (
            JapaneseReadingNotFoundError("未登録"),
            JapaneseReadingCrawlError("未登録"),
        ):
            self.mocked_ygocdb_card_client.fetch_japanese_reading.side_effect = error
            with (
                self.subTest(error=type(error).__name__),
                self.assertRaises(type(error)),
            ):
                service.get_japanese_reading("未登録")
            self.assertEqual(self.resource.read_bytes(), original)
            self.assertIsNone(service.try_get_japanese_reading("未登録"))

    def test_add_japanese_reading_mapping_appends_utf8_sig_row(self):
        service = self._service()
        service.add_japanese_reading_mapping("新規", "シンキ")
        dataframe = pd.read_csv(
            self.resource,
            dtype=str,
            encoding="utf-8-sig",
            keep_default_na=False,
        )
        self.assertEqual(
            list(dataframe.columns),
            ["display_name_jpn", "reading_jpn"],
        )
        self.assertEqual(dataframe.iloc[-1].tolist(), ["新規", "シンキ"])
        self.assertEqual(self.resource.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_japanese_reading_conflict_does_not_overwrite(self):
        service = self._service()
        service.add_japanese_reading_mapping("新規", "シンキ")
        original = self.resource.read_bytes()
        service.add_japanese_reading_mapping("新規", "シンキ")
        self.assertEqual(self.resource.read_bytes(), original)
        with self.assertRaisesRegex(CardReferenceDataConflictError, "新規"):
            service.add_japanese_reading_mapping("新規", "ベツ")
        self.assertEqual(self.resource.read_bytes(), original)

    def test_atomic_write_failures_preserve_resource_and_cache(self):
        for target in (
            "pandas.DataFrame.to_csv",
            "yugioh_editor.services.card_reference_data_service.os.replace",
        ):
            with self.subTest(target=target):
                self._write_rows([("青眼の白龍", "ブルーアイズ")])
                service = self._service()
                original = self.resource.read_bytes()
                with (
                    patch(target, side_effect=OSError("controlled")),
                    self.assertRaisesRegex(CardReferenceDataResourceError, "新規"),
                ):
                    service.add_japanese_reading_mapping("新規", "シンキ")
                self.assertEqual(self.resource.read_bytes(), original)
                self.assertIsNone(service.try_get_japanese_reading("新規"))
                self.assertFalse(
                    self.resource.with_name(f".{self.resource.name}.tmp").exists()
                )

    def test_resource_validation_rejects_invalid_tables(self):
        invalid_frames = (
            pd.DataFrame({"display_name_jpn": ["A"]}),
            pd.DataFrame(
                {
                    "display_name_jpn": ["A"],
                    "reading_jpn": ["B"],
                    "extra": ["C"],
                }
            ),
            pd.DataFrame({"display_name_jpn": [" "], "reading_jpn": ["B"]}),
            pd.DataFrame({"display_name_jpn": ["A"], "reading_jpn": [""]}),
            pd.DataFrame(
                {
                    "display_name_jpn": ["A", "A"],
                    "reading_jpn": ["B", "C"],
                }
            ),
        )
        for dataframe in invalid_frames:
            with self.subTest(columns=list(dataframe.columns)):
                dataframe.to_csv(
                    self.resource,
                    index=False,
                    encoding="utf-8-sig",
                )
                with self.assertRaises(CardReferenceDataResourceError):
                    self._service()

    def test_reload_refreshes_cache_and_default_path_ignores_cwd(self):
        service = self._service()
        self._write_rows([("別名", "ベツメイ")])
        service.reload()
        self.assertIsNone(service.try_get_japanese_reading("青眼の白龍"))
        self.assertEqual(service.try_get_japanese_reading("別名"), "ベツメイ")

        previous = Path.cwd()
        try:
            os.chdir(self.root)
            default = CardReferenceDataService(
                ygocdb_client=self.mocked_ygocdb_card_client
            )
        finally:
            os.chdir(previous)
        self.assertEqual(
            default.japanese_reading_resource_path.name,
            "card_reading_jpn.csv",
        )
        self.assertIsNotNone(default.try_get_japanese_reading("地縛霊"))

    def test_default_japanese_readings_do_not_call_remote_api(self):
        service = CardReferenceDataService(ygocdb_client=self.mocked_ygocdb_card_client)
        dataframe = pd.read_csv(
            service.japanese_reading_resource_path,
            dtype=str,
            encoding="utf-8-sig",
            keep_default_na=False,
        )
        readings = [
            service.get_japanese_reading(name) for name in dataframe["display_name_jpn"]
        ]
        self.assertEqual(len(readings), len(dataframe))
        self.mocked_ygocdb_card_client.fetch_japanese_reading.assert_not_called()

    def test_suggest_card_reference_validates_and_delegates(self):
        expected = object()
        self.mocked_official_card_client.fetch_card_reference.return_value = expected
        service = self._service()
        self.assertIs(
            service.suggest_card_reference("Blue-Eyes White Dragon", "ENG"),
            expected,
        )
        self.mocked_official_card_client.fetch_card_reference.assert_called_once_with(
            "Blue-Eyes White Dragon",
            "eng",
        )
        for name, language in (("", "eng"), ("Name", "xxx")):
            with (
                self.subTest(name=name, language=language),
                self.assertRaises(ValueError),
            ):
                service.suggest_card_reference(name, language)

    def test_official_direct_success_does_not_call_fallbacks(self):
        expected = self._reference("Current")
        self.mocked_official_card_client.fetch_card_reference.return_value = expected
        result = self._service().suggest_card_reference("Current", "eng")
        self.assertEqual(result.source, "official_direct")
        self.mocked_alias_client.resolve_alias.assert_not_called()
        self.mocked_ygocdb_card_client.fetch_card_reference.assert_not_called()

    def test_official_success_enriches_password_with_canonical_english_name(self):
        reference = CardReferenceData(
            matched_name="Japanese Name",
            matched_language="jpn",
            localized_names={
                "eng": "Canonical English Name",
                "jpn": "Japanese Name",
            },
            localized_descriptions={"jpn": "Description"},
        )
        self.mocked_official_card_client.fetch_card_reference.return_value = reference
        self.mocked_ygocdb_card_client.fetch_card_password.return_value = "01234567"

        result = self._service().suggest_card_reference("Japanese Query", "jpn")

        self.assertEqual(result.password, "01234567")
        self.assertEqual(result.source, "official_direct")
        self.mocked_ygocdb_card_client.fetch_card_password.assert_called_once_with(
            "Canonical English Name",
            "eng",
        )
        self.mocked_ygocdb_card_client.fetch_card_reference.assert_not_called()
        self.mocked_alias_client.resolve_alias.assert_not_called()

    def test_official_missing_sentinel_is_enriched_instead_of_cached_as_password(self):
        reference = CardReferenceData(
            matched_name="Canonical Name",
            matched_language="eng",
            localized_names={"eng": "Canonical Name"},
            localized_descriptions={},
            password="FFFFFFFF",
        )
        self.mocked_official_card_client.fetch_card_reference.return_value = reference
        self.mocked_ygocdb_card_client.fetch_card_password.return_value = "00123456"

        result = self._service().suggest_card_reference("Canonical Name", "eng")

        self.assertEqual(result.password, "00123456")
        self.mocked_ygocdb_card_client.fetch_card_password.assert_called_once_with(
            "Canonical Name",
            "eng",
        )

    def test_transient_password_enrichment_failure_returns_official_and_retries(self):
        reference = self._reference("Canonical Name")
        self.mocked_official_card_client.fetch_card_reference.return_value = reference
        self.mocked_ygocdb_card_client.fetch_card_password.side_effect = (
            CardSuggestionError("temporary outage"),
            "00123456",
        )
        service = self._service()

        with self.assertLogs(level="WARNING"):
            first = service.suggest_card_reference("Canonical Name", "eng")
        second = service.suggest_card_reference(" canonical   name ", "eng")
        third = service.suggest_card_reference("Canonical Name", "eng")

        self.assertIsNone(first.password)
        self.assertEqual(first.source, "official_direct")
        self.assertEqual(second.password, "00123456")
        self.assertIs(second, third)
        self.assertEqual(
            self.mocked_official_card_client.fetch_card_reference.call_count,
            2,
        )
        self.assertEqual(
            self.mocked_ygocdb_card_client.fetch_card_password.call_count,
            2,
        )

    def test_ambiguous_password_enrichment_is_deterministic_and_cacheable(self):
        reference = self._reference("Canonical Name")
        self.mocked_official_card_client.fetch_card_reference.return_value = reference
        self.mocked_ygocdb_card_client.fetch_card_password.side_effect = (
            CardReferenceAmbiguityError("multiple provider matches")
        )
        service = self._service()

        with self.assertLogs(level="WARNING"):
            first = service.suggest_card_reference("Canonical Name", "eng")
        second = service.suggest_card_reference(" canonical   name ", "eng")

        self.assertIsNone(first.password)
        self.assertIs(first, second)
        self.mocked_official_card_client.fetch_card_reference.assert_called_once_with(
            "Canonical Name",
            "eng",
        )
        self.mocked_ygocdb_card_client.fetch_card_password.assert_called_once_with(
            "Canonical Name",
            "eng",
        )

    def test_alias_retries_official_and_preserves_provider_metadata(self):
        expected = self._reference("Slime Toad")
        self.mocked_official_card_client.fetch_card_reference.side_effect = [
            None,
            expected,
        ]
        self.mocked_alias_client.resolve_alias.return_value = "Slime Toad"
        result = self._service().suggest_card_reference("Frog the Jam", "eng")
        self.assertEqual(result.source, "official_after_alias")
        self.assertEqual(result.matched_name, "Slime Toad")
        self.mocked_official_card_client.fetch_card_reference.assert_any_call(
            "Slime Toad", "eng"
        )
        self.mocked_ygocdb_card_client.fetch_card_reference.assert_not_called()

    def test_official_error_uses_ygocdb_fallback(self):
        self.mocked_official_card_client.fetch_card_reference.side_effect = (
            CardSuggestionError("offline")
        )
        self.mocked_alias_client.resolve_alias.return_value = None
        self.mocked_ygocdb_card_client.fetch_card_reference.return_value = (
            self._reference("Fallback")
        )
        result = self._service().suggest_card_reference("Fallback", "eng")
        self.assertEqual(result.source, "ygocdb_fallback")

    def test_negative_lookup_is_cached_and_ambiguity_is_not_swallowed(self):
        self.mocked_official_card_client.fetch_card_reference.return_value = None
        self.mocked_alias_client.resolve_alias.return_value = None
        self.mocked_ygocdb_card_client.fetch_card_reference.return_value = None
        service = self._service()
        self.assertIsNone(service.suggest_card_reference("Missing", "eng"))
        self.assertIsNone(service.suggest_card_reference(" missing ", "eng"))
        self.mocked_official_card_client.fetch_card_reference.assert_called_once()
        self.mocked_ygocdb_card_client.fetch_card_reference.assert_called_once()

        ambiguous_service = self._service()
        self.mocked_official_card_client.fetch_card_reference.side_effect = (
            CardReferenceAmbiguityError("ambiguous")
        )
        with self.assertRaises(CardReferenceAmbiguityError):
            ambiguous_service.suggest_card_reference("Ambiguous", "eng")

    def test_concurrent_equivalent_lookups_share_one_request_and_result(self):
        expected = self._reference("Canonical Name")
        service = self._service()
        counting_lock = _EntryCountingLock(expected_entries=4)
        service._lock = counting_lock

        def fetch(_name, _language):
            self.assertTrue(counting_lock.reached.wait(timeout=2))
            return expected

        self.mocked_official_card_client.fetch_card_reference.side_effect = fetch
        start = Barrier(4)
        names = (
            "Canonical Name",
            " canonical name ",
            "CANONICAL NAME",
            "Canonical   Name",
        )

        def lookup(name):
            start.wait(timeout=2)
            return service.suggest_card_reference(name, "eng")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(lookup, name) for name in names]
            results = [future.result(timeout=3) for future in futures]

        self.assertTrue(all(result is results[0] for result in results))
        self.mocked_official_card_client.fetch_card_reference.assert_called_once()
        self.assertEqual(service._lookup_in_flight, {})

    def test_in_flight_waiter_has_finite_timeout_and_owner_still_cleans_up(self):
        expected = self._reference("Canonical Name")
        service = self._service()
        owner_started = Event()
        release_owner = Event()

        def fetch(_name, _language):
            owner_started.set()
            self.assertTrue(release_owner.wait(timeout=2))
            return expected

        self.mocked_official_card_client.fetch_card_reference.side_effect = fetch
        with ThreadPoolExecutor(max_workers=1) as executor:
            owner = executor.submit(
                service.suggest_card_reference,
                "Canonical Name",
                "eng",
            )
            self.assertTrue(owner_started.wait(timeout=2))
            try:
                with (
                    patch(
                        "yugioh_editor.services.card_reference_data_service."
                        "_IN_FLIGHT_WAIT_TIMEOUT_SECONDS",
                        0.01,
                    ),
                    self.assertRaises(TimeoutError),
                ):
                    service.suggest_card_reference(" canonical name ", "eng")
            finally:
                release_owner.set()
            owner_result = owner.result(timeout=2)

        self.mocked_official_card_client.fetch_card_reference.assert_called_once()
        self.assertEqual(service._lookup_in_flight, {})
        self.assertEqual(owner_result.matched_name, expected.matched_name)
        self.assertIs(
            service.suggest_card_reference("CANONICAL NAME", "eng"),
            owner_result,
        )

    def test_different_lookup_keys_run_fallbacks_in_parallel(self):
        self.mocked_official_card_client.fetch_card_reference.return_value = None
        self.mocked_alias_client.resolve_alias.return_value = None
        provider_barrier = Barrier(2)

        def fetch_fallback(name, _language):
            provider_barrier.wait(timeout=2)
            return self._reference(name)

        self.mocked_ygocdb_card_client.fetch_card_reference.side_effect = fetch_fallback
        service = self._service()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(service.suggest_card_reference, name, "eng")
                for name in ("First", "Second")
            ]
            results = [future.result(timeout=3) for future in futures]

        self.assertEqual(
            [result.matched_name for result in results],
            ["First", "Second"],
        )
        self.assertEqual(
            self.mocked_ygocdb_card_client.fetch_card_reference.call_count,
            2,
        )

    def test_concurrent_lookup_failure_is_shared_cleaned_and_retried(self):
        service = self._service()
        counting_lock = _EntryCountingLock(expected_entries=3)
        service._lock = counting_lock
        shared_error = CardReferenceAmbiguityError("ambiguous")
        expected = self._reference("Retry")
        call_count = 0

        def fetch(_name, _language):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                self.assertTrue(counting_lock.reached.wait(timeout=2))
                raise shared_error
            return expected

        self.mocked_official_card_client.fetch_card_reference.side_effect = fetch
        start = Barrier(3)

        def lookup():
            start.wait(timeout=2)
            return service.suggest_card_reference("Retry", "eng")

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(lookup) for _index in range(3)]
            errors = [future.exception(timeout=3) for future in futures]

        self.assertTrue(all(error is shared_error for error in errors))
        self.assertEqual(service._lookup_in_flight, {})
        retry = service.suggest_card_reference("Retry", "eng")
        self.assertEqual(retry.matched_name, expected.matched_name)
        self.assertEqual(retry.source, "official_direct")
        self.assertEqual(call_count, 2)

    def test_image_cache_uses_canonical_key_and_never_caches_failures(self):
        service = self._service()
        self.mocked_image_client.fetch_card_image.side_effect = (
            CardImageError("not found"),
            b"canonical-image",
        )

        with self.assertRaises(CardImageError):
            service.crawl_card_image("仮面魔獣デス・ガーディウス")
        self.assertEqual(
            service.crawl_card_image("Masked Beast Des Gardius"),
            b"canonical-image",
        )
        self.assertEqual(
            service.crawl_card_image("  masked   beast des gardius  "),
            b"canonical-image",
        )
        self.assertEqual(
            self.mocked_image_client.fetch_card_image.call_args_list,
            [
                call("仮面魔獣デス・ガーディウス"),
                call("Masked Beast Des Gardius"),
            ],
        )

    def test_concurrent_equivalent_image_names_share_one_request_and_result(self):
        service = self._service()
        counting_lock = _EntryCountingLock(expected_entries=3)
        service._lock = counting_lock

        def fetch(_name):
            self.assertTrue(counting_lock.reached.wait(timeout=2))
            return b"shared-image"

        self.mocked_image_client.fetch_card_image.side_effect = fetch
        start = Barrier(3)

        def crawl(name):
            start.wait(timeout=2)
            return service.crawl_card_image(name)

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(crawl, name)
                for name in ("Card Name", " card name ", "CARD   NAME")
            ]
            results = [future.result(timeout=3) for future in futures]

        self.assertEqual(results, [b"shared-image"] * 3)
        self.mocked_image_client.fetch_card_image.assert_called_once()
        self.assertEqual(service._image_in_flight, {})

    def test_different_image_keys_run_in_parallel(self):
        provider_barrier = Barrier(2)

        def fetch(name):
            provider_barrier.wait(timeout=2)
            return name.encode("ascii")

        self.mocked_image_client.fetch_card_image.side_effect = fetch
        service = self._service()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(service.crawl_card_image, name)
                for name in ("First", "Second")
            ]
            results = [future.result(timeout=3) for future in futures]

        self.assertEqual(results, [b"First", b"Second"])
        self.assertEqual(self.mocked_image_client.fetch_card_image.call_count, 2)

    def test_concurrent_image_failure_is_shared_cleaned_and_retried(self):
        service = self._service()
        counting_lock = _EntryCountingLock(expected_entries=2)
        service._lock = counting_lock
        shared_error = CardImageError("temporary")
        call_count = 0

        def fetch(_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                self.assertTrue(counting_lock.reached.wait(timeout=2))
                raise shared_error
            return b"retry-image"

        self.mocked_image_client.fetch_card_image.side_effect = fetch
        start = Barrier(2)

        def crawl():
            start.wait(timeout=2)
            return service.crawl_card_image("Retry Card")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(crawl) for _index in range(2)]
            errors = [future.exception(timeout=3) for future in futures]

        self.assertTrue(all(error is shared_error for error in errors))
        self.assertEqual(service._image_in_flight, {})
        self.assertEqual(service.crawl_card_image("Retry Card"), b"retry-image")
        self.assertEqual(call_count, 2)

    def test_password_image_cache_normalizes_key_and_never_caches_failures(self):
        service = self._service()
        self.mocked_image_client.fetch_card_image_by_password.side_effect = (
            CardImageError("not found"),
            b"direct-image",
        )

        with self.assertRaises(CardImageError):
            service.crawl_card_image_by_password("0123abcd")
        self.assertEqual(
            service.crawl_card_image_by_password("0123ABCD"),
            b"direct-image",
        )
        self.assertEqual(
            service.crawl_card_image_by_password(" 0123abcd "),
            b"direct-image",
        )
        self.assertEqual(
            self.mocked_image_client.fetch_card_image_by_password.call_args_list,
            [call("0123ABCD"), call("0123ABCD")],
        )

    def test_concurrent_password_images_share_canonical_eight_character_key(self):
        service = self._service()
        counting_lock = _EntryCountingLock(expected_entries=3)
        service._lock = counting_lock

        def fetch(_password):
            self.assertTrue(counting_lock.reached.wait(timeout=2))
            return b"password-image"

        self.mocked_image_client.fetch_card_image_by_password.side_effect = fetch
        start = Barrier(3)

        def crawl(password):
            start.wait(timeout=2)
            return service.crawl_card_image_by_password(password)

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(crawl, password)
                for password in ("0123abcd", " 0123ABCD ", "0123AbCd")
            ]
            results = [future.result(timeout=3) for future in futures]

        self.assertEqual(results, [b"password-image"] * 3)
        self.mocked_image_client.fetch_card_image_by_password.assert_called_once_with(
            "0123ABCD"
        )
        self.assertEqual(list(service._image_cache), ["password:0123ABCD"])
        self.assertEqual(service._image_in_flight, {})


if __name__ == "__main__":
    unittest.main()
