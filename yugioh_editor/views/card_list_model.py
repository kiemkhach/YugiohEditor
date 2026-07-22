from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from yugioh_editor.common.card_properties import (
    display_property_label,
    normalize_property_label,
)
from yugioh_editor.common.constants import DEFAULT_LANGUAGE, normalize_language_code
from yugioh_editor.models.card_editing import CardDetailData, CardEditDraft

_DISABLED_PACK = "disabled"
_ENABLE_ALL_PACK = "joey"
_NON_GAME_CARD_TYPE_CODE = 0x17
_PROTECTED_GOD_CARD_NAMES = frozenset(
    name.casefold()
    for name in {
        "Obelisk the Tormentor",
        "Slifer the Sky Dragon",
        "The Winged Dragon of Ra",
    }
)


@dataclass(frozen=True, slots=True)
class EnableAllResult:
    updated: int
    protected_non_game: int
    protected_god: int
    protected_token: int
    already_enabled: int

    @property
    def protected(self) -> int:
        return self.protected_non_game + self.protected_god + self.protected_token

    @property
    def skipped(self) -> int:
        return self.protected + self.already_enabled


class CardListModel(QAbstractTableModel):
    COLUMNS = (
        ("card_index", "Card Index"),
        ("card_id", "Card ID"),
        ("name", "Card Name"),
        ("description", "Description"),
        ("password", "Password"),
        ("level", "Level"),
        ("attack", "Attack"),
        ("defense", "Defense"),
        ("attribute", "Attribute"),
        ("card_type", "Card Type"),
        ("card_category", "Category"),
        ("pack", "Pack"),
        ("image_name", "Image Name"),
    )

    def __init__(
        self,
        cards: Sequence[CardDetailData | CardEditDraft] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._cards: list[CardEditDraft] = []
        self._row_by_card_index: dict[int, int] = {}
        self._display_language = DEFAULT_LANGUAGE
        self.reset_from_project(cards)

    def rowCount(self, _parent=QModelIndex()) -> int:
        return len(self._cards)

    def columnCount(self, _parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._cards):
            return None
        card = self._cards[index.row()]
        field_name = self.COLUMNS[index.column()][0]
        if field_name == "name":
            value = card.localized_text.names[self._display_language]
        elif field_name == "description":
            value = card.localized_text.descriptions[self._display_language]
        else:
            value = getattr(card, field_name)
        if role == Qt.UserRole:
            return value
        if role != Qt.DisplayRole:
            return None
        if field_name in {"attribute", "card_type", "card_category", "pack"}:
            return display_property_label(value)
        return "" if value is None else str(value)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section][1]
        if orientation == Qt.Vertical:
            return section + 1
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def cards(self) -> tuple[CardEditDraft, ...]:
        return tuple(card.clone() for card in self._cards)

    @property
    def display_language(self) -> str:
        return self._display_language

    def set_display_language(self, language: str) -> None:
        normalized = normalize_language_code(language)
        if normalized == self._display_language:
            return
        self._display_language = normalized
        if not self._cards:
            return
        columns = {
            name: position for position, (name, _label) in enumerate(self.COLUMNS)
        }
        self.dataChanged.emit(
            self.index(0, columns["name"]),
            self.index(len(self._cards) - 1, columns["description"]),
            [Qt.DisplayRole, Qt.UserRole],
        )

    def card_at(self, row: int) -> CardEditDraft:
        return self._cards[row].clone()

    def card_by_index(self, card_index: int) -> CardEditDraft | None:
        row = self.row_for_card_index(card_index)
        return None if row is None else self._cards[row].clone()

    def row_for_card_index(self, card_index: int) -> int | None:
        return self._row_by_card_index.get(int(card_index))

    def card_index_bounds(self) -> tuple[int, int]:
        if not self._row_by_card_index:
            raise IndexError("The project contains no cards.")
        indexes = self._row_by_card_index
        return min(indexes), max(indexes)

    def has_dirty_cards(self) -> bool:
        return any(card.dirty for card in self._cards)

    def dirty_cards(self) -> tuple[CardEditDraft, ...]:
        return tuple(card.clone() for card in self._cards if card.dirty)

    def pack_at(self, row: int) -> str:
        return self._cards[row].pack

    def enable_all_eligible_cards(self) -> EnableAllResult:
        staged = [card.clone() for card in self._cards]
        counts = {
            "updated": 0,
            "protected_non_game": 0,
            "protected_god": 0,
            "protected_token": 0,
            "already_enabled": 0,
        }
        for card in staged:
            disposition = _enable_all_disposition(card)
            if disposition != "updated":
                counts[disposition] += 1
                continue
            card.pack = _ENABLE_ALL_PACK
            card.mark_touched("pack")
            counts["updated"] += 1

        result = EnableAllResult(**counts)
        if not result.updated:
            return result
        self._cards = staged
        pack_column = next(
            position
            for position, (field_name, _label) in enumerate(self.COLUMNS)
            if field_name == "pack"
        )
        self.dataChanged.emit(
            self.index(0, pack_column),
            self.index(len(self._cards) - 1, pack_column),
            [Qt.DisplayRole, Qt.UserRole],
        )
        return result

    def update_card(self, card: CardDetailData | CardEditDraft) -> None:
        draft = card.clone() if isinstance(card, CardEditDraft) else card.to_draft()
        row = self.row_for_card_index(draft.card_index)
        if row is None:
            raise IndexError(f"Card index {draft.card_index} is not in the model.")
        self._cards[row] = draft
        self.dataChanged.emit(
            self.index(row, 0),
            self.index(row, self.columnCount() - 1),
        )

    def insert_card(self, card: CardDetailData | CardEditDraft) -> None:
        draft = card.clone() if isinstance(card, CardEditDraft) else card.to_draft()
        if self.row_for_card_index(draft.card_index) is not None:
            raise ValueError(f"Card index {draft.card_index} already exists.")
        row = len(self._cards)
        self.beginInsertRows(QModelIndex(), row, row)
        self._cards.append(draft)
        self._row_by_card_index[draft.card_index] = row
        self.endInsertRows()

    def reset_from_project(
        self,
        cards: Sequence[CardDetailData | CardEditDraft],
    ) -> None:
        self.beginResetModel()
        self._cards = [
            card.clone() if isinstance(card, CardEditDraft) else card.to_draft()
            for card in cards
        ]
        self._row_by_card_index = {
            card.card_index: row for row, card in enumerate(self._cards)
        }
        self.endResetModel()


