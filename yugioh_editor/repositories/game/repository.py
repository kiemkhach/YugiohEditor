from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from inspect import getattr_static
from numbers import Integral
from pathlib import Path
from typing import Any

import pandas as pd

from yugioh_editor.common.card_name_normalization import CardNameNormalizer
from yugioh_editor.common.constants import (
    AUDIO_EXTENSIONS,
    CONTAINER_SIGNATURE,
    EXECUTABLE_PATTERN,
    IMAGE_EXTENSIONS,
    LOGICAL_DAT_FILES,
    SUPPORTED_GAME_FILES,
    TABLE_CODEC_OPERATIONS,
    language_encoding,
)
from yugioh_editor.common.errors import (
    InvalidFileFormatError,
    PackResourceError,
    RulePipelineError,
)
from yugioh_editor.common.subfile_rules_config import SUBFILE_RULE_CONFIGS
from yugioh_editor.models.entities import (
    ContainerArchive,
    ContainerEntry,
    DeckFile,
    ProjectFileRecord,
    ProjectResource,
)
from yugioh_editor.repositories.game.connection import GameFolderConnection
from yugioh_editor.repositories.game.subfile_rule import (
    RuleMethodCall,
    RuleProcessingContext,
    SubfileRule,
    deep_thaw,
)
from yugioh_editor.repositories.game.subfile_rule_factory import (
    ALLOWED_RULE_METHODS,
    SubfileRuleFactory,
)


