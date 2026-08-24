from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from tests.step8_executable_support import (
    configure_repository_profile,
    controlled_stock,
    exact_stock_path_from_environment,
    executable_resource,
    move_helper_raw_data,
    patch_controlled,
    refresh_source_hash,
    va_to_offset,
)
from yugioh_editor.common.errors import RulePipelineError
from yugioh_editor.repositories.game.repository import GameRepository


class Step8ExecutableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stock, cls.profile = controlled_stock()

    def test_count_contract_and_formula_boundaries(self) -> None:
        arbitrary = bytearray(b"not a PE and not the registered stock executable")
        original = bytes(arbitrary)
        with self.assertRaisesRegex(ValueError, "at least 1115"):
            patch_controlled(arbitrary, self.profile, 1114)
        self.assertEqual(patch_controlled(arbitrary, self.profile, 1115), original)
        self.assertEqual(arbitrary, bytearray(original))
        for count in (1116, 2049, 4095):
            with self.subTest(count=count):
                output = patch_controlled(self.stock, self.profile, count)
                GameRepository.verify_executable_card_capacity(
                    output,
                    card_record_count=count,
                    profile=self.profile,
                )
                self.assertEqual(
                    GameRepository._calculate_executable_card_capacity_values(
                        count, self.profile
                    ),
                    {
                        "maximum_active_slot": count - 1,
                        "exclusive_upper_bound": count,
                        "active_state_end_address": 0x00C24000 + count * 2,
                    },
                )
        with self.assertRaisesRegex(ValueError, "at most 4095"):
            patch_controlled(arbitrary, self.profile, 4096)
        self.assertEqual(arbitrary, bytearray(original))

    def test_optional_capacity_plan_is_recomputed_and_compared_exactly(self) -> None:
        count = 2049
        expected = {
            "maximum_active_slot": 2048,
            "exclusive_upper_bound": 2049,
            "active_state_end_address": 0x00C25002,
        }
        output = patch_controlled(
            self.stock,
            self.profile,
            count,
            capacity_plan=expected,
        )
        GameRepository.verify_executable_card_capacity(
            output, card_record_count=count, profile=self.profile
        )
        for supplied, message in (
            ({**expected, "maximum_active_slot": 7}, "maximum_active_slot"),
            ({key: expected[key] for key in tuple(expected)[1:]}, "missing"),
            ({**expected, "unknown": 1}, "unknown"),
            ({**expected, "exclusive_upper_bound": True}, "integer"),
        ):
            with self.subTest(supplied=supplied):
                with self.assertRaisesRegex((TypeError, ValueError), message):
                    patch_controlled(
                        self.stock,
                        self.profile,
                        count,
                        capacity_plan=supplied,
                    )

    def test_generated_output_contains_every_declared_step8_structure(self) -> None:
        count = 2049
        mutable_source = bytearray(self.stock)
        original_profile = deepcopy(self.profile)
        output = patch_controlled(mutable_source, self.profile, count)
        self.assertEqual(mutable_source, bytearray(self.stock))
        self.assertEqual(self.profile, original_profile)
        pe = GameRepository._parse_executable_pe(output)
        updates = self.profile["pe_header_updates"]
        self.assertEqual(pe["number_of_sections"], updates["number_of_sections"])
        self.assertEqual(pe["size_of_code"], updates["size_of_code"])
        self.assertEqual(
            pe["size_of_uninitialized_data"],
            updates["size_of_uninitialized_data"],
        )
        self.assertEqual(pe["size_of_image"], updates["size_of_image"])
        self.assertEqual(len(output), updates["output_size_before_icon"])
        for actual, expected in zip(
            pe["sections"][-2:], self.profile["pe_sections"], strict=True
        ):
            for field in (
                "name",
                "virtual_size",
                "virtual_address",
                "raw_size",
                "raw_pointer",
                "characteristics",
            ):
                self.assertEqual(actual[field], expected[field])
        helper_section = pe["sections"][-1]
        helper = output[
            helper_section["raw_pointer"] : helper_section["raw_pointer"]
            + helper_section["raw_size"]
        ]
        self.assertEqual(
            hashlib.sha256(helper).hexdigest(),
            self.profile["helper_section_sha256"],
        )
        for fragment in self.profile["helper_fragments"]:
            offset = fragment["offset"]
            self.assertEqual(
                helper[offset : offset + len(fragment["bytes"])],
                fragment["bytes"],
            )

        relocation_count = 0
        for group in self.profile["state_relocation_groups"]:
            encoded = group["replacement"].to_bytes(group["value_width"], "little")
            for site in group["sites"]:
                expected = bytearray(site["expected"])
                start = site["value_offset"]
                expected[start : start + group["value_width"]] = encoded
                self.assertEqual(
                    self._bytes_at(output, pe, site["va"], len(expected)), expected
                )
                relocation_count += 1
        self.assertEqual(relocation_count, 69)
        for field in (
            "snapshot_patches",
            "fixed_patch_sites",
            "hooks",
            "alias_consumer_patches",
        ):
            for site in self.profile[field]:
                self.assertEqual(
                    self._bytes_at(output, pe, site["va"], len(site["replacement"])),
                    site["replacement"],
                )
        derived = GameRepository._calculate_executable_card_capacity_values(
            count, self.profile
        )
        self.assertEqual(len(self.profile["dynamic_patch_sites"]), 17)
        for site in self.profile["dynamic_patch_sites"]:
            expected = bytearray(site["expected"])
            start = site["value_offset"]
            width = site["value_width"]
            expected[start : start + width] = derived[site["value_name"]].to_bytes(
                width, "little"
            )
            self.assertEqual(
                self._bytes_at(output, pe, site["va"], len(expected)), expected
            )
        for field in ("invariant_sites", "known_false_matches"):
            for site in self.profile[field]:
                self.assertEqual(
                    self._bytes_at(output, pe, site["va"], len(site["expected"])),
                    site["expected"],
                )

    def test_declared_changed_region_gate_rejects_an_unlisted_change(self) -> None:
        output = bytearray(patch_controlled(self.stock, self.profile, 1116))
        output[0x1800] ^= 0x01
        source_pe = GameRepository._parse_executable_pe(self.stock)
        with self.assertRaisesRegex(ValueError, "undeclared byte"):
            GameRepository._validate_executable_changed_regions(
                self.stock,
                bytes(output),
                self.profile,
                source_pe,
            )

    def test_structural_verifier_rejects_invariant_and_false_match_changes(
        self,
    ) -> None:
        output = patch_controlled(self.stock, self.profile, 1116)
        pe = GameRepository._parse_executable_pe(output)
        cases = (
            self.profile["invariant_sites"][0],
            self.profile["known_false_matches"][0],
        )
        for site in cases:
            with self.subTest(va=site["va"]):
                corrupted = bytearray(output)
                offset = GameRepository._executable_va_to_file_offset(
                    corrupted, pe, site["va"], len(site["expected"])
                )
                corrupted[offset] ^= 0x01
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    GameRepository.verify_executable_card_capacity(
                        bytes(corrupted),
                        card_record_count=1116,
                        profile=self.profile,
                    )

    def test_source_size_hash_pe_header_slack_and_full_windows_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "source size"):
            patch_controlled(self.stock[:-1], self.profile, 1116)

        wrong_hash_source = bytearray(self.stock)
        wrong_hash_source[-1] ^= 0x01
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            patch_controlled(wrong_hash_source, self.profile, 1116)

        pe_source = bytearray(self.stock)
        pe_offset = self.profile["source"]["pe"]["pe_offset"]
        struct.pack_into("<H", pe_source, pe_offset + 4, 0x8664)
        pe_profile = deepcopy(self.profile)
        refresh_source_hash(pe_profile, pe_source)
        with self.assertRaisesRegex(ValueError, "PE field 'machine'"):
            patch_controlled(pe_source, pe_profile, 1116)

        source_pe = self.profile["source"]["pe"]
        pe_corruptions = (
            ("DOS magic", 0, b"NZ"),
            ("PE signature", source_pe["pe_offset"], b"PX\x00\x00"),
            (
                "PE32",
                source_pe["pe_offset"] + 24,
                struct.pack("<H", 0x020B),
            ),
            (
                "section 0 field 'raw_pointer'",
                source_pe["section_table_offset"] + 20,
                struct.pack("<I", 0x2000),
            ),
        )
        for message, offset, replacement in pe_corruptions:
            with self.subTest(message=message):
                corrupted = bytearray(self.stock)
                corrupted[offset : offset + len(replacement)] = replacement
                corrupted_profile = deepcopy(self.profile)
                refresh_source_hash(corrupted_profile, corrupted)
                with self.assertRaisesRegex(ValueError, message):
                    patch_controlled(corrupted, corrupted_profile, 1116)

        slack_source = bytearray(self.stock)
        slack_source[self.profile["source"]["pe"]["section_table_end"] + 100] = 1
        slack_profile = deepcopy(self.profile)
        refresh_source_hash(slack_profile, slack_source)
        with self.assertRaisesRegex(ValueError, "header slack"):
            patch_controlled(slack_source, slack_profile, 1116)

        window_source = bytearray(self.stock)
        snapshot = self.profile["snapshot_patches"][0]
        window_offset = va_to_offset(
            self.profile, snapshot["va"], len(snapshot["expected"])
        )
        window_source[window_offset + len(snapshot["expected"]) - 1] ^= 0x01
        window_profile = deepcopy(self.profile)
        refresh_source_hash(window_profile, window_source)
        with self.assertRaisesRegex(ValueError, "stock window mismatch"):
            patch_controlled(window_source, window_profile, 1116)

    def test_structural_verifier_rejects_pe_and_dynamic_site_corruption(self) -> None:
        output = patch_controlled(self.stock, self.profile, 1116)
        pe = GameRepository._parse_executable_pe(output)
        bad_size_of_code = bytearray(output)
        struct.pack_into(
            "<I",
            bad_size_of_code,
            pe["size_of_code_offset"],
            pe["size_of_code"] + 0x1000,
        )
        with self.assertRaisesRegex(ValueError, "size_of_code"):
            GameRepository.verify_executable_card_capacity(
                bytes(bad_size_of_code),
                card_record_count=1116,
                profile=self.profile,
            )

        dynamic = self.profile["dynamic_patch_sites"][0]
        bad_dynamic = bytearray(output)
        dynamic_offset = GameRepository._executable_va_to_file_offset(
            bad_dynamic,
            pe,
            dynamic["va"],
            len(dynamic["expected"]),
        )
        bad_dynamic[dynamic_offset + dynamic["value_offset"]] ^= 0x01
        with self.assertRaisesRegex(ValueError, "verification failed"):
            GameRepository.verify_executable_card_capacity(
                bytes(bad_dynamic),
                card_record_count=1116,
                profile=self.profile,
            )

        moved_headers = bytearray(output)
        original_pe_offset = pe["pe_offset"]
        relocated_pe_offset = 0xC00
        header = output[original_pe_offset : pe["section_table_end"]]
        moved_headers[relocated_pe_offset : relocated_pe_offset + len(header)] = header
        struct.pack_into("<I", moved_headers, 0x3C, relocated_pe_offset)
        with self.assertRaisesRegex(ValueError, "pe_offset"):
            GameRepository.verify_executable_card_capacity(
                bytes(moved_headers),
                card_record_count=1116,
                profile=self.profile,
            )

    def test_profile_schema_geometry_windows_and_control_flow_are_strict(self) -> None:
        cases = []
        missing = deepcopy(self.profile)
        del missing["hooks"]
        cases.append((missing, "missing hooks"))
        unknown = deepcopy(self.profile)
        unknown["obsolete"] = True
        cases.append((unknown, "unknown obsolete"))
        overlap = deepcopy(self.profile)
        overlap["dynamic_patch_sites"][1]["va"] = overlap["dynamic_patch_sites"][0][
            "va"
        ]
        cases.append((overlap, "overlap"))
        bad_digest = deepcopy(self.profile)
        bad_digest["helper_section_sha256"] = "0" * 64
        cases.append((bad_digest, "helper-section digest"))
        bad_call = deepcopy(self.profile)
        bad_call["hooks"][0]["replacement"] = b"\xe9\x00\x00\x00\x00\x90\x90"
        cases.append((bad_call, "helper jump"))
        bad_section = deepcopy(self.profile)
        bad_section["pe_sections"][1]["virtual_address"] = bad_section["pe_sections"][
            0
        ]["virtual_address"]
        cases.append((bad_section, "overlap|helper base"))
        bad_helper_raw_size = deepcopy(self.profile)
        bad_helper_raw_size["pe_sections"][1]["raw_size"] += 0x1000
        cases.append((bad_helper_raw_size, "raw size.*helper storage"))
        for invalid, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex((TypeError, ValueError), message):
                    GameRepository.preflight_executable_card_capacity(
                        b"arbitrary legacy bytes",
                        card_record_count=1115,
                        profile=invalid,
                    )

    def test_nonbinary_and_count_metadata_fail_without_mutating_input(self) -> None:
        for value in (None, "bytes", memoryview(b"bytes"), [1, 2, 3]):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "bytes or bytearray"):
                    GameRepository.patch_executable_card_capacity(
                        value,
                        context=SimpleNamespace(metadata={"card_record_count": 1116}),
                        profile=self.profile,
                    )
        for metadata in (
            {},
            {"card_record_count": "1116"},
            {"card_record_count": 1116.0},
            {"card_record_count": True},
            {"card_record_count": 1116, "card_capacity_plan": []},
        ):
            with self.subTest(metadata=metadata):
                with self.assertRaisesRegex(
                    (TypeError, ValueError), "card_record_count|card_capacity_plan"
                ):
                    GameRepository.patch_executable_card_capacity(
                        self.stock,
                        context=SimpleNamespace(metadata=metadata),
                        profile=self.profile,
                    )

    def test_public_preflight_uses_configured_rule_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            configure_repository_profile(repository, self.profile)
            resource = executable_resource(self.stock)
            plan = repository.preflight_executable_resource(
                resource,
                metadata={"card_record_count": 1116},
            )
            self.assertEqual(
                plan,
                {
                    "maximum_active_slot": 1115,
                    "exclusive_upper_bound": 1116,
                    "active_state_end_address": 0x00C248B8,
                },
            )
            self.assertEqual(list(repository.root.iterdir()), [])
            legacy = executable_resource(b"arbitrary legacy executable")
            self.assertEqual(
                repository.preflight_executable_resource(
                    legacy,
                    metadata={"card_record_count": 1115},
                )["exclusive_upper_bound"],
                1115,
            )
            with self.assertRaises(RulePipelineError):
                repository.preflight_executable_resource(
                    resource,
                    metadata={"card_record_count": 4096},
                )

    def test_public_preflight_verifies_the_complete_custom_pipeline_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            configure_repository_profile(
                repository,
                self.profile,
                extra_pre_encode=(
                    {
                        "method_name": "slice_bytes",
                        "params": {"end": -1},
                    },
                ),
            )
            with self.assertRaisesRegex(ValueError, "raw data is incomplete"):
                repository.preflight_executable_resource(
                    executable_resource(self.stock),
                    metadata={"card_record_count": 1116},
                )
            self.assertEqual(list(repository.root.iterdir()), [])

    def test_write_verifies_before_write_and_after_raw_layout_moving_icon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            configure_repository_profile(repository, self.profile)
            resource = executable_resource(self.stock)

            def move_icon(file_name: str, icon_data: bytes):
                del icon_data
                current = repository._connection.read_executable(file_name)
                repository._connection.write_executable(
                    file_name, move_helper_raw_data(current)
                )

            with patch.object(
                repository._connection,
                "update_executable_icon",
                side_effect=move_icon,
            ):
                path = repository.write_executable_resource(
                    "joey_pc.exe",
                    resource,
                    metadata={"card_record_count": 1116},
                    icon_data=b"controlled icon",
                )
            written = path.read_bytes()
            self.assertGreater(
                len(written),
                self.profile["pe_header_updates"]["output_size_before_icon"],
            )
            GameRepository.verify_executable_card_capacity(
                written, card_record_count=1116, profile=self.profile
            )
            with self.assertRaisesRegex(ValueError, "size|raw pointer"):
                GameRepository._verify_extended_executable(
                    written,
                    card_record_count=1116,
                    profile=self.profile,
                    require_profile_raw_layout=True,
                )

    def test_write_post_icon_verifier_rejects_corrupted_moved_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            configure_repository_profile(repository, self.profile)
            resource = executable_resource(self.stock)

            def corrupt_icon(file_name: str, icon_data: bytes):
                del icon_data
                current = repository._connection.read_executable(file_name)
                repository._connection.write_executable(
                    file_name, move_helper_raw_data(current, corrupt=True)
                )

            with patch.object(
                repository._connection,
                "update_executable_icon",
                side_effect=corrupt_icon,
            ):
                with self.assertRaisesRegex(ValueError, "helper digest"):
                    repository.write_executable_resource(
                        "joey_pc.exe",
                        resource,
                        metadata={"card_record_count": 1116},
                        icon_data=b"controlled icon",
                    )

    def test_stock_1115_write_has_no_step8_section_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            configure_repository_profile(repository, self.profile)
            arbitrary = b"legacy executable bytes are preserved"
            path = repository.write_executable_resource(
                "joey_pc.exe",
                executable_resource(arbitrary),
                metadata={"card_record_count": 1115},
            )
            self.assertEqual(path.read_bytes(), arbitrary)

    @staticmethod
    def _bytes_at(
        value: bytes,
        pe: dict[str, object],
        va: int,
        size: int,
    ) -> bytes:
        offset = GameRepository._executable_va_to_file_offset(value, pe, va, size)
        return value[offset : offset + size]


