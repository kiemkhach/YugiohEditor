from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from yugioh_editor.common.card_errors import (
    CardReferenceAmbiguityError,
    CardSuggestionError,
)
from yugioh_editor.common.card_properties import (
    ATTRIBUTE_LABELS,
    CARD_LEVEL_MAX,
    CARD_LEVEL_MIN,
    CARD_STAT_MAX,
    CARD_STAT_MIN,
    MONSTER_CATEGORY_LABELS,
    MONSTER_TYPE_LABELS,
    SPELL_TRAP_SUBTYPE_LABELS,
    normalize_property_label,
)
from yugioh_editor.common.card_reference_text import (
    normalize_reference_card_description,
    normalize_reference_card_name,
)
from yugioh_editor.common.constants import LANGUAGE_PREFIXES, normalize_language_code
from yugioh_editor.models.card_editing import CardReferenceData

OFFICIAL_LOCALES: Mapping[str, str] = {
    "eng": "en",
    "fra": "fr",
    "jpn": "ja",
    "spa": "es",
    "ita": "it",
    "ger": "de",
}
_BASE_URL = "https://www.db.yugioh-card.com/yugiohdb/card_search.action"
_MAX_HTML_BYTES = 4 * 1024 * 1024
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}

_RACE_LABELS = {
    "winged beast": "winged_beast",
    "beast warrior": "beast_warrior",
    "sea serpent": "sea_serpent",
    "divine beast": "divine",
}
_JAPANESE_RACE_LABELS = {
    "ドラゴン族": "dragon",
    "アンデット族": "zombie",
    "悪魔族": "fiend",
    "炎族": "pyro",
    "海竜族": "sea_serpent",
    "岩石族": "rock",
    "機械族": "machine",
    "魚族": "fish",
    "恐竜族": "dinosaur",
    "昆虫族": "insect",
    "獣族": "beast",
    "獣戦士族": "beast_warrior",
    "植物族": "plant",
    "水族": "aqua",
    "戦士族": "warrior",
    "鳥獣族": "winged_beast",
    "天使族": "fairy",
    "魔法使い族": "spellcaster",
    "雷族": "thunder",
    "爬虫類族": "reptile",
    "幻神獣族": "divine",
}
_MODERN_CATEGORIES = ("synchro", "xyz", "link", "pendulum")
_CARD_TEXT_TITLES = {
    "card text",
    "texte de carte",
    "texto de la carta",
    "testo carta",
    "testo della carta",
    "kartentext",
    "カードテキスト",
}
_REFERENCE_METADATA_TITLES = {
    "info",
    "informazioni",
    "nota",
    "hinweis",
    "お知らせ",
}


