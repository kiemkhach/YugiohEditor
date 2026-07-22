from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from yugioh_editor.common.card_passwords import MISSING_CARD_PASSWORD
from yugioh_editor.common.constants import LANGUAGE_PREFIXES

_CARD_CSV_BASE_COLUMNS = (
    "card_index",
    "card_id",
    "password",
    "level",
    "attack",
    "defense",
    "attribute",
    "card_type",
    "card_category",
    "pack",
    "image_name",
)


def card_name_column(language: str) -> str:
    return f"name_{language}"


def card_description_column(language: str) -> str:
    return f"desc_{language}"


CARD_CSV_COLUMNS = _CARD_CSV_BASE_COLUMNS + tuple(
    column
    for language in LANGUAGE_PREFIXES
    for column in (card_name_column(language), card_description_column(language))
)


@dataclass(slots=True)
class CardLocalizedText:
    names: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.names = _complete_localized_values(self.names, "names")
        self.descriptions = _complete_localized_values(
            self.descriptions,
            "descriptions",
        )

    def clone(self) -> CardLocalizedText:
        return CardLocalizedText(dict(self.names), dict(self.descriptions))


@dataclass(frozen=True, slots=True)
class CardDetailData:
    card_index: int
    card_id: int
    localized_text: CardLocalizedText
    password: str
    level: int | None
    attack: int | None
    defense: int | None
    attribute: str
    card_type: str
    card_category: str
    pack: str
    image_name: str
    note: str = ""
    monster_type_code: int | None = None
    card_category_code: int | None = None
    attribute_code: int | None = None

    def to_draft(self, *, is_new: bool = False) -> CardEditDraft:
        return CardEditDraft(
            card_index=self.card_index,
            card_id=self.card_id,
            localized_text=self.localized_text.clone(),
            password=self.password,
            level=self.level,
            attack=self.attack,
            defense=self.defense,
            attribute=self.attribute,
            card_type=self.card_type,
            card_category=self.card_category,
            pack=self.pack,
            image_name=self.image_name,
            note=self.note,
            monster_type_code=self.monster_type_code,
            card_category_code=self.card_category_code,
            attribute_code=self.attribute_code,
            is_new=is_new,
        )


@dataclass(slots=True)
class CardEditDraft:
    card_index: int
    card_id: int
    localized_text: CardLocalizedText = field(default_factory=CardLocalizedText)
    password: str = MISSING_CARD_PASSWORD
    level: int | None = None
    attack: int | None = None
    defense: int | None = None
    attribute: str = ""
    card_type: str = "non_game_card"
    card_category: str = ""
    pack: str = "disabled"
    image_name: str = "token_sl.bmp"
    large_image_source: Path | bytes | None = None
    small_image_source: Path | bytes | None = None
    note: str = ""
    is_new: bool = False
    dirty: bool = False
    touched_fields: set[str] = field(default_factory=set)
    monster_type_code: int | None = None
    card_category_code: int | None = None
    attribute_code: int | None = None

    def clone(self) -> CardEditDraft:
        return CardEditDraft(
            card_index=self.card_index,
            card_id=self.card_id,
            localized_text=self.localized_text.clone(),
            password=self.password,
            level=self.level,
            attack=self.attack,
            defense=self.defense,
            attribute=self.attribute,
            card_type=self.card_type,
            card_category=self.card_category,
            pack=self.pack,
            image_name=self.image_name,
            large_image_source=self.large_image_source,
            small_image_source=self.small_image_source,
            note=self.note,
            is_new=self.is_new,
            dirty=self.dirty,
            touched_fields=set(self.touched_fields),
            monster_type_code=self.monster_type_code,
            card_category_code=self.card_category_code,
            attribute_code=self.attribute_code,
        )

    def to_detail(self) -> CardDetailData:
        return CardDetailData(
            card_index=self.card_index,
            card_id=self.card_id,
            localized_text=self.localized_text.clone(),
            password=self.password,
            level=self.level,
            attack=self.attack,
            defense=self.defense,
            attribute=self.attribute,
            card_type=self.card_type,
            card_category=self.card_category,
            pack=self.pack,
            image_name=self.image_name,
            note=self.note,
            monster_type_code=self.monster_type_code,
            card_category_code=self.card_category_code,
            attribute_code=self.attribute_code,
        )

    def mark_touched(self, field_name: str) -> None:
        self.touched_fields.add(field_name)
        code_field = {
            "card_type": "monster_type_code",
            "card_category": "card_category_code",
            "attribute": "attribute_code",
        }.get(field_name)
        if code_field is not None:
            setattr(self, code_field, None)
        self.dirty = True


@dataclass(frozen=True, slots=True)
class CardReferenceData:
    matched_name: str | None
    matched_language: str | None
    localized_names: Mapping[str, str]
    localized_descriptions: Mapping[str, str]
    canonical_id: str | None = None
    password: str | None = None
    level: int | None = None
    attack: int | None = None
    defense: int | None = None
    attribute: str | None = None
    card_type: str | None = None
    card_category: str | None = None
    source: str = "official_card_database"
    confidence: str = "exact"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "localized_names",
            MappingProxyType(dict(self.localized_names)),
        )
        object.__setattr__(
            self,
            "localized_descriptions",
            MappingProxyType(dict(self.localized_descriptions)),
        )


@dataclass(frozen=True, slots=True)
class CardImportResult:
    rows: tuple[Mapping[str, str], ...]


@dataclass(frozen=True, slots=True)
class CardImportApplyResult:
    cards: tuple[CardEditDraft, ...]
    total_rows: int
    matched: int
    skipped_unknown_ids: int
    ignored_image_name_changes: int
    updated: int


@dataclass(frozen=True, slots=True)
class BulkSuggestionResult:
    cards: tuple[CardEditDraft, ...]
    total_candidates: int
    resolved: int
    partially_filled: int
    not_found: int
    skipped_no_query_name: int
    unchanged: int
    failed: int
    cancelled: bool
    image_staged: int = 0
    image_failed: int = 0
    total_source_cards: int = 0
    skipped_complete: int = 0
    selected_workers: int = 0
    available_memory_bytes: int | None = None

    @property
    def updated(self) -> int:
        return self.resolved


@dataclass(frozen=True, slots=True)
class CardSuggestionResult:
    draft: CardEditDraft
    applied_fields: tuple[str, ...]
    reference_found: bool
    image_staged: bool = False
    image_error: str | None = None
    reference_source: str = "not_found"
    reference_confidence: str | None = None


def _complete_localized_values(
    values: Mapping[str, object],
    field_name: str,
) -> dict[str, str]:
    unsupported = sorted(set(values).difference(LANGUAGE_PREFIXES))
    if unsupported:
        raise ValueError(
            f"Unsupported languages in CardLocalizedText.{field_name}: "
            + ", ".join(unsupported)
        )
    return {
        language: "" if values.get(language) is None else str(values.get(language, ""))
        for language in LANGUAGE_PREFIXES
    }
