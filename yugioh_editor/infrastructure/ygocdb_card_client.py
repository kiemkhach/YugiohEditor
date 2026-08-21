from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from yugioh_editor.common.card_errors import (
    CardReferenceAmbiguityError,
    CardSuggestionError,
    JapaneseReadingCrawlError,
    JapaneseReadingNotFoundError,
)
from yugioh_editor.common.card_passwords import normalize_card_password
from yugioh_editor.common.card_properties import (
    CARD_LEVEL_MAX,
    CARD_LEVEL_MIN,
    CARD_STAT_MAX,
    CARD_STAT_MIN,
)
from yugioh_editor.common.card_reference_text import (
    normalize_reference_card_description,
    normalize_reference_card_name,
)
from yugioh_editor.common.constants import (
    DEFAULT_LANGUAGE,
    JAPANESE_LANGUAGE,
    LANGUAGE_PREFIXES,
    normalize_language_code,
)
from yugioh_editor.models.card_editing import CardReferenceData

_API_ENDPOINT = "https://ygocdb.com/api/v0/"
_HYPHENS = str.maketrans({character: "-" for character in "‐-‒–—―−－﹣"})
_OTHER_MATCH_CHARACTERS = str.maketrans(
    {
        "･": "・",
        "·": "・",
        "•": "・",
        "’": "'",
        "‘": "'",
        "＇": "'",
    }
)

_LOCALIZED_NAME_FIELDS = {
    "jpn": ("jp_name",),
    "eng": ("en_name", "wiki_en"),
    "fra": ("fr_name", "fra_name", "name_fr"),
    "spa": ("es_name", "spa_name", "name_es"),
    "ita": ("it_name", "ita_name", "name_it"),
    "ger": ("de_name", "ger_name", "name_de"),
}
_ATTRIBUTE_BIT_MAP = {
    0x01: "earth",
    0x02: "water",
    0x04: "fire",
    0x08: "wind",
    0x10: "light",
    0x20: "dark",
    0x40: "divine",
}
_RACE_BIT_MAP = {
    0x000001: "warrior",
    0x000002: "spellcaster",
    0x000004: "fairy",
    0x000008: "fiend",
    0x000010: "zombie",
    0x000020: "machine",
    0x000040: "aqua",
    0x000080: "pyro",
    0x000100: "rock",
    0x000200: "winged_beast",
    0x000400: "plant",
    0x000800: "insect",
    0x001000: "thunder",
    0x002000: "dragon",
    0x004000: "beast",
    0x008000: "beast_warrior",
    0x010000: "dinosaur",
    0x020000: "fish",
    0x040000: "sea_serpent",
    0x080000: "reptile",
    0x400000: "divine",
}

_TYPE_MONSTER = 0x01
_TYPE_SPELL = 0x02
_TYPE_TRAP = 0x04
_TYPE_NORMAL = 0x10
_TYPE_EFFECT = 0x20
_TYPE_FUSION = 0x40
_TYPE_RITUAL = 0x80