class OfficialCardClient:
    """Read localized card data from Konami's official card database."""

    def __init__(self, *, timeout_seconds: float = 15.0, max_retries: int = 2) -> None:
        if isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be a finite positive number.")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number.")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer.")
        if set(OFFICIAL_LOCALES) != set(LANGUAGE_PREFIXES):
            raise ValueError("Official locale keys must match canonical languages.")
        self.timeout_seconds = timeout
        self.max_retries = max_retries

    def fetch_card_reference(
        self,
        card_name: str,
        language: str,
    ) -> CardReferenceData | None:
        if not isinstance(card_name, str) or not card_name.strip():
            raise ValueError("card_name must be a non-empty string.")
        normalized_language = normalize_language_code(language)
        cid = self._find_cid(card_name, normalized_language)
        if cid is None:
            return None

        localized_names: dict[str, str] = {}
        localized_descriptions: dict[str, str] = {}
        authoritative: _ParsedDetail | None = None
        ordered_languages = (
            normalized_language,
            *(code for code in LANGUAGE_PREFIXES if code != normalized_language),
        )
        errors: list[str] = []
        for code in ordered_languages:
            try:
                detail = self._fetch_detail(cid, code)
            except CardSuggestionError as error:
                errors.append(f"{code}: {error}")
                continue
            if detail.name:
                localized_names[code] = detail.name
            if detail.description:
                localized_descriptions[code] = detail.description
            if authoritative is None or code == "eng":
                authoritative = detail
        if authoritative is None:
            raise CardSuggestionError(
                f"Official detail lookup failed for cid {cid}: " + "; ".join(errors)
            )
        return CardReferenceData(
            matched_name=localized_names.get(
                normalized_language, normalize_reference_card_name(card_name)
            ),
            matched_language=normalized_language,
            localized_names=localized_names,
            localized_descriptions=localized_descriptions,
            canonical_id=cid,
            level=authoritative.level,
            attack=authoritative.attack,
            defense=authoritative.defense,
            attribute=authoritative.attribute,
            card_type=authoritative.card_type,
            card_category=authoritative.card_category,
            source="official_card_database",
            confidence="canonical_cid",
        )

    def _find_cid(self, card_name: str, language: str) -> str | None:
        query = urlencode(
            {
                "ope": "1",
                "keyword": card_name,
                "stype": "1",
                "request_locale": OFFICIAL_LOCALES[language],
            }
        )
        parser = _SearchParser()
        parser.feed(self._request_html(f"{_BASE_URL}?{query}", card_name))
        normalized_query = _normalize_name(card_name)
        exact = [
            cid
            for name, cid in parser.candidates
            if _normalize_name(name) == normalized_query
        ]
        distinct = tuple(dict.fromkeys(exact))
        if len(distinct) > 1:
            raise CardReferenceAmbiguityError(
                f"Official search returned multiple exact cards for {card_name!r}."
            )
        if distinct:
            return distinct[0]
        containing = tuple(
            dict.fromkeys(
                cid
                for name, cid in parser.candidates
                if normalized_query in _normalize_name(name)
            )
        )
        if len(containing) == 1:
            return containing[0]
        all_cids = tuple(dict.fromkeys(cid for _name, cid in parser.candidates))
        return all_cids[0] if len(all_cids) == 1 else None

    def _fetch_detail(self, cid: str, language: str) -> _ParsedDetail:
        query = urlencode(
            {
                "cid": cid,
                "ope": "2",
                "request_locale": OFFICIAL_LOCALES[language],
            }
        )
        parser = _DetailParser()
        parser.feed(
            self._request_html(f"{_BASE_URL}?{query}", f"cid {cid} ({language})")
        )
        return parser.result()

    def _request_html(self, url: str, context: str) -> str:
        request = Request(
            url,
            method="GET",
            headers={"Accept": "text/html", "User-Agent": "YGOEditor/1.0"},
        )
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    headers = getattr(response, "headers", None) or {}
                    content_type = str(headers.get("Content-Type", ""))
                    if content_type and "html" not in content_type.casefold():
                        raise CardSuggestionError(
                            "Official lookup returned unsupported content type "
                            f"{content_type!r}."
                        )
                    payload = response.read(_MAX_HTML_BYTES + 1)
                    if len(payload) > _MAX_HTML_BYTES:
                        raise CardSuggestionError(
                            "Official lookup response is too large."
                        )
                    return payload.decode("utf-8", errors="replace")
            except HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code <= 599
                if not retryable or attempt >= self.max_retries:
                    raise CardSuggestionError(
                        f"Official lookup failed for {context}: HTTP {error.code}."
                    ) from error
            except (TimeoutError, URLError) as error:
                if attempt >= self.max_retries:
                    raise CardSuggestionError(
                        f"Official lookup failed for {context}: {error}."
                    ) from error
            time.sleep(min(0.25 * (2**attempt), self.timeout_seconds))
        raise AssertionError("Official HTTP retry loop terminated unexpectedly.")


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, set[str]]] = []
        self._row_depth: int | None = None
        self._row_name: list[str] = []
        self._row_cid: str | None = None
        self._name_depth: int | None = None
        self._legacy_cid: str | None = None
        self._legacy_text: list[str] = []
        self._row_candidates: list[tuple[str, str]] = []
        self._legacy_candidates: list[tuple[str, str]] = []
        self.candidates: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.casefold()
        attributes = dict(attrs)
        classes = set(str(attributes.get("class", "")).casefold().split())
        if normalized not in _VOID_TAGS:
            self._stack.append((normalized, classes))
        if normalized == "div" and "t_row" in classes and self._row_depth is None:
            self._row_depth = len(self._stack)
            self._row_name = []
            self._row_cid = None
        if self._row_depth is not None:
            if normalized == "span" and "card_name" in classes:
                self._name_depth = len(self._stack)
            if normalized == "input" and classes.intersection({"link_value", "cid"}):
                self._row_cid = self._cid_from_value(str(attributes.get("value", "")))
        elif normalized == "a":
            cid = self._cid_from_value(str(attributes.get("href", "")))
            if cid is not None:
                self._legacy_cid = cid
                self._legacy_text = []

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._name_depth is not None:
            self._row_name.append(data)
        if self._legacy_cid is not None:
            self._legacy_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        depth = len(self._stack)
        if normalized == "span" and self._name_depth == depth:
            self._name_depth = None
        if normalized == "a" and self._legacy_cid is not None:
            name = _clean_text(" ".join(self._legacy_text))
            if name:
                self._legacy_candidates.append((name, self._legacy_cid))
            self._legacy_cid = None
            self._legacy_text = []
        if normalized == "div" and self._row_depth == depth:
            name = _clean_text(" ".join(self._row_name))
            if name and self._row_cid is not None:
                self._row_candidates.append((name, self._row_cid))
            self._row_depth = None
            self._row_name = []
            self._row_cid = None
        if self._stack:
            self._stack.pop()
        selected = self._row_candidates or self._legacy_candidates
        self.candidates = list(
            dict((cid, (name, cid)) for name, cid in selected).values()
        )

    @staticmethod
    def _cid_from_value(value: str) -> str | None:
        stripped = value.strip()
        if stripped.isdigit():
            return stripped
        cid = parse_qs(urlparse(stripped).query).get("cid", [None])[0]
        return str(cid) if cid is not None and str(cid).isdigit() else None


