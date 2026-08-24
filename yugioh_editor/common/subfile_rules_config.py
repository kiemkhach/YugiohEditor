from typing import Any

CARD_LIST_RECORD_PATTERN = (
    r"//\t(?P<name>[^\r\n]*)\r?\n"
    r"//\t(?P<index>\d{4}):\[(?P<card_id>\d{4})\]"
    r"(?:\s(?P<note>Back))?\r?\n"
    r"(?P<image_name>[^\r\n]*)\r?\n"
)
CARD_LIST_RECORD_TEMPLATE = (
    "//\t{name}\r\n//\t{index:04d}:[{card_id:04d}]{note_suffix}\r\n{image_name}\r\n\r\n"
)

CARD_DESCRIPTION_TEXT_LAYOUT = {
    "terminator": b"\x00",
    "alignment": 2,
    "minimum_padding": 2,
}
DIALOG_TEXT_LAYOUT = {
    "terminator": b"\x00",
    "alignment": 2,
    "minimum_padding": 1,
}
PACK_VALUE_LABELS = {
    0: "disabled",
    1: "yugi",
    2: "kaiba",
    3: "yugi_kaiba",
    4: "joey",
    5: "yugi_joey",
    6: "kaiba_joey",
    7: "yugi_kaiba_joey",
}

