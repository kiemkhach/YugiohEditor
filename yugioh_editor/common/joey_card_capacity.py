from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Integral

JOEY_STOCK_RECORD_COUNT = 1115

JOEY_DUMMY_SLOT = 0
JOEY_MAX_ACTIVE_SLOT = 0x0FFE
JOEY_MAX_RECORD_COUNT = 0x0FFF

JOEY_CARD_ID_MIN = 0x000
JOEY_CARD_ID_MAX = 0x0FFE
JOEY_INVALID_CARD_ID = 0x0FFF

JOEY_STATE_BASE_ADDRESS = 0x00C24000
JOEY_MAX_ACTIVE_STATE_END_ADDRESS = 0x00C25FFE

JOEY_LEGACY_ALIAS_TO_BASE = {
    2000: 0,
    2014: 14,
    2034: 34,
    2037: 37,
    2040: 40,
    2063: 63,
    2068: 68,
    2387: 387,
    2389: 389,
}
JOEY_PROTECTED_LEGACY_ALIAS_IDS = frozenset(JOEY_LEGACY_ALIAS_TO_BASE)

_CANONICAL_DECIMAL_PATTERN = re.compile(r"(?:0|[1-9][0-9]*|-[1-9][0-9]*)\Z")


class JoeyCardCapacityError(ValueError):
    """The physical Joey card-ID topology violates the runtime contract."""


@dataclass(frozen=True, slots=True)
class JoeyCardCapacityPlan:
    record_count: int
    active_count: int
    maximum_active_slot: int
    exclusive_upper_bound: int
    active_state_end_address: int


def analyze_joey_card_ids(values: Sequence[object]) -> JoeyCardCapacityPlan:
    """Validate a Pack-ready Joey card-ID table and derive its runtime bounds."""

    return _analyze_joey_card_ids(
        values,
        minimum_record_count=JOEY_STOCK_RECORD_COUNT,
    )


def validate_joey_edit_topology(
    values: Sequence[object],
) -> JoeyCardCapacityPlan:
    """Validate an editable Joey table, including pre-stock test/project tables."""

    return _analyze_joey_card_ids(values, minimum_record_count=1)


def allocate_safe_joey_card_id(existing_ids: Sequence[object]) -> int:
    """Return the lowest free Card ID that has no protected alias semantics."""

    occupied: set[int] = set()
    for position, value in enumerate(existing_ids):
        card_id = _parse_card_id(value, slot=position)
        if card_id == -1:
            continue
        _validate_active_card_id(card_id, slot=position)
        occupied.add(card_id)

    for candidate in range(JOEY_INVALID_CARD_ID):
        if candidate in occupied:
            continue
        if candidate in JOEY_PROTECTED_LEGACY_ALIAS_IDS:
            continue
        return candidate
    raise JoeyCardCapacityError("No safe Card ID remains in the 12-bit Joey namespace.")


def _analyze_joey_card_ids(
    values: Sequence[object],
    *,
    minimum_record_count: int,
) -> JoeyCardCapacityPlan:
    raw_values = tuple(values)
    record_count = len(raw_values)
    if record_count < minimum_record_count:
        if minimum_record_count == JOEY_STOCK_RECORD_COUNT:
            raise JoeyCardCapacityError(
                "Joey projects with fewer than 1115 records are not supported "
                "by this executable profile."
            )
        raise JoeyCardCapacityError("Joey card topology must contain dummy slot 0.")
    if record_count > JOEY_MAX_RECORD_COUNT:
        raise JoeyCardCapacityError(
            "Maximum Joey card capacity is 4094 active cards (4095 total records)."
        )

    card_ids = tuple(
        _parse_card_id(value, slot=slot) for slot, value in enumerate(raw_values)
    )
    if card_ids[JOEY_DUMMY_SLOT] != -1:
        raise JoeyCardCapacityError("Joey dummy slot 0 must contain Card ID -1.")

    occupied: set[int] = set()
    for slot, card_id in enumerate(card_ids[1:], start=1):
        _validate_active_card_id(card_id, slot=slot)
        if card_id in occupied:
            raise JoeyCardCapacityError(
                f"Project contains duplicate active Card ID {card_id}."
            )
        occupied.add(card_id)

    active_state_end_address = JOEY_STATE_BASE_ADDRESS + record_count * 2
    if active_state_end_address > JOEY_MAX_ACTIVE_STATE_END_ADDRESS:
        raise JoeyCardCapacityError(
            "Derived Joey active-state end exceeds the verified runtime bound."
        )
    return JoeyCardCapacityPlan(
        record_count=record_count,
        active_count=record_count - 1,
        maximum_active_slot=record_count - 1,
        exclusive_upper_bound=record_count,
        active_state_end_address=active_state_end_address,
    )


def _parse_card_id(value: object, *, slot: int) -> int:
    if isinstance(value, bool):
        raise JoeyCardCapacityError(
            f"Card ID at slot {slot} must be an integer or canonical decimal "
            f"string, got {value!r}."
        )
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, str) and _CANONICAL_DECIMAL_PATTERN.fullmatch(value):
        return int(value)
    raise JoeyCardCapacityError(
        f"Card ID at slot {slot} must be an integer or canonical decimal "
        f"string, got {value!r}."
    )


def _validate_active_card_id(card_id: int, *, slot: int) -> None:
    if card_id == JOEY_INVALID_CARD_ID:
        raise JoeyCardCapacityError("Card ID 4095 is reserved by the 12-bit runtime.")
    if not JOEY_CARD_ID_MIN <= card_id <= JOEY_CARD_ID_MAX:
        raise JoeyCardCapacityError(
            f"Card ID {card_id} at slot {slot} is outside the supported Joey "
            "range 0..4094."
        )