class _ParsedDetail:
    def __init__(self, values: Mapping[str, object]) -> None:
        self.name = normalize_reference_card_name(str(values.get("name") or ""))
        self.description = normalize_reference_card_description(
            str(values.get("description") or "")
        )
        self.attack = _bounded(values.get("attack"), CARD_STAT_MIN, CARD_STAT_MAX)
        self.defense = _bounded(values.get("defense"), CARD_STAT_MIN, CARD_STAT_MAX)
        self.level = _bounded(values.get("level"), CARD_LEVEL_MIN, CARD_LEVEL_MAX)
        self.attribute = _supported_label(
            values.get("attribute"), ATTRIBUTE_LABELS.values()
        )
        self.card_type = _card_type(values.get("card_type"))
        self.card_category = _card_category(values.get("category"))


class _DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._elements: list[tuple[str, set[str], str | None]] = []
        self._scoped_class_text: dict[str, list[str]] = {}
        self._legacy_class_text: dict[str, list[str]] = {}
        self._scoped_all_text: list[str] = []
        self._legacy_all_text: list[str] = []
        self._saw_card_set = False
        self._card_set_depth: int | None = None
        self._main_name: list[str] = []
        self._attribute_icon: str = ""
        self._item_box_depth: int | None = None
        self._item_box_title: list[str] = []
        self._item_box_value: list[str] = []
        self._item_boxes: list[tuple[str, str]] = []
        self._text_box_depth: int | None = None
        self._text_box_title: list[str] = []
        self._text_box_value: list[str] = []
        self._text_boxes: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        normalized = tag.casefold()
        classes = set(str(attributes.get("class", "")).casefold().split())
        element_id = str(attributes.get("id", "")).casefold() or None
        if normalized not in _VOID_TAGS:
            self._elements.append((normalized, classes, element_id))
        if normalized == "div" and element_id == "cardset":
            self._saw_card_set = True
            self._card_set_depth = len(self._elements)
        if (
            self._inside_card_set()
            and normalized == "div"
            and "item_box" in classes
            and self._item_box_depth is None
        ):
            self._item_box_depth = len(self._elements)
            self._item_box_title = []
            self._item_box_value = []
        if (
            self._inside_card_set()
            and "item_box_text" in classes
            and self._text_box_depth is None
        ):
            self._text_box_depth = len(self._elements)
            self._text_box_title = []
            self._text_box_value = []
        if self._inside_card_set() and normalized == "img":
            if any(item[2] == "cardimgset" for item in self._elements):
                alt = _clean_text(str(attributes.get("alt", "")))
                if alt and not self._main_name:
                    self._main_name.append(alt)
            source = str(attributes.get("src", ""))
            match = re.search(r"attribute_icon_([a-z]+)", source, re.IGNORECASE)
            if match:
                self._attribute_icon = match.group(1)
                if self._item_box_depth is not None:
                    self._item_box_title.append("attribute")
            if self._item_box_depth is not None:
                if "icon_level" in source:
                    self._item_box_title.append("level")
                elif "icon_rank" in source:
                    self._item_box_title.append("rank")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        cleaned = _clean_text(data)
        if not cleaned:
            return
        target_all = (
            self._scoped_all_text if self._inside_card_set() else self._legacy_all_text
        )
        target_classes = (
            self._scoped_class_text
            if self._inside_card_set()
            else self._legacy_class_text
        )
        target_all.append(cleaned)
        if self._inside_card_set() and self._is_main_name_context():
            self._main_name.append(cleaned)
        if self._item_box_depth is not None:
            if any("item_box_title" in item[1] for item in self._elements):
                self._item_box_title.append(cleaned)
            if any("item_box_value" in item[1] for item in self._elements):
                self._item_box_value.append(cleaned)
        if self._text_box_depth is not None:
            if self._inside_text_title():
                self._text_box_title.append(cleaned)
            else:
                self._text_box_value.append(cleaned)
        for _tag, classes, _id in self._elements:
            for class_name in classes:
                if class_name == "item_box_text" and self._inside_card_set():
                    continue
                target_classes.setdefault(class_name, []).append(cleaned)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        for position in range(len(self._elements) - 1, -1, -1):
            if self._elements[position][0] == normalized:
                if self._text_box_depth == len(self._elements):
                    title = _clean_text(" ".join(self._text_box_title))
                    value = _clean_text(" ".join(self._text_box_value))
                    if value:
                        self._text_boxes.append((title, value))
                    self._text_box_depth = None
                if normalized == "div" and self._item_box_depth == len(self._elements):
                    title = _clean_text(" ".join(self._item_box_title))
                    value = _clean_text(" ".join(self._item_box_value))
                    if title and value:
                        self._item_boxes.append((title, value))
                    self._item_box_depth = None
                del self._elements[position:]
                if (
                    self._card_set_depth is not None
                    and len(self._elements) < self._card_set_depth
                ):
                    self._card_set_depth = None
                return

    def result(self) -> _ParsedDetail:
        self._class_text = (
            self._scoped_class_text if self._saw_card_set else self._legacy_class_text
        )
        all_text = " | ".join(
            self._scoped_all_text if self._saw_card_set else self._legacy_all_text
        )
        item_values = self._item_box_values()
        species = self._first_class_text("species", "card_type", "card-type")
        icon = item_values.get("icon", "")
        values = {
            "name": _clean_text(" ".join(self._main_name))
            or self._first_class_text("card_name", "card-name"),
            "description": self._description_text(),
            "attack": item_values.get("atk")
            or self._number_from_classes(
                ("atk_power", "atk"), all_text, r"ATK\s*[/：:]?\s*(\d+|\?)"
            ),
            "defense": item_values.get("def")
            or self._number_from_classes(
                ("def_power", "def"), all_text, r"DEF\s*[/：:]?\s*(\d+|\?)"
            ),
            "level": item_values.get("level")
            or item_values.get("rank")
            or self._number_from_classes(
                ("level", "star"), all_text, r"(?:Level|Rank)\s*(\d+)"
            ),
            "attribute": self._attribute_icon
            or item_values.get("attribute")
            or self._first_class_text("attribute"),
            "card_type": icon or species,
            "category": icon or species,
        }
        if not values["name"]:
            raise CardSuggestionError(
                "Official detail page did not contain a card name."
            )
        return _ParsedDetail(values)

    def _inside_card_set(self) -> bool:
        return self._card_set_depth is not None

    def _is_main_name_context(self) -> bool:
        return bool(self._elements and self._elements[-1][0] == "h1") and any(
            item[2] == "cardname" for item in self._elements
        )

    def _inside_text_title(self) -> bool:
        return any(
            {"item_box_title", "text_title"}.intersection(item[1])
            for item in self._elements
        )

    def _description_text(self) -> str:
        if not self._saw_card_set:
            return self._first_class_text(
                "item_box_text", "card_text", "card-text", "description"
            )
        for title, value in self._text_boxes:
            if _normalize_heading(title) in _CARD_TEXT_TITLES:
                return value
        return next(
            (
                value
                for title, value in self._text_boxes
                if _normalize_heading(title) not in _REFERENCE_METADATA_TITLES
            ),
            "",
        )

    def _item_box_values(self) -> dict[str, str]:
        return {
            normalize_property_label(title): value
            for title, value in self._item_boxes
            if title and value
        }

    def _first_class_text(self, *needles: str) -> str:
        for needle in needles:
            for class_name, values in self._class_text.items():
                if needle in class_name:
                    text = _clean_text(" ".join(values))
                    if text:
                        return text
        return ""

    def _number_from_classes(
        self,
        needles: tuple[str, ...],
        fallback: str,
        pattern: str,
    ) -> str | None:
        text = self._first_class_text(*needles)
        match = re.search(r"(\d+|\?)", text)
        if match and match.group(1) != "?":
            return match.group(1)
        match = re.search(pattern, fallback, flags=re.IGNORECASE)
        return None if match is None or match.group(1) == "?" else match.group(1)


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_heading(value: str) -> str:
    return _clean_text(value).rstrip(":").casefold()


