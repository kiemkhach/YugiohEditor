from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from yugioh_editor.common.constants import CODEC_OPERATIONS, LANGUAGE_PREFIXES
from yugioh_editor.repositories.game.subfile_rule import (
    RuleMethodCall,
    SubfileRule,
    deep_freeze,
)

VALID_CODEC_NAMES = CODEC_OPERATIONS
ALLOWED_RULE_METHODS = frozenset(
    {
        "add_derived_fields",
        "append_bytes",
        "apply_reverse_value_map",
        "apply_value_map",
        "cast_dataframe_columns",
        "compile_regex_parameter",
        "dataframe_column_to_list",
        "dataframe_to_indexed_text_records",
        "dataframe_to_records",
        "inject_offset_dependency",
        "limit_parameter_by_dependency",
        "load_card_sort_records",
        "load_dependency_table",
        "log_dataframe_summary",
        "patch_executable_card_capacity",
        "generate_reverse_lookup",
        "generate_sort_indices",
        "generate_string_offsets",
        "pad_integer_sequence",
        "pad_integer_sequence_to_dependency_length",
        "records_to_dataframe",
        "sequence_to_dataframe",
        "slice_bytes",
        "validate_sequence_capacity",
    }
)
PIPELINE_FIELDS = (
    "pre_decode",
    "post_decode",
    "pre_encode",
    "post_encode",
)
_ALLOWED_FIELDS = frozenset(
    {
        "pattern",
        "codec_name",
        "decode_params",
        "encode_params",
        "virtual",
        "table_name",
        "table_parameters",
        "editor_columns",
        *PIPELINE_FIELDS,
    }
)
_ALLOWED_PIPELINE_STEP_FIELDS = frozenset({"method_name", "params"})
_PATTERN_TOKENS = re.compile(r"(\[lang\]|\*)")
_INDEXED_TEXT_LAYOUT_FIELDS = (
    "encoding",
    "terminator",
    "alignment",
    "minimum_padding",
)


