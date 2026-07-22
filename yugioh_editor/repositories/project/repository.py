from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path, PurePosixPath
from time import perf_counter

import pandas as pd

from yugioh_editor.common.card_images import CARD_IMAGE_NAME_PATTERN
from yugioh_editor.common.card_passwords import (
    legacy_card_password_to_hex,
    normalize_card_password,
)
from yugioh_editor.common.card_properties import (
    ATTRIBUTE_LABELS,
    CARD_PROPERTY_COLUMNS,
    MONSTER_CATEGORY_LABELS,
    MONSTER_TYPE_LABELS,
    SPELL_TRAP_SUBTYPE_LABELS,
    code_for_property_label,
    normalize_property_label,
    parse_property_code,
    property_label_for_code,
)
from yugioh_editor.common.constants import (
    DEFAULT_LANGUAGE,
    LANGUAGE_PREFIXES,
    PROJECT_BIN_DIRECTORY,
    normalize_language_code,
)
from yugioh_editor.common.subfile_rules_config import SUBFILE_RULE_CONFIGS
from yugioh_editor.common.worker_limits import (
    estimate_available_memory_bytes,
    select_worker_count,
)
from yugioh_editor.models.entities import (
    CURRENT_PROJECT_VERSION,
    CardImageVariant,
    NamedCardImagePair,
    ProjectFileRecord,
    ProjectManifest,
    ProjectResource,
)
from yugioh_editor.repositories.game.subfile_rule import SubfileRule
from yugioh_editor.repositories.game.subfile_rule_factory import SubfileRuleFactory
from yugioh_editor.repositories.project.connection import ProjectFolderConnection


def normalize_project_path(value: str | Path) -> PurePosixPath:
    parts: list[str] = []
    for part in str(value).replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError("Project resource paths must not contain '..'.")
        parts.append(part)
    if not parts:
        raise ValueError("Project resource paths must not be empty.")
    return PurePosixPath(*parts)


@dataclass(frozen=True, slots=True)
class LogicalTableHandler:
    name: str
    rule: SubfileRule | None
    reader: Callable[..., pd.DataFrame]
    writer: Callable[..., None] | None
    parameters: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    composite: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedCardImagePair:
    image_name: str
    large_payload: bytes
    mini_payload: bytes