def _bounded(value: object, minimum: int, maximum: int) -> int | None:
    match = re.search(r"\d+", str(value))
    if match is None:
        return None
    integer = int(match.group())
    return integer if minimum <= integer <= maximum else None


def _supported_label(value: object, allowed) -> str | None:
    normalized = normalize_property_label(value)
    allowed_values = set(allowed)
    return normalized if normalized in allowed_values else None


def _card_type(value: object) -> str | None:
    text = str(value)
    for race, label in _JAPANESE_RACE_LABELS.items():
        if race in text:
            return label
    normalized = normalize_property_label(value)
    if "spell" in normalized and "spellcaster" not in normalized:
        return "spell_card"
    if "trap" in normalized:
        return "trap_card"
    normalized = _RACE_LABELS.get(normalized.replace("_", " "), normalized)
    allowed = set(MONSTER_TYPE_LABELS.values())
    if normalized in allowed:
        return normalized
    padded = f"_{normalized}_"
    for label in allowed:
        if (
            label not in {"spell_card", "trap_card", "non_game_card"}
            and f"_{label}_" in padded
        ):
            return label
    return None


def _card_category(value: object) -> str | None:
    text = str(value).casefold()
    if any(modern in text for modern in _MODERN_CATEGORIES):
        return None
    allowed = set(MONSTER_CATEGORY_LABELS.values()) | set(
        SPELL_TRAP_SUBTYPE_LABELS.values()
    )
    for token, label in (
        ("カウンター", "counter"),
        ("フィールド", "field"),
        ("装備", "equip"),
        ("永続", "continuous"),
        ("速攻", "quick_play"),
        ("融合", "fusion"),
        ("儀式", "ritual"),
        ("効果", "effect"),
        ("通常", "normal"),
    ):
        if token in text:
            return label
    for label in (
        "counter",
        "field",
        "equip",
        "continuous",
        "quick_play",
        "fusion",
        "ritual",
        "effect",
        "normal",
    ):
        if label.replace("_", " ") in text and label in allowed:
            return label
    if "spell" in text or "trap" in text:
        return "normal"
    return None