def _enable_all_disposition(card: CardEditDraft) -> str:
    if card.pack != _DISABLED_PACK:
        return "already_enabled"
    if _is_non_game_card(card):
        return "protected_non_game"
    english_name = _normalize_canonical_english_name(
        card.localized_text.names.get(DEFAULT_LANGUAGE, "")
    )
    if english_name in _PROTECTED_GOD_CARD_NAMES:
        return "protected_god"
    if english_name.endswith(" token"):
        return "protected_token"
    return "updated"


def _is_non_game_card(card: CardEditDraft) -> bool:
    try:
        has_non_game_code = int(card.monster_type_code) == _NON_GAME_CARD_TYPE_CODE
    except (TypeError, ValueError):
        has_non_game_code = False
    return has_non_game_code or normalize_property_label(card.card_type) == (
        "non_game_card"
    )


def _normalize_canonical_english_name(value: object) -> str:
    normalized = unicodedata.normalize("NFC", str(value))
    return " ".join(normalized.split()).casefold()


class UnusedCardFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._unused_only = False
        self.setDynamicSortFilter(True)

    @property
    def unused_only(self) -> bool:
        return self._unused_only

    def set_unused_only(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if normalized == self._unused_only:
            return
        self._unused_only = normalized
        self.beginFilterChange()
        self.endFilterChange()

    def filterAcceptsRow(self, source_row: int, _source_parent: QModelIndex) -> bool:
        if not self._unused_only:
            return True
        model = self.sourceModel()
        return (
            isinstance(model, CardListModel) and model.pack_at(source_row) == "disabled"
        )
