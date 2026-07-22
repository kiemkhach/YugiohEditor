from __future__ import annotations

import logging
import os
import re
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import TypeVar

import pandas as pd

from yugioh_editor.common.card_errors import (
    CardReferenceAmbiguityError,
    CardReferenceDataConflictError,
    CardReferenceDataResourceError,
    CardSuggestionError,
    JapaneseReadingNotFoundError,
)
from yugioh_editor.common.card_images import generate_unique_card_image_name
from yugioh_editor.common.card_passwords import (
    MISSING_CARD_PASSWORD,
    normalize_card_password,
)
from yugioh_editor.common.constants import normalize_language_code
from yugioh_editor.infrastructure.official_card_client import OfficialCardClient
from yugioh_editor.infrastructure.ygo_vietnam_image_client import (
    YgoVietnamImageClient,
)
from yugioh_editor.infrastructure.ygocdb_card_client import YgocdbCardClient
from yugioh_editor.infrastructure.yugipedia_alias_client import YugipediaAliasClient
from yugioh_editor.models.card_editing import CardReferenceData
from yugioh_editor.resources import get_resource_path

_JAPANESE_READING_COLUMNS = ["display_name_jpn", "reading_jpn"]
_InFlightKey = TypeVar("_InFlightKey")
_InFlightResult = TypeVar("_InFlightResult")
_IN_FLIGHT_WAIT_TIMEOUT_SECONDS = 10 * 60


