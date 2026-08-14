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
    "source_sha256": (
        "c5749eb934a1cf68d9236e44ff81e98b8aaee486b4f8ebd417440505d44ac1ea"
    ),
    "known_output_sha256": {
        1116: "cd04132ea2915e186fa7c4d67f7db73fe9fdb784fc0c95c7ab5b96733d3da699",
    },
    "legacy_card_record_count": 1115,
    "minimum_patched_record_count": 1116,
    "maximum_card_record_count": 2166,
    "state_base_address": 0x00A53CCC,
    "state_limit_address": 0x00A54DB8,
    "state_record_size": 2,
    "snapshot_stack_overhead": 0x10,
    "integer_patch_sites": (
        {
            "offset": 0x2315,
            "expected": b"\x66\x81\xfe\x5b\x04",
            "value_offset": 3,
            "value_width": 2,
            "value_name": "maximum_internal_id",
            "description": "Set the CARD_ID getter maximum internal ID.",
        },
        {
            "offset": 0x3A9B2,
            "expected": b"\x81\xfe\x5b\x04\x00\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
            "description": "Bound the first card traversal by the record count.",
        },
        {
            "offset": 0x3AA9D,
            "expected": b"\x81\xfb\x5b\x04\x00\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
            "description": "Bound the second card traversal by the record count.",
        },
        {
            "offset": 0x45703,
            "expected": b"\x81\xfe\x5b\x04\x00\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
            "description": "Bound the card lookup traversal by the record count.",
        },
        {
            "offset": 0x6E5B3,
            "expected": b"\x3d\x5b\x04\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
            "description": "Bound the first card-state routine by the record count.",
        },
        {
            "offset": 0x76327,
            "expected": b"\x3d\x5b\x04\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
            "description": "Bound the second card-state routine by the record count.",
        },
        {
            "offset": 0x7DBFD,
            "expected": b"\x81\xff\x5b\x04\x00\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "exclusive_upper_bound",
            "description": "Bound snapshot iteration by the record count.",
        },
        {
            "offset": 0x6E5C7,
            "expected": b"\x3d\x5a\x04\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_internal_id",
            "description": "Clamp the first card-state index to the maximum ID.",
        },
        {
            "offset": 0x6E5CE,
            "expected": b"\xb8\x5a\x04\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_internal_id",
            "description": "Return the first clamped maximum internal ID.",
        },
        {
            "offset": 0x76339,
            "expected": b"\x3d\x5a\x04\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_internal_id",
            "description": "Clamp the second card-state index to the maximum ID.",
        },
        {
            "offset": 0x76340,
            "expected": b"\xb8\x5a\x04\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "maximum_internal_id",
            "description": "Return the second clamped maximum internal ID.",
        },
        {
            "offset": 0x63F18,
            "expected": b"\x3d\x82\x45\xa5\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "state_end_address",
            "description": "Set the first dynamic card-state end pointer.",
        },
        {
            "offset": 0x63F8B,
            "expected": b"\x81\xfe\x82\x45\xa5\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "state_end_address",
            "description": "Set the second dynamic card-state end pointer.",
        },
        {
            "offset": 0x7DA75,
            "expected": b"\x81\xfe\x82\x45\xa5\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "state_end_address",
            "description": "Set the snapshot source end pointer.",
        },
        {
            "offset": 0x7DC7D,
            "expected": b"\x81\xff\x82\x45\xa5\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "state_end_address",
            "description": "Set the snapshot destination end pointer.",
        },
        {
            "offset": 0x1BED0D,
            "expected": b"\x3d\x82\x45\xa5\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "state_end_address",
            "description": "Set the saved-state end pointer.",
        },
        {
            "offset": 0x1BEE16,
            "expected": b"\x81\xfe\x82\x45\xa5\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "state_end_address",
            "description": "Set the restored-state end pointer.",
        },
        {
            "offset": 0x7DCE0,
            "expected": b"\x81\xec\xc8\x08\x00\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "snapshot_stack_size",
            "description": "Allocate the dynamic card-state snapshot stack frame.",
        },
        {
            "offset": 0x7DDAB,
            "expected": b"\x81\xc4\xc8\x08\x00\x00",
            "value_offset": 2,
            "value_width": 4,
            "value_name": "snapshot_stack_size",
            "description": "Release the dynamic card-state snapshot stack frame.",
        },
        {
            "offset": 0x7DCEA,
            "expected": b"\xb9\x2d\x02\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "snapshot_dword_count",
            "description": "Copy the dynamic snapshot DWORD portion.",
        },
        {
            "offset": 0x7DD89,
            "expected": b"\x3d\xb6\x08\x00\x00",
            "value_offset": 1,
            "value_width": 4,
            "value_name": "state_byte_count",
            "description": "Process the dynamic card-state snapshot byte count.",
        },
    ),
    "conditional_patch_sites": (
        {
            "offset": 0x7DCFE,
            "expected": b"\x66\xa5",
            "odd_record_bytes": b"\x66\xa5",
            "even_record_bytes": b"\x90\x90",
            "description": "Copy the final WORD only when record_count is odd.",
        },
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
                "method_name": "pad_integer_sequence",
                "params": {
                    "capacity": 2048,
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