class ProjectRepository:
    """Own workspace storage and map logical tables to physical resources."""

    def __init__(
        self,
        root: str | Path | ProjectManifest | ProjectFolderConnection,
        connection: ProjectFolderConnection | None = None,
        *,
        manifest: ProjectManifest | None = None,
        subfile_rules: Sequence[SubfileRule] | None = None,
    ) -> None:
        if isinstance(root, ProjectManifest):
            self._manifest = root
            self._connection = connection or ProjectFolderConnection(root.root)
        elif isinstance(root, ProjectFolderConnection):
            self._connection = root
            self._manifest = manifest
        else:
            self._connection = connection or ProjectFolderConnection(root)
            self._manifest = manifest
        self._subfile_rules = tuple(
            subfile_rules
            if subfile_rules is not None
            else SubfileRuleFactory().build_rules(SUBFILE_RULE_CONFIGS)
        )
        self._table_handlers = self._build_table_handlers()
        self._pending_card_catalogs: dict[CardImageVariant, pd.DataFrame] = {}

    @property
    def root(self) -> Path:
        return self._connection.root

    def use_root(
        self,
        root: str | Path,
        *,
        manifest: ProjectManifest | None = None,
    ) -> ProjectRepository:
        return ProjectRepository(
            root,
            manifest=manifest,
            subfile_rules=self._subfile_rules,
        )

    def with_manifest(self, manifest: ProjectManifest) -> ProjectRepository:
        return ProjectRepository(
            self._connection,
            manifest=manifest,
            subfile_rules=self._subfile_rules,
        )

    def ensure_root(self) -> Path:
        return self._connection.ensure_root()

    def begin_create(self) -> ProjectRepository:
        staging = self._connection.create_staging_sibling("create")
        return ProjectRepository(
            staging,
            manifest=self._manifest,
            subfile_rules=self._subfile_rules,
        )

    def commit_create(self, staging: ProjectRepository) -> None:
        self._connection.commit_staging_root(staging._connection)

    def discard(self) -> None:
        self._connection.discard_root()

    def begin_pack(self) -> ProjectRepository:
        staging = self._connection.create_staging_sibling("pack")
        return ProjectRepository(
            staging,
            manifest=self._manifest,
            subfile_rules=self._subfile_rules,
        )

    def begin_update(self) -> ProjectRepository:
        staging = self._connection.create_staging_clone("cards")
        manifest = ProjectManifest.from_dict(self._require_manifest().to_dict())
        return ProjectRepository(
            staging,
            manifest=manifest,
            subfile_rules=self._subfile_rules,
        )

    def commit_update(self, staging: ProjectRepository) -> None:
        updated_manifest = staging._require_manifest()
        self._connection.replace_directory(staging._connection, ".")
        if self._manifest is None:
            self._manifest = updated_manifest
            return
        self._manifest.name = updated_manifest.name
        self._manifest.root_path = updated_manifest.root_path
        self._manifest.version_prefix = updated_manifest.version_prefix
        self._manifest.files[:] = updated_manifest.files
        self._manifest.version = updated_manifest.version
        self._manifest.executable = updated_manifest.executable
        self._manifest.game_files.clear()
        self._manifest.game_files.update(updated_manifest.game_files)

    def commit_pack(self, staging: ProjectRepository) -> Path:
        return self._connection.replace_directory(
            staging._connection,
            PROJECT_BIN_DIRECTORY,
        )

    def create(
        self,
        root: str | Path,
        manifest: ProjectManifest,
    ) -> ProjectManifest:
        repository = self.use_root(root, manifest=manifest)
        repository.ensure_root()
        repository.write_manifest(manifest)
        return manifest

    def load(self, root: str | Path | None = None) -> ProjectManifest:
        repository = self.use_root(root) if root is not None else self
        manifest = repository.read_manifest()
        manifest.validate()
        repository._validate_manifest_rule_metadata(manifest)
        repository._validate_physical_workspace_files(manifest)
        repository._manifest = manifest
        repository._migrate_loaded_project()
        return repository._require_manifest()

    def _migrate_loaded_project(self) -> None:
        manifest = self._require_manifest()
        if manifest.version >= CURRENT_PROJECT_VERSION:
            return
        if manifest.version not in {2, 3}:
            raise ValueError(
                f"Unsupported project schema version {manifest.version}; "
                "only versions 2 and 3 can be migrated."
            )
        staging = self.begin_update()
        try:
            if manifest.version == 2:
                handler = staging._table_handlers["card_properties"]
                if handler.rule is None:
                    raise RuntimeError("card_properties physical rule is missing.")
                record = staging._physical_table_record(handler.rule, {})
                legacy = staging._read_record_table(record)
                migrated = staging._normalize_card_properties(
                    legacy,
                    warn_legacy=False,
                    legacy_schema=True,
                )
                staging._write_record_table(
                    record,
                    migrated,
                    CARD_PROPERTY_COLUMNS,
                )
            passcode_handler = staging._table_handlers["card_passcodes"]
            if passcode_handler.rule is None:
                raise RuntimeError("card_passcodes physical rule is missing.")
            passcode_record = staging._physical_table_record(
                passcode_handler.rule,
                {},
            )
            legacy_passcodes = staging._read_record_table(passcode_record)
            migrated_passcodes = staging._migrate_legacy_card_passcodes(
                legacy_passcodes
            )
            staging._write_record_table(
                passcode_record,
                migrated_passcodes,
                ("value",),
            )
            staging._require_manifest().version = CURRENT_PROJECT_VERSION
            staging.save()
            self.commit_update(staging)
        except Exception:
            staging.discard()
            raise

    def save(self, manifest: ProjectManifest | None = None) -> None:
        current = manifest or self._require_manifest()
        current.validate()
        self._validate_manifest_rule_metadata(current)
        self._validate_physical_workspace_files(current)
        self._connection.write_manifest(current)
        self._manifest = current

    def read_manifest(self) -> ProjectManifest:
        return self._connection.read_manifest()

    def write_manifest(self, manifest: ProjectManifest) -> Path:
        manifest.validate()
        self._validate_manifest_rule_metadata(manifest)
        self._validate_physical_workspace_files(manifest)
        self._manifest = manifest
        return self._connection.write_manifest(manifest)

    def import_resources(
        self,
        resources: Iterable[ProjectResource],
    ) -> list[ProjectFileRecord]:
        records: list[ProjectFileRecord] = []
        for resource in resources:
            record = resource.record
            self._validate_record_rule_metadata(record)
            records.append(record)
            if record.virtual:
                continue
            path = record.workspace_path
            if path is None:
                raise ValueError(
                    f"Workspace path is missing for {record.relative_path}."
                )
            writers = {
                "table": lambda: self._connection.write_table(
                    path,
                    self._require_dataframe(resource.value),
                ),
                "text": lambda: self._connection.write_text(
                    path,
                    str(resource.value),
                ),
                "binary": lambda: self._connection.write_bytes(
                    path,
                    bytes(resource.value),
                ),
            }
            try:
                writers[record.storage_format]()
            except KeyError as error:
                raise ValueError(
                    f"Unknown project storage format '{record.storage_format}'."
                ) from error
        return records

    def export_resources(
        self,
        records: Iterable[ProjectFileRecord],
    ) -> list[ProjectResource]:
        resources: list[ProjectResource] = []
        for record in records:
            if record.virtual:
                resources.append(ProjectResource(record))
                continue
            if record.workspace_path is None:
                raise ValueError(
                    f"Workspace path is missing for {record.relative_path}."
                )
            readers = {
                "table": self._connection.read_table,
                "text": self._connection.read_text,
                "binary": self._connection.read_bytes,
            }
            try:
                value = readers[record.storage_format](record.workspace_path)
            except KeyError as error:
                raise ValueError(
                    f"Unknown project storage format '{record.storage_format}'."
                ) from error
            rule = self._match_subfile_rule(record.relative_path)
            if (
                rule is not None
                and rule.table_name == "card_properties"
                and isinstance(value, pd.DataFrame)
            ):
                value = self._normalize_card_properties(
                    value,
                    warn_legacy=True,
                )
            if (
                rule is not None
                and rule.table_name == "card_passcodes"
                and isinstance(value, pd.DataFrame)
            ):
                value = self._normalize_card_passcodes(value)
            resources.append(ProjectResource(record, value))
        return resources

    @staticmethod
    def list_resources(
        manifest: ProjectManifest,
        include_virtual: bool = False,
    ) -> list[ProjectFileRecord]:
        unique: dict[tuple[str, str], ProjectFileRecord] = {}
        for resource in manifest.files:
            if resource.virtual and not include_virtual:
                continue
            path = normalize_project_path(resource.relative_path)
            key = (
                resource.source_file.casefold(),
                path.as_posix().casefold(),
            )
            if key in unique:
                logging.warning(
                    "Duplicate project resource ignored: %s in %s",
                    resource.relative_path,
                    resource.source_file,
                )
                continue
            unique[key] = resource
        return sorted(
            unique.values(),
            key=lambda item: (
                item.source_file.casefold(),
                normalize_project_path(item.relative_path).as_posix().casefold(),
                item.relative_path,
            ),
        )

    @classmethod
    def list_visible_resources(
        cls,
        manifest: ProjectManifest,
    ) -> list[ProjectFileRecord]:
        return cls.list_resources(manifest, include_virtual=False)

    @staticmethod
    def find_records(
        manifest: ProjectManifest,
        *,
        source_file: str | None = None,
        suffix: str | None = None,
    ) -> list[ProjectFileRecord]:
        values = ProjectRepository.list_resources(
            manifest,
            include_virtual=True,
        )
        if source_file is not None:
            values = [
                item
                for item in values
                if item.source_file.casefold() == source_file.casefold()
            ]
        if suffix is not None:
            normalized = normalize_project_path(suffix).as_posix().casefold()
            values = [
                item
                for item in values
                if normalize_project_path(item.relative_path)
                .as_posix()
                .casefold()
                .endswith(normalized)
            ]
        return values

    def list_tables(self) -> tuple[str, ...]:
        return tuple(self._table_handlers)

    def has_table(self, table_name: str) -> bool:
        return table_name in self._table_handlers

    def get_table(
        self,
        table_name: str,
        **parameters: object,
    ) -> pd.DataFrame:
        handler = self._require_table_handler(table_name)
        self._validate_table_parameters(handler, parameters)
        return handler.reader(**parameters).copy()

    def save_table(
        self,
        table_name: str,
        table: pd.DataFrame,
        **parameters: object,
    ) -> None:
        handler = self._require_table_handler(table_name)
        self._validate_table_parameters(handler, parameters)
        if handler.writer is None:
            raise ValueError(f"Table '{table_name}' is read-only.")
        handler.writer(table.copy(), **parameters)

    def get_resource(
        self,
        resource: ProjectFileRecord | str,
    ) -> bytes | str | pd.DataFrame:
        record = self._resolve_record(resource)
        if record.workspace_path is None:
            raise ValueError(
                f"Virtual resources are not directly readable: {record.relative_path}"
            )
        readers = {
            "table": self._connection.read_table,
            "text": self._connection.read_text,
            "binary": self._connection.read_bytes,
        }
        value = readers[record.storage_format](record.workspace_path)
        rule = self._match_subfile_rule(record.relative_path)
        if (
            isinstance(value, pd.DataFrame)
            and rule is not None
            and rule.codec_name == "offset_string_table"
        ):
            return self._normalize_indexed_text_table(
                value,
                resource=record.relative_path,
            )
        if (
            isinstance(value, pd.DataFrame)
            and rule is not None
            and rule.table_name == "card_passcodes"
        ):
            return self._normalize_card_passcodes(value)
        return value

    def save_resource(
        self,
        resource: ProjectFileRecord | str,
        value: bytes | str | pd.DataFrame,
    ) -> None:
        record = self._resolve_record(resource)
        if record.workspace_path is None:
            raise ValueError(
                f"Virtual resources are not directly writable: {record.relative_path}"
            )
        if record.storage_format == "table":
            table = self._require_dataframe(value)
            rule = self._match_subfile_rule(record.relative_path)
            if rule is not None and rule.codec_name == "offset_string_table":
                table = self._normalize_indexed_text_table(
                    table,
                    resource=record.relative_path,
                )
            if rule is not None and rule.table_name == "card_passcodes":
                table = self._normalize_card_passcodes(table)
            self._connection.write_table(
                record.workspace_path,
                table,
            )
        elif record.storage_format == "text":
            self._connection.write_text(record.workspace_path, str(value))
        else:
            self._connection.write_bytes(
                record.workspace_path,
                bytes(value),
            )

    def get_resource_editor_columns(
        self,
        resource: ProjectFileRecord | str,
    ) -> tuple[str, ...]:
        record = self._resolve_record(resource)
        rule = self._match_subfile_rule(record.relative_path)
        return () if rule is None else rule.editor_columns

    def replace_resource(
        self,
        resource: ProjectFileRecord | str,
        source: str | Path,
    ) -> None:
        record = self._resolve_record(resource)
        if record.workspace_path is None:
            raise ValueError("Virtual resources cannot be replaced.")
        self._connection.copy_file(source, record.workspace_path)

    def replace_image_resource(
        self,
        resource: ProjectFileRecord | str,
        source: str | Path,
    ) -> None:
        record = self._resolve_record(resource)
        if record.workspace_path is None:
            raise ValueError("Virtual resources cannot be replaced.")
        self._connection.convert_image_to_bmp(
            source,
            record.workspace_path,
        )

    def resource_path(
        self,
        resource: ProjectFileRecord | str,
    ) -> Path:
        record = self._resolve_record(resource)
        if record.workspace_path is None:
            raise ValueError("Virtual resources do not have a path.")
        return self._connection.resolve(record.workspace_path)

    def get_binary_preview(
        self,
        resource: ProjectFileRecord | str,
        limit: int,
    ) -> tuple[bytes, int]:
        record = self._resolve_record(resource)
        if record.workspace_path is None:
            raise ValueError("Virtual resources do not have binary data.")
        if record.storage_format != "binary":
            raise TypeError("The selected resource is not binary.")
        return self._connection.read_bytes_preview(
            record.workspace_path,
            limit,
        )

    def get_game_file_name(self, logical_name: str) -> str:
        manifest = self._require_manifest()
        configured = next(
            (
                value
                for key, value in manifest.game_files.items()
                if key.casefold() == logical_name.casefold()
            ),
            None,
        )
        if configured is not None:
            return configured
        record = next(
            (
                item
                for item in manifest.files
                if item.source_file.casefold() == logical_name.casefold()
            ),
            None,
        )
        if record is None:
            raise KeyError(f"Game file was not found: {logical_name}")
        return record.source_file

    def add_card_images(
        self,
        source: str | Path,
        mini_source: str | Path | None = None,
    ) -> str:
        existing_names = {name.casefold() for name in self.existing_card_image_names()}
        index = 0
        while True:
            image_name = f"CUSTOM{index:04d}.bmp"
            if image_name.casefold() not in existing_names:
                break
            index += 1
        self.add_named_card_images_batch(
            (
                NamedCardImagePair(
                    image_name=image_name,
                    large_source=source,
                    mini_source=mini_source or source,
                ),
            ),
            save_manifest=True,
        )
        return image_name

    def existing_card_image_names(
        self,
        *,
        retain_catalogs: bool = False,
    ) -> set[str]:
        manifest = self._require_manifest()
        names = {
            Path(record.relative_path.replace("\\", "/")).name
            for record in manifest.files
            if record.file_kind == "image"
        }
        catalogs: dict[CardImageVariant, pd.DataFrame] = {}
        for variant in CardImageVariant:
            catalog = self._get_card_catalog(image_variant=variant)
            catalogs[variant] = catalog
            if "image_name" in catalog:
                names.update(
                    str(value)
                    for value in catalog["image_name"].tolist()
                    if str(value).strip()
                )
        for folder in ("data/card", "data/mini"):
            names.update(
                path.name
                for path in self._connection.list_files(folder, recursive=False)
            )
        if retain_catalogs:
            self._pending_card_catalogs = catalogs
        return names

    def card_image_pair_exists(self, image_name: str) -> bool:
        """Return whether both physical variants exist; reject a partial pair."""

        records = tuple(
            self._find_record(
                f"{folder}/{image_name}",
                required=False,
                logical_source="data.dat",
            )
            for folder in ("card", "mini")
        )
        present = tuple(record is not None for record in records)
        if present[0] != present[1]:
            raise ValueError(
                f"Card image {image_name!r} has only one physical variant."
            )
        return all(present)

    def add_named_card_images(
        self,
        image_name: str,
        large_source: str | Path | bytes,
        small_source: str | Path | bytes,
        *,
        save_manifest: bool = True,
    ) -> None:
        self.add_named_card_images_batch(
            (
                NamedCardImagePair(
                    image_name=image_name,
                    large_source=large_source,
                    mini_source=small_source,
                ),
            ),
            save_manifest=save_manifest,
        )

    def add_named_card_images_batch(
        self,
        images: Sequence[NamedCardImagePair],
        *,
        save_manifest: bool = False,
    ) -> tuple[ProjectFileRecord, ...]:
        """Add card image pairs with one inventory, order plan, and manifest update."""

        requested = tuple(images)
        if not requested:
            return ()

        started = perf_counter()
        manifest = self._require_manifest()
        manifest.validate()
        normalized_batch_names: set[str] = set()
        for item in requested:
            if not isinstance(item, NamedCardImagePair):
                raise TypeError(
                    "Card image batch items must be NamedCardImagePair values."
                )
            if item.large_source is None or item.mini_source is None:
                raise ValueError(
                    f"Card image {item.image_name!r} requires a complete pair."
                )
            image_name = str(item.image_name)
            normalized_path = image_name.replace("\\", "/")
            if (
                not image_name
                or image_name != image_name.strip()
                or "/" in normalized_path
                or not image_name.casefold().endswith(".bmp")
            ):
                raise ValueError(
                    f"Card image name must be a plain BMP filename: {image_name!r}"
                )
            normalized_name = image_name.casefold()
            if normalized_name in normalized_batch_names:
                raise ValueError(f"Duplicate card image name in batch: {image_name}")
            normalized_batch_names.add(normalized_name)

        inventory_started = perf_counter()
        existing_names = {
            name.casefold()
            for name in self.existing_card_image_names(retain_catalogs=True)
        }
        retained_catalogs = self._pending_card_catalogs
        self._pending_card_catalogs = {}
        conflicts = [
            item.image_name
            for item in requested
            if item.image_name.casefold() in existing_names
        ]
        if conflicts:
            raise ValueError(f"Card image name already exists: {conflicts[0]}")

        source_file = self.get_game_file_name("data.dat")
        source_records = [
            record
            for record in manifest.files
            if record.source_file.casefold() == source_file.casefold()
        ]
        large_folder = self._card_image_folder_spelling(source_records, "card")
        mini_folder = self._card_image_folder_spelling(source_records, "mini")
        mini_size = self._sample_image_size("mini/")
        available_memory = estimate_available_memory_bytes()
        worker_count = select_worker_count(
            len(requested),
            hard_cap=4,
            fallback_cap=2,
            available_memory_bytes=available_memory,
        )
        preparation_started = perf_counter()
        prepare = partial(self._prepare_named_card_image_pair, mini_size=mini_size)
        if worker_count == 1:
            prepared = tuple(prepare(item) for item in requested)
        else:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="card-image-save",
            ) as executor:
                prepared = tuple(executor.map(prepare, requested))

        large_records = [
            ProjectFileRecord(
                source_file=source_file,
                relative_path=f"{large_folder}/{item.image_name}",
                workspace_path=f"data/{large_folder}/{item.image_name}",
                file_kind="image",
                storage_format="binary",
                language=None,
                generated_on_pack=False,
                virtual=False,
                compressed=False,
            )
            for item in prepared
        ]
        mini_records = [
            ProjectFileRecord(
                source_file=source_file,
                relative_path=f"{mini_folder}/{item.image_name}",
                workspace_path=f"data/{mini_folder}/{item.image_name}",
                file_kind="image",
                storage_format="binary",
                language=None,
                generated_on_pack=False,
                virtual=False,
                compressed=False,
            )
            for item in prepared
        ]
        ordered_source_records = self._plan_card_image_record_order(
            source_records,
            large_records,
            mini_records,
        )
        planned_orders = {
            id(record): order for order, record in enumerate(ordered_source_records)
        }
        for record in (*large_records, *mini_records):
            record.order = planned_orders[id(record)]
        new_records = tuple((*large_records, *mini_records))
        planned_files = [
            replace(record, order=planned_orders.get(id(record), record.order))
            for record in manifest.files
        ]
        planned_files.extend(replace(record) for record in new_records)
        planned_manifest = replace(
            manifest,
            files=planned_files,
            game_files=dict(manifest.game_files),
        )
        planned_manifest.validate()
        self._validate_manifest_rule_metadata(planned_manifest)
        self._pending_card_catalogs = retained_catalogs

        destinations = tuple(
            record.workspace_path for record in new_records if record.workspace_path
        )
        manifest_mutated = False
        manifest_duration = 0.0
        write_duration = 0.0
        original_orders = tuple((record, record.order) for record in source_records)
        write_started = perf_counter()
        try:
            write = partial(
                self._write_prepared_card_image_pair,
                large_folder=large_folder,
                mini_folder=mini_folder,
            )
            if worker_count == 1:
                for item in prepared:
                    write(item)
            else:
                with ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="card-image-write",
                ) as executor:
                    tuple(executor.map(write, prepared))

            self._validate_physical_workspace_files(planned_manifest)
            for record, order in original_orders:
                record.order = planned_orders[id(record)]
            manifest.files.extend(new_records)
            manifest_mutated = True
            write_duration = perf_counter() - write_started
            if save_manifest:
                manifest_started = perf_counter()
                self.save(manifest)
                manifest_duration = perf_counter() - manifest_started
        except Exception:
            self._pending_card_catalogs.clear()
            if manifest_mutated:
                del manifest.files[-len(new_records) :]
            for record, order in original_orders:
                record.order = order
            for destination in destinations:
                self._connection.delete_file(destination)
            raise

        logging.info(
            "Card image batch saved: pairs=%d workers=%d available_memory=%s "
            "inventory=%.3fs preparation=%.3fs write=%.3fs manifest=%.3fs "
            "overall=%.3fs",
            len(requested),
            worker_count,
            available_memory,
            preparation_started - inventory_started,
            write_started - preparation_started,
            write_duration,
            manifest_duration,
            perf_counter() - started,
        )
        if save_manifest:
            self._pending_card_catalogs.clear()
        return new_records

    def read_card_image(self, image_name: str, *, mini: bool = False) -> bytes:
        folder = "mini" if mini else "card"
        record = self._find_record(
            f"{folder}/{image_name}",
            required=False,
            logical_source="data.dat",
        )
        if record is None or record.workspace_path is None:
            raise KeyError(f"Card image was not found: {folder}/{image_name}")
        return self._connection.read_image(record.workspace_path)

    def read_card_images(self, image_name: str) -> tuple[bytes, bytes]:
        return (
            self.read_card_image(image_name, mini=False),
            self.read_card_image(image_name, mini=True),
        )

    def delete_card_images(self, image_name: str) -> None:
        manifest = self._require_manifest()
        matches = [
            record
            for record in manifest.files
            if record.file_kind == "image"
            and Path(record.relative_path.replace("\\", "/")).name.casefold()
            == image_name.casefold()
        ]
        for record in matches:
            if record.workspace_path:
                self._connection.delete_file(record.workspace_path)
            manifest.files.remove(record)
        for source_file in {record.source_file.casefold() for record in matches}:
            source_records = sorted(
                (
                    record
                    for record in manifest.files
                    if record.source_file.casefold() == source_file
                ),
                key=lambda record: record.order,
            )
            for order, record in enumerate(source_records):
                record.order = order
        self.save(manifest)

    def replace_card_image(
        self,
        image_name: str,
        source: str | Path | bytes,
        *,
        mini: bool = False,
    ) -> None:
        folder = "mini" if mini else "card"
        record = next(
            (
                item
                for item in self._require_manifest().files
                if self._path_matches_suffix(
                    item.relative_path,
                    f"{folder}/{image_name}",
                )
            ),
            None,
        )
        if record is None or record.workspace_path is None:
            raise KeyError(f"Card image was not found: {folder}/{image_name}")
        size = self._connection.image_size(record.workspace_path) if mini else None
        self._connection.convert_image_to_bmp(
            source,
            record.workspace_path,
            size=size,
        )

    def exists(self, path: str | Path) -> bool:
        return self._connection.exists(path)

    def read_bytes(self, path: str | Path) -> bytes:
        return self._connection.read_bytes(path)

    def write_bytes(self, path: str | Path, data: bytes) -> Path:
        return self._connection.write_bytes(path, data)

    def read_text(self, path: str | Path) -> str:
        return self._connection.read_text(path)

    def write_text(self, path: str | Path, value: str) -> Path:
        return self._connection.write_text(path, value)

    def read_table(self, path: str | Path) -> pd.DataFrame:
        return self._connection.read_table(path)

    def write_table(
        self,
        path: str | Path,
        table: pd.DataFrame,
        columns: Iterable[str] | None = None,
    ) -> Path:
        return self._connection.write_table(path, table, columns)

    def read_external_table(self, path: str | Path) -> pd.DataFrame:
        return self._connection.read_external_table(path)

    def write_external_table(
        self,
        path: str | Path,
        table: pd.DataFrame,
        columns: Iterable[str],
    ) -> Path:
        return self._connection.write_external_table(path, table, columns)

    def prepare_image_bytes(
        self,
        source: str | Path | bytes,
        *,
        size: tuple[int, int] | None = None,
    ) -> bytes:
        return self._connection.convert_image_to_bmp_bytes(source, size=size)

    def copy_file(self, source: str | Path, path: str | Path) -> Path:
        return self._connection.copy_file(source, path)

    def delete_file(self, path: str | Path) -> None:
        self._connection.delete_file(path)

    def read_image(self, path: str | Path) -> bytes:
        return self._connection.read_image(path)

    def write_image(self, path: str | Path, data: bytes) -> Path:
        return self._connection.write_image(path, data)

    def read_audio(self, path: str | Path) -> bytes:
        return self._connection.read_audio(path)

    def write_audio(self, path: str | Path, data: bytes) -> Path:
        return self._connection.write_audio(path, data)

    def read_executable(self, path: str | Path) -> bytes:
        return self._connection.read_executable(path)

    def write_executable(self, path: str | Path, data: bytes) -> Path:
        return self._connection.write_executable(path, data)

    def read_binary(self, path: str | Path) -> bytes:
        return self._connection.read_binary(path)

    def write_binary(self, path: str | Path, data: bytes) -> Path:
        return self._connection.write_binary(path, data)

    def _build_table_handlers(self) -> dict[str, LogicalTableHandler]:
        handlers: dict[str, LogicalTableHandler] = {}
        for rule in self._subfile_rules:
            if rule.table_name is None:
                continue
            if rule.table_name in handlers:
                raise ValueError(
                    f"Duplicate logical table registration: {rule.table_name}"
                )
            handlers[rule.table_name] = LogicalTableHandler(
                name=rule.table_name,
                rule=rule,
                reader=partial(self._read_physical_table, rule),
                writer=partial(self._write_physical_table, rule),
                parameters=rule.table_parameters,
                required_parameters=rule.table_parameters,
            )
        composites = {
            "card_catalog": LogicalTableHandler(
                name="card_catalog",
                rule=None,
                reader=self._get_card_catalog,
                writer=self._save_card_catalog,
                parameters=("image_variant",),
                composite=True,
            ),
            "cards": LogicalTableHandler(
                name="cards",
                rule=None,
                reader=self._get_cards,
                writer=self._save_cards,
                parameters=("language",),
                composite=True,
            ),
            "deck_cards": LogicalTableHandler(
                name="deck_cards",
                rule=None,
                reader=self._get_deck_cards,
                writer=self._save_deck_cards,
                composite=True,
            ),
        }
        conflicts = sorted(set(handlers).intersection(composites))
        if conflicts:
            raise ValueError(
                "Physical table registrations conflict with composite tables: "
                + ", ".join(conflicts)
            )
        handlers.update(composites)
        return handlers

    def _read_physical_table(
        self,
        rule: SubfileRule,
        **parameters: object,
    ) -> pd.DataFrame:
        record = self._physical_table_record(rule, parameters)
        table = self._read_record_table(record).copy()
        if rule.table_name == "card_properties":
            return self._normalize_card_properties(table, warn_legacy=True)
        if rule.table_name == "card_passcodes":
            return self._normalize_card_passcodes(table)
        if rule.codec_name == "offset_string_table":
            return self._normalize_indexed_text_table(
                table,
                resource=record.relative_path,
            )
        return table

    def _write_physical_table(
        self,
        rule: SubfileRule,
        table: pd.DataFrame,
        **parameters: object,
    ) -> None:
        record = self._physical_table_record(rule, parameters)
        if rule.table_name == "card_properties":
            table = self._normalize_card_properties(table, warn_legacy=False)
        if rule.table_name == "card_passcodes":
            table = self._normalize_card_passcodes(table)
        if rule.codec_name == "offset_string_table":
            table = self._normalize_indexed_text_table(
                table,
                resource=record.relative_path,
            )
        self._write_record_table(record, table, None)

    @staticmethod
    def _normalize_card_passcodes(table: pd.DataFrame) -> pd.DataFrame:
        frame = table.reset_index(drop=True).copy()
        if "value" not in frame:
            raise ValueError("card_passcodes is missing required column 'value'.")
        values: list[str] = []
        for row_index, value in enumerate(frame["value"]):
            try:
                values.append(normalize_card_password(value))
            except ValueError as error:
                raise ValueError(f"card_passcodes row {row_index}: {error}") from error
        return pd.DataFrame({"value": values})

    @staticmethod
    def _migrate_legacy_card_passcodes(table: pd.DataFrame) -> pd.DataFrame:
        frame = table.reset_index(drop=True)
        if "value" not in frame:
            raise ValueError("card_passcodes is missing required column 'value'.")
        values: list[str] = []
        for row_index, value in enumerate(frame["value"]):
            try:
                values.append(legacy_card_password_to_hex(value))
            except ValueError as error:
                raise ValueError(f"card_passcodes row {row_index}: {error}") from error
        return pd.DataFrame({"value": values})

    @staticmethod
    def _normalize_indexed_text_table(
        table: pd.DataFrame,
        *,
        resource: str,
    ) -> pd.DataFrame:
        frame = table.reset_index(drop=True).copy()
        missing = [column for column in ("text", "is_reserved") if column not in frame]
        if missing:
            raise ValueError(
                f"{resource} is missing indexed-text columns: {', '.join(missing)}."
            )
        texts: list[str] = []
        reserved_values: list[bool] = []
        for row_index, row in frame.iterrows():
            value = row["text"]
            text = "" if pd.isna(value) else str(value)
            reserved = ProjectRepository._parse_canonical_bool(
                row["is_reserved"],
                resource=resource,
                row_index=row_index,
            )
            texts.append(text)
            reserved_values.append(bool(reserved and row_index > 0 and text == ""))
        frame["text"] = texts
        frame["is_reserved"] = reserved_values
        return frame

    @staticmethod
    def _parse_canonical_bool(
        value: object,
        *,
        resource: str,
        row_index: int,
    ) -> bool:
        if isinstance(value, bool):
            return value
        if type(value).__name__ == "bool_":
            return bool(value)
        if isinstance(value, str) and value in {"True", "False"}:
            return value == "True"
        raise ValueError(
            f"{resource} row {row_index}: is_reserved must be the canonical "
            "boolean True or False."
        )

    @staticmethod
    def _normalize_card_properties(
        table: pd.DataFrame,
        *,
        warn_legacy: bool,
        legacy_schema: bool = False,
    ) -> pd.DataFrame:
        frame = table.reset_index(drop=True).copy()
        legacy_changes: set[str] = set()
        if "monster_type" not in frame and "card_type" in frame:
            frame["monster_type"] = frame["card_type"]
            legacy_changes.add("card_type->monster_type")
        for field in ("attack", "defense", "level"):
            if field not in frame:
                raise ValueError(
                    f"card_properties is missing required column '{field}'."
                )
        frame["attack"] = frame["attack"].astype(int)
        frame["defense"] = frame["defense"].astype(int)
        frame["level"] = frame["level"].astype(int)

        for field in (
            "monster_type_code",
            "monster_type",
            "card_category_code",
            "card_category",
            "attribute_code",
            "attribute",
        ):
            if field not in frame:
                frame[field] = None
                legacy_changes.add(field)

        class_codes: list[int] = []
        class_labels: list[str] = []
        detail_codes: list[int] = []
        detail_labels: list[str] = []
        attribute_codes: list[int] = []
        attribute_labels: list[str] = []
        for row_index, row in frame.iterrows():
            class_code, class_label = ProjectRepository._normalize_property_pair(
                row,
                row_index=row_index,
                code_field="monster_type_code",
                label_field="monster_type",
                labels=MONSTER_TYPE_LABELS,
                ignore_label_mismatch=legacy_schema,
            )
            attribute_code, attribute_label = (
                ProjectRepository._migrate_legacy_attribute(row, row_index)
                if legacy_schema
                else ProjectRepository._normalize_property_pair(
                    row,
                    row_index=row_index,
                    code_field="attribute_code",
                    label_field="attribute",
                    labels=ATTRIBUTE_LABELS,
                )
            )
            is_monster = 1 <= class_code <= 20
            if legacy_schema:
                detail_code = ProjectRepository._migrate_legacy_detail(
                    row,
                    row_index,
                    class_code,
                )
                frame.at[row_index, "attack"] = int(row["attack"]) if is_monster else 0
                frame.at[row_index, "defense"] = (
                    int(row["defense"]) if is_monster else 0
                )
                frame.at[row_index, "level"] = int(row["level"]) if is_monster else 0
            else:
                detail_code = ProjectRepository._normalize_property_pair(
                    row,
                    row_index=row_index,
                    code_field="card_category_code",
                    label_field="card_category",
                    labels=ProjectRepository._detail_labels(class_code),
                )[0]
                if not is_monster:
                    frame.at[row_index, "attack"] = 0
                    frame.at[row_index, "defense"] = 0
                    frame.at[row_index, "level"] = 0
            canonical_detail = property_label_for_code(
                detail_code,
                ProjectRepository._detail_labels(class_code),
                field="card_category_code",
            )
            class_codes.append(class_code)
            class_labels.append(class_label)
            detail_codes.append(detail_code)
            detail_labels.append(canonical_detail)
            attribute_codes.append(attribute_code)
            attribute_labels.append(attribute_label)

        frame["monster_type_code"] = class_codes
        frame["monster_type"] = class_labels
        frame["card_category_code"] = detail_codes
        frame["card_category"] = detail_labels
        frame["attribute_code"] = attribute_codes
        frame["attribute"] = attribute_labels

        derived_tributes = frame["monster_type_code"].astype(int).between(1, 20) & (
            frame["level"].astype(int) >= 8
        )
        if "requires_two_tributes" not in frame:
            legacy_changes.add("requires_two_tributes")
        frame["requires_two_tributes"] = derived_tributes
        if warn_legacy and legacy_changes:
            logging.warning(
                "card_properties legacy schema normalized; %s",
                ", ".join(sorted(legacy_changes)),
            )
        return frame[list(CARD_PROPERTY_COLUMNS)]

    @staticmethod
    def _detail_labels(class_code: int) -> dict[int, str]:
        if 1 <= class_code <= 20:
            return MONSTER_CATEGORY_LABELS
        if class_code in {21, 22}:
            return SPELL_TRAP_SUBTYPE_LABELS
        if class_code in {23, 24}:
            return {0: ""}
        return {code: "" for code in range(8)}

    @staticmethod
    def _normalize_property_pair(
        row: pd.Series,
        *,
        row_index: int,
        code_field: str,
        label_field: str,
        labels: dict[int, str],
        ignore_label_mismatch: bool = False,
    ) -> tuple[int, str]:
        code_value = row[code_field]
        label_value = row[label_field]
        has_code = ProjectRepository._has_property_value(code_value)
        has_label = ProjectRepository._has_property_value(label_value)
        if has_code:
            code = parse_property_code(code_value, field=code_field)
        elif has_label:
            code = code_for_property_label(label_value, labels, field=label_field)
        else:
            code = 0
        canonical_label = property_label_for_code(code, labels, field=code_field)
        if has_code and has_label and not ignore_label_mismatch:
            supplied_label = normalize_property_label(label_value)
            if supplied_label != canonical_label:
                raise ValueError(
                    f"card_properties row {row_index}: {label_field} "
                    f"'{label_value}' does not match {code_field} "
                    f"0x{code:02X} ({canonical_label})."
                )
        return code, canonical_label

    @staticmethod
    def _migrate_legacy_attribute(
        row: pd.Series,
        row_index: int,
    ) -> tuple[int, str]:
        if not ProjectRepository._has_property_value(row["attribute_code"]):
            raise ValueError(
                f"card_properties row {row_index}: legacy attribute_code is required."
            )
        old_code = parse_property_code(
            row["attribute_code"],
            field="attribute_code",
        )
        if old_code not in range(0, 15, 2):
            raise ValueError(
                f"card_properties row {row_index}: invalid legacy attribute_code "
                f"{old_code}."
            )
        code = old_code // 2
        return code, ATTRIBUTE_LABELS[code]

    @staticmethod
    def _migrate_legacy_detail(
        row: pd.Series,
        row_index: int,
        class_code: int,
    ) -> int:
        if not ProjectRepository._has_property_value(row["card_category_code"]):
            raise ValueError(
                f"card_properties row {row_index}: legacy card_category_code is "
                "required."
            )
        old_code = parse_property_code(
            row["card_category_code"],
            field="card_category_code",
        )
        if old_code not in {0, 4, 8, 12}:
            raise ValueError(
                f"card_properties row {row_index}: invalid legacy "
                f"card_category_code {old_code}."
            )
        if 1 <= class_code <= 20:
            return old_code // 4
        if class_code in {21, 22}:
            old_attack = int(row["attack"])
            old_low_bits = (old_attack // 1280) & 0x03
            return (old_code | old_low_bits) >> 1
        return 0

    @staticmethod
    def _has_property_value(value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        try:
            return not bool(pd.isna(value))
        except (TypeError, ValueError):
            return True

    def _physical_table_record(
        self,
        rule: SubfileRule,
        parameters: dict[str, object],
        *,
        required: bool = True,
    ) -> ProjectFileRecord | None:
        relative_path = self._resolve_table_pattern(rule, parameters)
        candidates = [
            record
            for record in self._require_manifest().files
            if not record.virtual
            and self._path_matches_suffix(record.relative_path, relative_path)
        ]
        if len(candidates) > 1:
            try:
                data_source = self.get_game_file_name("data.dat")
            except KeyError:
                data_source = None
            if data_source is not None:
                candidates = [
                    record
                    for record in candidates
                    if record.source_file.casefold() == data_source.casefold()
                ]
        if len(candidates) > 1:
            raise ValueError(
                f"Physical table '{rule.table_name}' is ambiguous for "
                f"resource '{relative_path}'."
            )
        if candidates:
            return candidates[0]
        if required:
            raise KeyError(f"Required project resource was not found: {relative_path}")
        return None

    def _resolve_table_pattern(
        self,
        rule: SubfileRule,
        parameters: dict[str, object],
    ) -> str:
        resolved = rule.source_pattern
        if "language" in rule.table_parameters:
            resolved = resolved.replace(
                "[lang]",
                self._validate_language(parameters["language"]),
            )
        return resolved

    def read_card_image_list(
        self,
        *,
        image_variant: CardImageVariant | str,
    ) -> pd.DataFrame:
        return self._get_card_catalog(image_variant=image_variant).copy()

    def write_card_image_list(
        self,
        table: pd.DataFrame,
        *,
        image_variant: CardImageVariant | str,
    ) -> None:
        self._save_card_catalog(table.copy(), image_variant=image_variant)

    def _get_card_catalog(
        self,
        *,
        image_variant: object = CardImageVariant.LARGE,
        **_: object,
    ) -> pd.DataFrame:
        relative_path = self._card_image_list_path(image_variant)
        record = self._find_record(
            relative_path,
            required=False,
            logical_source="data.dat",
        )
        if record is None:
            return pd.DataFrame(
                columns=("name", "index", "card_id", "image_name", "note")
            )
        return self._read_record_table(record)

    def _save_card_catalog(
        self,
        table: pd.DataFrame,
        *,
        image_variant: object = CardImageVariant.LARGE,
        **_: object,
    ) -> None:
        variant = CardImageVariant(str(image_variant))
        relative_path = self._card_image_list_path(variant)
        record = self._find_record(
            relative_path,
            required=False,
            logical_source="data.dat",
        )
        if record is None:
            return
        self._write_record_table(
            record,
            table,
            ("name", "index", "card_id", "image_name", "note"),
        )
        self._pending_card_catalogs.pop(variant, None)

    @staticmethod
    def _card_image_list_path(image_variant: object) -> str:
        try:
            variant = (
                image_variant
                if isinstance(image_variant, CardImageVariant)
                else CardImageVariant(str(image_variant).casefold())
            )
        except ValueError as error:
            available = ", ".join(item.value for item in CardImageVariant)
            raise ValueError(
                f"Unsupported card image variant {image_variant!r}. "
                f"Available variants: {available}."
            ) from error
        return {
            CardImageVariant.LARGE: "card/list_card.txt",
            CardImageVariant.MINI: "mini/list_card.txt",
        }[variant]

    def _get_deck_cards(self, **_: object) -> pd.DataFrame:
        return self._read_record_table(
            self._find_record("deck.ydc", logical_source="deck.ydc")
        )

    def _save_deck_cards(
        self,
        table: pd.DataFrame,
        **_: object,
    ) -> None:
        self._write_record_table(
            self._find_record("deck.ydc", logical_source="deck.ydc"),
            table,
            ("card_id",),
        )

    def _get_cards(
        self,
        *,
        language: object = DEFAULT_LANGUAGE,
        **_: object,
    ) -> pd.DataFrame:
        code = self._validate_language(language)
        physical = {
            "card_id.bin": self.get_table("card_ids"),
            "card_pass.bin": self.get_table("card_passcodes"),
            "card_pack.bin": self.get_table("card_packs"),
            "card_prop.bin": self.get_table("card_properties"),
        }
        baseline = len(physical["card_id.bin"])
        for file_name, table in physical.items():
            if len(table) != baseline:
                raise ValueError(
                    f"{file_name} has {len(table)} records, but "
                    f"card_id.bin has {baseline} records."
                )
        frame = pd.DataFrame(
            {
                "card_index": range(baseline),
                "card_id": physical["card_id.bin"]["value"].astype(int),
                "passcode": physical["card_pass.bin"]["value"].astype(str),
                "pack": physical["card_pack.bin"]["value"].astype(str),
            }
        )
        properties = physical["card_prop.bin"].reset_index(drop=True)
        for column in CARD_PROPERTY_COLUMNS:
            frame[column] = properties[column].tolist()

        for available in LANGUAGE_PREFIXES:
            name_rule = self._table_handlers["card_names"].rule
            description_rule = self._table_handlers["card_descriptions"].rule
            if name_rule is None or description_rule is None:
                raise RuntimeError("Localized physical table rules are missing.")
            name_record = self._physical_table_record(
                name_rule,
                {"language": available},
                required=False,
            )
            description_record = self._physical_table_record(
                description_rule,
                {"language": available},
                required=False,
            )
            if name_record is not None:
                names = self.get_table("card_names", language=available)
                self._validate_count(name_record, names, baseline)
                frame[f"name_{available}"] = names["value"].astype(str).tolist()
            if description_record is not None:
                descriptions = self.get_table(
                    "card_descriptions",
                    language=available,
                )
                self._validate_count(
                    description_record,
                    descriptions,
                    baseline,
                )
                frame[f"desc_{available}"] = descriptions["text"].astype(str).tolist()

        frame["name"] = (
            frame[f"name_{code}"]
            if f"name_{code}" in frame
            else pd.Series([""] * baseline)
        )
        frame["description"] = (
            frame[f"desc_{code}"]
            if f"desc_{code}" in frame
            else pd.Series([""] * baseline)
        )
        catalog = self._get_card_catalog(image_variant=CardImageVariant.LARGE)
        frame["image_name"] = self._fit_column(
            catalog,
            "image_name",
            baseline,
        )
        frame["note"] = self._fit_column(catalog, "note", baseline)
        return frame

    def _save_cards(
        self,
        table: pd.DataFrame,
        *,
        language: object = DEFAULT_LANGUAGE,
        **_: object,
    ) -> None:
        started = perf_counter()
        code = self._validate_language(language)
        frame = table.reset_index(drop=True).copy()
        if "monster_type" not in frame and "card_type" in frame:
            frame["monster_type"] = frame["card_type"]
        frame["card_index"] = range(len(frame))
        required = {
            "card_id",
            "passcode",
            "pack",
            "attack",
            "defense",
            "attribute",
            "monster_type",
            "card_category",
            "level",
        }
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"Cards table is missing columns: {', '.join(missing)}")
        custom_names = [
            str(value).casefold()
            for value in frame.get(
                "image_name",
                pd.Series(dtype=object),
            ).fillna("")
            if str(value).casefold().startswith("custom")
            or CARD_IMAGE_NAME_PATTERN.fullmatch(str(value))
        ]
        if len(custom_names) != len(set(custom_names)):
            raise ValueError("Custom card image names must be unique.")
        passcode_table = self._normalize_card_passcodes(
            pd.DataFrame({"value": frame["passcode"]})
        )
        frame["passcode"] = passcode_table["value"].tolist()
        self.save_table(
            "card_ids",
            pd.DataFrame({"value": frame["card_id"].astype(int)}),
        )
        self.save_table("card_passcodes", passcode_table)
        self.save_table(
            "card_packs",
            pd.DataFrame({"value": frame["pack"].astype(str)}),
        )
        property_columns = [
            "attack",
            "defense",
            "monster_type_code",
            "monster_type",
            "card_category_code",
            "card_category",
            "attribute_code",
            "attribute",
            "level",
        ]
        property_frame = frame[
            [column for column in property_columns if column in frame]
        ].copy()
        for code_column, label_column in (
            ("monster_type_code", "monster_type"),
            ("card_category_code", "card_category"),
            ("attribute_code", "attribute"),
        ):
            if code_column in property_frame:
                has_label = property_frame[label_column].map(
                    lambda value: bool(str(value).strip())
                )
                property_frame.loc[has_label, code_column] = None
        properties = self._normalize_card_properties(
            property_frame,
            warn_legacy=False,
        )
        self.save_table("card_properties", properties)
        name_rule = self._table_handlers["card_names"].rule
        description_rule = self._table_handlers["card_descriptions"].rule
        if name_rule is None or description_rule is None:
            raise RuntimeError("Localized physical table rules are missing.")
        for available in LANGUAGE_PREFIXES:
            if (
                self._physical_table_record(
                    name_rule,
                    {"language": available},
                    required=False,
                )
                is not None
            ):
                column = f"name_{available}"
                if available == code and "name" in frame:
                    values = frame["name"]
                elif column in frame:
                    values = frame[column]
                else:
                    continue
                self.save_table(
                    "card_names",
                    pd.DataFrame({"value": values.fillna("")}),
                    language=available,
                )
            if (
                self._physical_table_record(
                    description_rule,
                    {"language": available},
                    required=False,
                )
                is not None
            ):
                column = f"desc_{available}"
                if available == code and "description" in frame:
                    values = frame["description"]
                elif column in frame:
                    values = frame[column]
                else:
                    continue
                existing = self.get_table(
                    "card_descriptions",
                    language=available,
                )
                self.save_table(
                    "card_descriptions",
                    self._merge_indexed_text_values(existing, values),
                    language=available,
                )
        english_names = (
            frame["name_eng"]
            if "name_eng" in frame
            else frame.get("name", pd.Series([""] * len(frame)))
        )
        for image_variant in CardImageVariant:
            if (
                self._find_record(
                    self._card_image_list_path(image_variant),
                    required=False,
                    logical_source="data.dat",
                )
                is None
            ):
                continue
            existing_catalog = self._pending_card_catalogs.get(image_variant)
            if existing_catalog is None:
                existing_catalog = self._get_card_catalog(
                    image_variant=image_variant,
                )
            self._save_card_catalog(
                pd.DataFrame(
                    {
                        "name": english_names.fillna(""),
                        "index": frame["card_index"].astype(int),
                        "card_id": frame["card_id"]
                        .astype(int)
                        .map(lambda value: 0 if value < 0 else value),
                        "image_name": frame.get(
                            "image_name",
                            pd.Series([""] * len(frame)),
                        ).fillna(""),
                        "note": frame.get(
                            "note",
                            pd.Series(
                                self._fit_column(existing_catalog, "note", len(frame))
                            ),
                        ).fillna(""),
                    }
                ),
                image_variant=image_variant,
            )
        self._pending_card_catalogs.clear()
        manifest_started = perf_counter()
        self.save()
        manifest_duration = perf_counter() - manifest_started
        logging.info(
            "Cards table saved: rows=%d physical_tables_and_catalogs=%.3fs "
            "manifest=%.3fs overall=%.3fs",
            len(frame),
            manifest_started - started,
            manifest_duration,
            perf_counter() - started,
        )

    @staticmethod
    def _merge_indexed_text_values(
        existing: pd.DataFrame,
        values: pd.Series,
    ) -> pd.DataFrame:
        old = ProjectRepository._normalize_indexed_text_table(
            existing,
            resource="card_descriptions",
        )
        texts = ["" if pd.isna(value) else str(value) for value in values.tolist()]
        reserved: list[bool] = []
        for row_index, text in enumerate(texts):
            if row_index == 0 or text:
                reserved.append(False)
            elif row_index < len(old):
                reserved.append(bool(old.iloc[row_index]["is_reserved"]))
            else:
                reserved.append(True)
        return pd.DataFrame({"text": texts, "is_reserved": reserved})

    def _read_record_table(
        self,
        record: ProjectFileRecord,
    ) -> pd.DataFrame:
        if record.workspace_path is None:
            raise ValueError(
                f"Physical table has no workspace path: {record.relative_path}"
            )
        return self._connection.read_table(record.workspace_path)

    def _write_record_table(
        self,
        record: ProjectFileRecord,
        table: pd.DataFrame,
        columns: tuple[str, ...] | None,
    ) -> None:
        if record.workspace_path is None:
            raise ValueError(
                f"Physical table has no workspace path: {record.relative_path}"
            )
        self._connection.write_table(
            record.workspace_path,
            table,
            columns,
        )

    def _find_record(
        self,
        suffix: str,
        *,
        required: bool = True,
        logical_source: str | None = None,
    ) -> ProjectFileRecord | None:
        source_file = (
            self.get_game_file_name(logical_source)
            if logical_source is not None
            else None
        )
        record = next(
            (
                item
                for item in self._require_manifest().files
                if not item.virtual
                and (
                    source_file is None
                    or item.source_file.casefold() == source_file.casefold()
                )
                and self._path_matches_suffix(item.relative_path, suffix)
            ),
            None,
        )
        if record is None and required:
            raise KeyError(f"Required project resource was not found: {suffix}")
        return record

    def _resolve_record(
        self,
        resource: ProjectFileRecord | str,
    ) -> ProjectFileRecord:
        if isinstance(resource, ProjectFileRecord):
            return resource
        normalized = normalize_project_path(resource).as_posix().casefold()
        record = next(
            (
                item
                for item in self._require_manifest().files
                if (
                    item.workspace_path is not None
                    and normalize_project_path(item.workspace_path)
                    .as_posix()
                    .casefold()
                    == normalized
                )
                or (
                    normalize_project_path(item.relative_path).as_posix().casefold()
                    == normalized
                )
            ),
            None,
        )
        if record is None:
            raise KeyError(f"Project resource was not found: {resource}")
        return record

    def _prepare_named_card_image_pair(
        self,
        item: NamedCardImagePair,
        *,
        mini_size: tuple[int, int] | None,
    ) -> _PreparedCardImagePair:
        return _PreparedCardImagePair(
            image_name=item.image_name,
            large_payload=self.prepare_image_bytes(item.large_source),
            mini_payload=self.prepare_image_bytes(item.mini_source, size=mini_size),
        )

    def _write_prepared_card_image_pair(
        self,
        item: _PreparedCardImagePair,
        *,
        large_folder: str,
        mini_folder: str,
    ) -> None:
        self._connection.write_image(
            f"data/{large_folder}/{item.image_name}",
            item.large_payload,
        )
        self._connection.write_image(
            f"data/{mini_folder}/{item.image_name}",
            item.mini_payload,
        )

    @staticmethod
    def _plan_card_image_record_order(
        source_records: Sequence[ProjectFileRecord],
        large_records: Sequence[ProjectFileRecord],
        mini_records: Sequence[ProjectFileRecord],
    ) -> list[ProjectFileRecord]:
        return sorted(
            (*source_records, *large_records, *mini_records),
            key=lambda record: (
                normalize_project_path(record.relative_path).as_posix().casefold()
            ),
        )

    @staticmethod
    def _card_image_folder_spelling(
        source_records: Sequence[ProjectFileRecord],
        folder: str,
    ) -> str:
        for record in sorted(source_records, key=lambda item: item.order):
            parts = normalize_project_path(record.relative_path).parts
            if parts and parts[0].casefold() == folder.casefold():
                return parts[0]
        return folder

    def _sample_image_size(
        self,
        folder_prefix: str,
    ) -> tuple[int, int] | None:
        record = next(
            (
                item
                for item in self._require_manifest().files
                if item.file_kind == "image"
                and item.workspace_path is not None
                and self._path_matches_prefix(
                    item.relative_path,
                    folder_prefix,
                )
            ),
            None,
        )
        if record is None or record.workspace_path is None:
            return None
        return self._connection.image_size(record.workspace_path)

    def _validate_physical_workspace_files(
        self,
        manifest: ProjectManifest,
    ) -> None:
        for record in manifest.files:
            if record.virtual:
                continue
            if record.workspace_path is None:
                raise ValueError(
                    f"Physical resource has no workspace path: {record.relative_path}"
                )
            if not self._connection.exists(record.workspace_path):
                raise FileNotFoundError(
                    "Physical project resource is missing from the workspace: "
                    f"{record.workspace_path}"
                )

    def _require_table_handler(
        self,
        table_name: str,
    ) -> LogicalTableHandler:
        try:
            return self._table_handlers[table_name]
        except KeyError as error:
            available = ", ".join(self.list_tables())
            raise KeyError(
                f"Unknown table '{table_name}'. Available tables: {available}."
            ) from error

    @staticmethod
    def _validate_table_parameters(
        handler: LogicalTableHandler,
        parameters: dict[str, object],
    ) -> None:
        missing = [
            name for name in handler.required_parameters if name not in parameters
        ]
        if missing:
            raise ValueError(
                f"Table '{handler.name}' requires parameter '{missing[0]}'."
            )
        unknown = sorted(set(parameters).difference(handler.parameters))
        if unknown:
            raise ValueError(
                f"Table '{handler.name}' does not accept parameter '{unknown[0]}'."
            )

    def _validate_manifest_rule_metadata(
        self,
        manifest: ProjectManifest,
    ) -> None:
        for record in manifest.files:
            self._validate_record_rule_metadata(record)

    def _validate_record_rule_metadata(
        self,
        record: ProjectFileRecord,
    ) -> None:
        rule = self._match_subfile_rule(record.relative_path)
        if rule is None:
            if record.virtual:
                raise ValueError(
                    f"Virtual resource '{record.relative_path}' has no virtual rule."
                )
            return
        if record.virtual != rule.virtual:
            raise ValueError(
                f"Virtual metadata mismatch for '{record.relative_path}': "
                f"manifest={record.virtual}, rule={rule.virtual}."
            )

    def _match_subfile_rule(
        self,
        relative_path: str,
    ) -> SubfileRule | None:
        file_name = Path(relative_path.replace("\\", "/")).name
        return next(
            (
                rule
                for rule in reversed(self._subfile_rules)
                if rule.compiled_pattern.fullmatch(file_name) is not None
            ),
            None,
        )

    def _require_manifest(self) -> ProjectManifest:
        if self._manifest is None:
            if self._connection.exists("project.json"):
                self._manifest = self._connection.read_manifest()
            else:
                raise RuntimeError(
                    "This repository operation requires a project manifest."
                )
        return self._manifest

    @staticmethod
    def _require_dataframe(value: object) -> pd.DataFrame:
        if not isinstance(value, pd.DataFrame):
            raise TypeError("Project table value must be a DataFrame.")
        return value

    @staticmethod
    def _validate_language(value: object) -> str:
        try:
            return normalize_language_code(value)
        except ValueError as error:
            raise ValueError(f"Unsupported language prefix: {value!r}") from error

    @staticmethod
    def _validate_count(
        record: ProjectFileRecord,
        table: pd.DataFrame,
        expected: int,
    ) -> None:
        if len(table) != expected:
            file_name = Path(record.relative_path.replace("\\", "/")).name
            raise ValueError(
                f"{file_name} has {len(table)} records, but "
                f"card_id.bin has {expected} records."
            )

    @staticmethod
    def _fit_column(
        table: pd.DataFrame,
        column: str,
        count: int,
    ) -> list[str]:
        values = table[column].astype(str).tolist() if column in table else []
        return (values + [""] * count)[:count]

    @staticmethod
    def _path_matches_suffix(path: str, suffix: str) -> bool:
        normalized_path = normalize_project_path(path).as_posix().casefold()
        normalized_suffix = (
            normalize_project_path(suffix).as_posix().casefold().lstrip("/")
        )
        return normalized_path == normalized_suffix or normalized_path.endswith(
            f"/{normalized_suffix}"
        )

    @staticmethod
    def _path_matches_prefix(path: str, prefix: str) -> bool:
        normalized_path = normalize_project_path(path).as_posix().casefold()
        normalized_prefix = (
            normalize_project_path(prefix).as_posix().casefold().strip("/")
        )
        return normalized_path == normalized_prefix or normalized_path.startswith(
            f"{normalized_prefix}/"
        )