class GameRepository:
    """Coordinate game-file rules through generic connection operations."""

    def __init__(
        self,
        connection: GameFolderConnection,
        rule_factory: SubfileRuleFactory | None = None,
        card_name_normalizer: CardNameNormalizer | None = None,
    ) -> None:
        self._connection = connection
        self._rule_factory = rule_factory or SubfileRuleFactory()
        self._subfile_rules = self._rule_factory.build_rules(SUBFILE_RULE_CONFIGS)
        self._card_name_normalizer = card_name_normalizer or CardNameNormalizer(None)
        self._validate_rule_pipeline_methods(self._subfile_rules)
        self._readers = {
            ".ydc": self._connection.read_deck,
            ".exe": self._connection.read_executable,
        }

    @classmethod
    def from_root(
        cls,
        root: str | Path,
        card_name_normalizer: CardNameNormalizer | None = None,
    ) -> GameRepository:
        return cls(
            GameFolderConnection(root),
            card_name_normalizer=card_name_normalizer,
        )

    def use_root(self, root: str | Path) -> GameRepository:
        return GameRepository(
            self._connection.use_root(root),
            self._rule_factory,
            self._card_name_normalizer,
        )

    def list_files(self, supported_only: bool = False) -> list[Path]:
        files = self._connection.list_files(recursive=False)
        if not supported_only:
            return files
        supported = {name.casefold() for name in SUPPORTED_GAME_FILES}
        return [path for path in files if path.name.casefold() in supported]

    def find_game_executable(self) -> Path | None:
        matches = sorted(
            (
                path
                for path in self._connection.list_files(recursive=False)
                if EXECUTABLE_PATTERN.fullmatch(path.name)
            ),
            key=lambda path: path.name.casefold(),
        )
        return matches[0] if matches else None

    def find_logical_dat_files(self) -> dict[str, Path]:
        groups: dict[str, list[Path]] = {
            logical_name: [] for logical_name in LOGICAL_DAT_FILES
        }
        for path in self._connection.list_files(recursive=False):
            logical_name = path.name.casefold()
            if logical_name in groups:
                groups[logical_name].append(path)
        selected: dict[str, Path] = {}
        for logical_name, candidates in groups.items():
            ordered = sorted(
                candidates,
                key=lambda path: (path.name.casefold(), path.name),
            )
            if not ordered:
                continue
            if len(ordered) > 1:
                logging.warning(
                    "Duplicate logical game file '%s'; using '%s' and ignoring: %s",
                    logical_name,
                    ordered[0].name,
                    ", ".join(path.name for path in ordered[1:]),
                )
            selected[logical_name] = ordered[0]
        return selected

    def find_file(self, logical_name: str) -> Path | None:
        matches = sorted(
            (
                path
                for path in self._connection.list_files(recursive=False)
                if path.name.casefold() == logical_name.casefold()
            ),
            key=lambda path: (path.name.casefold(), path.name),
        )
        if len(matches) > 1:
            logging.warning(
                "Duplicate logical game file '%s'; using '%s'.",
                logical_name,
                matches[0].name,
            )
        return matches[0] if matches else None

    def require_file_path(self, relative_path: str | Path) -> Path:
        path = self._connection.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"Game file does not exist: {path}")
        return path

    def find_rule(self, file_name: str | Path) -> SubfileRule:
        match = self._match_rule(file_name)
        if match is None:
            raise LookupError(f"No sub-file rule matches '{file_name}'.")
        return match[0]

    def subfile_rule(self, relative_path: str | Path) -> SubfileRule | None:
        match = self._match_rule(relative_path)
        return match[0] if match is not None else None

    def virtual_subfile_rule(
        self,
        relative_path: str | Path,
    ) -> SubfileRule | None:
        rule = self.subfile_rule(relative_path)
        return rule if rule is not None and rule.virtual else None

    @classmethod
    def find_bin_codec(cls, relative_path: str | Path) -> str | None:
        rule = cls.from_root(".").subfile_rule(relative_path)
        return rule.codec_name if rule is not None else None

    def decode_archive(
        self,
        archive: ContainerArchive,
        output_directory: str,
    ) -> list[ProjectResource]:
        entries = {
            self._normalize(item.relative_path): item for item in archive.entries
        }
        resources: list[ProjectResource] = []
        for entry in sorted(archive.entries, key=lambda item: item.order):
            matched = self._match_rule(entry.relative_path)
            rule = matched[0] if matched is not None else None
            language = self._match_language(matched)
            if rule is not None and rule.virtual:
                resources.append(
                    ProjectResource(
                        ProjectFileRecord(
                            source_file=archive.source_name,
                            relative_path=entry.relative_path,
                            workspace_path=None,
                            file_kind="virtual",
                            storage_format="virtual",
                            language=language,
                            generated_on_pack=True,
                            virtual=True,
                            compressed=entry.compressed,
                            order=entry.order,
                        )
                    )
                )
                continue

            workspace_path = (
                Path(output_directory) / entry.relative_path.replace("\\", "/")
            ).as_posix()
            if rule is None:
                value = entry.data
                file_kind = "binary"
                storage = "binary"
            elif self._archive_entry_stays_raw(rule):
                value = entry.data
                file_kind = "binary"
                storage = "binary"
            else:
                value = self._decode_rule_value(
                    rule,
                    entry.data,
                    language,
                    entries,
                    relative_path=entry.relative_path,
                )
                file_kind, storage = self._resource_representation(
                    entry.relative_path,
                    rule.codec_name,
                )
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        source_file=archive.source_name,
                        relative_path=entry.relative_path,
                        workspace_path=workspace_path,
                        file_kind=file_kind,
                        storage_format=storage,
                        language=language,
                        compressed=entry.compressed,
                        order=entry.order,
                    ),
                    value,
                )
            )
        return resources

    def encode_archive(
        self,
        source_file: str,
        resources: list[ProjectResource],
    ) -> ContainerArchive:
        by_path = {
            self._normalize(item.record.relative_path): item for item in resources
        }
        operation_metadata: dict[str, object] = {
            "resources": by_path,
            "generated_values": {},
            "generation_stack": [],
        }
        entries: list[ContainerEntry] = []
        for resource in sorted(resources, key=lambda item: item.record.order):
            record = resource.record
            logging.debug(
                "Encoding resource source=%s path=%s virtual=%s",
                source_file,
                record.relative_path,
                record.virtual,
            )
            try:
                data = self._encode_resource(
                    resource,
                    by_path,
                    metadata=operation_metadata,
                )
            except Exception as error:
                if isinstance(error, PackResourceError):
                    raise
                matched = self._match_rule(record.relative_path)
                rule = matched[0] if matched is not None else None
                pipeline_error = error if isinstance(error, RulePipelineError) else None
                raise PackResourceError(
                    source_file=source_file,
                    resource=record.relative_path,
                    pattern=(
                        pipeline_error.pattern
                        if pipeline_error is not None
                        else rule.source_pattern
                        if rule is not None
                        else "<raw>"
                    ),
                    codec=(
                        pipeline_error.codec
                        if pipeline_error is not None
                        else rule.codec_name
                        if rule is not None
                        else "binary"
                    ),
                    virtual=record.virtual,
                    phase=(
                        pipeline_error.phase if pipeline_error is not None else "encode"
                    ),
                    step=(pipeline_error.step if pipeline_error is not None else None),
                    method=(
                        pipeline_error.method
                        if pipeline_error is not None
                        else "encode_resource"
                    ),
                    cause=error,
                ) from error
            entries.append(
                ContainerEntry(
                    relative_path=record.relative_path,
                    data=data,
                    full_size=len(data),
                    stored_size=len(data),
                    compressed=record.compressed,
                    order=record.order,
                )
            )
        return ContainerArchive(source_name=source_file, entries=entries)

    def read_deck_resource(
        self,
        file_name: str,
        workspace_path: str,
    ) -> ProjectResource:
        deck = self._connection.read_deck(file_name)
        return ProjectResource(
            ProjectFileRecord(
                source_file=file_name,
                relative_path=file_name,
                workspace_path=workspace_path,
                file_kind="table",
                storage_format="table",
            ),
            pd.DataFrame({"card_id": deck.card_ids}),
        )

    def write_deck_resource(
        self,
        file_name: str,
        resource: ProjectResource,
    ) -> Path:
        table = self._require_table(resource)
        return self._connection.write_deck(
            file_name,
            DeckFile(card_ids=table["card_id"].astype(int).tolist()),
        )

    def read_raw_resource(
        self,
        file_name: str,
        workspace_path: str,
        *,
        file_kind: str = "binary",
    ) -> ProjectResource:
        return ProjectResource(
            ProjectFileRecord(
                source_file=file_name,
                relative_path=file_name,
                workspace_path=workspace_path,
                file_kind=file_kind,
                storage_format="binary",
            ),
            self._connection.read_binary(file_name),
        )

    def write_raw_resource(
        self,
        file_name: str,
        resource: ProjectResource,
    ) -> Path:
        return self._connection.write_binary(file_name, bytes(resource.value))

    def read_executable_resource(
        self,
        file_name: str,
        workspace_path: str,
    ) -> ProjectResource:
        resource = self.read_raw_resource(
            file_name,
            workspace_path,
            file_kind="exe",
        )
        resource.record.relative_path = workspace_path
        return resource

    def write_executable_resource(
        self,
        file_name: str,
        resource: ProjectResource,
    ) -> Path:
        return self._connection.write_executable(file_name, bytes(resource.value))

    def read_binary_resource(
        self,
        relative_path: str | Path,
        language: str | None = None,
    ) -> pd.DataFrame | bytes | str:
        data = self._connection.read_binary_file(relative_path)
        matched = self._match_rule(relative_path)
        if matched is None:
            return data
        rule = matched[0]
        active_language = language or self._match_language(matched)
        if self._requires_archive_context(rule):
            raise ValueError(
                f"Rule '{rule.source_pattern}' requires archive dependencies."
            )
        return self._decode_rule_value(
            rule,
            data,
            active_language,
            {},
            relative_path=str(relative_path),
        )

    def write_binary_resource(
        self,
        relative_path: str | Path,
        value: pd.DataFrame | bytes | str,
        language: str | None = None,
    ) -> Path:
        data = self._encode_path_value(relative_path, value, language)
        return self._connection.write_binary_file(relative_path, data)

    @classmethod
    def decode_binary_resource(
        cls,
        relative_path: str | Path,
        data: bytes,
        language: str | None = None,
    ) -> pd.DataFrame | bytes | str:
        repository = cls.from_root(".")
        matched = repository._match_rule(relative_path)
        if matched is None:
            return data
        rule = matched[0]
        active_language = language or repository._match_language(matched)
        if repository._requires_archive_context(rule):
            raise ValueError(
                f"Rule '{rule.source_pattern}' requires archive dependencies."
            )
        return repository._decode_rule_value(
            rule,
            data,
            active_language,
            {},
            relative_path=str(relative_path),
        )

    @classmethod
    def encode_binary_resource(
        cls,
        relative_path: str | Path,
        value: pd.DataFrame | bytes | str,
        language: str | None = None,
    ) -> bytes:
        return cls.from_root(".")._encode_path_value(
            relative_path,
            value,
            language,
        )

    def read_file(self, relative_path: str | Path):
        path = Path(relative_path)
        rule = self.subfile_rule(path)
        if rule is not None and rule.codec_name == "container":
            return self.read_container(path)
        reader = self._readers.get(
            path.suffix.casefold(),
            self._connection.read_binary,
        )
        return reader(path)

    def read_container(self, relative_path: str | Path) -> ContainerArchive:
        path = Path(relative_path)
        data = self._connection.read_bytes(path)
        if not data.startswith(CONTAINER_SIGNATURE):
            raise InvalidFileFormatError(
                f"Game container '{path.name}' does not have the KCEJYUGI signature."
            )
        return self._connection.read_container(path)

    def write_container(
        self,
        relative_path: str | Path,
        archive: ContainerArchive,
        compression: str = "preserve",
    ) -> Path:
        return self._connection.write_container(
            relative_path,
            archive,
            compression,
        )

    def read_deck(self, relative_path: str | Path) -> DeckFile:
        return self._connection.read_deck(relative_path)

    def write_deck(
        self,
        relative_path: str | Path,
        deck: DeckFile,
    ) -> Path:
        return self._connection.write_deck(relative_path, deck)

    def read_executable(self, relative_path: str | Path) -> bytes:
        return self._connection.read_executable(relative_path)

    def write_executable(
        self,
        relative_path: str | Path,
        data: bytes,
    ) -> Path:
        return self._connection.write_executable(relative_path, data)

    def read_binary(self, relative_path: str | Path) -> bytes:
        return self._connection.read_binary(relative_path)

    def write_binary(
        self,
        relative_path: str | Path,
        data: bytes,
    ) -> Path:
        return self._connection.write_binary(relative_path, data)

    def _match_rule(
        self,
        relative_path: str | Path,
    ) -> tuple[SubfileRule, re.Match[str]] | None:
        file_name = Path(str(relative_path).replace("\\", "/")).name
        for rule in reversed(self._subfile_rules):
            match = rule.compiled_pattern.fullmatch(file_name)
            if match is not None:
                return rule, match
        return None

    @staticmethod
    def _match_language(
        matched: tuple[SubfileRule, re.Match[str]] | None,
    ) -> str | None:
        if matched is None or "lang" not in matched[1].re.groupindex:
            return None
        return matched[1].group("lang").casefold()

    def _decode_rule_value(
        self,
        rule: SubfileRule,
        data: bytes,
        language: str | None,
        entries: Mapping[str, ContainerEntry],
        *,
        relative_path: str,
        dependency_stack: list[str] | None = None,
    ) -> object:
        codec_name = rule.codec_name
        context = self._create_rule_context(
            rule,
            relative_path=relative_path,
            language=language,
            metadata={
                "entries": entries,
                "dependency_stack": (
                    dependency_stack
                    if dependency_stack is not None
                    else [relative_path]
                ),
            },
        )
        decode_input = self._run_rule_pipeline(
            data,
            rule.pre_decode,
            context=context,
            phase="pre_decode",
        )
        try:
            decoded = self._connection.decode_resource(
                codec_name,
                decode_input,
                **context.decode_params,
            )
        except Exception as error:
            raise RulePipelineError(
                "Rule codec failed: "
                f"resource='{context.relative_path}', "
                f"pattern='{rule.source_pattern}', "
                f"codec='{codec_name}', "
                f"virtual={rule.virtual}, phase='decode', "
                f"language={language!r}, "
                f"encoding={context.decode_params.get('encoding')!r}: {error}",
                resource=context.relative_path,
                pattern=rule.source_pattern,
                codec=codec_name,
                virtual=rule.virtual,
                phase="decode",
                step=-1,
                method=codec_name,
            ) from error
        return self._run_rule_pipeline(
            decoded,
            rule.post_decode,
            context=context,
            phase="post_decode",
        )

    def _encode_path_value(
        self,
        relative_path: str | Path,
        value: pd.DataFrame | bytes | str,
        language: str | None,
    ) -> bytes:
        matched = self._match_rule(relative_path)
        if matched is None:
            return bytes(value)
        rule = matched[0]
        active_language = language or self._match_language(matched)
        return self._encode_rule_value(
            rule,
            value,
            active_language,
            relative_path=str(relative_path),
        )

    def _encode_resource(
        self,
        resource: ProjectResource,
        resources: Mapping[str, ProjectResource],
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> bytes:
        matched = self._match_rule(resource.record.relative_path)
        if matched is None:
            if resource.record.virtual:
                raise ValueError(
                    f"Virtual resource '{resource.record.relative_path}' "
                    "has no virtual rule."
                )
            return bytes(resource.value)
        rule = matched[0]
        if resource.record.virtual != rule.virtual:
            raise ValueError(
                f"Virtual metadata mismatch for '{resource.record.relative_path}': "
                f"manifest={resource.record.virtual}, rule={rule.virtual}."
            )
        return self._encode_rule_value(
            rule,
            None if resource.record.virtual else resource.value,
            resource.record.language or self._match_language(matched),
            relative_path=resource.record.relative_path,
            metadata=metadata or {"resources": resources},
        )

    def _encode_rule_value(
        self,
        rule: SubfileRule,
        value: object,
        language: str | None,
        *,
        relative_path: str,
        metadata: Mapping[str, object] | None = None,
    ) -> bytes:
        codec_name = rule.codec_name
        context = self._create_rule_context(
            rule,
            relative_path=relative_path,
            language=language,
            metadata=metadata,
        )
        prepared = self._prepare_encode_value(value, context=context)
        try:
            encoded = self._connection.encode_resource(
                codec_name,
                prepared,
                **context.encode_params,
            )
        except Exception as error:
            raise RulePipelineError(
                "Rule codec failed: "
                f"resource='{context.relative_path}', "
                f"pattern='{rule.source_pattern}', "
                f"codec='{codec_name}', "
                f"virtual={rule.virtual}, phase='encode', "
                f"language={language!r}, "
                f"encoding={context.encode_params.get('encoding')!r}: {error}",
                resource=context.relative_path,
                pattern=rule.source_pattern,
                codec=codec_name,
                virtual=rule.virtual,
                phase="encode",
                step=-1,
                method=codec_name,
            ) from error
        result = self._run_rule_pipeline(
            encoded,
            rule.post_encode,
            context=context,
            phase="post_encode",
        )
        if not isinstance(result, bytes):
            raise TypeError(
                f"Post-encode pipeline for '{relative_path}' must return bytes."
            )
        return result

    def _prepare_encode_value(
        self,
        value: object,
        *,
        context: RuleProcessingContext,
    ) -> object:
        if not context.rule.virtual:
            return self._run_rule_pipeline(
                value,
                context.rule.pre_encode,
                context=context,
                phase="pre_encode",
            )

        cache = context.metadata.setdefault("generated_values", {})
        if not isinstance(cache, dict):
            raise TypeError("Generated-value cache metadata must be a dictionary.")
        key = self._normalize(context.relative_path)
        if key in cache:
            return cache[key]

        stack = context.metadata.setdefault("generation_stack", [])
        if not isinstance(stack, list):
            raise TypeError("Generation stack metadata must be a list.")
        normalized_stack = {self._normalize(str(item)) for item in stack}
        if key in normalized_stack:
            raise ValueError(
                "Circular virtual-resource dependency: "
                + " -> ".join([*(str(item) for item in stack), context.relative_path])
            )

        stack.append(context.relative_path)
        try:
            prepared = self._run_rule_pipeline(
                value,
                context.rule.pre_encode,
                context=context,
                phase="pre_encode",
            )
        finally:
            stack.pop()
        cache[key] = prepared
        return prepared

    def _run_rule_pipeline(
        self,
        value: object,
        steps: Sequence[RuleMethodCall],
        *,
        context: RuleProcessingContext,
        phase: str,
    ) -> object:
        result = value
        for index, step in enumerate(steps):
            try:
                method = self._resolve_rule_method(step.method_name)
                result = method(
                    result,
                    context=context,
                    **deep_thaw(step.params),
                )
            except Exception as error:
                raise RulePipelineError(
                    "Rule pipeline failed: "
                    f"resource='{context.relative_path}', "
                    f"pattern='{context.rule.source_pattern}', "
                    f"codec='{context.rule.codec_name}', "
                    f"virtual={context.rule.virtual}, "
                    f"phase='{phase}', step={index}, "
                    f"method='{step.method_name}': {error}",
                    resource=context.relative_path,
                    pattern=context.rule.source_pattern,
                    codec=context.rule.codec_name,
                    virtual=context.rule.virtual,
                    phase=phase,
                    step=index,
                    method=step.method_name,
                ) from error
        return result

    def _resolve_rule_method(
        self,
        method_name: str,
    ) -> Callable[..., object]:
        if method_name not in ALLOWED_RULE_METHODS:
            available = ", ".join(sorted(ALLOWED_RULE_METHODS))
            raise ValueError(
                f"Unknown rule processing method '{method_name}'. "
                f"Available methods: {available}"
            )
        method = getattr(type(self), method_name, None)
        if method is None or not callable(method):
            raise ValueError(
                f"Configured rule processing method '{method_name}' does not exist."
            )
        return method

    @classmethod
    def _validate_rule_pipeline_methods(
        cls,
        rules: Sequence[SubfileRule],
    ) -> None:
        for rule in rules:
            for phase in (
                "pre_decode",
                "post_decode",
                "pre_encode",
                "post_encode",
            ):
                for index, step in enumerate(getattr(rule, phase)):
                    descriptor = getattr_static(cls, step.method_name, None)
                    if not isinstance(descriptor, staticmethod) or not callable(
                        descriptor.__func__
                    ):
                        raise TypeError(
                            f"Rule '{rule.source_pattern}' {phase} step {index} "
                            f"method '{step.method_name}' must be an existing "
                            "static method of GameRepository."
                        )

    @staticmethod
    def sequence_to_dataframe(
        value: object,
        *,
        context: RuleProcessingContext,
        column: str = "value",
    ) -> pd.DataFrame:
        del context
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError("sequence_to_dataframe expects a sequence.")
        return pd.DataFrame({column: list(value)})

    @staticmethod
    def records_to_dataframe(
        value: object,
        *,
        context: RuleProcessingContext,
        columns: Sequence[str] = (),
    ) -> pd.DataFrame:
        del context
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError("records_to_dataframe expects a record sequence.")
        rows = [dict(row) for row in value]
        return pd.DataFrame.from_records(rows, columns=tuple(columns) or None)

    @staticmethod
    def dataframe_column_to_list(
        value: object,
        *,
        context: RuleProcessingContext,
        column: str = "value",
        fill_value: object | None = None,
        cast: str | None = None,
    ) -> list[object]:
        del context
        table = GameRepository._require_dataframe(value)
        if column not in table.columns:
            raise ValueError(f"DataFrame does not contain column '{column}'.")
        series = table[column]
        if fill_value is not None:
            series = series.fillna(fill_value)
        values = series.tolist()
        if cast is None:
            return values
        converters = {"int": int, "str": str, "float": float}
        try:
            converter = converters[cast]
        except KeyError as error:
            available = ", ".join(sorted(converters))
            raise ValueError(
                f"Unknown DataFrame value cast '{cast}'. Available casts: {available}."
            ) from error
        return [converter(item) for item in values]

    @staticmethod
    def dataframe_to_records(
        value: object,
        *,
        context: RuleProcessingContext,
    ) -> list[dict[str, object]]:
        del context
        return GameRepository._require_dataframe(value).to_dict("records")

    @staticmethod
    def dataframe_to_indexed_text_records(
        value: object,
        *,
        context: RuleProcessingContext,
    ) -> list[dict[str, object]]:
        table = GameRepository._require_dataframe(value)
        required = ("text", "is_reserved")
        missing = [column for column in required if column not in table.columns]
        if missing:
            raise ValueError(
                f"{context.relative_path} cannot be encoded: missing indexed-text "
                f"columns: {', '.join(missing)}."
            )
        records: list[dict[str, object]] = []
        for record_index, row in table.reset_index(drop=True).iterrows():
            text_value = row["text"]
            if pd.isna(text_value):
                text = ""
            elif isinstance(text_value, str):
                text = text_value
            else:
                text = str(text_value)
            is_reserved = GameRepository._parse_indexed_text_bool(
                row["is_reserved"],
                record_index=record_index,
                relative_path=context.relative_path,
            )
            records.append(
                {
                    "text": text,
                    "is_reserved": bool(
                        is_reserved and record_index > 0 and text == ""
                    ),
                }
            )
        return records

    @staticmethod
    def _parse_indexed_text_bool(
        value: object,
        *,
        record_index: int,
        relative_path: str,
    ) -> bool:
        if isinstance(value, bool):
            return value
        if type(value).__name__ == "bool_":
            return bool(value)
        if isinstance(value, str) and value in {"True", "False"}:
            return value == "True"
        raise ValueError(
            f"{relative_path} row {record_index}: is_reserved must be the "
            "canonical boolean True or False."
        )

    @staticmethod
    def log_dataframe_summary(
        value: object,
        *,
        context: RuleProcessingContext,
        required_columns: Sequence[str] = (),
        distribution_columns: Sequence[str] = (),
    ) -> pd.DataFrame:
        table = GameRepository._require_dataframe(value)
        missing = [column for column in required_columns if column not in table.columns]
        if missing:
            file_name = Path(context.relative_path.replace("\\", "/")).name
            if len(missing) == 1:
                detail = f"required logical column '{missing[0]}' is missing"
            else:
                detail = "required logical columns are missing: " + ", ".join(missing)
            raise ValueError(f"{file_name} cannot be encoded: {detail}.")
        distributions = {
            column: {
                str(label): int(count)
                for label, count in table[column]
                .fillna("<missing>")
                .astype(str)
                .value_counts()
                .sort_index()
                .items()
            }
            for column in distribution_columns
            if column in table.columns
        }
        logging.debug(
            "Encoding %s: records=%d columns=%s distributions=%s",
            context.relative_path,
            len(table),
            list(table.columns),
            distributions,
        )
        return table

    @staticmethod
    def apply_value_map(
        value: object,
        *,
        context: RuleProcessingContext,
        mapping: Mapping[object, object],
        unknown_template: str | None = None,
    ) -> list[object]:
        del context
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError("apply_value_map expects a sequence.")
        result: list[object] = []
        for item in value:
            if item in mapping:
                result.append(mapping[item])
            elif unknown_template is not None:
                result.append(unknown_template.format(value=item))
            else:
                raise ValueError(f"Unsupported mapped value: {item}")
        return result

    @staticmethod
    def apply_reverse_value_map(
        value: object,
        *,
        context: RuleProcessingContext,
        mapping: Mapping[object, object],
    ) -> list[object]:
        del context
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError("apply_reverse_value_map expects a sequence.")
        reverse = {mapped: source for source, mapped in mapping.items()}
        unknown = sorted(
            {str(item) for item in value if item not in reverse},
        )
        if unknown:
            raise ValueError(f"Unsupported mapped values: {', '.join(unknown)}")
        return [reverse[item] for item in value]

    @staticmethod
    def cast_dataframe_columns(
        value: object,
        *,
        context: RuleProcessingContext,
        columns: Sequence[str],
        type: str,
    ) -> pd.DataFrame:
        del context
        converters = {"int": int, "str": str, "float": float}
        try:
            converter = converters[type]
        except KeyError as error:
            available = ", ".join(sorted(converters))
            raise ValueError(
                f"Unknown DataFrame column cast '{type}'. Available casts: {available}."
            ) from error
        table = GameRepository._require_dataframe(value).copy()
        missing = [column for column in columns if column not in table.columns]
        if missing:
            raise ValueError(
                f"DataFrame does not contain columns: {', '.join(missing)}."
            )
        for column in columns:
            table[column] = table[column].map(converter)
        return table

    @staticmethod
    def add_derived_fields(
        value: object,
        *,
        context: RuleProcessingContext,
        fields: Mapping[str, Mapping[str, object]],
    ) -> list[dict[str, object]]:
        del context
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError("add_derived_fields expects a record sequence.")
        rows = [dict(row) for row in value]
        for row in rows:
            for target, specification in fields.items():
                source = str(specification.get("source", target))
                source_value = str(row.get(source, ""))
                if specification.get("omit_if_empty") and not source_value:
                    row[target] = ""
                else:
                    row[target] = (
                        str(specification.get("prefix", ""))
                        + source_value
                        + str(specification.get("suffix", ""))
                    )
        return rows

    @staticmethod
    def compile_regex_parameter(
        value: object,
        *,
        context: RuleProcessingContext,
        param_name: str = "pattern",
        flags: Sequence[str] = (),
    ) -> object:
        pattern = context.decode_params.get(param_name)
        if not isinstance(pattern, str):
            raise TypeError(f"Decode parameter '{param_name}' must be a regex string.")
        available_flags = {
            "ASCII": re.ASCII,
            "DOTALL": re.DOTALL,
            "IGNORECASE": re.IGNORECASE,
            "MULTILINE": re.MULTILINE,
            "VERBOSE": re.VERBOSE,
        }
        unknown = sorted(set(flags).difference(available_flags))
        if unknown:
            raise ValueError(f"Unknown regex flags: {', '.join(unknown)}.")
        combined_flags = re.NOFLAG
        for flag in flags:
            combined_flags |= available_flags[flag]
        context.decode_params[param_name] = re.compile(pattern, combined_flags)
        return value

    @staticmethod
    def inject_offset_dependency(
        value: object,
        *,
        context: RuleProcessingContext,
        table: str,
        param_name: str = "offsets",
        column: str = "value",
    ) -> object:
        dependency = context.repository._read_rule_dependency(context, table)
        context.decode_params[param_name] = context.repository._dependency_values(
            dependency,
            column=column,
        )
        return value

    @staticmethod
    def limit_parameter_by_dependency(
        value: object,
        *,
        context: RuleProcessingContext,
        param_name: str,
        table: str,
        column: str = "value",
    ) -> object:
        if param_name not in context.decode_params:
            raise ValueError(f"Decode parameter '{param_name}' does not exist.")
        dependency = context.repository._read_rule_dependency(context, table)
        limit = len(
            context.repository._dependency_values(
                dependency,
                column=column,
            )
        )
        context.decode_params[param_name] = list(context.decode_params[param_name])[
            :limit
        ]
        return value

    @staticmethod
    def load_dependency_table(
        value: object,
        *,
        context: RuleProcessingContext,
        table: str,
    ) -> pd.DataFrame:
        del value
        return context.repository._get_dependency_table(
            table,
            context=context,
        )

    @staticmethod
    def load_card_sort_records(
        value: object,
        *,
        context: RuleProcessingContext,
        name_table: str,
        id_table: str,
    ) -> list[dict[str, object]]:
        del value
        names = context.repository._get_dependency_table(
            name_table,
            context=context,
        )
        card_ids = context.repository._get_dependency_table(
            id_table,
            context=context,
        )
        if "value" not in names or "value" not in card_ids:
            raise ValueError("card_sort dependencies require a 'value' column.")
        if len(names) != len(card_ids):
            raise ValueError(
                f"Cannot generate {context.relative_path}: language="
                f"{context.language or '<none>'}, name_count={len(names)}, "
                f"card_id_count={len(card_ids)}."
            )
        records: list[dict[str, object]] = []
        for index, (name, card_id) in enumerate(
            zip(names["value"].fillna(""), card_ids["value"], strict=True)
        ):
            if isinstance(card_id, bool):
                raise TypeError(
                    f"Cannot generate {context.relative_path}: card_id at "
                    f"index {index} must be an integer."
                )
            if isinstance(card_id, Integral):
                normalized_card_id = int(card_id)
            elif isinstance(card_id, str) and re.fullmatch(r"-?\d+", card_id):
                normalized_card_id = int(card_id)
            else:
                raise TypeError(
                    f"Cannot generate {context.relative_path}: card_id at "
                    f"index {index} must be an integer."
                )
            records.append(
                {
                    "card_index": index,
                    "name": str(name),
                    "card_id": normalized_card_id,
                }
            )
        return records

    @staticmethod
    def generate_string_offsets(
        value: object,
        *,
        context: RuleProcessingContext,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
    ) -> list[int]:
        values = GameRepository._pipeline_sequence(
            value,
            method_name="generate_string_offsets",
        )
        active_encoding = (
            language_encoding(context.language) if encoding == "language" else encoding
        )
        return context.repository._calculate_offset_string_positions(
            values,
            encoding=active_encoding,
            terminator=bytes(terminator),
            alignment=alignment,
            minimum_padding=minimum_padding,
        )

    @staticmethod
    def generate_sort_indices(
        value: object,
        *,
        context: RuleProcessingContext,
    ) -> list[int]:
        rows = GameRepository._pipeline_sequence(
            value,
            method_name="generate_sort_indices",
        )
        records = [dict(item) for item in rows]
        if not records:
            raise ValueError("Cannot generate card_sort from an empty card table.")
        language = context.language
        if language is None:
            raise ValueError("Cannot generate card_sort without a language.")
        names = [str(record["name"]) for record in records]
        card_ids: list[int] = []
        for index, record in enumerate(records):
            card_id = record.get("card_id")
            if isinstance(card_id, bool) or not isinstance(card_id, int):
                raise TypeError(
                    f"Cannot generate card_sort: card_id at index {index} must "
                    "be an integer."
                )
            if card_id < 0 and not (index == 0 and card_id == -1):
                raise ValueError(
                    f"Cannot generate card_sort: card_id at index {index} must "
                    "not be negative."
                )
            card_ids.append(card_id)
        normalized_names = [
            context.repository._card_name_normalizer.normalize(name, language)
            for name in names
        ]
        ordered_indices = sorted(
            range(len(records)),
            key=lambda index: (normalized_names[index], card_ids[index]),
        )
        if ordered_indices[0] != 0:
            raise ValueError(
                "Cannot generate card_sort: dummy card index 0 is not first "
                "after normalization."
            )
        rank_by_index = [0] * len(records)
        for rank, card_index in enumerate(ordered_indices[1:]):
            rank_by_index[card_index] = rank
        result = [0]
        result.extend(rank_by_index[index] for index in range(1, len(records)))
        card_count = len(card_ids)
        target_length = GameRepository.find_next_power_of_two(card_count)
        if target_length < len(result):
            raise ValueError(
                f"Cannot generate card_sort: target length {target_length} from "
                f"card count {card_count} is smaller than result length "
                f"{len(result)}."
            )
        if len(records) - 2 > 0xFFFF:
            raise ValueError("Cannot generate card_sort: ranks do not fit uint16.")
        result.extend([0] * (target_length - len(result)))
        if (
            result[0] != 0
            or sorted(result[1 : len(records)]) != list(range(len(records) - 1))
            or any(result[len(records) :])
        ):
            raise ValueError("Generated card_sort violates rank-table invariants.")
        return result

    @staticmethod
    def find_next_power_of_two(value: int) -> int:
        if value <= 0:
            return 1
        if value & (value - 1) == 0:
            return value
        return 1 << value.bit_length()

    @staticmethod
    def generate_reverse_lookup(
        value: object,
        *,
        context: RuleProcessingContext,
    ) -> list[int]:
        values = [
            int(item)
            for item in GameRepository._pipeline_sequence(
                value,
                method_name="generate_reverse_lookup",
            )
        ]
        valid_ids = [item for item in values if item >= 0]
        if not valid_ids:
            raise ValueError(
                "Cannot generate card_intid: card_id contains no non-negative IDs."
            )
        maximum_id = max(valid_ids)
        target_count = 1 << maximum_id.bit_length()
        output = [0] * target_count
        for card_index, card_id in enumerate(values):
            if card_id >= 0:
                output[card_id] = card_index
        logging.debug(
            "Reverse lookup diagnostics resource=%s source_records=%d "
            "dynamic_count=%d max_id=%d negative_count=%d",
            context.relative_path,
            len(values),
            target_count,
            maximum_id,
            len(values) - len(valid_ids),
        )
        return output

    @staticmethod
    def validate_sequence_capacity(
        value: object,
        *,
        context: RuleProcessingContext,
        capacity: int,
        label: str = "Sequence",
    ) -> list[object]:
        del context
        values = GameRepository._pipeline_sequence(
            value,
            method_name="validate_sequence_capacity",
        )
        if capacity <= 0:
            raise ValueError("Sequence capacity must be positive.")
        if len(values) > capacity:
            raise ValueError(f"{label} exceeds {capacity} records.")
        return values

    @staticmethod
    def pad_integer_sequence(
        value: object,
        *,
        context: RuleProcessingContext,
        capacity: int,
        pad_value: int = 0,
    ) -> list[int]:
        values = GameRepository.validate_sequence_capacity(
            value,
            context=context,
            capacity=capacity,
            label="Integer sequence",
        )
        integers = [int(item) for item in values]
        return integers + [int(pad_value)] * (capacity - len(integers))

    @staticmethod
    def pad_integer_sequence_to_dependency_length(
        value: object,
        *,
        context: RuleProcessingContext,
        dependency: str,
        pad_value: int = 0,
        value_label: str = "Integer sequence",
    ) -> list[int]:
        values = GameRepository._pipeline_sequence(
            value,
            method_name="pad_integer_sequence_to_dependency_length",
        )
        dependency_value = context.repository._get_dependency_value(
            dependency,
            context=context,
        )
        target_count = context.repository._logical_value_length(
            dependency_value,
            dependency=dependency,
        )
        if len(values) > target_count:
            output_label = context.rule.source_pattern.split("[", 1)[0]
            dependency_label = Path(dependency).stem
            raise ValueError(
                f"Cannot generate {output_label}: resource={context.relative_path}, "
                f"language={context.language or '<none>'}, {value_label}_count="
                f"{len(values)}, {dependency_label}_count={target_count}."
            )
        integers = [int(item) for item in values]
        return integers + [int(pad_value)] * (target_count - len(integers))

    @staticmethod
    def slice_bytes(
        value: object,
        *,
        context: RuleProcessingContext,
        start: int = 0,
        end: int | None = None,
    ) -> bytes:
        del context
        if not isinstance(value, bytes):
            raise TypeError("slice_bytes expects bytes.")
        return value[start:end]

    @staticmethod
    def append_bytes(
        value: object,
        *,
        context: RuleProcessingContext,
        suffix: bytes,
    ) -> bytes:
        del context
        if not isinstance(value, bytes):
            raise TypeError("append_bytes expects bytes.")
        return value + bytes(suffix)

    def _read_rule_dependency(
        self,
        context: RuleProcessingContext,
        template: str,
    ) -> object:
        entries = context.metadata.get("entries")
        if not isinstance(entries, Mapping):
            raise KeyError(
                f"Dependency '{template}' is unavailable outside an archive context."
            )
        entry = self._dependency_entry(entries, template, context.language)
        stack = context.metadata.setdefault(
            "dependency_stack",
            [context.relative_path],
        )
        if not isinstance(stack, list):
            raise TypeError("Dependency stack metadata must be a list.")
        normalized = {self._normalize(str(item)) for item in stack}
        if self._normalize(entry.relative_path) in normalized:
            raise ValueError(
                "Circular sub-file dependency: "
                + " -> ".join([*(str(item) for item in stack), entry.relative_path])
            )
        stack.append(entry.relative_path)
        try:
            matched = self._match_rule(entry.relative_path)
            if matched is None:
                return entry.data
            return self._decode_rule_value(
                matched[0],
                entry.data,
                self._match_language(matched) or context.language,
                entries,
                relative_path=entry.relative_path,
                dependency_stack=stack,
            )
        finally:
            stack.pop()

    def _get_dependency_table(
        self,
        template: str,
        *,
        context: RuleProcessingContext,
    ) -> pd.DataFrame:
        dependency = self._resolve_dependency(template, context.language)
        if self._path_matches_suffix(context.relative_path, dependency):
            raise ValueError(
                f"Virtual resource '{context.relative_path}' cannot depend on "
                f"itself through template '{template}' resolved as '{dependency}'."
            )
        resources = context.metadata.get("resources")
        if not isinstance(resources, Mapping):
            raise KeyError(
                f"Virtual resource '{context.relative_path}' dependency "
                f"template '{template}' resolved as '{dependency}', but no "
                "project resources are available."
            )
        resource = next(
            (
                item
                for path, item in resources.items()
                if self._path_matches_suffix(str(path), dependency)
            ),
            None,
        )
        if resource is None:
            raise KeyError(
                f"Virtual resource '{context.relative_path}' dependency "
                f"template '{template}' resolved as '{dependency}' was not found."
            )
        return self._require_table(resource)

    def _get_dependency_value(
        self,
        template: str,
        *,
        context: RuleProcessingContext,
    ) -> object:
        dependency = self._resolve_dependency(template, context.language)
        resources = context.metadata.get("resources")
        if not isinstance(resources, Mapping):
            raise KeyError(
                f"Virtual resource '{context.relative_path}' dependency "
                f"template '{template}' resolved as '{dependency}', but no "
                "project resources are available."
            )
        resource = next(
            (
                item
                for path, item in resources.items()
                if self._path_matches_suffix(str(path), dependency)
            ),
            None,
        )
        if resource is None:
            raise KeyError(
                f"Virtual resource '{context.relative_path}' dependency "
                f"template '{template}' resolved as '{dependency}' was not found."
            )
        if not resource.record.virtual:
            return resource.value

        matched = self._match_rule(resource.record.relative_path)
        if matched is None or not matched[0].virtual:
            raise ValueError(
                f"Virtual dependency '{resource.record.relative_path}' has no "
                "matching virtual rule."
            )
        dependency_context = self._create_rule_context(
            matched[0],
            relative_path=resource.record.relative_path,
            language=resource.record.language or self._match_language(matched),
            metadata=context.metadata,
        )
        return self._prepare_encode_value(None, context=dependency_context)

    @staticmethod
    def _logical_value_length(value: object, *, dependency: str) -> int:
        if isinstance(value, pd.DataFrame):
            return len(value.index)
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError(
                f"Dependency '{dependency}' must resolve to a logical sequence."
            )
        return len(value)

    def _calculate_offset_string_positions(
        self,
        values: Sequence[Mapping[str, object]],
        *,
        encoding: str,
        terminator: bytes,
        alignment: int,
        minimum_padding: int,
    ) -> list[int]:
        return self._connection.calculate_offset_string_positions(
            values,
            encoding=encoding,
            terminator=terminator,
            alignment=alignment,
            minimum_padding=minimum_padding,
        )

    @staticmethod
    def _dependency_values(
        value: object,
        *,
        column: str,
    ) -> list[object]:
        if isinstance(value, pd.DataFrame):
            if column not in value.columns:
                raise ValueError(
                    f"Dependency DataFrame does not contain column '{column}'."
                )
            return value[column].tolist()
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError("Rule dependency must decode to a sequence.")
        return list(value)

    @staticmethod
    def _pipeline_sequence(
        value: object,
        *,
        method_name: str,
    ) -> list[object]:
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError(f"{method_name} expects a sequence.")
        return list(value)

    def _dependency_entry(
        self,
        entries: Mapping[str, ContainerEntry],
        template: str,
        language: str | None,
    ) -> ContainerEntry:
        dependency = self._resolve_dependency(template, language)
        entry = next(
            (
                item
                for path, item in entries.items()
                if self._path_matches_suffix(path, dependency)
            ),
            None,
        )
        if entry is None:
            raise KeyError(f"Missing dependency resource: {dependency}")
        return entry

    @staticmethod
    def _resolve_dependency(template: str, language: str | None) -> str:
        if "[lang]" not in template:
            return template
        if language is None:
            raise ValueError(f"Dependency template '{template}' requires a language.")
        return template.replace("[lang]", language)

    def _create_rule_context(
        self,
        rule: SubfileRule,
        *,
        relative_path: str,
        language: str | None,
        metadata: Mapping[str, object] | None = None,
    ) -> RuleProcessingContext:
        return RuleProcessingContext(
            repository=self,
            rule=rule,
            relative_path=relative_path,
            language=language,
            decode_params=self._resolve_parameter_values(
                rule.decode_params,
                language,
            ),
            encode_params=self._resolve_parameter_values(
                rule.encode_params,
                language,
            ),
            metadata=dict(metadata or {}),
        )

    def _resolve_parameter_values(
        self,
        parameters: Mapping[str, Any],
        language: str | None,
    ) -> dict[str, Any]:
        mutable = deep_thaw(parameters)
        if not isinstance(mutable, dict):
            raise TypeError("Rule parameters must thaw to a dictionary.")
        return {
            key: (
                language_encoding(language)
                if key == "encoding" and value == "language"
                else value
            )
            for key, value in mutable.items()
        }

    @staticmethod
    def _resource_representation(
        relative_path: str,
        codec_name: str,
    ) -> tuple[str, str]:
        if codec_name in TABLE_CODEC_OPERATIONS:
            return "table", "table"
        if codec_name == "text":
            return "text", "text"
        suffix = Path(relative_path.replace("\\", "/")).suffix.casefold()
        if suffix in IMAGE_EXTENSIONS:
            return "image", "binary"
        if suffix in AUDIO_EXTENSIONS:
            return "audio", "binary"
        return "binary", "binary"

    @staticmethod
    def _archive_entry_stays_raw(rule: SubfileRule) -> bool:
        return rule.codec_name in {"container"}

    @staticmethod
    def _requires_archive_context(rule: SubfileRule) -> bool:
        return any(
            step.method_name
            in {
                "inject_offset_dependency",
                "limit_parameter_by_dependency",
            }
            for step in rule.pre_decode
        )

    @staticmethod
    def _require_table(resource: ProjectResource) -> pd.DataFrame:
        return GameRepository._require_dataframe(resource.value)

    @staticmethod
    def _require_dataframe(value: object) -> pd.DataFrame:
        if not isinstance(value, pd.DataFrame):
            raise TypeError("A structured resource requires a DataFrame.")
        return value

    @staticmethod
    def _normalize(path: str) -> str:
        return path.replace("/", "\\").casefold()

    @classmethod
    def _path_matches_suffix(cls, path: str, suffix: str) -> bool:
        normalized_path = cls._normalize(path)
        normalized_suffix = cls._normalize(suffix).lstrip("\\")
        return normalized_path == normalized_suffix or normalized_path.endswith(
            f"\\{normalized_suffix}"
        )