class YgocdbCardClient:
    """Fetch card reference data from the public YGOCDB API."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        if isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be a finite positive number.")
        try:
            normalized_timeout = float(timeout_seconds)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "timeout_seconds must be a finite positive number."
            ) from error
        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number.")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer.")
        self.timeout_seconds = normalized_timeout
        self.max_retries = max_retries

    def fetch_japanese_reading(self, display_name_jpn: str) -> str:
        results = self._search(
            display_name_jpn,
            accept_language="ja",
            error_type=JapaneseReadingCrawlError,
            operation="fetch_japanese_reading",
        )
        return self._select_reading_from_results(display_name_jpn, results)

    def fetch_card_reference(
        self,
        card_name: str,
        language: str,
    ) -> CardReferenceData | None:
        self._validate_query_name(card_name)
        normalized_language = normalize_language_code(language)
        results = self._search(
            card_name,
            accept_language=normalized_language,
            error_type=CardSuggestionError,
            operation="fetch_card_reference search",
        )
        selected = self._select_reference_candidate(
            card_name,
            normalized_language,
            results,
        )
        if selected is None:
            return None
        candidate, confidence = selected
        detail: Mapping[str, Any] = {}
        passcode = _optional_int(candidate.get("id"))
        if passcode is not None:
            detail_payload = self._request_json(
                self._request(
                    f"{_API_ENDPOINT}card/{passcode}?{urlencode({'show': 'all'})}",
                    accept_language=normalized_language,
                ),
                card_name,
                error_type=CardSuggestionError,
                operation="fetch_card_reference detail",
            )
            detail = self._extract_detail_record(detail_payload, card_name)
        return self._build_card_reference(
            card_name,
            normalized_language,
            candidate,
            detail,
            confidence,
        )

    def fetch_card_password(self, card_name: str, language: str) -> str | None:
        """Return the exact eight-character provider passcode when available."""

        self._validate_query_name(card_name)
        normalized_language = normalize_language_code(language)
        results = self._search(
            card_name,
            accept_language=normalized_language,
            error_type=CardSuggestionError,
            operation="fetch_card_password search",
        )
        selected = self._select_reference_candidate(
            card_name,
            normalized_language,
            results,
        )
        if selected is None:
            return None
        candidate, _confidence = selected
        return _normalize_provider_password(candidate.get("id"))

    def _search(
        self,
        query_name: str,
        *,
        accept_language: str,
        error_type: type[Exception],
        operation: str,
    ) -> list[object]:
        self._validate_query_name(query_name)
        request = self._request(
            f"{_API_ENDPOINT}?{urlencode({'search': query_name})}",
            accept_language=accept_language,
        )
        payload = self._request_json(
            request,
            query_name,
            error_type=error_type,
            operation=operation,
        )
        if not isinstance(payload, Mapping):
            raise error_type(
                f"{operation} for {query_name!r} returned an invalid response root."
            )
        results = payload.get("result", [])
        if not isinstance(results, list):
            raise error_type(
                f"{operation} for {query_name!r} returned an invalid result list."
            )
        return results

    @staticmethod
    def _request(url: str, *, accept_language: str) -> Request:
        return Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Accept-Language": accept_language,
                "User-Agent": "YGOEditor/1.0",
            },
        )

    def _request_json(
        self,
        request: Request,
        context_name: str,
        *,
        error_type: type[Exception] = JapaneseReadingCrawlError,
        operation: str = "fetch_japanese_reading",
    ) -> object:
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code <= 599
                if not retryable or attempt >= self.max_retries:
                    raise error_type(
                        f"{operation} failed for {context_name!r}: HTTP {error.code}."
                    ) from error
                retry_after = (
                    error.headers.get("Retry-After")
                    if error.headers is not None
                    else None
                )
                self._sleep_before_retry(attempt, retry_after)
            except (TimeoutError, URLError) as error:
                if attempt >= self.max_retries:
                    raise error_type(
                        f"{operation} failed for {context_name!r}: {error}."
                    ) from error
                self._sleep_before_retry(attempt, None)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise error_type(
                    f"{operation} received invalid JSON for {context_name!r}: {error}."
                ) from error
        raise AssertionError("HTTP retry loop terminated unexpectedly.")

    def _sleep_before_retry(
        self,
        attempt: int,
        retry_after: str | None,
    ) -> None:
        delay = 0.25 * (2**attempt)
        if retry_after is not None:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                pass
        time.sleep(min(delay, self.timeout_seconds))

    @staticmethod
    def _select_reading_from_results(
        display_name_jpn: str,
        results: Sequence[Mapping[str, Any]],
    ) -> str:
        candidates = YgocdbCardClient._validated_candidates(
            display_name_jpn,
            results,
        )
        raw = [item for item in candidates if item["jp_name"] == display_name_jpn]
        if raw:
            return YgocdbCardClient._require_ruby(display_name_jpn, raw[0])
        normalized_name = _normalize_api_match_name(display_name_jpn)
        normalized = [
            item
            for item in candidates
            if _normalize_api_match_name(item["jp_name"]) == normalized_name
        ]
        selected = YgocdbCardClient._select_unique_candidate(
            display_name_jpn,
            normalized,
            "normalized",
        )
        if selected is not None:
            return YgocdbCardClient._require_ruby(display_name_jpn, selected)
        compact_name = _compact_api_match_name(display_name_jpn)
        compact = [
            item
            for item in candidates
            if _compact_api_match_name(item["jp_name"]) == compact_name
        ]
        selected = YgocdbCardClient._select_unique_candidate(
            display_name_jpn,
            compact,
            "compact",
        )
        if selected is not None:
            return YgocdbCardClient._require_ruby(display_name_jpn, selected)
        raise JapaneseReadingNotFoundError(
            f"fetch_japanese_reading found no API result matching {display_name_jpn!r}."
        )

    @staticmethod
    def _select_reference_candidate(
        card_name: str,
        language: str,
        results: Sequence[object],
    ) -> tuple[Mapping[str, Any], str] | None:
        candidates = [item for item in results if isinstance(item, Mapping)]
        fields = _LOCALIZED_NAME_FIELDS.get(language, ())
        raw = [
            candidate
            for candidate in candidates
            if any(candidate.get(field) == card_name for field in fields)
        ]
        raw = _deduplicate_reference_candidates(raw)
        if raw and language == DEFAULT_LANGUAGE:
            return raw[0], "raw_exact"
        if len(raw) == 1:
            return raw[0], "raw_exact"
        if len(raw) > 1:
            raise CardReferenceAmbiguityError(
                f"fetch_card_reference found multiple raw {language} matches "
                f"for {card_name!r}."
            )
        normalized_query = _normalize_api_match_name(card_name)
        if language == DEFAULT_LANGUAGE:
            normalized_query = normalized_query.casefold()
        normalized = []
        for candidate in candidates:
            for field in fields:
                value = candidate.get(field)
                if not isinstance(value, str):
                    continue
                normalized_value = _normalize_api_match_name(value)
                if language == DEFAULT_LANGUAGE:
                    normalized_value = normalized_value.casefold()
                if normalized_value == normalized_query:
                    normalized.append(candidate)
                    break
        normalized = _deduplicate_reference_candidates(normalized)
        if normalized and language == DEFAULT_LANGUAGE:
            return normalized[0], "normalized_exact"
        if len(normalized) == 1:
            return normalized[0], "normalized_exact"
        if len(normalized) > 1:
            raise CardReferenceAmbiguityError(
                f"fetch_card_reference found multiple normalized {language} "
                f"matches for {card_name!r}."
            )
        if language not in {DEFAULT_LANGUAGE, JAPANESE_LANGUAGE}:
            weighted = [
                candidate
                for candidate in candidates
                if _optional_int(candidate.get("weight")) == 100
            ]
            weighted = _deduplicate_reference_candidates(weighted)
            if len(weighted) == 1:
                return weighted[0], "weight_100"
            if len(weighted) > 1:
                raise CardReferenceAmbiguityError(
                    "fetch_card_reference found multiple weight-100 results for "
                    f"{card_name!r} ({language})."
                )
        return None

    @staticmethod
    def _extract_detail_record(
        payload: object,
        card_name: str,
    ) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise CardSuggestionError(
                f"fetch_card_reference detail for {card_name!r} returned an "
                "invalid response root."
            )
        result = payload.get("result", payload)
        if isinstance(result, Mapping):
            return result
        if (
            isinstance(result, list)
            and len(result) == 1
            and isinstance(result[0], Mapping)
        ):
            return result[0]
        raise CardSuggestionError(
            f"fetch_card_reference detail for {card_name!r} returned an "
            "invalid result record."
        )

    @staticmethod
    def _build_card_reference(
        card_name: str,
        language: str,
        candidate: Mapping[str, Any],
        detail: Mapping[str, Any],
        confidence: str,
    ) -> CardReferenceData:
        combined = dict(candidate)
        combined.update(detail)
        localized_names: dict[str, str] = {}
        for code, fields in _LOCALIZED_NAME_FIELDS.items():
            value = next(
                (
                    combined.get(field)
                    for field in fields
                    if isinstance(combined.get(field), str)
                    and str(combined.get(field)).strip()
                ),
                None,
            )
            if value is not None:
                normalized_name = normalize_reference_card_name(str(value))
                if normalized_name:
                    localized_names[code] = normalized_name
        localized_descriptions = _localized_descriptions(combined)
        data = combined.get("data", {})
        if not isinstance(data, Mapping):
            data = {}
        type_bits = _optional_int(data.get("type"))
        race_bits = _optional_int(data.get("race"))
        attribute_bits = _optional_int(data.get("attribute"))
        card_type = _map_card_type(type_bits, race_bits)
        return CardReferenceData(
            matched_name=localized_names.get(
                language, normalize_reference_card_name(card_name)
            ),
            matched_language=language,
            localized_names=localized_names,
            localized_descriptions=localized_descriptions,
            password=_normalize_provider_password(combined.get("id")),
            level=_bounded_int(data.get("level"), CARD_LEVEL_MIN, CARD_LEVEL_MAX),
            attack=_bounded_int(data.get("atk"), CARD_STAT_MIN, CARD_STAT_MAX),
            defense=_bounded_int(data.get("def"), CARD_STAT_MIN, CARD_STAT_MAX),
            attribute=_ATTRIBUTE_BIT_MAP.get(attribute_bits),
            card_type=card_type,
            card_category=_map_card_category(type_bits),
            source="ygocdb",
            confidence=confidence,
        )

    @staticmethod
    def _validated_candidates(
        display_name_jpn: str,
        results: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for position, item in enumerate(results):
            if not isinstance(item, Mapping):
                raise JapaneseReadingCrawlError(
                    "fetch_japanese_reading received an invalid candidate for "
                    f"{display_name_jpn!r} at position {position}."
                )
            jp_name = item.get("jp_name")
            if not isinstance(jp_name, str) or not jp_name.strip():
                raise JapaneseReadingCrawlError(
                    "fetch_japanese_reading received an invalid jp_name for "
                    f"{display_name_jpn!r} at position {position}."
                )
            cid = item.get("cid")
            if cid is not None:
                if isinstance(cid, bool) or not isinstance(cid, (int, str)):
                    raise JapaneseReadingCrawlError(
                        "fetch_japanese_reading received an invalid cid for "
                        f"{display_name_jpn!r} at position {position}."
                    )
                try:
                    cid = int(cid)
                except (TypeError, ValueError) as error:
                    raise JapaneseReadingCrawlError(
                        "fetch_japanese_reading received an invalid cid for "
                        f"{display_name_jpn!r} at position {position}."
                    ) from error
            candidates.append(
                {
                    "jp_name": jp_name,
                    "jp_ruby": item.get("jp_ruby"),
                    "cid": cid,
                    "position": position,
                }
            )
        return candidates

    @staticmethod
    def _select_unique_candidate(
        display_name_jpn: str,
        candidates: list[dict[str, Any]],
        match_kind: str,
    ) -> dict[str, Any] | None:
        distinct: list[dict[str, Any]] = []
        seen: set[tuple[str, object]] = set()
        for candidate in candidates:
            identity = (
                ("cid", candidate["cid"])
                if candidate["cid"] is not None
                else ("position", candidate["position"])
            )
            if identity not in seen:
                seen.add(identity)
                distinct.append(candidate)
        if len(distinct) > 1:
            raise JapaneseReadingCrawlError(
                f"fetch_japanese_reading found ambiguous {match_kind} matches "
                f"for {display_name_jpn!r}."
            )
        return distinct[0] if distinct else None

    @staticmethod
    def _require_ruby(
        display_name_jpn: str,
        candidate: Mapping[str, Any],
    ) -> str:
        reading = candidate.get("jp_ruby")
        if not isinstance(reading, str) or not reading.strip():
            raise JapaneseReadingCrawlError(
                "fetch_japanese_reading received an empty jp_ruby for "
                f"{display_name_jpn!r}."
            )
        return reading

    @staticmethod
    def _validate_query_name(query_name: object) -> None:
        if not isinstance(query_name, str) or not query_name.strip():
            raise ValueError(f"query_name must be a non-empty string: {query_name!r}.")


def _normalize_api_match_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(_HYPHENS).translate(_OTHER_MATCH_CHARACTERS)
    return re.sub(r"\s+", " ", normalized).strip()


def _compact_api_match_name(value: str) -> str:
    return re.sub(r"\s+", "", _normalize_api_match_name(value))


def _normalize_provider_password(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        if not 0 <= value <= 99_999_999:
            return None
        candidate = f"{value:08d}"
    elif isinstance(value, str):
        candidate = value.strip()
    else:
        return None
    try:
        return normalize_card_password(candidate)
    except ValueError:
        return None


def _deduplicate_reference_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    distinct: list[Mapping[str, Any]] = []
    seen_provider_ids: set[tuple[str, object]] = set()
    for candidate in candidates:
        provider_id = _provider_id_identity(candidate.get("id"))
        if provider_id is None:
            distinct.append(candidate)
            continue
        if provider_id in seen_provider_ids:
            continue
        seen_provider_ids.add(provider_id)
        distinct.append(candidate)
    return distinct


def _provider_id_identity(value: object) -> tuple[str, object] | None:
    if isinstance(value, bool) or value is None:
        return None
    numeric = _optional_int(value)
    if numeric is not None:
        return "numeric", numeric
    if not isinstance(value, str) or not value.strip():
        return None
    normalized_password = _normalize_provider_password(value)
    if normalized_password is not None:
        return "password", normalized_password
    return "text", value.strip()


def _optional_int(value: object) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bounded_int(value: object, minimum: int, maximum: int) -> int | None:
    integer = _optional_int(value)
    if integer is None or not minimum <= integer <= maximum:
        return None
    return integer


def _map_card_type(type_bits: int | None, race_bits: int | None) -> str | None:
    if type_bits is not None:
        if type_bits & _TYPE_SPELL and not type_bits & (_TYPE_MONSTER | _TYPE_TRAP):
            return "spell_card"
        if type_bits & _TYPE_TRAP and not type_bits & (_TYPE_MONSTER | _TYPE_SPELL):
            return "trap_card"
    return _RACE_BIT_MAP.get(race_bits)


def _map_card_category(type_bits: int | None) -> str | None:
    if type_bits is None:
        return None
    if type_bits & _TYPE_FUSION:
        return "fusion"
    if type_bits & _TYPE_RITUAL:
        return "ritual"
    if type_bits & _TYPE_EFFECT:
        return "effect"
    if type_bits & _TYPE_NORMAL or type_bits & (_TYPE_SPELL | _TYPE_TRAP):
        return "normal"
    return None


def _localized_descriptions(value: Mapping[str, Any]) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for field in ("localized_text", "localized_texts", "texts"):
        localized = value.get(field)
        if not isinstance(localized, Mapping):
            continue
        for language in LANGUAGE_PREFIXES:
            item = localized.get(language)
            if isinstance(item, Mapping):
                item = item.get("desc") or item.get("description")
            if isinstance(item, str) and item.strip():
                normalized = normalize_reference_card_description(item)
                if normalized:
                    descriptions[language] = normalized
    text = value.get("text")
    if isinstance(text, Mapping):
        language = str(text.get("language", "")).casefold()
        description = text.get("desc") or text.get("description")
        if (
            language in LANGUAGE_PREFIXES
            and isinstance(description, str)
            and description.strip()
        ):
            normalized = normalize_reference_card_description(description)
            if normalized:
                descriptions[language] = normalized
    return descriptions