EXACT_STOCK_PATH = exact_stock_path_from_environment()


@unittest.skipUnless(
    EXACT_STOCK_PATH is not None and EXACT_STOCK_PATH.is_file(),
    "set YGOEDITOR_JOEY_STOCK_EXE for the optional registered-stock check",
)
class ExactStockStep8IntegrationTests(unittest.TestCase):
    def test_registered_stock_patches_and_structurally_verifies(self) -> None:
        from yugioh_editor.common.subfile_rules_config import (
            EXECUTABLE_CARD_CAPACITY_PROFILE,
        )

        assert EXACT_STOCK_PATH is not None
        source = EXACT_STOCK_PATH.read_bytes()
        self.assertEqual(
            hashlib.sha256(source).hexdigest(),
            EXECUTABLE_CARD_CAPACITY_PROFILE["source"]["sha256"],
        )
        self.assertEqual(
            patch_controlled(
                source,
                EXECUTABLE_CARD_CAPACITY_PROFILE,
                1115,
            ),
            source,
        )
        for count in (1116, 2049, 4095):
            with self.subTest(count=count):
                output = patch_controlled(
                    source,
                    EXECUTABLE_CARD_CAPACITY_PROFILE,
                    count,
                )
                GameRepository.verify_executable_card_capacity(
                    output,
                    card_record_count=count,
                    profile=EXECUTABLE_CARD_CAPACITY_PROFILE,
                )

    @unittest.skipUnless(sys.platform == "win32", "native icon update requires Windows")
    def test_registered_stock_survives_a_native_icon_resource_update(self) -> None:
        from io import BytesIO

        from PIL import Image

        assert EXACT_STOCK_PATH is not None
        source = EXACT_STOCK_PATH.read_bytes()
        stream = BytesIO()
        Image.new("RGBA", (32, 32), (200, 20, 50, 255)).save(
            stream,
            format="ICO",
            sizes=[(32, 32)],
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = GameRepository.from_root(directory)
            path = repository.write_executable_resource(
                "joey_pc.exe",
                executable_resource(source),
                metadata={"card_record_count": 1116},
                icon_data=stream.getvalue(),
            )
            self.assertTrue(path.is_file())
        self.assertEqual(EXACT_STOCK_PATH.read_bytes(), source)


if __name__ == "__main__":
    unittest.main()
