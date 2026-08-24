from __future__ import annotations

import hashlib
import logging
import re
import struct
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

    @property
    def root(self) -> Path:
        return self._connection.root

    def ensure_root(self) -> Path:
        return self._connection.ensure_root()

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
        return self._connection.write_binary(
            file_name,
            self.encode_deck_resource(resource),
        )

    def encode_deck_resource(self, resource: ProjectResource) -> bytes:
        table = self._require_table(resource)
        return self._connection.encode_deck(
            DeckFile(card_ids=table["card_id"].astype(int).tolist())
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
        return self._connection.write_binary(
            file_name,
            self.encode_raw_resource(resource),
        )

    @staticmethod
    def encode_raw_resource(resource: ProjectResource) -> bytes:
        return bytes(resource.value)

    def read_executable_resource(
        self,
        file_name: str,
        workspace_path: str,
    ) -> ProjectResource:
        data = self._connection.read_executable(file_name)
        matched = self._match_rule(workspace_path)
        value: object = data
        if matched is not None:
            value = self._decode_rule_value(
                matched[0],
                data,
                self._match_language(matched),
                {},
                relative_path=workspace_path,
            )
        if not isinstance(value, bytes):
            raise TypeError(
                f"Executable rule for '{workspace_path}' must decode to bytes."
            )
        return ProjectResource(
            ProjectFileRecord(
                source_file=file_name,
                relative_path=workspace_path,
                workspace_path=workspace_path,
                file_kind="exe",
                storage_format="binary",
            ),
            value,
        )

    def write_executable_resource(
        self,
        file_name: str,
        resource: ProjectResource,
        *,
        metadata: Mapping[str, object] | None = None,
        icon_data: bytes | None = None,
    ) -> Path:
        resources = {self._normalize(resource.record.relative_path): resource}
        operation_metadata: dict[str, object] = {
            "resources": resources,
            "generated_values": {},
            "generation_stack": [],
        }
        if metadata is not None:
            operation_metadata.update(metadata)
        profile = self._configured_executable_capacity_profile(
            resource.record.relative_path
        )
        data = self._encode_resource(
            resource,
            resources,
            metadata=operation_metadata,
        )
        record_count, _ = self._executable_capacity_metadata(operation_metadata)
        legacy_count = int(profile["record_counts"]["legacy"])
        if record_count > legacy_count:
            self.verify_executable_card_capacity(
                data,
                card_record_count=record_count,
                profile=profile,
            )
        path = self._connection.write_executable(file_name, data)
        if icon_data is not None:
            self._connection.update_executable_icon(file_name, icon_data)
        if record_count > legacy_count:
            written = self._connection.read_executable(file_name)
            self.verify_executable_card_capacity(
                written,
                card_record_count=record_count,
                profile=profile,
            )
        return path

    def preflight_executable_resource(
        self,
        resource: ProjectResource,
        *,
        metadata: Mapping[str, object],
    ) -> dict[str, int]:
        """Run an executable's configured encode pipeline without writing it."""

        if not isinstance(metadata, Mapping):
            raise TypeError("Executable preflight metadata must be a mapping.")
        resources = {self._normalize(resource.record.relative_path): resource}
        operation_metadata: dict[str, object] = {
            "resources": resources,
            "generated_values": {},
            "generation_stack": [],
        }
        operation_metadata.update(metadata)
        profile = self._configured_executable_capacity_profile(
            resource.record.relative_path
        )
        data = self._encode_resource(
            resource,
            resources,
            metadata=operation_metadata,
        )
        record_count, capacity_plan = self._executable_capacity_metadata(
            operation_metadata
        )
        derived = self._calculate_executable_card_capacity_values(record_count, profile)
        self._validate_executable_capacity_plan(capacity_plan, derived)
        legacy_count = int(profile["record_counts"]["legacy"])
        if record_count > legacy_count:
            self.verify_executable_card_capacity(
                data,
                card_record_count=record_count,
                profile=profile,
            )
        return derived

    def _configured_executable_capacity_profile(
        self,
        relative_path: str | Path,
    ) -> Mapping[str, object]:
        rule = self.find_rule(relative_path)
        capacity_steps = tuple(
            step
            for step in rule.pre_encode
            if step.method_name == "patch_executable_card_capacity"
        )
        if len(capacity_steps) != 1:
            raise ValueError(
                f"Executable rule for '{relative_path}' must contain exactly one "
                "card-capacity pre-encode step."
            )
        params = deep_thaw(capacity_steps[0].params)
        profile = params.get("profile")
        if not isinstance(profile, Mapping):
            raise TypeError("Executable capacity step profile must be a mapping.")
        return profile

    @staticmethod
    def validate_executable_icon(icon_data: bytes) -> None:
        from yugioh_editor.repositories.game.windows_icon_resources import (
            validate_icon_data,
        )

        validate_icon_data(icon_data)

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
    def patch_executable_card_capacity(
        value: object,
        *,
        context: RuleProcessingContext,
        profile: Mapping[str, object],
    ) -> bytes:
        source = GameRepository._require_executable_bytes(value)
        record_count, capacity_plan = GameRepository._executable_capacity_metadata(
            context.metadata
        )
        derived = GameRepository.preflight_executable_card_capacity(
            source,
            card_record_count=record_count,
            card_capacity_plan=capacity_plan,
            profile=profile,
        )
        record_counts = GameRepository._require_executable_mapping(
            profile["record_counts"], "record_counts"
        )
        if record_count == int(record_counts["legacy"]):
            return source

        source_pe = GameRepository._parse_executable_pe(source)
        output = bytearray(source)

        for group in GameRepository._profile_sequence(
            profile, "state_relocation_groups"
        ):
            width = int(group["value_width"])
            replacement = int(group["replacement"]).to_bytes(width, "little")
            for site in GameRepository._mapping_sequence(group["sites"], "sites"):
                offset = GameRepository._executable_va_to_file_offset(
                    source,
                    source_pe,
                    int(site["va"]),
                    len(site["expected"]),
                )
                start = offset + int(site["value_offset"])
                output[start : start + width] = replacement

        for field in (
            "snapshot_patches",
            "fixed_patch_sites",
            "hooks",
            "alias_consumer_patches",
        ):
            for site in GameRepository._profile_sequence(profile, field):
                GameRepository._write_executable_va_bytes(
                    output,
                    source_pe,
                    int(site["va"]),
                    bytes(site["replacement"]),
                )

        for site in GameRepository._profile_sequence(profile, "dynamic_patch_sites"):
            width = int(site["value_width"])
            value_name = str(site["value_name"])
            encoded = derived[value_name].to_bytes(width, "little")
            offset = GameRepository._executable_va_to_file_offset(
                source,
                source_pe,
                int(site["va"]),
                len(site["expected"]),
            )
            start = offset + int(site["value_offset"])
            output[start : start + width] = encoded

        GameRepository._install_executable_step8_sections(output, profile, source_pe)
        result = bytes(output)
        GameRepository._verify_extended_executable(
            result,
            card_record_count=record_count,
            profile=profile,
            require_profile_raw_layout=True,
        )
        GameRepository._validate_executable_changed_regions(
            source,
            result,
            profile,
            source_pe,
        )
        return result

    @staticmethod
    def preflight_executable_card_capacity(
        value: object,
        *,
        card_record_count: int,
        card_capacity_plan: Mapping[str, object] | None = None,
        profile: Mapping[str, object],
    ) -> dict[str, int]:
        """Validate a source executable and return independently derived bounds."""

        source = GameRepository._require_executable_bytes(value)
        GameRepository._validate_executable_card_capacity_profile(profile)
        derived = GameRepository._calculate_executable_card_capacity_values(
            card_record_count,
            profile,
        )
        GameRepository._validate_executable_capacity_plan(
            card_capacity_plan,
            derived,
        )
        record_counts = GameRepository._require_executable_mapping(
            profile["record_counts"], "record_counts"
        )
        if card_record_count == int(record_counts["legacy"]):
            return derived
        GameRepository._validate_stock_executable(source, profile)
        return derived

    @staticmethod
    def verify_executable_card_capacity(
        value: object,
        *,
        card_record_count: int,
        profile: Mapping[str, object],
    ) -> None:
        """Validate Step 8 runtime structure using the executable's current PE map."""

        result = GameRepository._require_executable_bytes(value)
        GameRepository._validate_executable_card_capacity_profile(profile)
        record_counts = GameRepository._require_executable_mapping(
            profile["record_counts"], "record_counts"
        )
        GameRepository._calculate_executable_card_capacity_values(
            card_record_count,
            profile,
        )
        if card_record_count == int(record_counts["legacy"]):
            return
        GameRepository._verify_extended_executable(
            result,
            card_record_count=card_record_count,
            profile=profile,
            require_profile_raw_layout=False,
        )

    @staticmethod
    def _require_executable_bytes(value: object) -> bytes:
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("Executable capacity input must be bytes or bytearray.")
        return bytes(value)

    @staticmethod
    def _executable_capacity_metadata(
        metadata: object,
    ) -> tuple[int, Mapping[str, object] | None]:
        if not isinstance(metadata, Mapping):
            raise TypeError("Executable encode metadata must be a mapping.")
        if "card_record_count" not in metadata:
            raise ValueError(
                "Executable encode metadata is missing 'card_record_count'."
            )
        record_count = metadata["card_record_count"]
        if type(record_count) is not int:
            raise TypeError("'card_record_count' must be an integer and not bool.")
        capacity_plan = metadata.get("card_capacity_plan")
        if capacity_plan is not None and not isinstance(capacity_plan, Mapping):
            raise TypeError("'card_capacity_plan' must be a mapping when supplied.")
        return record_count, capacity_plan

    @staticmethod
    def _calculate_executable_card_capacity_values(
        record_count: int,
        profile: Mapping[str, object],
    ) -> dict[str, int]:
        if type(record_count) is not int:
            raise TypeError(
                "Executable card record count must be an integer and not bool."
            )
        record_counts = GameRepository._require_executable_mapping(
            profile.get("record_counts"), "record_counts"
        )
        legacy = GameRepository._mapping_integer(
            record_counts, "legacy", "record_counts"
        )
        minimum = GameRepository._mapping_integer(
            record_counts, "minimum_extended", "record_counts"
        )
        maximum = GameRepository._mapping_integer(
            record_counts, "maximum", "record_counts"
        )
        if record_count < legacy:
            raise ValueError(
                "Executable profile requires at least "
                f"{legacy} card records; got {record_count}."
            )
        if record_count > maximum:
            raise ValueError(
                f"Executable profile supports at most {maximum} card records; "
                f"got {record_count}."
            )
        if legacy < record_count < minimum:
            raise ValueError(
                "Executable extended card record counts start at "
                f"{minimum}; got {record_count}."
            )
        layout = GameRepository._require_executable_mapping(
            profile.get("runtime_layout"), "runtime_layout"
        )
        state_base = GameRepository._mapping_integer(
            layout, "state_base", "runtime_layout"
        )
        state_record_size = GameRepository._mapping_integer(
            layout, "state_record_size", "runtime_layout"
        )
        derived = {
            "maximum_active_slot": record_count - 1,
            "exclusive_upper_bound": record_count,
            "active_state_end_address": state_base + record_count * state_record_size,
        }
        maximum_end = (
            state_base + (int(layout["maximum_active_slot"]) + 1) * state_record_size
        )
        if derived["active_state_end_address"] > maximum_end:
            raise ValueError(
                "Derived active card-state end exceeds the Step 8 runtime bound "
                f"0x{maximum_end:08X}."
            )
        return derived

    @staticmethod
    def _validate_executable_capacity_plan(
        supplied: Mapping[str, object] | None,
        derived: Mapping[str, int],
    ) -> None:
        if supplied is None:
            return
        expected_fields = set(derived)
        if set(supplied) != expected_fields:
            missing = expected_fields.difference(supplied)
            unknown = set(supplied).difference(expected_fields)
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(sorted(missing)))
            if unknown:
                details.append("unknown " + ", ".join(sorted(unknown)))
            raise ValueError(
                "'card_capacity_plan' fields do not match the executable plan: "
                + "; ".join(details)
                + "."
            )
        for name, expected in derived.items():
            actual = supplied[name]
            if type(actual) is not int:
                raise TypeError(
                    f"'card_capacity_plan.{name}' must be an integer and not bool."
                )
            if actual != expected:
                raise ValueError(
                    f"Executable capacity plan mismatch for '{name}': "
                    f"expected {expected}, got {actual}."
                )

    @staticmethod
    def _require_executable_mapping(
        value: object,
        label: str,
    ) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError(f"Executable profile '{label}' must be a mapping.")
        return value

    @staticmethod
    def _mapping_sequence(
        value: object,
        label: str,
    ) -> tuple[Mapping[str, object], ...]:
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError(f"Executable profile '{label}' must be a sequence.")
        result: list[Mapping[str, object]] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise TypeError(
                    f"Executable profile '{label}[{index}]' must be a mapping."
                )
            result.append(item)
        return tuple(result)

    @staticmethod
    def _profile_sequence(
        profile: Mapping[str, object],
        field: str,
    ) -> tuple[Mapping[str, object], ...]:
        return GameRepository._mapping_sequence(profile.get(field), field)

    @staticmethod
    def _require_executable_fields(
        value: Mapping[str, object],
        fields: set[str],
        label: str,
    ) -> None:
        actual = set(value)
        missing = fields.difference(actual)
        unknown = actual.difference(fields)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append(
                "unknown " + ", ".join(sorted(str(item) for item in unknown))
            )
        if details:
            raise ValueError(
                f"Executable profile '{label}' fields are invalid: "
                + "; ".join(details)
                + "."
            )

    @staticmethod
    def _mapping_integer(
        value: Mapping[str, object],
        field: str,
        label: str,
    ) -> int:
        result = value.get(field)
        if type(result) is not int:
            raise TypeError(
                f"Executable profile '{label}.{field}' must be an integer and not bool."
            )
        if result < 0:
            raise ValueError(
                f"Executable profile '{label}.{field}' cannot be negative."
            )
        return result

    @staticmethod
    def _mapping_bytes(
        value: Mapping[str, object],
        field: str,
        label: str,
        *,
        allow_empty: bool = False,
    ) -> bytes:
        result = value.get(field)
        if not isinstance(result, bytes):
            raise TypeError(f"Executable profile '{label}.{field}' must be bytes.")
        if not allow_empty and not result:
            raise ValueError(f"Executable profile '{label}.{field}' cannot be empty.")
        return result

    @staticmethod
    def _mapping_text(
        value: Mapping[str, object],
        field: str,
        label: str,
    ) -> str:
        result = value.get(field)
        if not isinstance(result, str) or not result:
            raise ValueError(
                f"Executable profile '{label}.{field}' must be non-empty text."
            )
        return result

    @staticmethod
    def _validate_executable_sha256(value: object, label: str) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None
        ):
            raise ValueError(
                f"Executable profile '{label}' must be a 64-character SHA-256 digest."
            )
        return value.casefold()

    @staticmethod
    def _validate_executable_section_descriptor(
        section: Mapping[str, object],
        label: str,
        *,
        allow_fill: bool,
    ) -> dict[str, object]:
        fields = {
            "name",
            "virtual_size",
            "virtual_address",
            "raw_size",
            "raw_pointer",
            "characteristics",
        }
        if allow_fill:
            fields.add("fill_byte")
        GameRepository._require_executable_fields(section, fields, label)
        name = GameRepository._mapping_text(section, "name", label)
        try:
            encoded_name = bytes(name, "ascii")
        except UnicodeEncodeError as error:
            raise ValueError(
                f"Executable profile '{label}.name' must be ASCII."
            ) from error
        if len(encoded_name) > 8:
            raise ValueError(
                f"Executable profile '{label}.name' exceeds eight PE bytes."
            )
        result: dict[str, object] = {"name": name}
        for field in (
            "virtual_size",
            "virtual_address",
            "raw_size",
            "raw_pointer",
            "characteristics",
        ):
            integer = GameRepository._mapping_integer(section, field, label)
            if integer > 0xFFFFFFFF:
                raise ValueError(
                    f"Executable profile '{label}.{field}' exceeds a PE DWORD."
                )
            result[field] = integer
        if allow_fill:
            fill = GameRepository._mapping_integer(section, "fill_byte", label)
            if fill > 0xFF:
                raise ValueError(
                    f"Executable profile '{label}.fill_byte' must fit one byte."
                )
            result["fill_byte"] = fill
        return result

    @staticmethod
    def _validate_executable_section_geometry(
        sections: Sequence[Mapping[str, object]],
        *,
        section_alignment: int,
        file_alignment: int,
        file_size: int | None,
        label: str,
    ) -> None:
        names: set[str] = set()
        virtual_regions: list[tuple[int, int, str]] = []
        raw_regions: list[tuple[int, int, str]] = []
        for section in sections:
            name = str(section["name"])
            folded = name.casefold()
            if folded in names:
                raise ValueError(f"Executable {label} has duplicate section '{name}'.")
            names.add(folded)
            virtual_address = int(section["virtual_address"])
            virtual_size = int(section["virtual_size"])
            raw_pointer = int(section["raw_pointer"])
            raw_size = int(section["raw_size"])
            if virtual_address % section_alignment:
                raise ValueError(
                    f"Executable {label} section '{name}' has an unaligned RVA."
                )
            if raw_size:
                if raw_pointer % file_alignment or raw_size % file_alignment:
                    raise ValueError(
                        f"Executable {label} section '{name}' has unaligned raw data."
                    )
                raw_end = raw_pointer + raw_size
                if file_size is not None and raw_end > file_size:
                    raise ValueError(
                        f"Executable {label} section '{name}' extends past the file."
                    )
                raw_regions.append((raw_pointer, raw_end, name))
            elif raw_pointer:
                raise ValueError(
                    f"Executable {label} section '{name}' has a raw pointer "
                    "without data."
                )
            virtual_extent = GameRepository._align_executable_value(
                max(virtual_size, raw_size), section_alignment
            )
            if virtual_extent:
                virtual_regions.append(
                    (virtual_address, virtual_address + virtual_extent, name)
                )
        for regions, kind in ((virtual_regions, "virtual"), (raw_regions, "raw")):
            ordered = sorted(regions)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if current[0] < previous[1]:
                    raise ValueError(
                        f"Executable {label} {kind} sections overlap: "
                        f"'{previous[2]}' and '{current[2]}'."
                    )

    @staticmethod
    def _validate_executable_card_capacity_profile(
        profile: Mapping[str, object],
    ) -> None:
        if not isinstance(profile, Mapping):
            raise TypeError("Executable card-capacity profile must be a mapping.")
        top_fields = {
            "source",
            "record_counts",
            "runtime_layout",
            "pe_sections",
            "pe_header_updates",
            "state_relocation_groups",
            "snapshot_patches",
            "fixed_patch_sites",
            "hooks",
            "helper_fragments",
            "helper_section_sha256",
            "alias_consumer_patches",
            "dynamic_patch_sites",
            "legacy_aliases",
            "invariant_sites",
            "known_false_matches",
        }
        GameRepository._require_executable_fields(profile, top_fields, "profile")

        source = GameRepository._require_executable_mapping(profile["source"], "source")
        GameRepository._require_executable_fields(
            source, {"sha256", "size", "pe"}, "source"
        )
        GameRepository._validate_executable_sha256(source["sha256"], "source.sha256")
        source_size = GameRepository._mapping_integer(source, "size", "source")
        if source_size == 0:
            raise ValueError("Executable profile 'source.size' must be positive.")

        pe = GameRepository._require_executable_mapping(source["pe"], "source.pe")
        pe_fields = {
            "dos_magic",
            "pe_offset",
            "signature",
            "machine",
            "optional_header_size",
            "optional_header_magic",
            "image_base",
            "section_alignment",
            "file_alignment",
            "number_of_sections",
            "size_of_code",
            "size_of_initialized_data",
            "size_of_uninitialized_data",
            "size_of_image",
            "size_of_headers",
            "section_table_offset",
            "section_table_end",
            "zero_header_slack_size",
            "sections",
        }
        GameRepository._require_executable_fields(pe, pe_fields, "source.pe")
        if GameRepository._mapping_bytes(pe, "dos_magic", "source.pe") != b"MZ":
            raise ValueError("Executable profile source DOS magic must be 'MZ'.")
        if GameRepository._mapping_bytes(pe, "signature", "source.pe") != b"PE\x00\x00":
            raise ValueError("Executable profile source PE signature is invalid.")
        pe_values = {
            field: GameRepository._mapping_integer(pe, field, "source.pe")
            for field in pe_fields.difference({"dos_magic", "signature", "sections"})
        }
        word_fields = {
            "machine",
            "optional_header_size",
            "optional_header_magic",
            "number_of_sections",
        }
        for field, integer in pe_values.items():
            limit = 0xFFFF if field in word_fields else 0xFFFFFFFF
            if integer > limit:
                raise ValueError(
                    f"Executable profile 'source.pe.{field}' exceeds its PE width."
                )
        if pe_values["optional_header_magic"] != 0x10B:
            raise ValueError(
                "Executable Step 8 profile requires a PE32 optional header."
            )
        if pe_values["section_alignment"] <= 0 or pe_values["file_alignment"] <= 0:
            raise ValueError("Executable PE alignments must be positive.")
        if pe_values["section_alignment"] & (
            pe_values["section_alignment"] - 1
        ) or pe_values["file_alignment"] & (pe_values["file_alignment"] - 1):
            raise ValueError("Executable PE alignments must be powers of two.")
        if pe_values["size_of_headers"] % pe_values["file_alignment"]:
            raise ValueError("Executable PE SizeOfHeaders is not file-aligned.")
        if pe_values["size_of_headers"] > source_size:
            raise ValueError("Executable PE headers extend past the source file.")
        if pe_values["number_of_sections"] <= 0:
            raise ValueError("Executable source must declare at least one PE section.")
        if pe_values["section_table_offset"] != (
            pe_values["pe_offset"] + 24 + pe_values["optional_header_size"]
        ):
            raise ValueError("Executable source section-table offset is inconsistent.")
        if pe_values["section_table_end"] != (
            pe_values["section_table_offset"] + pe_values["number_of_sections"] * 40
        ):
            raise ValueError("Executable source section-table end is inconsistent.")
        if pe_values["zero_header_slack_size"] != (
            pe_values["size_of_headers"] - pe_values["section_table_end"]
        ):
            raise ValueError("Executable source header-slack size is inconsistent.")
        source_sections_raw = GameRepository._mapping_sequence(
            pe["sections"], "source.pe.sections"
        )
        if len(source_sections_raw) != pe_values["number_of_sections"]:
            raise ValueError(
                "Executable source section count does not match its PE header."
            )
        source_sections = tuple(
            GameRepository._validate_executable_section_descriptor(
                section,
                f"source.pe.sections[{index}]",
                allow_fill=False,
            )
            for index, section in enumerate(source_sections_raw)
        )
        GameRepository._validate_executable_section_geometry(
            source_sections,
            section_alignment=pe_values["section_alignment"],
            file_alignment=pe_values["file_alignment"],
            file_size=source_size,
            label="source profile",
        )
        if any(
            int(section["raw_size"])
            and int(section["raw_pointer"]) < pe_values["size_of_headers"]
            for section in source_sections
        ):
            raise ValueError("Executable source section raw data overlaps PE headers.")

        record_counts = GameRepository._require_executable_mapping(
            profile["record_counts"], "record_counts"
        )
        GameRepository._require_executable_fields(
            record_counts,
            {"legacy", "minimum_extended", "maximum"},
            "record_counts",
        )
        legacy = GameRepository._mapping_integer(
            record_counts, "legacy", "record_counts"
        )
        minimum = GameRepository._mapping_integer(
            record_counts, "minimum_extended", "record_counts"
        )
        maximum = GameRepository._mapping_integer(
            record_counts, "maximum", "record_counts"
        )
        if (legacy, minimum, maximum) != (1115, 1116, 4095):
            raise ValueError(
                "Executable Step 8 record-count contract must be 1115/1116/4095."
            )

        layout = GameRepository._require_executable_mapping(
            profile["runtime_layout"], "runtime_layout"
        )
        layout_fields = {
            "state_base",
            "state_record_size",
            "state_word_capacity",
            "state_structural_end",
            "snapshot_base",
            "snapshot_byte_capacity",
            "snapshot_end",
            "helper_base",
            "helper_size",
            "legacy_persistent_slot_count",
            "legacy_bridge_byte_count",
            "maximum_active_slot",
            "invalid_slot",
            "maximum_card_id",
            "invalid_card_id",
        }
        GameRepository._require_executable_fields(
            layout, layout_fields, "runtime_layout"
        )
        layout_values = {
            field: GameRepository._mapping_integer(layout, field, "runtime_layout")
            for field in layout_fields
        }
        if layout_values["state_record_size"] != 2:
            raise ValueError("Executable Step 8 state records must be two bytes.")
        if (
            layout_values["state_base"]
            + (
                layout_values["state_word_capacity"]
                * layout_values["state_record_size"]
            )
            != layout_values["state_structural_end"]
        ):
            raise ValueError("Executable Step 8 state geometry is inconsistent.")
        if layout_values["snapshot_base"] != layout_values["state_structural_end"]:
            raise ValueError("Executable Step 8 snapshot must follow the state region.")
        if (
            layout_values["snapshot_base"] + layout_values["snapshot_byte_capacity"]
            != layout_values["snapshot_end"]
        ):
            raise ValueError("Executable Step 8 snapshot geometry is inconsistent.")
        if layout_values["helper_base"] != layout_values["snapshot_end"]:
            raise ValueError(
                "Executable Step 8 helper must follow the snapshot region."
            )
        if layout_values["maximum_active_slot"] != maximum - 1:
            raise ValueError("Executable maximum active slot is inconsistent.")
        if layout_values["invalid_slot"] != maximum:
            raise ValueError("Executable invalid slot is inconsistent.")
        if (
            layout_values["maximum_card_id"] != maximum - 1
            or layout_values["invalid_card_id"] != maximum
        ):
            raise ValueError("Executable card-ID bounds are inconsistent.")
        if layout_values["legacy_bridge_byte_count"] != (
            layout_values["legacy_persistent_slot_count"]
            * layout_values["state_record_size"]
        ):
            raise ValueError("Executable legacy bridge size is inconsistent.")
        if maximum > layout_values["state_word_capacity"] - 1:
            raise ValueError(
                "Executable supported count exceeds the state-slot contract."
            )

        target_sections_raw = GameRepository._mapping_sequence(
            profile["pe_sections"], "pe_sections"
        )
        if len(target_sections_raw) != 2:
            raise ValueError(
                "Executable Step 8 profile must declare exactly two PE sections."
            )
        target_sections: list[dict[str, object]] = []
        for index, section in enumerate(target_sections_raw):
            name = section.get("name")
            target_sections.append(
                GameRepository._validate_executable_section_descriptor(
                    section,
                    f"pe_sections[{index}]",
                    allow_fill=name == ".ygsx",
                )
            )
        if [section["name"] for section in target_sections] != [".ygst", ".ygsx"]:
            raise ValueError("Executable Step 8 sections must be '.ygst' then '.ygsx'.")
        ygst, ygsx = target_sections
        if int(ygst["raw_size"]) != 0 or int(ygst["raw_pointer"]) != 0:
            raise ValueError("Executable '.ygst' must be an uninitialized section.")
        if int(ygsx["raw_size"]) == 0 or int(ygsx["raw_pointer"]) == 0:
            raise ValueError("Executable '.ygsx' must have initialized raw data.")
        if int(ygsx["fill_byte"]) != 0x90:
            raise ValueError(
                "Executable '.ygsx' canonical fill byte must be NOP (0x90)."
            )
        if (
            pe_values["image_base"] + int(ygst["virtual_address"])
            != layout_values["state_base"]
        ):
            raise ValueError("Executable '.ygst' does not begin at the state base.")
        if int(ygst["virtual_size"]) != (
            layout_values["helper_base"] - layout_values["state_base"]
        ):
            raise ValueError(
                "Executable '.ygst' does not span state plus snapshot storage."
            )
        if (
            pe_values["image_base"] + int(ygsx["virtual_address"])
            != layout_values["helper_base"]
        ):
            raise ValueError("Executable '.ygsx' does not begin at the helper base.")
        if int(ygsx["virtual_size"]) != layout_values["helper_size"]:
            raise ValueError(
                "Executable '.ygsx' virtual size does not match helper storage."
            )
        if int(ygsx["raw_size"]) != layout_values["helper_size"]:
            raise ValueError(
                "Executable '.ygsx' raw size does not match helper storage."
            )
        GameRepository._validate_executable_section_geometry(
            (*source_sections, *target_sections),
            section_alignment=pe_values["section_alignment"],
            file_alignment=pe_values["file_alignment"],
            file_size=None,
            label="combined profile",
        )

        updates = GameRepository._require_executable_mapping(
            profile["pe_header_updates"], "pe_header_updates"
        )
        update_fields = {
            "number_of_sections",
            "size_of_code",
            "size_of_uninitialized_data",
            "size_of_image",
            "output_size_before_icon",
        }
        GameRepository._require_executable_fields(
            updates, update_fields, "pe_header_updates"
        )
        update_values = {
            field: GameRepository._mapping_integer(updates, field, "pe_header_updates")
            for field in update_fields
        }
        for field, integer in update_values.items():
            limit = 0xFFFF if field == "number_of_sections" else 0xFFFFFFFF
            if integer > limit:
                raise ValueError(
                    f"Executable profile 'pe_header_updates.{field}' exceeds "
                    "its PE width."
                )
        if update_values["number_of_sections"] != pe_values["number_of_sections"] + 2:
            raise ValueError("Executable updated PE section count is inconsistent.")
        if update_values["size_of_code"] != pe_values["size_of_code"] + int(
            ygsx["raw_size"]
        ):
            raise ValueError("Executable updated SizeOfCode is inconsistent.")
        if update_values["size_of_uninitialized_data"] != (
            pe_values["size_of_uninitialized_data"] + int(ygst["virtual_size"])
        ):
            raise ValueError(
                "Executable updated uninitialized-data size is inconsistent."
            )
        image_end = int(ygsx["virtual_address"]) + int(ygsx["virtual_size"])
        expected_image_size = GameRepository._align_executable_value(
            image_end, pe_values["section_alignment"]
        )
        if update_values["size_of_image"] != expected_image_size:
            raise ValueError("Executable updated SizeOfImage is inconsistent.")
        if update_values["output_size_before_icon"] != int(ygsx["raw_pointer"]) + int(
            ygsx["raw_size"]
        ):
            raise ValueError("Executable pre-icon output size is inconsistent.")
        if int(ygsx["raw_pointer"]) != source_size:
            raise ValueError(
                "Executable helper raw data must append to the stock file."
            )

        GameRepository._validate_executable_patch_contract(
            profile,
            layout_values=layout_values,
            source_pe_values=pe_values,
            source_sections=source_sections,
            source_size=source_size,
        )

    @staticmethod
    def _validate_executable_patch_contract(
        profile: Mapping[str, object],
        *,
        layout_values: Mapping[str, int],
        source_pe_values: Mapping[str, int],
        source_sections: Sequence[Mapping[str, object]],
        source_size: int,
    ) -> None:
        mutation_regions: list[tuple[int, int, str]] = []
        observation_regions: list[tuple[int, int, str]] = []
        maximum = layout_values["invalid_slot"]

        relocation_groups = GameRepository._profile_sequence(
            profile, "state_relocation_groups"
        )
        group_contract = (
            ("state_base", 59, layout_values["state_base"]),
            ("state_high_byte_base", 4, layout_values["state_base"] + 1),
            ("state_slot1_base", 5, layout_values["state_base"] + 2),
            ("state_structural_end", 1, layout_values["state_structural_end"]),
        )
        if len(relocation_groups) != len(group_contract):
            raise ValueError(
                "Executable Step 8 profile must declare four relocation groups."
            )
        relocation_site_count = 0
        for group_index, (group, contract) in enumerate(
            zip(relocation_groups, group_contract, strict=True)
        ):
            label = f"state_relocation_groups[{group_index}]"
            GameRepository._require_executable_fields(
                group,
                {"value_name", "source_value", "replacement", "value_width", "sites"},
                label,
            )
            expected_name, expected_count, expected_replacement = contract
            name = GameRepository._mapping_text(group, "value_name", label)
            if name != expected_name:
                raise ValueError(
                    f"Executable relocation group {group_index} must be "
                    f"'{expected_name}'."
                )
            width = GameRepository._mapping_integer(group, "value_width", label)
            if width not in (1, 2, 4):
                raise ValueError(
                    f"Executable profile '{label}.value_width' is invalid."
                )
            source_value = GameRepository._mapping_integer(group, "source_value", label)
            replacement = GameRepository._mapping_integer(group, "replacement", label)
            limit = 1 << (width * 8)
            if source_value >= limit or replacement >= limit:
                raise ValueError(
                    f"Executable profile '{label}' value exceeds its width."
                )
            if replacement != expected_replacement:
                raise ValueError(
                    f"Executable relocation group '{name}' replacement is inconsistent."
                )
            sites = GameRepository._mapping_sequence(group["sites"], f"{label}.sites")
            if len(sites) != expected_count:
                raise ValueError(
                    f"Executable relocation group '{name}' must contain "
                    f"{expected_count} complete instruction windows."
                )
            for site_index, site in enumerate(sites):
                site_label = f"{label}.sites[{site_index}]"
                GameRepository._require_executable_fields(
                    site, {"va", "expected", "value_offset"}, site_label
                )
                va = GameRepository._mapping_integer(site, "va", site_label)
                expected = GameRepository._mapping_bytes(site, "expected", site_label)
                value_offset = GameRepository._mapping_integer(
                    site, "value_offset", site_label
                )
                if value_offset + width > len(expected):
                    raise ValueError(
                        f"Executable profile '{site_label}' immediate exceeds "
                        "its full window."
                    )
                if (
                    int.from_bytes(
                        expected[value_offset : value_offset + width], "little"
                    )
                    != source_value
                ):
                    raise ValueError(
                        f"Executable profile '{site_label}' does not contain "
                        "its source value."
                    )
                mutation_regions.append((va, va + len(expected), site_label))
                relocation_site_count += 1
        if relocation_site_count != 69:
            raise ValueError("Executable Step 8 profile must declare 69 relocations.")

        snapshots = GameRepository._profile_sequence(profile, "snapshot_patches")
        if tuple(site.get("name") for site in snapshots) != (
            "snapshot_copy",
            "snapshot_restore",
        ):
            raise ValueError("Executable Step 8 snapshot patch set is incomplete.")
        for index, site in enumerate(snapshots):
            label = f"snapshot_patches[{index}]"
            GameRepository._require_executable_fields(
                site,
                {"name", "va", "expected", "replacement", "successor_va"},
                label,
            )
            GameRepository._mapping_text(site, "name", label)
            va = GameRepository._mapping_integer(site, "va", label)
            expected = GameRepository._mapping_bytes(site, "expected", label)
            replacement = GameRepository._mapping_bytes(site, "replacement", label)
            successor = GameRepository._mapping_integer(site, "successor_va", label)
            if len(replacement) != len(expected):
                raise ValueError(
                    f"Executable profile '{label}' replacement length differs."
                )
            if successor != va + len(expected):
                raise ValueError(
                    f"Executable profile '{label}' successor does not follow "
                    "its full window."
                )
            mutation_regions.append((va, successor, label))

        fixed_sites = GameRepository._profile_sequence(profile, "fixed_patch_sites")
        if len(fixed_sites) != 1 or fixed_sites[0].get("name") != "card_prop_slot_mask":
            raise ValueError(
                "Executable Step 8 profile must declare its slot mask once."
            )
        for index, site in enumerate(fixed_sites):
            label = f"fixed_patch_sites[{index}]"
            GameRepository._require_executable_fields(
                site, {"name", "va", "expected", "replacement"}, label
            )
            GameRepository._mapping_text(site, "name", label)
            va = GameRepository._mapping_integer(site, "va", label)
            expected = GameRepository._mapping_bytes(site, "expected", label)
            replacement = GameRepository._mapping_bytes(site, "replacement", label)
            if len(expected) != len(replacement):
                raise ValueError(
                    f"Executable profile '{label}' replacement length differs."
                )
            mutation_regions.append((va, va + len(expected), label))

        helper_fragments = GameRepository._profile_sequence(profile, "helper_fragments")
        helper_names = (
            "legacy_save_bridge",
            "legacy_load_bridge",
            "direct_card_id_lookup",
            "canonicalize_legacy_alias",
            "canonicalize_esi_wrapper",
            "canonicalize_edi_wrapper",
            "canonicalize_ecx_wrapper",
            "legacy_alias_table",
        )
        if tuple(item.get("name") for item in helper_fragments) != helper_names:
            raise ValueError(
                "Executable Step 8 helper fragment set is incomplete or reordered."
            )
        helper_section = bytearray([0x90]) * layout_values["helper_size"]
        helper_regions: list[tuple[int, int, str]] = []
        fragments_by_name: dict[str, Mapping[str, object]] = {}
        for index, fragment in enumerate(helper_fragments):
            label = f"helper_fragments[{index}]"
            GameRepository._require_executable_fields(
                fragment, {"name", "offset", "va", "bytes"}, label
            )
            name = GameRepository._mapping_text(fragment, "name", label)
            offset = GameRepository._mapping_integer(fragment, "offset", label)
            va = GameRepository._mapping_integer(fragment, "va", label)
            fragment_bytes = GameRepository._mapping_bytes(fragment, "bytes", label)
            if va != layout_values["helper_base"] + offset:
                raise ValueError(
                    f"Executable helper fragment '{name}' VA is inconsistent."
                )
            end = offset + len(fragment_bytes)
            if end > len(helper_section):
                raise ValueError(
                    f"Executable helper fragment '{name}' exceeds '.ygsx'."
                )
            helper_regions.append((offset, end, name))
            helper_section[offset:end] = fragment_bytes
            fragments_by_name[name] = fragment
        GameRepository._validate_nonoverlapping_executable_regions(
            helper_regions, "helper fragments"
        )
        helper_digest = GameRepository._validate_executable_sha256(
            profile["helper_section_sha256"], "helper_section_sha256"
        )
        if hashlib.sha256(helper_section).hexdigest() != helper_digest:
            raise ValueError(
                "Executable canonical helper-section digest is inconsistent."
            )

        hooks = GameRepository._profile_sequence(profile, "hooks")
        if tuple(item.get("name") for item in hooks) != (
            "legacy_save_bridge",
            "legacy_load_bridge",
            "direct_card_id_lookup",
        ):
            raise ValueError("Executable Step 8 hook set is incomplete or reordered.")
        for index, hook in enumerate(hooks):
            label = f"hooks[{index}]"
            name = str(hook.get("name", ""))
            fields = {"name", "va", "expected", "replacement", "helper_va"}
            if name != "direct_card_id_lookup":
                fields.add("return_va")
            GameRepository._require_executable_fields(hook, fields, label)
            GameRepository._mapping_text(hook, "name", label)
            va = GameRepository._mapping_integer(hook, "va", label)
            expected = GameRepository._mapping_bytes(hook, "expected", label)
            replacement = GameRepository._mapping_bytes(hook, "replacement", label)
            helper_va = GameRepository._mapping_integer(hook, "helper_va", label)
            if len(expected) != len(replacement) or len(replacement) < 5:
                raise ValueError(
                    f"Executable profile '{label}' hook window is invalid."
                )
            if replacement[0] != 0xE9 or any(byte != 0x90 for byte in replacement[5:]):
                raise ValueError(
                    f"Executable profile '{label}' must be JMP plus NOP padding."
                )
            if (
                GameRepository._decode_executable_rel32_target(replacement, va, 0, 0xE9)
                != helper_va
            ):
                raise ValueError(
                    f"Executable profile '{label}' helper jump is inconsistent."
                )
            fragment = fragments_by_name[name]
            fragment_va = GameRepository._mapping_integer(
                fragment, "va", f"helper.{name}"
            )
            fragment_bytes = GameRepository._mapping_bytes(
                fragment, "bytes", f"helper.{name}"
            )
            if fragment_va != helper_va:
                raise ValueError(
                    f"Executable hook '{name}' does not target its fragment."
                )
            if name == "direct_card_id_lookup":
                if not fragment_bytes.startswith(expected):
                    raise ValueError(
                        "Executable lookup helper does not replay its displaced "
                        "prologue."
                    )
            else:
                return_va = GameRepository._mapping_integer(hook, "return_va", label)
                if return_va != va + len(expected):
                    raise ValueError(
                        f"Executable hook '{name}' return VA is inconsistent."
                    )
                replay_offset = len(fragment_bytes) - len(expected) - 5
                if (
                    replay_offset < 0
                    or fragment_bytes[replay_offset : replay_offset + len(expected)]
                    != expected
                ):
                    raise ValueError(
                        f"Executable helper '{name}' does not replay displaced bytes."
                    )
                if (
                    GameRepository._decode_executable_rel32_target(
                        fragment_bytes,
                        fragment_va,
                        len(fragment_bytes) - 5,
                        0xE9,
                    )
                    != return_va
                ):
                    raise ValueError(
                        f"Executable helper '{name}' return jump is inconsistent."
                    )
            mutation_regions.append((va, va + len(expected), label))

        aliases = profile["legacy_aliases"]
        if not isinstance(aliases, Mapping) or len(aliases) != 9:
            raise ValueError(
                "Executable Step 8 profile must declare nine legacy aliases."
            )
        alias_pairs: list[tuple[int, int]] = []
        for source_id, target_id in aliases.items():
            if type(source_id) is not int or type(target_id) is not int:
                raise TypeError(
                    "Executable legacy alias IDs must be integers and not bool."
                )
            if (
                source_id < 0
                or source_id > 0xFFFF
                or target_id < 0
                or target_id > 0xFFFF
            ):
                raise ValueError("Executable legacy alias IDs must fit unsigned WORDs.")
            if target_id != source_id - 2000:
                raise ValueError("Executable legacy alias mapping is not canonical.")
            alias_pairs.append((source_id, target_id))
        alias_table = fragments_by_name["legacy_alias_table"]
        alias_bytes = GameRepository._mapping_bytes(
            alias_table, "bytes", "legacy_alias_table"
        )
        if len(alias_bytes) != len(alias_pairs) * 2:
            raise ValueError(
                "Executable helper alias-table byte count is inconsistent."
            )
        table_ids = tuple(
            int.from_bytes(alias_bytes[offset : offset + 2], "little")
            for offset in range(0, len(alias_bytes), 2)
        )
        if table_ids != tuple(source_id for source_id, _ in alias_pairs):
            raise ValueError(
                "Executable helper alias-table order differs from the profile map."
            )
        canonicalizer = fragments_by_name["canonicalize_legacy_alias"]
        canonicalizer_bytes = GameRepository._mapping_bytes(
            canonicalizer, "bytes", "canonicalize_legacy_alias"
        )
        alias_table_va = GameRepository._mapping_integer(
            alias_table, "va", "legacy_alias_table"
        )
        if (
            len(canonicalizer_bytes) < 17
            or canonicalizer_bytes[7] != 0xB9
            or int.from_bytes(canonicalizer_bytes[8:12], "little") != len(alias_pairs)
            or canonicalizer_bytes[12] != 0xBF
            or int.from_bytes(canonicalizer_bytes[13:17], "little") != alias_table_va
        ):
            raise ValueError(
                "Executable alias canonicalizer table contract is inconsistent."
            )

        consumer_patches = GameRepository._profile_sequence(
            profile, "alias_consumer_patches"
        )
        consumer_names = (
            "comparison_esi",
            "comparison_eax",
            "deck_recipe_eax",
            "deck_recipe_scan_eax",
            "relation_block_a",
            "relation_block_b",
            "relation_edi",
            "packed_relation_eax_first",
            "packed_relation_esi_first",
            "packed_relation_eax_second",
            "packed_relation_esi_second",
        )
        if tuple(item.get("name") for item in consumer_patches) != consumer_names:
            raise ValueError(
                "Executable Step 8 alias-consumer set is incomplete or reordered."
            )
        for index, site in enumerate(consumer_patches):
            label = f"alias_consumer_patches[{index}]"
            relation = "equal_target_va" in site or "unequal_target_va" in site
            fields = {"name", "va", "expected", "replacement", "call_targets"}
            if relation:
                fields.update({"equal_target_va", "unequal_target_va"})
            GameRepository._require_executable_fields(site, fields, label)
            GameRepository._mapping_text(site, "name", label)
            va = GameRepository._mapping_integer(site, "va", label)
            expected = GameRepository._mapping_bytes(site, "expected", label)
            replacement = GameRepository._mapping_bytes(site, "replacement", label)
            if len(expected) != len(replacement):
                raise ValueError(
                    f"Executable profile '{label}' replacement length differs."
                )
            targets_value = site["call_targets"]
            if isinstance(targets_value, (str, bytes, bytearray)) or not isinstance(
                targets_value, Sequence
            ):
                raise TypeError(
                    f"Executable profile '{label}.call_targets' must be a sequence."
                )
            targets: list[int] = []
            for target_index, target in enumerate(targets_value):
                if type(target) is not int or target < 0:
                    raise TypeError(
                        f"Executable profile '{label}.call_targets[{target_index}]' "
                        "must be a non-negative integer."
                    )
                targets.append(target)
            call_offsets = (0, 9) if relation else (0,)
            if len(targets) != len(call_offsets):
                raise ValueError(
                    f"Executable profile '{label}' call-target count is invalid."
                )
            decoded_targets = tuple(
                GameRepository._decode_executable_rel32_target(
                    replacement, va, offset, 0xE8
                )
                for offset in call_offsets
            )
            if decoded_targets != tuple(targets):
                raise ValueError(
                    f"Executable profile '{label}' call targets are inconsistent."
                )
            if relation:
                if (
                    replacement[5:9] != b"\x8b\x4c\x24\x18"
                    or replacement[14:16] != b"\x3b\xc1"
                    or replacement[16] != 0x75
                    or any(byte != 0x90 for byte in replacement[18:])
                ):
                    raise ValueError(
                        f"Executable relation patch '{label}' has invalid structure."
                    )
                equal_target = GameRepository._mapping_integer(
                    site, "equal_target_va", label
                )
                unequal_target = GameRepository._mapping_integer(
                    site, "unequal_target_va", label
                )
                if equal_target != va + len(replacement):
                    raise ValueError(
                        f"Executable relation patch '{label}' equal target is "
                        "inconsistent."
                    )
                displacement = int.from_bytes(replacement[17:18], "little", signed=True)
                if va + 18 + displacement != unequal_target:
                    raise ValueError(
                        f"Executable relation patch '{label}' failure target is "
                        "inconsistent."
                    )
            elif any(byte != 0x90 for byte in replacement[5:]):
                raise ValueError(
                    f"Executable alias patch '{label}' must be CALL plus NOP padding."
                )
            mutation_regions.append((va, va + len(expected), label))

        dynamic_sites = GameRepository._profile_sequence(profile, "dynamic_patch_sites")
        if len(dynamic_sites) != 17:
            raise ValueError("Executable Step 8 profile must declare 17 dynamic sites.")
        dynamic_counts = {
            "maximum_active_slot": 0,
            "exclusive_upper_bound": 0,
            "active_state_end_address": 0,
        }
        maximum_values = {
            "maximum_active_slot": maximum - 1,
            "exclusive_upper_bound": maximum,
            "active_state_end_address": layout_values["state_base"]
            + maximum * layout_values["state_record_size"],
        }
        for index, site in enumerate(dynamic_sites):
            label = f"dynamic_patch_sites[{index}]"
            GameRepository._require_executable_fields(
                site,
                {"va", "expected", "value_offset", "value_width", "value_name"},
                label,
            )
            va = GameRepository._mapping_integer(site, "va", label)
            expected = GameRepository._mapping_bytes(site, "expected", label)
            value_offset = GameRepository._mapping_integer(site, "value_offset", label)
            width = GameRepository._mapping_integer(site, "value_width", label)
            name = GameRepository._mapping_text(site, "value_name", label)
            if width not in (1, 2, 4) or value_offset + width > len(expected):
                raise ValueError(
                    f"Executable dynamic patch '{label}' immediate is invalid."
                )
            if name not in dynamic_counts:
                raise ValueError(
                    f"Executable dynamic patch '{label}' has unknown value '{name}'."
                )
            if maximum_values[name] >= 1 << (width * 8):
                raise ValueError(
                    f"Executable dynamic patch '{label}' cannot encode its maximum."
                )
            dynamic_counts[name] += 1
            mutation_regions.append((va, va + len(expected), label))
        if dynamic_counts != {
            "maximum_active_slot": 5,
            "exclusive_upper_bound": 6,
            "active_state_end_address": 6,
        }:
            raise ValueError("Executable Step 8 dynamic-site distribution is invalid.")

        invariants = GameRepository._profile_sequence(profile, "invariant_sites")
        if len(invariants) != 2:
            raise ValueError(
                "Executable Step 8 profile must declare two invariant sites."
            )
        for index, site in enumerate(invariants):
            label = f"invariant_sites[{index}]"
            GameRepository._require_executable_fields(
                site, {"name", "va", "expected"}, label
            )
            GameRepository._mapping_text(site, "name", label)
            va = GameRepository._mapping_integer(site, "va", label)
            expected = GameRepository._mapping_bytes(site, "expected", label)
            observation_regions.append((va, va + len(expected), label))

        false_matches = GameRepository._profile_sequence(profile, "known_false_matches")
        if len(false_matches) != 4:
            raise ValueError(
                "Executable Step 8 profile must declare four known false matches."
            )
        for index, site in enumerate(false_matches):
            label = f"known_false_matches[{index}]"
            GameRepository._require_executable_fields(site, {"va", "expected"}, label)
            va = GameRepository._mapping_integer(site, "va", label)
            expected = GameRepository._mapping_bytes(site, "expected", label)
            observation_regions.append((va, va + len(expected), label))

        mapped_mutations = [
            (
                GameRepository._profile_executable_va_offset(
                    va_start,
                    va_end - va_start,
                    image_base=source_pe_values["image_base"],
                    sections=source_sections,
                    file_size=source_size,
                    label=region_label,
                ),
                va_end - va_start,
                region_label,
            )
            for va_start, va_end, region_label in mutation_regions
        ]
        GameRepository._validate_nonoverlapping_executable_regions(
            [
                (offset, offset + size, label)
                for offset, size, label in mapped_mutations
            ],
            "stock mutation windows",
        )
        mutation_va_regions = sorted(mutation_regions)
        GameRepository._validate_nonoverlapping_executable_regions(
            observation_regions,
            "stock observation windows",
        )
        for va_start, va_end, region_label in observation_regions:
            GameRepository._profile_executable_va_offset(
                va_start,
                va_end - va_start,
                image_base=source_pe_values["image_base"],
                sections=source_sections,
                file_size=source_size,
                label=region_label,
            )
            for changed_start, changed_end, changed_label in mutation_va_regions:
                if va_start < changed_end and changed_start < va_end:
                    raise ValueError(
                        f"Executable observation '{region_label}' overlaps "
                        f"mutation '{changed_label}'."
                    )

    @staticmethod
    def _decode_executable_rel32_target(
        code: bytes,
        instruction_va: int,
        opcode_offset: int,
        opcode: int,
    ) -> int:
        if opcode_offset < 0 or opcode_offset + 5 > len(code):
            raise ValueError("Executable rel32 instruction exceeds its byte window.")
        if code[opcode_offset] != opcode:
            raise ValueError(
                f"Executable rel32 instruction expected opcode 0x{opcode:02X}."
            )
        displacement = int.from_bytes(
            code[opcode_offset + 1 : opcode_offset + 5],
            "little",
            signed=True,
        )
        return instruction_va + opcode_offset + 5 + displacement

    @staticmethod
    def _align_executable_value(value: int, alignment: int) -> int:
        if alignment <= 0:
            raise ValueError("Executable alignment must be positive.")
        return ((value + alignment - 1) // alignment) * alignment

    @staticmethod
    def _validate_nonoverlapping_executable_regions(
        regions: Sequence[tuple[int, int, str]],
        label: str,
    ) -> None:
        ordered = sorted(regions, key=lambda item: (item[0], item[1], item[2]))
        for start, end, region_label in ordered:
            if start < 0 or end <= start:
                raise ValueError(
                    f"Executable {label} region '{region_label}' is empty or invalid."
                )
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current[0] < previous[1]:
                raise ValueError(
                    f"Executable {label} overlap: '{previous[2]}' and '{current[2]}'."
                )

    @staticmethod
    def _profile_executable_va_offset(
        va: int,
        size: int,
        *,
        image_base: int,
        sections: Sequence[Mapping[str, object]],
        file_size: int,
        label: str,
    ) -> int:
        if size <= 0:
            raise ValueError(f"Executable profile region '{label}' must be non-empty.")
        rva = va - image_base
        for section in sections:
            section_rva = int(section["virtual_address"])
            raw_size = int(section["raw_size"])
            delta = rva - section_rva
            if delta < 0 or delta + size > raw_size:
                continue
            offset = int(section["raw_pointer"]) + delta
            if offset + size > file_size:
                break
            return offset
        raise ValueError(
            f"Executable profile region '{label}' at VA 0x{va:08X} "
            "is not backed by complete source raw bytes."
        )

    @staticmethod
    def _parse_executable_pe(value: bytes) -> dict[str, object]:
        if len(value) < 0x40:
            raise ValueError("Executable PE source is too short for a DOS header.")
        if value[:2] != b"MZ":
            raise ValueError("Executable PE source has invalid DOS magic.")
        pe_offset = struct.unpack_from("<I", value, 0x3C)[0]
        if pe_offset < 0x40 or pe_offset + 24 > len(value):
            raise ValueError("Executable PE header offset is outside the file.")
        if value[pe_offset : pe_offset + 4] != b"PE\x00\x00":
            raise ValueError("Executable PE signature is invalid.")
        machine, number_of_sections = struct.unpack_from("<HH", value, pe_offset + 4)
        optional_header_size = struct.unpack_from("<H", value, pe_offset + 20)[0]
        optional_offset = pe_offset + 24
        optional_end = optional_offset + optional_header_size
        if optional_header_size < 0x60 or optional_end > len(value):
            raise ValueError("Executable PE optional header is incomplete.")
        optional_magic = struct.unpack_from("<H", value, optional_offset)[0]
        if optional_magic != 0x10B:
            raise ValueError("Executable Step 8 patching supports PE32 images only.")
        size_of_code = struct.unpack_from("<I", value, optional_offset + 4)[0]
        size_of_initialized_data = struct.unpack_from("<I", value, optional_offset + 8)[
            0
        ]
        size_of_uninitialized_data = struct.unpack_from(
            "<I", value, optional_offset + 12
        )[0]
        image_base = struct.unpack_from("<I", value, optional_offset + 28)[0]
        section_alignment = struct.unpack_from("<I", value, optional_offset + 32)[0]
        file_alignment = struct.unpack_from("<I", value, optional_offset + 36)[0]
        size_of_image = struct.unpack_from("<I", value, optional_offset + 56)[0]
        size_of_headers = struct.unpack_from("<I", value, optional_offset + 60)[0]
        if section_alignment == 0 or file_alignment == 0:
            raise ValueError("Executable PE contains a zero alignment.")
        section_table_offset = optional_end
        section_table_end = section_table_offset + number_of_sections * 40
        if section_table_end > len(value) or section_table_end > size_of_headers:
            raise ValueError("Executable PE section table is incomplete.")
        sections: list[dict[str, object]] = []
        for index in range(number_of_sections):
            header_offset = section_table_offset + index * 40
            raw_name = value[header_offset : header_offset + 8]
            try:
                name = str(raw_name.split(b"\x00", 1)[0], "ascii")
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"Executable PE section {index} has a non-ASCII name."
                ) from error
            (
                virtual_size,
                virtual_address,
                raw_size,
                raw_pointer,
                pointer_to_relocations,
                pointer_to_line_numbers,
                number_of_relocations,
                number_of_line_numbers,
                characteristics,
            ) = struct.unpack_from("<IIIIIIHHI", value, header_offset + 8)
            sections.append(
                {
                    "name": name,
                    "virtual_size": virtual_size,
                    "virtual_address": virtual_address,
                    "raw_size": raw_size,
                    "raw_pointer": raw_pointer,
                    "pointer_to_relocations": pointer_to_relocations,
                    "pointer_to_line_numbers": pointer_to_line_numbers,
                    "number_of_relocations": number_of_relocations,
                    "number_of_line_numbers": number_of_line_numbers,
                    "characteristics": characteristics,
                    "header_offset": header_offset,
                }
            )
        return {
            "dos_magic": value[:2],
            "pe_offset": pe_offset,
            "signature": value[pe_offset : pe_offset + 4],
            "machine": machine,
            "number_of_sections": number_of_sections,
            "optional_header_size": optional_header_size,
            "optional_header_magic": optional_magic,
            "optional_header_offset": optional_offset,
            "image_base": image_base,
            "section_alignment": section_alignment,
            "file_alignment": file_alignment,
            "size_of_code": size_of_code,
            "size_of_initialized_data": size_of_initialized_data,
            "size_of_uninitialized_data": size_of_uninitialized_data,
            "size_of_image": size_of_image,
            "size_of_headers": size_of_headers,
            "section_table_offset": section_table_offset,
            "section_table_end": section_table_end,
            "sections": tuple(sections),
            "number_of_sections_offset": pe_offset + 6,
            "size_of_code_offset": optional_offset + 4,
            "size_of_uninitialized_data_offset": optional_offset + 12,
            "size_of_image_offset": optional_offset + 56,
        }

    @staticmethod
    def _executable_va_to_file_offset(
        value: bytes | bytearray,
        pe: Mapping[str, object],
        va: int,
        size: int,
    ) -> int:
        if type(va) is not int or va < 0 or type(size) is not int or size <= 0:
            raise ValueError(
                "Executable VA mapping requires a non-negative VA and positive size."
            )
        image_base = int(pe["image_base"])
        rva = va - image_base
        sections = pe["sections"]
        assert isinstance(sections, Sequence)
        for raw_section in sections:
            assert isinstance(raw_section, Mapping)
            section_rva = int(raw_section["virtual_address"])
            raw_size = int(raw_section["raw_size"])
            delta = rva - section_rva
            if delta < 0 or delta + size > raw_size:
                continue
            offset = int(raw_section["raw_pointer"]) + delta
            if offset < 0 or offset + size > len(value):
                raise ValueError(
                    f"Executable VA 0x{va:08X} maps past the current file bytes."
                )
            return offset
        raise ValueError(
            f"Executable VA 0x{va:08X} is not backed by {size} raw byte(s)."
        )

    @staticmethod
    def _write_executable_va_bytes(
        output: bytearray,
        pe: Mapping[str, object],
        va: int,
        replacement: bytes,
    ) -> None:
        offset = GameRepository._executable_va_to_file_offset(
            output, pe, va, len(replacement)
        )
        output[offset : offset + len(replacement)] = replacement

    @staticmethod
    def _stock_executable_expected_sites(
        profile: Mapping[str, object],
    ) -> tuple[tuple[int, bytes, str], ...]:
        result: list[tuple[int, bytes, str]] = []
        for group_index, group in enumerate(
            GameRepository._profile_sequence(profile, "state_relocation_groups")
        ):
            for site_index, site in enumerate(
                GameRepository._mapping_sequence(
                    group["sites"], f"state_relocation_groups[{group_index}].sites"
                )
            ):
                result.append(
                    (
                        int(site["va"]),
                        bytes(site["expected"]),
                        f"state_relocation_groups[{group_index}].sites[{site_index}]",
                    )
                )
        for field in (
            "snapshot_patches",
            "fixed_patch_sites",
            "hooks",
            "alias_consumer_patches",
            "dynamic_patch_sites",
            "invariant_sites",
            "known_false_matches",
        ):
            for index, site in enumerate(
                GameRepository._profile_sequence(profile, field)
            ):
                result.append(
                    (int(site["va"]), bytes(site["expected"]), f"{field}[{index}]")
                )
        return tuple(result)

    @staticmethod
    def _stock_executable_mutation_sites(
        profile: Mapping[str, object],
    ) -> tuple[tuple[int, bytes, str], ...]:
        return tuple(
            item
            for item in GameRepository._stock_executable_expected_sites(profile)
            if not item[2].startswith("invariant_sites[")
            and not item[2].startswith("known_false_matches[")
        )

    @staticmethod
    def _validate_stock_executable(
        source: bytes,
        profile: Mapping[str, object],
    ) -> dict[str, object]:
        source_profile = GameRepository._require_executable_mapping(
            profile["source"], "source"
        )
        expected_size = int(source_profile["size"])
        if len(source) != expected_size:
            raise ValueError(
                f"Unsupported executable source size: expected {expected_size} bytes, "
                f"got {len(source)}."
            )
        expected_digest = str(source_profile["sha256"]).casefold()
        actual_digest = hashlib.sha256(source).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError(
                "Unsupported executable source SHA-256: "
                f"expected {expected_digest}, got {actual_digest}."
            )
        actual_pe = GameRepository._parse_executable_pe(source)
        expected_pe = GameRepository._require_executable_mapping(
            source_profile["pe"], "source.pe"
        )
        scalar_fields = (
            "dos_magic",
            "pe_offset",
            "signature",
            "machine",
            "optional_header_size",
            "optional_header_magic",
            "image_base",
            "section_alignment",
            "file_alignment",
            "number_of_sections",
            "size_of_code",
            "size_of_initialized_data",
            "size_of_uninitialized_data",
            "size_of_image",
            "size_of_headers",
            "section_table_offset",
            "section_table_end",
        )
        for field in scalar_fields:
            if actual_pe[field] != expected_pe[field]:
                raise ValueError(
                    f"Unsupported executable PE field '{field}': expected "
                    f"{expected_pe[field]!r}, got {actual_pe[field]!r}."
                )
        actual_sections_value = actual_pe["sections"]
        assert isinstance(actual_sections_value, Sequence)
        expected_sections = GameRepository._mapping_sequence(
            expected_pe["sections"], "source.pe.sections"
        )
        for index, (actual, expected) in enumerate(
            zip(actual_sections_value, expected_sections, strict=True)
        ):
            assert isinstance(actual, Mapping)
            for field in (
                "name",
                "virtual_size",
                "virtual_address",
                "raw_size",
                "raw_pointer",
                "characteristics",
            ):
                if actual[field] != expected[field]:
                    raise ValueError(
                        f"Unsupported executable PE section {index} field '{field}': "
                        f"expected {expected[field]!r}, got {actual[field]!r}."
                    )
            for field in (
                "pointer_to_relocations",
                "pointer_to_line_numbers",
                "number_of_relocations",
                "number_of_line_numbers",
            ):
                if actual[field] != 0:
                    raise ValueError(
                        f"Unsupported executable PE section {index} has nonzero "
                        f"'{field}'."
                    )
        geometry_sections = tuple(
            {
                field: section[field]
                for field in (
                    "name",
                    "virtual_size",
                    "virtual_address",
                    "raw_size",
                    "raw_pointer",
                    "characteristics",
                )
            }
            for section in actual_sections_value
            if isinstance(section, Mapping)
        )
        GameRepository._validate_executable_section_geometry(
            geometry_sections,
            section_alignment=int(actual_pe["section_alignment"]),
            file_alignment=int(actual_pe["file_alignment"]),
            file_size=len(source),
            label="stock PE",
        )
        slack_start = int(actual_pe["section_table_end"])
        slack_size = int(expected_pe["zero_header_slack_size"])
        slack_end = slack_start + slack_size
        if slack_end > len(source) or source[slack_start:slack_end] != bytes(
            slack_size
        ):
            raise ValueError(
                "Executable source PE header slack is not canonically zero."
            )
        if slack_size < 80:
            raise ValueError(
                "Executable source PE header lacks two section-header slots."
            )
        for va, expected, label in GameRepository._stock_executable_expected_sites(
            profile
        ):
            offset = GameRepository._executable_va_to_file_offset(
                source, actual_pe, va, len(expected)
            )
            actual = source[offset : offset + len(expected)]
            if actual != expected:
                raise ValueError(
                    f"Executable stock window mismatch at VA 0x{va:08X} ({label}): "
                    f"expected {expected.hex(' ')}, got {actual.hex(' ')}."
                )
        return actual_pe

    @staticmethod
    def _build_executable_helper_section(
        profile: Mapping[str, object],
    ) -> bytes:
        layout = GameRepository._require_executable_mapping(
            profile["runtime_layout"], "runtime_layout"
        )
        section_size = int(layout["helper_size"])
        target_sections = GameRepository._profile_sequence(profile, "pe_sections")
        fill = int(target_sections[1]["fill_byte"])
        result = bytearray([fill]) * section_size
        for fragment in GameRepository._profile_sequence(profile, "helper_fragments"):
            offset = int(fragment["offset"])
            fragment_bytes = bytes(fragment["bytes"])
            result[offset : offset + len(fragment_bytes)] = fragment_bytes
        return bytes(result)

    @staticmethod
    def _pack_executable_section_header(section: Mapping[str, object]) -> bytes:
        name = bytes(str(section["name"]), "ascii").ljust(8, b"\x00")
        return name + struct.pack(
            "<IIIIIIHHI",
            int(section["virtual_size"]),
            int(section["virtual_address"]),
            int(section["raw_size"]),
            int(section["raw_pointer"]),
            0,
            0,
            0,
            0,
            int(section["characteristics"]),
        )

    @staticmethod
    def _install_executable_step8_sections(
        output: bytearray,
        profile: Mapping[str, object],
        source_pe: Mapping[str, object],
    ) -> None:
        updates = GameRepository._require_executable_mapping(
            profile["pe_header_updates"], "pe_header_updates"
        )
        struct.pack_into(
            "<H",
            output,
            int(source_pe["number_of_sections_offset"]),
            int(updates["number_of_sections"]),
        )
        for offset_name, field in (
            ("size_of_code_offset", "size_of_code"),
            ("size_of_uninitialized_data_offset", "size_of_uninitialized_data"),
            ("size_of_image_offset", "size_of_image"),
        ):
            struct.pack_into(
                "<I", output, int(source_pe[offset_name]), int(updates[field])
            )
        header_offset = int(source_pe["section_table_end"])
        for section in GameRepository._profile_sequence(profile, "pe_sections"):
            header = GameRepository._pack_executable_section_header(section)
            output[header_offset : header_offset + 40] = header
            header_offset += 40
        target_size = int(updates["output_size_before_icon"])
        if len(output) > target_size:
            raise ValueError(
                "Executable stock source is larger than the Step 8 output layout."
            )
        output.extend(bytes(target_size - len(output)))
        helper = GameRepository._build_executable_helper_section(profile)
        helper_section = GameRepository._profile_sequence(profile, "pe_sections")[1]
        raw_pointer = int(helper_section["raw_pointer"])
        raw_size = int(helper_section["raw_size"])
        if len(helper) != raw_size:
            raise ValueError("Executable helper bytes do not fill '.ygsx'.")
        output[raw_pointer : raw_pointer + raw_size] = helper

    @staticmethod
    def _verify_extended_executable(
        result: bytes,
        *,
        card_record_count: int,
        profile: Mapping[str, object],
        require_profile_raw_layout: bool,
    ) -> None:
        derived = GameRepository._calculate_executable_card_capacity_values(
            card_record_count, profile
        )
        pe = GameRepository._parse_executable_pe(result)
        source = GameRepository._require_executable_mapping(profile["source"], "source")
        source_pe = GameRepository._require_executable_mapping(
            source["pe"], "source.pe"
        )
        updates = GameRepository._require_executable_mapping(
            profile["pe_header_updates"], "pe_header_updates"
        )
        for field in (
            "pe_offset",
            "machine",
            "optional_header_size",
            "optional_header_magic",
            "image_base",
            "section_alignment",
            "file_alignment",
            "size_of_headers",
            "section_table_offset",
        ):
            if pe[field] != source_pe[field]:
                raise ValueError(
                    f"Generated executable PE field '{field}' changed unexpectedly."
                )
        expected_section_table_end = int(source_pe["section_table_end"]) + 80
        if pe["section_table_end"] != expected_section_table_end:
            raise ValueError(
                "Generated executable PE section-table end does not match Step 8."
            )
        for field in (
            "number_of_sections",
            "size_of_code",
            "size_of_uninitialized_data",
            "size_of_image",
        ):
            if pe[field] != updates[field]:
                raise ValueError(
                    f"Generated executable PE field '{field}' does not match Step 8."
                )
        if require_profile_raw_layout:
            if pe["size_of_initialized_data"] != source_pe["size_of_initialized_data"]:
                raise ValueError(
                    "Generated executable SizeOfInitializedData changed before "
                    "icon update."
                )
            if len(result) != int(updates["output_size_before_icon"]):
                raise ValueError(
                    "Generated executable size does not match the pre-icon "
                    "Step 8 layout."
                )
        elif int(pe["size_of_initialized_data"]) < int(
            source_pe["size_of_initialized_data"]
        ) or int(pe["size_of_initialized_data"]) % int(pe["file_alignment"]):
            raise ValueError(
                "Generated executable post-icon SizeOfInitializedData is invalid."
            )

        sections_value = pe["sections"]
        assert isinstance(sections_value, Sequence)
        sections = tuple(
            section for section in sections_value if isinstance(section, Mapping)
        )
        if len(sections) != int(updates["number_of_sections"]):
            raise ValueError("Generated executable PE section table is incomplete.")
        source_sections = GameRepository._mapping_sequence(
            source_pe["sections"], "source.pe.sections"
        )
        target_sections = GameRepository._profile_sequence(profile, "pe_sections")
        expected_names = tuple(
            str(section["name"]) for section in (*source_sections, *target_sections)
        )
        actual_names = tuple(str(section["name"]) for section in sections)
        if actual_names != expected_names:
            raise ValueError(
                "Generated executable PE section names/order do not match Step 8."
            )
        for index, (actual, expected) in enumerate(
            zip(sections[: len(source_sections)], source_sections, strict=True)
        ):
            for field in ("name", "virtual_address", "characteristics"):
                if actual[field] != expected[field]:
                    raise ValueError(
                        f"Generated executable source section {index} field "
                        f"'{field}' changed."
                    )
            if require_profile_raw_layout:
                preserved_fields = (
                    "virtual_size",
                    "raw_size",
                    "raw_pointer",
                )
            elif str(expected["name"]).casefold() == ".rsrc":
                preserved_fields = ()
            else:
                preserved_fields = ("virtual_size", "raw_size")
            for field in preserved_fields:
                if actual[field] != expected[field]:
                    raise ValueError(
                        f"Generated executable source section {index} field "
                        f"'{field}' changed."
                    )
            for field in (
                "pointer_to_relocations",
                "pointer_to_line_numbers",
                "number_of_relocations",
                "number_of_line_numbers",
            ):
                if actual[field] != 0:
                    raise ValueError(
                        f"Generated executable section {index} has nonzero '{field}'."
                    )

        ygst_actual, ygsx_actual = sections[-2:]
        ygst_expected, ygsx_expected = target_sections
        for field in (
            "name",
            "virtual_size",
            "virtual_address",
            "raw_size",
            "raw_pointer",
            "characteristics",
        ):
            if ygst_actual[field] != ygst_expected[field]:
                raise ValueError(
                    f"Generated executable '.ygst' field '{field}' is invalid."
                )
        for field in (
            "name",
            "virtual_size",
            "virtual_address",
            "raw_size",
            "characteristics",
        ):
            if ygsx_actual[field] != ygsx_expected[field]:
                raise ValueError(
                    f"Generated executable '.ygsx' field '{field}' is invalid."
                )
        for index, section in enumerate(
            (ygst_actual, ygsx_actual), start=len(sections) - 2
        ):
            for field in (
                "pointer_to_relocations",
                "pointer_to_line_numbers",
                "number_of_relocations",
                "number_of_line_numbers",
            ):
                if section[field] != 0:
                    raise ValueError(
                        f"Generated executable section {index} has nonzero '{field}'."
                    )
        ygsx_raw_pointer = int(ygsx_actual["raw_pointer"])
        ygsx_raw_size = int(ygsx_actual["raw_size"])
        if require_profile_raw_layout and ygsx_raw_pointer != int(
            ygsx_expected["raw_pointer"]
        ):
            raise ValueError("Generated executable '.ygsx' raw pointer is invalid.")
        if ygsx_raw_pointer % int(pe["file_alignment"]):
            raise ValueError("Generated executable '.ygsx' raw pointer is unaligned.")
        if ygsx_raw_pointer + ygsx_raw_size > len(result):
            raise ValueError("Generated executable '.ygsx' raw data is incomplete.")

        geometry_sections = tuple(
            {
                field: section[field]
                for field in (
                    "name",
                    "virtual_size",
                    "virtual_address",
                    "raw_size",
                    "raw_pointer",
                    "characteristics",
                )
            }
            for section in sections
        )
        GameRepository._validate_executable_section_geometry(
            geometry_sections,
            section_alignment=int(pe["section_alignment"]),
            file_alignment=int(pe["file_alignment"]),
            file_size=len(result),
            label="generated PE",
        )

        helper = result[ygsx_raw_pointer : ygsx_raw_pointer + ygsx_raw_size]
        expected_helper_digest = str(profile["helper_section_sha256"]).casefold()
        if hashlib.sha256(helper).hexdigest() != expected_helper_digest:
            raise ValueError("Generated executable '.ygsx' helper digest is invalid.")
        for fragment in GameRepository._profile_sequence(profile, "helper_fragments"):
            offset = int(fragment["offset"])
            expected = bytes(fragment["bytes"])
            if helper[offset : offset + len(expected)] != expected:
                raise ValueError(
                    "Generated executable helper fragment "
                    f"'{fragment['name']}' is invalid."
                )

        for group in GameRepository._profile_sequence(
            profile, "state_relocation_groups"
        ):
            width = int(group["value_width"])
            encoded = int(group["replacement"]).to_bytes(width, "little")
            for site in GameRepository._mapping_sequence(group["sites"], "sites"):
                expected = bytearray(site["expected"])
                value_offset = int(site["value_offset"])
                expected[value_offset : value_offset + width] = encoded
                GameRepository._verify_executable_va_bytes(
                    result,
                    pe,
                    int(site["va"]),
                    bytes(expected),
                    str(group["value_name"]),
                )
        for field in (
            "snapshot_patches",
            "fixed_patch_sites",
            "hooks",
            "alias_consumer_patches",
        ):
            for site in GameRepository._profile_sequence(profile, field):
                GameRepository._verify_executable_va_bytes(
                    result,
                    pe,
                    int(site["va"]),
                    bytes(site["replacement"]),
                    str(site["name"]),
                )
        for site in GameRepository._profile_sequence(profile, "dynamic_patch_sites"):
            expected = bytearray(site["expected"])
            width = int(site["value_width"])
            value_offset = int(site["value_offset"])
            encoded = derived[str(site["value_name"])].to_bytes(width, "little")
            expected[value_offset : value_offset + width] = encoded
            GameRepository._verify_executable_va_bytes(
                result,
                pe,
                int(site["va"]),
                bytes(expected),
                str(site["value_name"]),
            )
        for field in ("invariant_sites", "known_false_matches"):
            for index, site in enumerate(
                GameRepository._profile_sequence(profile, field)
            ):
                GameRepository._verify_executable_va_bytes(
                    result,
                    pe,
                    int(site["va"]),
                    bytes(site["expected"]),
                    str(site.get("name", f"{field}[{index}]")),
                )
        layout = GameRepository._require_executable_mapping(
            profile["runtime_layout"], "runtime_layout"
        )
        if derived["maximum_active_slot"] >= int(layout["invalid_slot"]):
            raise ValueError(
                "Generated executable active slot reaches the invalid sentinel."
            )
        if derived["active_state_end_address"] > int(layout["state_structural_end"]):
            raise ValueError("Generated executable active state exceeds '.ygst'.")

    @staticmethod
    def _verify_executable_va_bytes(
        value: bytes,
        pe: Mapping[str, object],
        va: int,
        expected: bytes,
        label: str,
    ) -> None:
        offset = GameRepository._executable_va_to_file_offset(
            value, pe, va, len(expected)
        )
        actual = value[offset : offset + len(expected)]
        if actual != expected:
            raise ValueError(
                f"Generated executable verification failed at VA 0x{va:08X} "
                f"({label}): expected {expected.hex(' ')}, got {actual.hex(' ')}."
            )

    @staticmethod
    def _validate_executable_changed_regions(
        source: bytes,
        result: bytes,
        profile: Mapping[str, object],
        source_pe: Mapping[str, object],
    ) -> None:
        updates = GameRepository._require_executable_mapping(
            profile["pe_header_updates"], "pe_header_updates"
        )
        if len(result) != int(updates["output_size_before_icon"]):
            raise ValueError(
                "Executable changed-region gate received an invalid output size."
            )
        if len(result) < len(source):
            raise ValueError(
                "Executable changed-region gate detected source truncation."
            )
        allowed: list[tuple[int, int, str]] = []
        for va, expected, label in GameRepository._stock_executable_mutation_sites(
            profile
        ):
            offset = GameRepository._executable_va_to_file_offset(
                source, source_pe, va, len(expected)
            )
            allowed.append((offset, offset + len(expected), label))
        for offset_name, size, label in (
            ("number_of_sections_offset", 2, "PE NumberOfSections"),
            ("size_of_code_offset", 4, "PE SizeOfCode"),
            ("size_of_uninitialized_data_offset", 4, "PE SizeOfUninitializedData"),
            ("size_of_image_offset", 4, "PE SizeOfImage"),
        ):
            offset = int(source_pe[offset_name])
            allowed.append((offset, offset + size, label))
        section_header_start = int(source_pe["section_table_end"])
        allowed.append(
            (
                section_header_start,
                section_header_start + 80,
                "Step 8 PE section headers",
            )
        )
        ordered = sorted(allowed, key=lambda item: (item[0], item[1]))
        merged: list[tuple[int, int]] = []
        for start, end, _ in ordered:
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        cursor = 0
        for start, end in merged:
            if source[cursor:start] != result[cursor:start]:
                mismatch = next(
                    index
                    for index, (before, after) in enumerate(
                        zip(source[cursor:start], result[cursor:start], strict=True),
                        start=cursor,
                    )
                    if before != after
                )
                raise ValueError(
                    f"Executable changed-region gate found an undeclared byte at "
                    f"file offset 0x{mismatch:X}."
                )
            cursor = max(cursor, end)
        if source[cursor:] != result[cursor : len(source)]:
            mismatch = next(
                index
                for index, (before, after) in enumerate(
                    zip(source[cursor:], result[cursor : len(source)], strict=True),
                    start=cursor,
                )
                if before != after
            )
            raise ValueError(
                f"Executable changed-region gate found an undeclared byte at "
                f"file offset 0x{mismatch:X}."
            )
        helper_section = GameRepository._profile_sequence(profile, "pe_sections")[1]
        raw_pointer = int(helper_section["raw_pointer"])
        if raw_pointer != len(source):
            raise ValueError(
                "Executable changed-region gate expected append-only helper data."
            )
        expected_helper = GameRepository._build_executable_helper_section(profile)
        if result[raw_pointer:] != expected_helper:
            raise ValueError(
                "Executable changed-region gate found undeclared appended bytes."
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
    def pad_integer_sequence_to_power_of_two(
        value: object,
        *,
        context: RuleProcessingContext,
        minimum_capacity: int,
        pad_value: int = 0,
    ) -> list[int]:
        del context
        values = GameRepository._pipeline_sequence(
            value,
            method_name="pad_integer_sequence_to_power_of_two",
        )
        if minimum_capacity <= 0:
            raise ValueError("Minimum integer-sequence capacity must be positive.")
        required_count = len(values)
        derived_capacity = max(
            minimum_capacity,
            GameRepository.find_next_power_of_two(required_count),
        )
        integers = [int(item) for item in values]
        return integers + [int(pad_value)] * (derived_capacity - required_count)

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