EXECUTABLE_CARD_CAPACITY_PROFILE: dict[str, Any] = {
    "source": {
        "sha256": ("c5749eb934a1cf68d9236e44ff81e98b8aaee486b4f8ebd417440505d44ac1ea"),
        "size": 0x3BD000,
        "pe": {
            "dos_magic": b"MZ",
            "pe_offset": 0x900,
            "signature": b"PE\x00\x00",
            "machine": 0x014C,
            "optional_header_size": 0xE0,
            "optional_header_magic": 0x010B,
            "image_base": 0x00400000,
            "section_alignment": 0x1000,
            "file_alignment": 0x1000,
            "number_of_sections": 8,
            "size_of_code": 0x29D000,
            "size_of_initialized_data": 0x11E000,
            "size_of_uninitialized_data": 0,
            "size_of_image": 0x824000,
            "size_of_headers": 0x1000,
            "section_table_offset": 0x9F8,
            "section_table_end": 0xB38,
            "zero_header_slack_size": 0x4C8,
            "sections": (
                {
                    "name": ".text",
                    "virtual_size": 0x1D9000,
                    "virtual_address": 0x1000,
                    "raw_size": 0x1D9000,
                    "raw_pointer": 0x1000,
                    "characteristics": 0x60000020,
                },
                {
                    "name": ".rdata",
                    "virtual_size": 0x102DC,
                    "virtual_address": 0x1DA000,
                    "raw_size": 0x11000,
                    "raw_pointer": 0x1DA000,
                    "characteristics": 0xC0000040,
                },
                {
                    "name": ".data",
                    "virtual_size": 0x46E950,
                    "virtual_address": 0x1EB000,
                    "raw_size": 0x8000,
                    "raw_pointer": 0x1EB000,
                    "characteristics": 0xC0000040,
                },
                {
                    "name": ".ksss",
                    "virtual_size": 0xC4000,
                    "virtual_address": 0x65A000,
                    "raw_size": 0xC4000,
                    "raw_pointer": 0x1F3000,
                    "characteristics": 0x60000020,
                },
                {
                    "name": ".ycnett",
                    "virtual_size": 0xFE04C,
                    "virtual_address": 0x71E000,
                    "raw_size": 0xFF000,
                    "raw_pointer": 0x2B7000,
                    "characteristics": 0xC0000040,
                },
                {
                    "name": ".idata",
                    "virtual_size": 0x1BDA,
                    "virtual_address": 0x81D000,
                    "raw_size": 0x2000,
                    "raw_pointer": 0x3B6000,
                    "characteristics": 0x40000040,
                },
                {
                    "name": ".rsrc",
                    "virtual_size": 0x3660,
                    "virtual_address": 0x81F000,
                    "raw_size": 0x4000,
                    "raw_pointer": 0x3B8000,
                    "characteristics": 0x40000040,
                },
                {
                    "name": ".$$$",
                    "virtual_size": 0x1000,
                    "virtual_address": 0x823000,
                    "raw_size": 0x1000,
                    "raw_pointer": 0x3BC000,
                    "characteristics": 0xE0000060,
                },
            ),
        },
    },
    "record_counts": {
        "legacy": 1115,
        "minimum_extended": 1116,
        "maximum": 0x0FFF,
    },
    "runtime_layout": {
        "state_base": 0x00C24000,
        "state_record_size": 2,
        "state_word_capacity": 0x1000,
        "state_structural_end": 0x00C26000,
        "snapshot_base": 0x00C26000,
        "snapshot_byte_capacity": 0x1000,
        "snapshot_end": 0x00C27000,
        "helper_base": 0x00C27000,
        "helper_size": 0x1000,
        "legacy_persistent_slot_count": 0x800,
        "legacy_bridge_byte_count": 0x1000,
        "maximum_active_slot": 0x0FFE,
        "invalid_slot": 0x0FFF,
        "maximum_card_id": 0x0FFE,
        "invalid_card_id": 0x0FFF,
    },
    "pe_sections": (
        {
            "name": ".ygst",
            "virtual_size": 0x3000,
            "virtual_address": 0x824000,
            "raw_size": 0,
            "raw_pointer": 0,
            "characteristics": 0xC0000080,
        },
        {
            "name": ".ygsx",
            "virtual_size": 0x1000,
            "virtual_address": 0x827000,
            "raw_size": 0x1000,
            "raw_pointer": 0x3BD000,
            "characteristics": 0x60000020,
            "fill_byte": 0x90,
        },
    ),
    "pe_header_updates": {
        "number_of_sections": 10,
        "size_of_code": 0x29E000,
        "size_of_uninitialized_data": 0x3000,
        "size_of_image": 0x828000,
        "output_size_before_icon": 0x3BE000,
    },
    "state_relocation_groups": (
        {
            "value_name": "state_base",
            "source_value": 0x00A53CCC,
            "replacement": 0x00C24000,
            "value_width": 4,
            "sites": (
                {
                    "va": 0x0044573A,
                    "expected": bytes.fromhex("8A 1C 45 CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x00469F43,
                    "expected": bytes.fromhex("66 85 1C 4D CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x0047DF26,
                    "expected": bytes.fromhex("8A 0C 45 CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x0047E86F,
                    "expected": bytes.fromhex("8A 04 7D CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x0047F70B,
                    "expected": bytes.fromhex("8A 04 7D CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x0047F720,
                    "expected": bytes.fromhex("66 8B 04 7D CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x0047F730,
                    "expected": bytes.fromhex("66 89 04 7D CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BDCCB,
                    "expected": bytes.fromhex("66 09 2C 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BDD31,
                    "expected": bytes.fromhex("66 09 2C 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE202,
                    "expected": bytes.fromhex("66 8B 04 4D CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE285,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE2AE,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE2E1,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE30A,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE333,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE35C,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE39E,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE3CD,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE3F6,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE431,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE45A,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE483,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE4DF,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE502,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE525,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE544,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE563,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE58E,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE5C1,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE5E0,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE5FF,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE62B,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE654,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE694,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE6B1,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE6DA,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE6FB,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE73A,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE757,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE786,
                    "expected": bytes.fromhex("66 8B 0C 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE7A3,
                    "expected": bytes.fromhex("66 8B 14 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE844,
                    "expected": bytes.fromhex("66 8B 04 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE84C,
                    "expected": bytes.fromhex("8D 0C 75 CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x005BE894,
                    "expected": bytes.fromhex("66 8B 04 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE89C,
                    "expected": bytes.fromhex("8D 0C 75 CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x005BE909,
                    "expected": bytes.fromhex("8D 0C 45 CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x005BE910,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE97D,
                    "expected": bytes.fromhex("8D 0C 45 CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x005BE984,
                    "expected": bytes.fromhex("66 8B 04 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE9D5,
                    "expected": bytes.fromhex("66 8B 34 45 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BE9DD,
                    "expected": bytes.fromhex("8D 1C 45 CC 3C A5 00"),
                    "value_offset": 3,
                },
                {
                    "va": 0x005BEA8B,
                    "expected": bytes.fromhex("B8 CC 3C A5 00"),
                    "value_offset": 1,
                },
                {
                    "va": 0x005BEADD,
                    "expected": bytes.fromhex("66 8B 04 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BEB14,
                    "expected": bytes.fromhex("66 89 0C 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BEB9F,
                    "expected": bytes.fromhex("66 8B 04 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BEBD7,
                    "expected": bytes.fromhex("66 89 14 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BEC67,
                    "expected": bytes.fromhex("66 8B 04 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BEC9B,
                    "expected": bytes.fromhex("66 89 0C 75 CC 3C A5 00"),
                    "value_offset": 4,
                },
                {
                    "va": 0x005BED00,
                    "expected": bytes.fromhex("B8 CC 3C A5 00"),
                    "value_offset": 1,
                },
            ),
        },
        {
            "value_name": "state_high_byte_base",
            "source_value": 0x00A53CCD,
            "replacement": 0x00C24001,
            "value_width": 4,
            "sites": (
                {
                    "va": 0x004693D1,
                    "expected": bytes.fromhex("80 0C 45 CD 3C A5 00 40"),
                    "value_offset": 3,
                },
                {
                    "va": 0x004698D3,
                    "expected": bytes.fromhex("80 0C 45 CD 3C A5 00 40"),
                    "value_offset": 3,
                },
                {
                    "va": 0x0047DB69,
                    "expected": bytes.fromhex("80 0D CD 3C A5 00 40"),
                    "value_offset": 2,
                },
                {
                    "va": 0x0047EB07,
                    "expected": bytes.fromhex("F6 04 4D CD 3C A5 00 40"),
                    "value_offset": 3,
                },
            ),
        },
        {
            "value_name": "state_slot1_base",
            "source_value": 0x00A53CCE,
            "replacement": 0x00C24002,
            "value_width": 4,
            "sites": (
                {
                    "va": 0x00463F0A,
                    "expected": bytes.fromhex("B8 CE 3C A5 00"),
                    "value_offset": 1,
                },
                {
                    "va": 0x00463F2E,
                    "expected": bytes.fromhex("BE CE 3C A5 00"),
                    "value_offset": 1,
                },
                {
                    "va": 0x0047DA40,
                    "expected": bytes.fromhex("BE CE 3C A5 00"),
                    "value_offset": 1,
                },
                {
                    "va": 0x0047DC3C,
                    "expected": bytes.fromhex("BF CE 3C A5 00"),
                    "value_offset": 1,
                },
                {
                    "va": 0x005BEDD8,
                    "expected": bytes.fromhex("BE CE 3C A5 00"),
                    "value_offset": 1,
                },
            ),
        },
        {
            "value_name": "state_structural_end",
            "source_value": 0x00A54CCC,
            "replacement": 0x00C26000,
            "value_width": 4,
            "sites": (
                {
                    "va": 0x005BEA98,
                    "expected": bytes.fromhex("3D CC 4C A5 00"),
                    "value_offset": 1,
                },
            ),
        },
    ),
    "snapshot_patches": (
        {
            "name": "snapshot_copy",
            "va": 0x0047DCEA,
            "expected": bytes.fromhex(
                "B9 2D 02 00 00 BE CC 3C A5 00 8D 7C 24 20 F3 A5 8D 4C 24 14 66 A5"
            ),
            "replacement": bytes.fromhex(
                "31 C9 B5 10 BE 01 40 C2 00 BF 00 60 C2 00 A4 46 E2 FC 8D 4C 24 14"
            ),
            "successor_va": 0x0047DD00,
        },
        {
            "name": "snapshot_restore",
            "va": 0x0047DD71,
            "expected": bytes.fromhex(
                "33 C0 B9 00 40 00 00 66 85 4C 04 20 74 07 66 09 "
                "88 CC 3C A5 00 83 C0 02 3D B6 08 00 00 7C E8"
            ),
            "replacement": bytes.fromhex(
                "33 C0 F6 80 00 60 C2 00 40 74 08 80 0C 45 01 40 "
                "C2 00 40 40 80 FC 10 72 E9 B9 00 40 00 00 90"
            ),
            "successor_va": 0x0047DD90,
        },
    ),
    "fixed_patch_sites": (
        {
            "name": "card_prop_slot_mask",
            "va": 0x0040262E,
            "expected": bytes.fromhex("81 E1 FF 07 00 00"),
            "replacement": bytes.fromhex("81 E1 FF 0F 00 00"),
        },
    ),
    "hooks": (
        {
            "name": "legacy_save_bridge",
            "va": 0x00483150,
            "expected": bytes.fromhex("6A FF 68 48 62 5D 00"),
            "replacement": bytes.fromhex("E9 AB 3E 7A 00 90 90"),
            "helper_va": 0x00C27000,
            "return_va": 0x00483157,
        },
        {
            "name": "legacy_load_bridge",
            "va": 0x004833CF,
            "expected": bytes.fromhex("85 C0 89 5C 24 18"),
            "replacement": bytes.fromhex("E9 5C 3C 7A 00 90"),
            "helper_va": 0x00C27030,
            "return_va": 0x004833D5,
        },
        {
            "name": "direct_card_id_lookup",
            "va": 0x00402490,
            "expected": bytes.fromhex("56 8B 74 24 08"),
            "replacement": bytes.fromhex("E9 6B 4C 82 00"),
            "helper_va": 0x00C27100,
        },
    ),
    "helper_fragments": (
        {
            "name": "legacy_save_bridge",
            "offset": 0x000,
            "va": 0x00C27000,
            "bytes": bytes.fromhex(
                "9C 60 FC BE 00 40 C2 00 BF CC 3C A5 00 B9 00 04 "
                "00 00 F3 A5 E8 E7 70 99 FF 61 9D 6A FF 68 48 62 "
                "5D 00 E9 30 C1 85 FF"
            ),
        },
        {
            "name": "legacy_load_bridge",
            "offset": 0x030,
            "va": 0x00C27030,
            "bytes": bytes.fromhex(
                "9C 60 FC BE CC 3C A5 00 BF 00 40 C2 00 B9 00 04 "
                "00 00 F3 A5 61 9D 85 C0 89 5C 24 18 E9 84 C3 85 FF"
            ),
        },
        {
            "name": "direct_card_id_lookup",
            "offset": 0x100,
            "va": 0x00C27100,
            "bytes": bytes.fromhex(
                "56 8B 74 24 08 81 E6 FF FF 00 00 81 FE FF 0F 00 "
                "00 73 10 B9 10 24 5F 00 E8 03 B1 91 FF 0F B7 04 "
                "70 5E C3 33 C0 5E C3 90 90"
            ),
        },
        {
            "name": "canonicalize_legacy_alias",
            "offset": 0x129,
            "va": 0x00C27129,
            "bytes": bytes.fromhex(
                "25 FF FF 00 00 51 57 B9 09 00 00 00 BF 78 71 C2 "
                "00 66 3B 07 74 08 83 C7 02 E2 F6 5F 59 C3 2D D0 "
                "07 00 00 5F 59 C3 90"
            ),
        },
        {
            "name": "canonicalize_esi_wrapper",
            "offset": 0x150,
            "va": 0x00C27150,
            "bytes": bytes.fromhex("50 8B C6 E8 D1 FF FF FF 8B F0 58 C3 90"),
        },
        {
            "name": "canonicalize_edi_wrapper",
            "offset": 0x15D,
            "va": 0x00C2715D,
            "bytes": bytes.fromhex("50 8B C7 E8 C4 FF FF FF 8B F8 58 C3 90"),
        },
        {
            "name": "canonicalize_ecx_wrapper",
            "offset": 0x16A,
            "va": 0x00C2716A,
            "bytes": bytes.fromhex("50 8B C1 E8 B7 FF FF FF 8B C8 58 C3 90 90"),
        },
        {
            "name": "legacy_alias_table",
            "offset": 0x178,
            "va": 0x00C27178,
            "bytes": bytes.fromhex(
                "D0 07 DE 07 F2 07 F5 07 F8 07 0F 08 14 08 53 09 55 09"
            ),
        },
    ),
    "helper_section_sha256": (
        "c7beb9a90d18c91f4b05d3713386bfbf5e47a7abc7580c670655872a9f33e02b"
    ),
    "alias_consumer_patches": (
        {
            "name": "comparison_esi",
            "va": 0x005674FA,
            "expected": bytes.fromhex("66 81 FE D0 07 72 06 81 C6 30 F8 00 00"),
            "replacement": bytes.fromhex("E8 51 FC 6B 00 90 90 90 90 90 90 90 90"),
            "call_targets": (0x00C27150,),
        },
        {
            "name": "comparison_eax",
            "va": 0x00567507,
            "expected": bytes.fromhex("66 3D D0 07 72 05 05 30 F8 00 00"),
            "replacement": bytes.fromhex("E8 1D FC 6B 00 90 90 90 90 90 90"),
            "call_targets": (0x00C27129,),
        },
        {
            "name": "deck_recipe_eax",
            "va": 0x005918CD,
            "expected": bytes.fromhex("66 3D D0 07 72 05 05 30 F8 00 00"),
            "replacement": bytes.fromhex("E8 57 58 69 00 90 90 90 90 90 90"),
            "call_targets": (0x00C27129,),
        },
        {
            "name": "deck_recipe_scan_eax",
            "va": 0x005919BD,
            "expected": bytes.fromhex("66 3D D0 07 72 05 05 30 F8 00 00"),
            "replacement": bytes.fromhex("E8 67 57 69 00 90 90 90 90 90 90"),
            "call_targets": (0x00C27129,),
        },
        {
            "name": "relation_block_a",
            "va": 0x00591A8A,
            "expected": bytes.fromhex(
                "66 3B 44 24 18 74 22 56 E8 49 08 E7 FF 8B 4C 24 "
                "1C 25 FF FF 00 00 81 E1 FF FF 00 00 83 C4 04 81 "
                "C1 D0 07 00 00 3B C1 75 18"
            ),
            "replacement": bytes.fromhex(
                "E8 9A 56 69 00 8B 4C 24 18 E8 D2 56 69 00 3B C1 "
                "75 2F 90 90 90 90 90 90 90 90 90 90 90 90 90 "
                "90 90 90 90 90 90 90 90 90 90"
            ),
            "call_targets": (0x00C27129, 0x00C2716A),
            "equal_target_va": 0x00591AB3,
            "unequal_target_va": 0x00591ACB,
        },
        {
            "name": "relation_block_b",
            "va": 0x00591B37,
            "expected": bytes.fromhex(
                "66 3B 44 24 18 74 22 56 E8 9C 07 E7 FF 8B 4C 24 "
                "1C 25 FF FF 00 00 81 E1 FF FF 00 00 83 C4 04 81 "
                "C1 D0 07 00 00 3B C1 75 18"
            ),
            "replacement": bytes.fromhex(
                "E8 ED 55 69 00 8B 4C 24 18 E8 25 56 69 00 3B C1 "
                "75 2F 90 90 90 90 90 90 90 90 90 90 90 90 90 "
                "90 90 90 90 90 90 90 90 90 90"
            ),
            "call_targets": (0x00C27129, 0x00C2716A),
            "equal_target_va": 0x00591B60,
            "unequal_target_va": 0x00591B78,
        },
        {
            "name": "relation_edi",
            "va": 0x00591D48,
            "expected": bytes.fromhex("66 81 FF D0 07 72 06 81 C7 30 F8 00 00"),
            "replacement": bytes.fromhex("E8 10 54 69 00 90 90 90 90 90 90 90 90"),
            "call_targets": (0x00C2715D,),
        },
        {
            "name": "packed_relation_eax_first",
            "va": 0x00592083,
            "expected": bytes.fromhex("3D D0 07 00 00 7C 05 2D D0 07 00 00"),
            "replacement": bytes.fromhex("E8 A1 50 69 00 90 90 90 90 90 90 90"),
            "call_targets": (0x00C27129,),
        },
        {
            "name": "packed_relation_esi_first",
            "va": 0x0059208F,
            "expected": bytes.fromhex("81 FE D0 07 00 00 7C 06 81 EE D0 07 00 00"),
            "replacement": bytes.fromhex("E8 BC 50 69 00 90 90 90 90 90 90 90 90 90"),
            "call_targets": (0x00C27150,),
        },
        {
            "name": "packed_relation_eax_second",
            "va": 0x00592113,
            "expected": bytes.fromhex("3D D0 07 00 00 7C 05 2D D0 07 00 00"),
            "replacement": bytes.fromhex("E8 11 50 69 00 90 90 90 90 90 90 90"),
            "call_targets": (0x00C27129,),
        },
        {
            "name": "packed_relation_esi_second",
            "va": 0x0059211F,
            "expected": bytes.fromhex("81 FE D0 07 00 00 7C 06 81 EE D0 07 00 00"),
            "replacement": bytes.fromhex("E8 2C 50 69 00 90 90 90 90 90 90 90 90 90"),
            "call_targets": (0x00C27150,),
        },
    ),
    "dynamic_patch_sites": (
        {
            "va": 0x00402315,
            "expected": bytes.fromhex("66 81 FE 5B 04"),
            "value_offset": 3,
            "value_width": 2,
            "value_name": "maximum_active_slot",
        },
        {
            "va": 0x0046E5C7,
            "expected": bytes.fromhex("3D 5A 04 00 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_active_slot",
        },
        {
            "va": 0x0046E5CE,
            "expected": bytes.fromhex("B8 5A 04 00 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_active_slot",
        },
        {
            "va": 0x00476339,
            "expected": bytes.fromhex("3D 5A 04 00 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_active_slot",
        },
        {
            "va": 0x00476340,
            "expected": bytes.fromhex("B8 5A 04 00 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_active_slot",
        },
        {
            "va": 0x0043A9B2,
            "expected": bytes.fromhex("81 FE 5B 04 00 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
        },
        {
            "va": 0x0043AA9D,
            "expected": bytes.fromhex("81 FB 5B 04 00 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
        },
        {
            "va": 0x00445703,
            "expected": bytes.fromhex("81 FE 5B 04 00 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
        },
        {
            "va": 0x0047DBFD,
            "expected": bytes.fromhex("81 FF 5B 04 00 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
        },
        {
            "va": 0x0046E5B3,
            "expected": bytes.fromhex("3D 5B 04 00 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
        },
        {
            "va": 0x00476327,
            "expected": bytes.fromhex("3D 5B 04 00 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
        },
        {
            "va": 0x00463F18,
            "expected": bytes.fromhex("3D 82 45 A5 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "active_state_end_address",
        },
        {
            "va": 0x00463F8B,
            "expected": bytes.fromhex("81 FE 82 45 A5 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "active_state_end_address",
        },
        {
            "va": 0x0047DA75,
            "expected": bytes.fromhex("81 FE 82 45 A5 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "active_state_end_address",
        },
        {
            "va": 0x0047DC7D,
            "expected": bytes.fromhex("81 FF 82 45 A5 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "active_state_end_address",
        },
        {
            "va": 0x005BED0D,
            "expected": bytes.fromhex("3D 82 45 A5 00"),
            "value_offset": 1,
            "value_width": 4,
            "value_name": "active_state_end_address",
        },
        {
            "va": 0x005BEE16,
            "expected": bytes.fromhex("81 FE 82 45 A5 00"),
            "value_offset": 2,
            "value_width": 4,
            "value_name": "active_state_end_address",
        },
    ),
    "legacy_aliases": {
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
    "invariant_sites": (
        {
            "name": "packed_card_id_mask_primary",
            "va": 0x005B91D7,
            "expected": bytes.fromhex("25 FF 0F 00 00"),
        },
        {
            "name": "packed_card_id_mask_secondary",
            "va": 0x005B9214,
            "expected": bytes.fromhex("25 FF 0F 00 00"),
        },
    ),
    "known_false_matches": (
        {"va": 0x00AF17E1, "expected": bytes.fromhex("81 04 24 8D 42 A5 00")},
        {"va": 0x00AF317E, "expected": bytes.fromhex("C7 04 24 87 48 A5 00")},
        {"va": 0x00AFF1E7, "expected": bytes.fromhex("81 04 24 0F 4A A5 00")},
        {"va": 0x00B037CC, "expected": bytes.fromhex("C7 04 24 17 3D A5 00")},
    ),
}

SUBFILE_RULE_CONFIGS: tuple[dict[str, Any], ...] = (
    {
        "pattern": "*.dat",
        "codec_name": "container",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "region.dat",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "*_pc.exe",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
        "pre_encode": (
            {
                "method_name": "patch_executable_card_capacity",
                "params": {"profile": EXECUTABLE_CARD_CAPACITY_PROFILE},
            },
        ),
    },
    {
        "pattern": "*.bmp",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "*.png",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "*.jpg",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "*.jpeg",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "*.gif",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "*.wav",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "*.txt",
        "codec_name": "text",
        "decode_params": {
            "encoding": "cp932",
        },
        "encode_params": {
            "encoding": "cp932",
        },
        "virtual": False,
    },
    {
        "pattern": "*.text",
        "codec_name": "text",
        "decode_params": {
            "encoding": "cp932",
        },
        "encode_params": {
            "encoding": "cp932",
        },
        "virtual": False,
    },
    {
        "pattern": "*.bin",
        "codec_name": "binary",
        "decode_params": {},
        "encode_params": {},
        "virtual": False,
    },
    {
        "pattern": "card_id.bin",
        "table_name": "card_ids",
        "codec_name": "integer_list",
        "decode_params": {
            "byte_width": 2,
            "signed": True,
            "byte_order": "little",
        },
        "encode_params": {
            "byte_width": 2,
            "signed": True,
            "byte_order": "little",
        },
        "virtual": False,
        "post_decode": (
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "dataframe_column_to_list",
                "params": {
                    "column": "value",
                    "fill_value": 0,
                    "cast": "int",
                },
            },
        ),
    },
    {
        "pattern": "card_intid.bin",
        "codec_name": "integer_list",
        "decode_params": {
            "byte_width": 2,
            "signed": False,
            "byte_order": "little",
        },
        "encode_params": {
            "byte_width": 2,
            "signed": False,
            "byte_order": "little",
        },
        "virtual": True,
        "post_decode": (
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "load_dependency_table",
                "params": {
                    "table": "card_id.bin",
                },
            },
            {
                "method_name": "dataframe_column_to_list",
                "params": {
                    "column": "value",
                    "fill_value": 0,
                    "cast": "int",
                },
            },
            {
                "method_name": "generate_reverse_lookup",
                "params": {},
            },
        ),
    },
    {
        "pattern": "card_pack.bin",
        "table_name": "card_packs",
        "codec_name": "integer_list",
        "decode_params": {
            "byte_width": 2,
            "signed": False,
            "byte_order": "little",
        },
        "encode_params": {
            "byte_width": 2,
            "signed": False,
            "byte_order": "little",
        },
        "virtual": False,
        "post_decode": (
            {
                "method_name": "apply_value_map",
                "params": {
                    "mapping": PACK_VALUE_LABELS,
                    "unknown_template": "unknown_{value}",
                },
            },
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "dataframe_column_to_list",
                "params": {
                    "column": "value",
                    "fill_value": "",
                    "cast": "str",
                },
            },
            {
                "method_name": "apply_reverse_value_map",
                "params": {
                    "mapping": PACK_VALUE_LABELS,
                },
            },
        ),
    },
    {
        "pattern": "card_pass.bin",
        "table_name": "card_passcodes",
        "codec_name": "fixed_hex_list",
        "decode_params": {
            "byte_width": 4,
        },
        "encode_params": {
            "byte_width": 4,
        },
        "virtual": False,
        "post_decode": (
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "dataframe_column_to_list",
                "params": {
                    "column": "value",
                },
            },
        ),
    },
    {
        "pattern": "card_prop.bin",
        "table_name": "card_properties",
        "codec_name": "record_table",
        "decode_params": {
            "record_size": 4,
            "row_codec": "nibble_statistics",
        },
        "encode_params": {
            "record_size": 4,
            "row_codec": "nibble_statistics",
        },
        "virtual": False,
        "post_decode": (
            {
                "method_name": "records_to_dataframe",
                "params": {},
            },
        ),
        "pre_encode": (
            {
                "method_name": "log_dataframe_summary",
                "params": {
                    "required_columns": (
                        "attack",
                        "defense",
                        "monster_type_code",
                        "monster_type",
                        "card_category_code",
                        "card_category",
                        "attribute_code",
                        "attribute",
                        "level",
                        "requires_two_tributes",
                    ),
                    "distribution_columns": (
                        "card_category",
                        "monster_type",
                    ),
                },
            },
            {
                "method_name": "dataframe_to_records",
                "params": {},
            },
        ),
    },
    {
        "pattern": "card_indx[lang].bin",
        "codec_name": "integer_list",
        "decode_params": {
            "byte_width": 4,
            "signed": False,
            "byte_order": "little",
        },
        "encode_params": {
            "byte_width": 4,
            "signed": False,
            "byte_order": "little",
        },
        "virtual": True,
        "post_decode": (
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "load_dependency_table",
                "params": {
                    "table": "card_desc[lang].bin",
                },
            },
            {
                "method_name": "dataframe_to_indexed_text_records",
                "params": {},
            },
            {
                "method_name": "generate_string_offsets",
                "params": {
                    **CARD_DESCRIPTION_TEXT_LAYOUT,
                    "encoding": "language",
                },
            },
            {
                "method_name": "pad_integer_sequence_to_power_of_two",
                "params": {
                    "minimum_capacity": 2048,
                    "pad_value": 0,
                },
            },
        ),
    },
    {
        "pattern": "card_desc[lang].bin",
        "table_name": "card_descriptions",
        "table_parameters": ("language",),
        "editor_columns": ("text",),
        "codec_name": "offset_string_table",
        "decode_params": {
            **CARD_DESCRIPTION_TEXT_LAYOUT,
            "encoding": "language",
            "input_padding_policy": "pointer_bounded",
        },
        "encode_params": {
            **CARD_DESCRIPTION_TEXT_LAYOUT,
            "encoding": "language",
        },
        "virtual": False,
        "pre_decode": (
            {
                "method_name": "inject_offset_dependency",
                "params": {
                    "table": "card_indx[lang].bin",
                    "param_name": "offsets",
                },
            },
            {
                "method_name": "limit_parameter_by_dependency",
                "params": {
                    "param_name": "offsets",
                    "table": "card_id.bin",
                },
            },
        ),
        "post_decode": (
            {
                "method_name": "records_to_dataframe",
                "params": {"columns": ("text", "is_reserved")},
            },
        ),
        "pre_encode": (
            {
                "method_name": "dataframe_to_indexed_text_records",
                "params": {},
            },
        ),
    },
    {
        "pattern": "dlg_indx[lang].bin",
        "codec_name": "integer_list",
        "decode_params": {
            "byte_width": 4,
            "signed": False,
            "byte_order": "little",
        },
        "encode_params": {
            "byte_width": 4,
            "signed": False,
            "byte_order": "little",
        },
        "virtual": True,
        "post_decode": (
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "load_dependency_table",
                "params": {
                    "table": "dlg_text[lang].bin",
                },
            },
            {
                "method_name": "dataframe_to_indexed_text_records",
                "params": {},
            },
            {
                "method_name": "generate_string_offsets",
                "params": {
                    **DIALOG_TEXT_LAYOUT,
                    "encoding": "language",
                },
            },
        ),
    },
    {
        "pattern": "dlg_text[lang].bin",
        "table_name": "dialog_texts",
        "table_parameters": ("language",),
        "editor_columns": ("text",),
        "codec_name": "offset_string_table",
        "decode_params": {
            **DIALOG_TEXT_LAYOUT,
            "encoding": "language",
            "input_padding_policy": "pointer_bounded",
        },
        "encode_params": {
            **DIALOG_TEXT_LAYOUT,
            "encoding": "language",
        },
        "virtual": False,
        "pre_decode": (
            {
                "method_name": "inject_offset_dependency",
                "params": {
                    "table": "dlg_indx[lang].bin",
                    "param_name": "offsets",
                },
            },
        ),
        "post_decode": (
            {
                "method_name": "records_to_dataframe",
                "params": {"columns": ("text", "is_reserved")},
            },
        ),
        "pre_encode": (
            {
                "method_name": "dataframe_to_indexed_text_records",
                "params": {},
            },
        ),
    },
    {
        "pattern": "card_sort[lang].bin",
        "codec_name": "integer_list",
        "decode_params": {
            "byte_width": 2,
            "signed": False,
            "byte_order": "little",
        },
        "encode_params": {
            "byte_width": 2,
            "signed": False,
            "byte_order": "little",
        },
        "virtual": True,
        "post_decode": (
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "load_card_sort_records",
                "params": {
                    "name_table": "card_name[lang].bin",
                    "id_table": "card_id.bin",
                },
            },
            {
                "method_name": "generate_sort_indices",
                "params": {},
            },
        ),
    },
    {
        "pattern": "card_name[lang].bin",
        "table_name": "card_names",
        "table_parameters": ("language",),
        "codec_name": "fixed_string_list",
        "decode_params": {
            "record_size": 64,
            "encoding": "language",
        },
        "encode_params": {
            "record_size": 64,
            "encoding": "language",
        },
        "virtual": False,
        "post_decode": (
            {
                "method_name": "sequence_to_dataframe",
                "params": {"column": "value"},
            },
        ),
        "pre_encode": (
            {
                "method_name": "dataframe_column_to_list",
                "params": {
                    "column": "value",
                    "fill_value": "",
                    "cast": "str",
                },
            },
        ),
    },
    {
        "pattern": "list_card.txt",
        "codec_name": "regex_record_table",
        "decode_params": {
            "encoding": "utf-8-sig",
            "pattern": CARD_LIST_RECORD_PATTERN,
        },
        "encode_params": {
            "encoding": "utf-8",
            "template": CARD_LIST_RECORD_TEMPLATE,
        },
        "virtual": False,
        "pre_decode": (
            {
                "method_name": "compile_regex_parameter",
                "params": {
                    "param_name": "pattern",
                    "flags": ("MULTILINE",),
                },
            },
        ),
        "post_decode": (
            {
                "method_name": "records_to_dataframe",
                "params": {
                    "columns": ("name", "index", "card_id", "image_name", "note"),
                },
            },
            {
                "method_name": "cast_dataframe_columns",
                "params": {
                    "columns": ("index", "card_id"),
                    "type": "int",
                },
            },
        ),
        "pre_encode": (
            {
                "method_name": "cast_dataframe_columns",
                "params": {
                    "columns": ("index", "card_id"),
                    "type": "int",
                },
            },
            {
                "method_name": "dataframe_to_records",
                "params": {},
            },
            {
                "method_name": "add_derived_fields",
                "params": {
                    "fields": {
                        "note_suffix": {
                            "source": "note",
                            "prefix": " ",
                            "omit_if_empty": True,
                        },
                    },
                },
            },
        ),
    },
)