class CardReferenceDataService:
    """
    Provide cached card reference data backed by packaged resources and
    external card-data providers.

    The current implementation supports Japanese card-name readings.
    Additional card reference datasets may be added without changing the
    service's role.
    """

    def __init__(
        self,
        japanese_reading_resource_path: Path | None = None,
        *,
        ygocdb_client: YgocdbCardClient | None = None,
        official_card_client: OfficialCardClient | None = None,
        image_client: YgoVietnamImageClient | None = None,
        yugipedia_alias_client: YugipediaAliasClient | None = None,
        lookup_cache_size: int = 256,
        image_cache_size: int = 32,
    ) -> None:
        self._japanese_reading_resource_path = (
            Path(japanese_reading_resource_path).expanduser().resolve()
            if japanese_reading_resource_path is not None
            else get_resource_path("card_reading_jpn.csv")
        )
        self._japanese_reading_dataframe = pd.DataFrame(
            columns=_JAPANESE_READING_COLUMNS
        )
        self._japanese_reading_by_name: dict[str, str] = {}
        self._ygocdb_client = (
            YgocdbCardClient() if ygocdb_client is None else ygocdb_client
        )
        self._official_card_client = (
            official_card_client
            if official_card_client is not None
            else OfficialCardClient()
        )
        self._image_client = image_client or YgoVietnamImageClient()
        self._yugipedia_alias_client = (
            YugipediaAliasClient()
            if yugipedia_alias_client is None
            else yugipedia_alias_client
        )
        if isinstance(lookup_cache_size, bool) or lookup_cache_size <= 0:
            raise ValueError("lookup_cache_size must be a positive integer.")
        if isinstance(image_cache_size, bool) or image_cache_size <= 0:
            raise ValueError("image_cache_size must be a positive integer.")
        self._lookup_cache_size = int(lookup_cache_size)
        self._image_cache_size = int(image_cache_size)
        self._lookup_cache: OrderedDict[tuple[str, str], CardReferenceData | None] = (
            OrderedDict()
        )
        self._image_cache: OrderedDict[str, bytes] = OrderedDict()
        self._lookup_in_flight: dict[
            tuple[str, str], Future[CardReferenceData | None]
        ] = {}
        self._image_in_flight: dict[str, Future[bytes]] = {}
        self._lock = RLock()
        self.reload()

    @property
    def japanese_reading_resource_path(self) -> Path:
        return self._japanese_reading_resource_path

    def try_get_japanese_reading(self, display_name_jpn: str) -> str | None:
        self._validate_text(display_name_jpn, "display_name_jpn")
        with self._lock:
            return self._japanese_reading_by_name.get(display_name_jpn)

    def get_japanese_reading(
        self,
        display_name_jpn: str,
        *,
        allow_crawl: bool = True,
    ) -> str:
        self._validate_text(display_name_jpn, "display_name_jpn")
        with self._lock:
            existing = self._japanese_reading_by_name.get(display_name_jpn)
            if existing is not None:
                return existing
            if not allow_crawl:
                raise JapaneseReadingNotFoundError(
                    "get_japanese_reading found no mapping for "
                    f"{display_name_jpn!r} in "
                    f"{self._japanese_reading_resource_path}."
                )
        reading = self.crawl_japanese_reading(display_name_jpn)
        self.add_japanese_reading_mapping(display_name_jpn, reading)
        return reading

    def crawl_japanese_reading(self, display_name_jpn: str) -> str:
        self._validate_text(display_name_jpn, "display_name_jpn")
        return self._ygocdb_client.fetch_japanese_reading(display_name_jpn)

    def suggest_card_reference(
        self,
        card_name: str,
        language: str,
    ) -> CardReferenceData | None:
        self._validate_text(card_name, "card_name")
        normalized_language = normalize_language_code(language)
        key = (_normalize_lookup_name(card_name), normalized_language)
        with self._lock:
            if key in self._lookup_cache:
                cached = self._lookup_cache.pop(key)
                self._lookup_cache[key] = cached
                return cached
            pending = self._lookup_in_flight.get(key)
            is_owner = pending is None
            if pending is None:
                pending = Future()
                self._lookup_in_flight[key] = pending

        if not is_owner:
            return pending.result(timeout=_IN_FLIGHT_WAIT_TIMEOUT_SECONDS)
        return self._run_in_flight_owner(
            self._lookup_in_flight,
            key,
            pending,
            lambda: self._suggest_card_reference_uncached(
                card_name,
                normalized_language,
                key,
            ),
        )

    def _suggest_card_reference_uncached(
        self,
        card_name: str,
        normalized_language: str,
        key: tuple[str, str],
    ) -> CardReferenceData | None:

        errors: list[str] = []
        completed_provider_call = False
        canonical_name: str | None = None
        try:
            result = self._official_card_client.fetch_card_reference(
                card_name, normalized_language
            )
            completed_provider_call = True
            if result is not None:
                if isinstance(result, CardReferenceData):
                    result = replace(
                        result,
                        source="official_direct",
                        confidence="exact",
                    )
                    result, cacheable = self._enrich_reference_password(
                        result,
                        card_name,
                        normalized_language,
                    )
                    if not cacheable:
                        return result
                return self._cache_lookup(key, result)
        except CardReferenceAmbiguityError:
            raise
        except CardSuggestionError as error:
            errors.append(f"Official: {error}")

        if normalized_language == "eng":
            try:
                resolved = self._yugipedia_alias_client.resolve_alias(card_name)
                if resolved and _normalize_lookup_name(resolved) != key[0]:
                    canonical_name = resolved
            except CardReferenceAmbiguityError:
                raise
            except CardSuggestionError as error:
                errors.append(f"Yugipedia: {error}")
            if canonical_name is not None:
                try:
                    result = self._official_card_client.fetch_card_reference(
                        canonical_name, normalized_language
                    )
                    completed_provider_call = True
                    if result is not None:
                        if isinstance(result, CardReferenceData):
                            result = replace(
                                result,
                                matched_name=canonical_name,
                                source="official_after_alias",
                                confidence="redirect",
                            )
                            result, cacheable = self._enrich_reference_password(
                                result,
                                canonical_name,
                                normalized_language,
                            )
                            if not cacheable:
                                return result
                        return self._cache_lookup(key, result)
                except CardReferenceAmbiguityError:
                    raise
                except CardSuggestionError as error:
                    errors.append(f"Official (alias): {error}")

        fallback_names = tuple(
            dict.fromkeys(name for name in (canonical_name, card_name) if name)
        )
        for fallback_name in fallback_names:
            try:
                result = self._ygocdb_client.fetch_card_reference(
                    fallback_name, normalized_language
                )
                completed_provider_call = True
                if result is not None:
                    if isinstance(result, CardReferenceData):
                        result = replace(
                            result,
                            matched_name=fallback_name,
                            source="ygocdb_fallback",
                            confidence=(
                                "redirect" if canonical_name else result.confidence
                            ),
                        )
                    return self._cache_lookup(key, result)
            except CardReferenceAmbiguityError:
                raise
            except CardSuggestionError as error:
                errors.append(f"YGOCDB ({fallback_name}): {error}")
        if not completed_provider_call and errors:
            raise CardSuggestionError(
                "All card reference providers failed: " + "; ".join(errors)
            )
        return self._cache_lookup(key, None)

    def _enrich_reference_password(
        self,
        reference: CardReferenceData,
        fallback_name: str,
        fallback_language: str,
    ) -> tuple[CardReferenceData, bool]:
        if isinstance(reference.password, str):
            try:
                normalized_password = normalize_card_password(reference.password)
                if normalized_password != MISSING_CARD_PASSWORD:
                    return (
                        replace(reference, password=normalized_password),
                        True,
                    )
            except ValueError:
                pass
        english_name = reference.localized_names.get("eng")
        lookup_name = (
            english_name
            if isinstance(english_name, str) and english_name.strip()
            else reference.matched_name or fallback_name
        )
        lookup_language = "eng" if lookup_name == english_name else fallback_language
        try:
            password = self._ygocdb_client.fetch_card_password(
                lookup_name,
                lookup_language,
            )
            if password is None:
                return reference, True
            return (
                replace(reference, password=normalize_card_password(password)),
                True,
            )
        except CardReferenceAmbiguityError as error:
            logging.warning(
                "Card password enrichment was ambiguous for %r: %s",
                lookup_name,
                error,
            )
            return reference, True
        except (CardSuggestionError, ValueError) as error:
            logging.warning(
                "Card password enrichment failed for %r: %s",
                lookup_name,
                error,
            )
            return reference, False

    def _cache_lookup(
        self,
        key: tuple[str, str],
        value: CardReferenceData | None,
    ) -> CardReferenceData | None:
        with self._lock:
            self._lookup_cache[key] = value
            self._lookup_cache.move_to_end(key)
            while len(self._lookup_cache) > self._lookup_cache_size:
                self._lookup_cache.popitem(last=False)
        return value

    @staticmethod
    def generate_card_image_name(existing_names: set[str]) -> str:
        return generate_unique_card_image_name(existing_names)

    def crawl_card_image(self, english_name: str) -> bytes:
        self._validate_text(english_name, "english_name")
        key = "name:" + _normalize_lookup_name(english_name)
        with self._lock:
            if key in self._image_cache:
                cached = self._image_cache.pop(key)
                self._image_cache[key] = cached
                return cached
            pending = self._image_in_flight.get(key)
            is_owner = pending is None
            if pending is None:
                pending = Future()
                self._image_in_flight[key] = pending

        if not is_owner:
            return pending.result(timeout=_IN_FLIGHT_WAIT_TIMEOUT_SECONDS)
        return self._run_in_flight_owner(
            self._image_in_flight,
            key,
            pending,
            lambda: self._fetch_and_cache_image(
                key,
                lambda: self._image_client.fetch_card_image(english_name),
            ),
        )

    def crawl_card_image_by_password(self, password: str) -> bytes:
        normalized = normalize_card_password(password)
        key = "password:" + normalized
        with self._lock:
            if key in self._image_cache:
                cached = self._image_cache.pop(key)
                self._image_cache[key] = cached
                return cached
            pending = self._image_in_flight.get(key)
            is_owner = pending is None
            if pending is None:
                pending = Future()
                self._image_in_flight[key] = pending

        if not is_owner:
            return pending.result(timeout=_IN_FLIGHT_WAIT_TIMEOUT_SECONDS)
        return self._run_in_flight_owner(
            self._image_in_flight,
            key,
            pending,
            lambda: self._fetch_and_cache_image(
                key,
                lambda: self._image_client.fetch_card_image_by_password(normalized),
            ),
        )

    def _fetch_and_cache_image(
        self,
        key: str,
        fetch: Callable[[], bytes],
    ) -> bytes:
        payload = fetch()
        with self._lock:
            self._image_cache[key] = payload
            self._image_cache.move_to_end(key)
            while len(self._image_cache) > self._image_cache_size:
                self._image_cache.popitem(last=False)
        return payload

    def _run_in_flight_owner(
        self,
        registry: dict[_InFlightKey, Future[_InFlightResult]],
        key: _InFlightKey,
        pending: Future[_InFlightResult],
        operation: Callable[[], _InFlightResult],
    ) -> _InFlightResult:
        try:
            result = operation()
        except BaseException as error:
            pending.set_exception(error)
            raise
        else:
            pending.set_result(result)
            return result
        finally:
            with self._lock:
                if registry.get(key) is pending:
                    del registry[key]

    def add_japanese_reading_mapping(
        self,
        display_name_jpn: str,
        reading_jpn: str,
    ) -> None:
        self._validate_text(display_name_jpn, "display_name_jpn")
        self._validate_text(reading_jpn, "reading_jpn")
        with self._lock:
            existing = self._japanese_reading_by_name.get(display_name_jpn)
            if existing == reading_jpn:
                return
            if existing is not None:
                raise CardReferenceDataConflictError(
                    "add_japanese_reading_mapping found a conflict for "
                    f"{display_name_jpn!r} in "
                    f"{self._japanese_reading_resource_path}: "
                    f"existing={existing!r}, new={reading_jpn!r}."
                )
            new_row = pd.DataFrame(
                [
                    {
                        "display_name_jpn": display_name_jpn,
                        "reading_jpn": reading_jpn,
                    }
                ],
                columns=_JAPANESE_READING_COLUMNS,
            )
            updated = pd.concat(
                [self._japanese_reading_dataframe, new_row],
                ignore_index=True,
            )
            temporary_path = self._japanese_reading_resource_path.with_name(
                f".{self._japanese_reading_resource_path.name}.tmp"
            )
            try:
                updated.to_csv(
                    temporary_path,
                    index=False,
                    encoding="utf-8-sig",
                    lineterminator="\n",
                )
                os.replace(temporary_path, self._japanese_reading_resource_path)
            except Exception as error:
                temporary_path.unlink(missing_ok=True)
                raise CardReferenceDataResourceError(
                    "add_japanese_reading_mapping failed for "
                    f"{display_name_jpn!r} in "
                    f"{self._japanese_reading_resource_path}: {error}"
                ) from error
            self._japanese_reading_dataframe = updated
            self._japanese_reading_by_name[display_name_jpn] = reading_jpn

    def reload(self) -> None:
        with self._lock:
            try:
                dataframe = pd.read_csv(
                    self._japanese_reading_resource_path,
                    dtype=str,
                    encoding="utf-8-sig",
                    keep_default_na=False,
                )
            except Exception as error:
                raise CardReferenceDataResourceError(
                    "reload failed for Japanese reading resource "
                    f"{self._japanese_reading_resource_path}: {error}"
                ) from error
            self._validate_japanese_reading_dataframe(dataframe)
            self._japanese_reading_dataframe = dataframe
            self._japanese_reading_by_name = dict(
                zip(
                    dataframe["display_name_jpn"],
                    dataframe["reading_jpn"],
                    strict=True,
                )
            )

    def _validate_japanese_reading_dataframe(
        self,
        dataframe: pd.DataFrame,
    ) -> None:
        resource_path = self._japanese_reading_resource_path
        if list(dataframe.columns) != _JAPANESE_READING_COLUMNS:
            raise CardReferenceDataResourceError(
                f"reload failed for Japanese reading resource {resource_path}: "
                "header must be exactly: " + ",".join(_JAPANESE_READING_COLUMNS)
            )
        if dataframe.isna().any(axis=None):
            raise CardReferenceDataResourceError(
                f"reload failed for Japanese reading resource {resource_path}: "
                "contains NaN values."
            )
        for column in _JAPANESE_READING_COLUMNS:
            empty = dataframe[column].map(
                lambda value: not isinstance(value, str) or not value.strip()
            )
            if empty.any():
                raise CardReferenceDataResourceError(
                    f"reload failed for Japanese reading resource {resource_path}: "
                    "contains an empty "
                    f"{column}."
                )
        duplicates = dataframe["display_name_jpn"].duplicated(keep=False)
        if duplicates.any():
            name = dataframe.loc[duplicates, "display_name_jpn"].iloc[0]
            raise CardReferenceDataResourceError(
                f"reload failed for Japanese reading resource {resource_path}: "
                "contains duplicate "
                f"name {name!r}."
            )

    @staticmethod
    def _validate_text(value: object, field: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string: {value!r}.")


def _normalize_lookup_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()