class SubfileRuleFactory:
    def build_rules(
        self,
        configs: Sequence[Mapping[str, Any]],
    ) -> tuple[SubfileRule, ...]:
        rules = tuple(
            self.build_rule(config, index=index) for index, config in enumerate(configs)
        )
        self._validate_table_registrations(rules)
        self._validate_indexed_text_layouts(rules)
        return rules

    def build_rule(
        self,
        config: Mapping[str, Any],
        *,
        index: int,
    ) -> SubfileRule:
        self._validate_config(config, index=index)
        source_pattern = str(config["pattern"])
        return SubfileRule(
            source_pattern=source_pattern,
            compiled_pattern=self._compile_pattern(source_pattern),
            codec_name=str(config["codec_name"]),
            decode_params=deep_freeze(dict(config.get("decode_params", {}))),
            encode_params=deep_freeze(dict(config.get("encode_params", {}))),
            virtual=config.get("virtual", False),
            table_name=config.get("table_name"),
            table_parameters=tuple(config.get("table_parameters", ())),
            editor_columns=tuple(config.get("editor_columns", ())),
            pre_decode=self._build_pipeline(config.get("pre_decode", ())),
            post_decode=self._build_pipeline(config.get("post_decode", ())),
            pre_encode=self._build_pipeline(config.get("pre_encode", ())),
            post_encode=self._build_pipeline(config.get("post_encode", ())),
        )

    def _validate_config(
        self,
        config: Mapping[str, Any],
        *,
        index: int,
    ) -> None:
        if not isinstance(config, Mapping):
            raise TypeError(
                f"Invalid sub-file rule at index {index}: config must be a mapping."
            )
        if "operation" in config:
            self._invalid(index, "field 'operation' duplicates 'codec_name'")
        unknown = sorted(set(config).difference(_ALLOWED_FIELDS))
        if unknown:
            self._invalid(index, f"unknown fields: {', '.join(unknown)}")

        pattern = config.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            self._invalid(index, "'pattern' must be a non-empty string")

        codec_name = config.get("codec_name")
        if not isinstance(codec_name, str) or not codec_name:
            self._invalid(index, "'codec_name' must be a non-empty string")
        if codec_name not in VALID_CODEC_NAMES:
            available = ", ".join(sorted(VALID_CODEC_NAMES))
            self._invalid(
                index,
                (
                    f"codec_name '{codec_name}' is not a supported generic codec "
                    f"operation. Available operations: {available}"
                ),
            )

        decode_params = config.get("decode_params", {})
        encode_params = config.get("encode_params", {})
        if not isinstance(decode_params, Mapping):
            self._invalid(index, "'decode_params' must be a mapping")
        if not isinstance(encode_params, Mapping):
            self._invalid(index, "'encode_params' must be a mapping")

        virtual = config.get("virtual", False)
        if not isinstance(virtual, bool):
            self._invalid(index, "'virtual' must be a bool")

        self._validate_table_metadata(config, pattern=str(pattern), index=index)
        for parameters in (decode_params, encode_params):
            self._validate_parameters(parameters, pattern=pattern, index=index)
        for field in PIPELINE_FIELDS:
            self._validate_pipeline(
                config.get(field, ()),
                field=field,
                index=index,
            )

        if "generator" in encode_params:
            self._invalid(
                index,
                "'generator' is not an encode parameter; use 'pre_encode'",
            )
        if virtual:
            self._validate_virtual_pipeline(
                str(pattern),
                config.get("pre_encode", ()),
                index=index,
            )

    def _validate_virtual_pipeline(
        self,
        pattern: str,
        pipeline: Sequence[Mapping[str, Any]],
        *,
        index: int,
    ) -> None:
        if not pipeline:
            self._invalid(
                index,
                (
                    f"virtual rule '{pattern}' cannot be encoded because its "
                    "'pre_encode' pipeline is empty"
                ),
            )
        first = pipeline[0]
        method_name = str(first["method_name"])
        if method_name not in {
            "load_dependency_table",
            "load_card_sort_records",
        }:
            self._invalid(
                index,
                (
                    f"virtual rule '{pattern}' must start 'pre_encode' with a "
                    "method that can create a value from None"
                ),
            )
        params = first["params"]
        dependencies = (
            (params.get("table"),)
            if method_name == "load_dependency_table"
            else (params.get("name_table"), params.get("id_table"))
        )
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency:
                self._invalid(
                    index,
                    "virtual dependency templates must be non-empty strings",
                )
            if dependency.casefold() == pattern.casefold():
                self._invalid(
                    index,
                    f"virtual rule '{pattern}' cannot depend on itself",
                )
            if "[lang]" in dependency and "[lang]" not in pattern:
                self._invalid(
                    index,
                    (
                        f"dependency '{dependency}' uses [lang] without a "
                        "language pattern"
                    ),
                )

    def _validate_pipeline(
        self,
        pipeline: object,
        *,
        field: str,
        index: int,
    ) -> None:
        if not isinstance(pipeline, (list, tuple)):
            self._invalid(index, f"'{field}' must be a list or tuple")
        for step_index, step in enumerate(pipeline):
            label = f"'{field}' step {step_index}"
            if not isinstance(step, Mapping):
                self._invalid(index, f"{label} must be a mapping")
            unknown = sorted(set(step).difference(_ALLOWED_PIPELINE_STEP_FIELDS))
            if unknown:
                self._invalid(
                    index,
                    f"{label} has unknown fields: {', '.join(unknown)}",
                )
            method_name = step.get("method_name")
            if not isinstance(method_name, str) or not method_name.strip():
                self._invalid(
                    index,
                    f"{label} 'method_name' must be a non-empty string",
                )
            params = step.get("params")
            if not isinstance(params, Mapping):
                self._invalid(index, f"{label} 'params' must be a mapping")
            if method_name not in ALLOWED_RULE_METHODS:
                available = ", ".join(sorted(ALLOWED_RULE_METHODS))
                self._invalid(
                    index,
                    (
                        f"{label} method '{method_name}' is not allowed. "
                        f"Available methods: {available}"
                    ),
                )

    @staticmethod
    def _build_pipeline(
        pipeline: Sequence[Mapping[str, Any]],
    ) -> tuple[RuleMethodCall, ...]:
        return tuple(
            RuleMethodCall(
                method_name=str(step["method_name"]),
                params=deep_freeze(dict(step["params"])),
            )
            for step in pipeline
        )

    def _validate_table_metadata(
        self,
        config: Mapping[str, Any],
        *,
        pattern: str,
        index: int,
    ) -> None:
        table_name = config.get("table_name")
        table_parameters = config.get("table_parameters", ())
        editor_columns = config.get("editor_columns", ())
        if table_name is not None and (
            not isinstance(table_name, str) or not table_name.strip()
        ):
            self._invalid(index, "'table_name' must be a non-empty string")
        if not isinstance(table_parameters, (list, tuple)):
            self._invalid(index, "'table_parameters' must be a list or tuple")
        if any(
            not isinstance(parameter, str) or not parameter.strip()
            for parameter in table_parameters
        ):
            self._invalid(
                index,
                "'table_parameters' must contain non-empty strings",
            )
        if len(set(table_parameters)) != len(table_parameters):
            self._invalid(index, "'table_parameters' must not contain duplicates")
        if table_name is None and table_parameters:
            self._invalid(index, "'table_parameters' requires 'table_name'")
        if not isinstance(editor_columns, (list, tuple)):
            self._invalid(index, "'editor_columns' must be a list or tuple")
        if any(
            not isinstance(column, str) or not column.strip()
            for column in editor_columns
        ):
            self._invalid(index, "'editor_columns' must contain non-empty strings")
        if len(set(editor_columns)) != len(editor_columns):
            self._invalid(index, "'editor_columns' must not contain duplicates")
        if table_name is None and editor_columns:
            self._invalid(index, "'editor_columns' requires 'table_name'")
        if table_name is not None and config.get("virtual", False):
            self._invalid(index, "virtual rules cannot expose editable logical tables")
        if (
            table_name is not None
            and "[lang]" in pattern
            and "language" not in table_parameters
        ):
            self._invalid(
                index,
                "a table pattern containing '[lang]' requires parameter 'language'",
            )

    @staticmethod
    def _validate_table_registrations(
        rules: Sequence[SubfileRule],
    ) -> None:
        registrations: dict[tuple[str, tuple[str, ...]], str] = {}
        for rule in rules:
            if rule.table_name is None:
                continue
            key = (rule.table_name, rule.table_parameters)
            previous = registrations.get(key)
            if previous is not None:
                raise ValueError(
                    "Conflicting physical logical-table registration "
                    f"'{rule.table_name}{rule.table_parameters}': "
                    f"'{previous}' and '{rule.source_pattern}'."
                )
            registrations[key] = rule.source_pattern

    @classmethod
    def _validate_indexed_text_layouts(
        cls,
        rules: Sequence[SubfileRule],
    ) -> None:
        indexed_rules = {
            rule.source_pattern.casefold(): rule
            for rule in rules
            if rule.codec_name == "offset_string_table"
        }
        encode_layouts: dict[str, dict[str, object]] = {}
        for pattern, rule in indexed_rules.items():
            decode_layout = cls._require_indexed_text_layout(
                rule.decode_params,
                resource=rule.source_pattern,
                location="decode_params",
            )
            encode_layout = cls._require_indexed_text_layout(
                rule.encode_params,
                resource=rule.source_pattern,
                location="encode_params",
            )
            if decode_layout != encode_layout:
                raise ValueError(
                    "Indexed-text layout mismatch: "
                    f"resource '{rule.source_pattern}' decodes strings with "
                    f"{decode_layout}, but encodes strings with {encode_layout}."
                )
            encode_layouts[pattern] = encode_layout

        for rule in rules:
            generators = tuple(
                step
                for step in rule.pre_encode
                if step.method_name == "generate_string_offsets"
            )
            if not generators:
                continue
            dependencies = tuple(
                step.params.get("table")
                for step in rule.pre_encode
                if step.method_name == "load_dependency_table"
            )
            if len(dependencies) != 1 or not isinstance(dependencies[0], str):
                raise ValueError(
                    "Indexed-text layout mismatch: "
                    f"resource '{rule.source_pattern}' generates string offsets "
                    "but does not declare exactly one text dependency."
                )
            dependency_pattern = dependencies[0]
            dependency = indexed_rules.get(dependency_pattern.casefold())
            if dependency is None:
                raise ValueError(
                    "Indexed-text layout mismatch: "
                    f"resource '{rule.source_pattern}' generates string offsets "
                    f"for dependency '{dependency_pattern}', which is not an "
                    "offset-string-table rule."
                )
            expected = encode_layouts[dependency_pattern.casefold()]
            for generator in generators:
                actual = cls._require_indexed_text_layout(
                    generator.params,
                    resource=rule.source_pattern,
                    location="generate_string_offsets",
                )
                if actual != expected:
                    raise ValueError(
                        "Indexed-text layout mismatch: "
                        f"resource '{rule.source_pattern}' generates offsets with "
                        f"{actual}, but dependency '{dependency.source_pattern}' "
                        f"encodes strings with {expected}."
                    )

    @staticmethod
    def _require_indexed_text_layout(
        parameters: Mapping[str, Any],
        *,
        resource: str,
        location: str,
    ) -> dict[str, object]:
        missing = [
            field for field in _INDEXED_TEXT_LAYOUT_FIELDS if field not in parameters
        ]
        if missing:
            raise ValueError(
                "Indexed-text layout mismatch: "
                f"resource '{resource}' {location} is missing explicit "
                f"parameters: {', '.join(missing)}."
            )
        return {field: parameters[field] for field in _INDEXED_TEXT_LAYOUT_FIELDS}

    def _validate_parameters(
        self,
        parameters: Mapping[str, Any],
        *,
        pattern: str,
        index: int,
    ) -> None:
        byte_width = parameters.get("byte_width")
        if byte_width is not None and (
            isinstance(byte_width, bool)
            or not isinstance(byte_width, int)
            or byte_width <= 0
        ):
            self._invalid(index, "'byte_width' must be a positive integer")
        signed = parameters.get("signed")
        if signed is not None and not isinstance(signed, bool):
            self._invalid(index, "'signed' must be a bool")
        byte_order = parameters.get("byte_order")
        if byte_order is not None and byte_order not in {"little", "big"}:
            self._invalid(index, "'byte_order' must be 'little' or 'big'")

        for key in ("table", "limit_table"):
            dependency = parameters.get(key)
            if dependency is not None and not isinstance(dependency, str):
                self._invalid(index, f"'{key}' must be a string")
            if (
                isinstance(dependency, str)
                and "[lang]" in dependency
                and "[lang]" not in pattern
            ):
                self._invalid(
                    index,
                    f"dependency '{dependency}' uses [lang] without a language pattern",
                )

    @staticmethod
    def _compile_pattern(source_pattern: str) -> re.Pattern[str]:
        language_pattern = "|".join(re.escape(code) for code in LANGUAGE_PREFIXES)
        parts: list[str] = []
        position = 0
        language_seen = False
        for match in _PATTERN_TOKENS.finditer(source_pattern):
            parts.append(re.escape(source_pattern[position : match.start()]))
            token = match.group(0)
            if token == "*":
                parts.append(r"[^/\\]*")
            else:
                if language_seen:
                    raise ValueError(
                        "A sub-file rule may contain at most one [lang] placeholder."
                    )
                parts.append(f"(?P<lang>{language_pattern})")
                language_seen = True
            position = match.end()
        parts.append(re.escape(source_pattern[position:]))
        return re.compile("^" + "".join(parts) + "$", re.IGNORECASE)

    @staticmethod
    def _invalid(index: int, details: str) -> None:
        raise ValueError(f"Invalid sub-file rule at index {index}: {details}.")
