from __future__ import annotations

import hashlib
import struct
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from yugioh_editor.common.subfile_rules_config import (
    EXECUTABLE_CARD_CAPACITY_PROFILE,
)
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.game.subfile_rule_factory import SubfileRuleFactory


def controlled_profile() -> dict[str, Any]:
    return deepcopy(EXECUTABLE_CARD_CAPACITY_PROFILE)


def va_to_offset(profile: dict[str, Any], va: int, size: int) -> int:
    source = profile["source"]
    pe = source["pe"]
    rva = va - pe["image_base"]
    for section in pe["sections"]:
        delta = rva - section["virtual_address"]
        if 0 <= delta and delta + size <= section["raw_size"]:
            return section["raw_pointer"] + delta
    raise AssertionError(f"Fixture VA 0x{va:08X} is not raw-backed.")


def refresh_source_hash(profile: dict[str, Any], source: bytes | bytearray) -> None:
    profile["source"]["sha256"] = hashlib.sha256(source).hexdigest()


def controlled_stock() -> tuple[bytes, dict[str, Any]]:
    profile = controlled_profile()
    source_profile = profile["source"]
    pe = source_profile["pe"]
    source = bytearray(source_profile["size"])
    source[:2] = pe["dos_magic"]
    struct.pack_into("<I", source, 0x3C, pe["pe_offset"])
    pe_offset = pe["pe_offset"]
    source[pe_offset : pe_offset + 4] = pe["signature"]
    struct.pack_into(
        "<HHIIIHH",
        source,
        pe_offset + 4,
        pe["machine"],
        pe["number_of_sections"],
        0,
        0,
        0,
        pe["optional_header_size"],
        0x010F,
    )
    optional_offset = pe_offset + 24
    struct.pack_into("<H", source, optional_offset, pe["optional_header_magic"])
    struct.pack_into("<I", source, optional_offset + 4, pe["size_of_code"])
    struct.pack_into("<I", source, optional_offset + 8, pe["size_of_initialized_data"])
    struct.pack_into(
        "<I", source, optional_offset + 12, pe["size_of_uninitialized_data"]
    )
    struct.pack_into("<I", source, optional_offset + 28, pe["image_base"])
    struct.pack_into("<I", source, optional_offset + 32, pe["section_alignment"])
    struct.pack_into("<I", source, optional_offset + 36, pe["file_alignment"])
    struct.pack_into("<I", source, optional_offset + 56, pe["size_of_image"])
    struct.pack_into("<I", source, optional_offset + 60, pe["size_of_headers"])
    for index, section in enumerate(pe["sections"]):
        header_offset = pe["section_table_offset"] + index * 40
        source[header_offset : header_offset + 8] = (
            section["name"].encode("ascii").ljust(8, b"\x00")
        )
        struct.pack_into(
            "<IIIIIIHHI",
            source,
            header_offset + 8,
            section["virtual_size"],
            section["virtual_address"],
            section["raw_size"],
            section["raw_pointer"],
            0,
            0,
            0,
            0,
            section["characteristics"],
        )
    for va, expected, _ in GameRepository._stock_executable_expected_sites(profile):
        offset = va_to_offset(profile, va, len(expected))
        source[offset : offset + len(expected)] = expected
    result = bytes(source)
    refresh_source_hash(profile, result)
    GameRepository._validate_executable_card_capacity_profile(profile)
    GameRepository._validate_stock_executable(result, profile)
    return result, profile


def patch_controlled(
    source: bytes | bytearray,
    profile: dict[str, Any],
    count: int,
    *,
    capacity_plan: dict[str, int] | None = None,
) -> bytes:
    metadata: dict[str, object] = {"card_record_count": count}
    if capacity_plan is not None:
        metadata["card_capacity_plan"] = capacity_plan
    return GameRepository.patch_executable_card_capacity(
        source,
        context=SimpleNamespace(metadata=metadata),
        profile=profile,
    )


def configure_repository_profile(
    repository: GameRepository,
    profile: dict[str, Any],
    *,
    extra_pre_encode: tuple[dict[str, Any], ...] = (),
) -> None:
    configs = (
        {
            "pattern": "*_pc.exe",
            "codec_name": "binary",
            "decode_params": {},
            "encode_params": {},
            "virtual": False,
            "pre_encode": (
                {
                    "method_name": "patch_executable_card_capacity",
                    "params": {"profile": profile},
                },
                *extra_pre_encode,
            ),
        },
    )
    repository._subfile_rules = SubfileRuleFactory().build_rules(configs)
    repository._validate_rule_pipeline_methods(repository._subfile_rules)


def move_helper_raw_data(
    value: bytes,
    *,
    corrupt: bool = False,
) -> bytes:
    pe = GameRepository._parse_executable_pe(value)
    sections = pe["sections"]
    helper = sections[-1]
    old_pointer = helper["raw_pointer"]
    raw_size = helper["raw_size"]
    file_alignment = pe["file_alignment"]
    new_pointer = ((len(value) + file_alignment - 1) // file_alignment) * file_alignment
    output = bytearray(value)
    output.extend(bytes(new_pointer + raw_size - len(output)))
    output[new_pointer : new_pointer + raw_size] = value[
        old_pointer : old_pointer + raw_size
    ]
    if corrupt:
        output[new_pointer] ^= 0x01
    struct.pack_into("<I", output, helper["header_offset"] + 20, new_pointer)
    return bytes(output)


def executable_resource(value: bytes, name: str = "joey_pc.exe"):
    from yugioh_editor.models.entities import ProjectFileRecord, ProjectResource

    return ProjectResource(
        ProjectFileRecord(
            source_file=name,
            relative_path=name,
            workspace_path=name,
            file_kind="exe",
            storage_format="binary",
        ),
        value,
    )


def exact_stock_path_from_environment() -> Path | None:
    import os

    configured = os.environ.get("YGOEDITOR_JOEY_STOCK_EXE")
    return Path(configured) if configured else None
