from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from enum import IntEnum

from yugioh_editor.common.joey_card_capacity import (
    JOEY_CARD_ID_MAX,
    JOEY_INVALID_CARD_ID,
    JOEY_LEGACY_ALIAS_TO_BASE,
    JOEY_MAX_ACTIVE_SLOT,
    JOEY_MAX_RECORD_COUNT,
    JOEY_PROTECTED_LEGACY_ALIAS_IDS,
    JOEY_STATE_BASE_ADDRESS,
    JoeyCardCapacityError,
    allocate_safe_joey_card_id,
    analyze_joey_card_ids,
    validate_joey_edit_topology,
)


class _IntegralCardId(IntEnum):
    DUMMY = -1
    FIRST = 0


class JoeyCardCapacityTests(unittest.TestCase):
    @staticmethod
    def _pack_values(record_count: int) -> list[int]:
        return [-1, *range(record_count - 1)]

    def test_pack_record_count_boundaries(self):
        with self.assertRaisesRegex(JoeyCardCapacityError, "fewer than 1115"):
            analyze_joey_card_ids(self._pack_values(1114))

        for record_count in (1115, 1116, JOEY_MAX_RECORD_COUNT):
            with self.subTest(record_count=record_count):
                plan = analyze_joey_card_ids(self._pack_values(record_count))
                self.assertEqual(plan.record_count, record_count)
                self.assertEqual(plan.active_count, record_count - 1)
                self.assertEqual(plan.maximum_active_slot, record_count - 1)

        with self.assertRaisesRegex(JoeyCardCapacityError, "4095 total records"):
            analyze_joey_card_ids(self._pack_values(4096))

    def test_maximum_plan_is_immutable_and_uses_verified_formulas(self):
        values = [-1, *range(4093), JOEY_CARD_ID_MAX]
        plan = analyze_joey_card_ids(values)

        self.assertEqual(plan.record_count, 0x0FFF)
        self.assertEqual(plan.active_count, 4094)
        self.assertEqual(plan.maximum_active_slot, JOEY_MAX_ACTIVE_SLOT)
        self.assertEqual(plan.exclusive_upper_bound, 0x0FFF)
        self.assertEqual(plan.active_state_end_address, 0x00C25FFE)
        self.assertEqual(
            plan.active_state_end_address,
            JOEY_STATE_BASE_ADDRESS + plan.record_count * 2,
        )
        with self.assertRaises(FrozenInstanceError):
            plan.record_count = 1  # type: ignore[misc]

    def test_pack_topology_rejects_invalid_dummy_active_ids_and_duplicates(self):
        valid = self._pack_values(1115)
        invalid_cases = (
            ([0, *valid[1:]], "dummy slot 0"),
            ([valid[0], -2, *valid[2:]], "outside the supported Joey range"),
            ([valid[0], JOEY_INVALID_CARD_ID, *valid[2:]], "4095 is reserved"),
            ([valid[0], 1, 1, *valid[3:]], "duplicate active Card ID 1"),
        )
        for values, message in invalid_cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(JoeyCardCapacityError, message),
            ):
                analyze_joey_card_ids(values)

    def test_physical_workspace_strings_are_strict_canonical_decimals(self):
        values = ["-1", *(str(card_id) for card_id in range(1114))]
        self.assertEqual(analyze_joey_card_ids(values).record_count, 1115)
        self.assertEqual(
            validate_joey_edit_topology(
                [_IntegralCardId.DUMMY, _IntegralCardId.FIRST]
            ).active_count,
            1,
        )

        for invalid in (True, 1.0, None, "01", "+1", "-0", " 1", "1 ", "1.0"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(
                    JoeyCardCapacityError,
                    "integer or canonical decimal string",
                ),
            ):
                validate_joey_edit_topology(["-1", invalid])

    def test_edit_topology_allows_substock_but_requires_a_dummy(self):
        plan = validate_joey_edit_topology([-1, 17, 4094])
        self.assertEqual(plan.record_count, 3)
        self.assertEqual(plan.active_count, 2)
        with self.assertRaisesRegex(JoeyCardCapacityError, "dummy slot 0"):
            validate_joey_edit_topology([])
        with self.assertRaisesRegex(JoeyCardCapacityError, "dummy slot 0"):
            validate_joey_edit_topology([1])

    def test_all_nine_legacy_aliases_are_valid_but_protected(self):
        self.assertEqual(
            JOEY_LEGACY_ALIAS_TO_BASE,
            {
                2000: 0,
                2014: 14,
                2034: 34,
                2037: 37,
                2040: 40,
                2063: 63,
                2068: 68,
                2387: 387,
                2389: 389,
            },
        )
        self.assertEqual(
            JOEY_PROTECTED_LEGACY_ALIAS_IDS,
            frozenset(JOEY_LEGACY_ALIAS_TO_BASE),
        )
        values = [-1, *range(1105), *JOEY_LEGACY_ALIAS_TO_BASE]
        self.assertEqual(analyze_joey_card_ids(values).record_count, 1115)

    def test_allocator_skips_free_aliases_and_keeps_4093_allocatable(self):
        self.assertEqual(
            allocate_safe_joey_card_id([-1, *range(2000)]),
            2001,
        )
        self.assertEqual(
            allocate_safe_joey_card_id([-1, *range(4093)]),
            4093,
        )

    def test_allocator_fails_when_no_safe_id_remains(self):
        with self.assertRaisesRegex(JoeyCardCapacityError, "No safe Card ID"):
            allocate_safe_joey_card_id([-1, *range(JOEY_INVALID_CARD_ID)])


if __name__ == "__main__":
    unittest.main()
