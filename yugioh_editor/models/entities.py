from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from yugioh_editor.common.constants import (
    VERSION_PREFIX_PATTERN,
    normalize_language_code,
    validate_language_resource_path,
)

CURRENT_PROJECT_VERSION = 4


class CardImageVariant(StrEnum):
    LARGE = "large"
    MINI = "mini"


@dataclass(frozen=True, slots=True)
class NamedCardImagePair:
    """One named large/mini image pair prepared for project persistence."""

    image_name: str
    large_source: str | Path | bytes
    mini_source: str | Path | bytes


@dataclass(slots=True)
class ContainerEntry:
    relative_path: str
    offset: int = 0
    full_size: int = 0
    stored_size: int = 0
    data: bytes = b""
    compressed: bool = False
    order: int = 0


@dataclass(slots=True)
class ContainerArchive:
    source_name: str = ""
    signature: bytes = b"KCEJYUGI"
    entries: list[ContainerEntry] = field(default_factory=list)


@dataclass(slots=True)
class DeckFile:
    card_ids: list[int] = field(default_factory=list)
    header: bytes = b"\x00" * 8


@dataclass(slots=True)
class ProjectFileRecord:
    source_file: str
    relative_path: str
    workspace_path: str | None
    file_kind: str
    storage_format: str
    language: str | None = None
    generated_on_pack: bool = False
    virtual: bool = False
    compressed: bool = False
    order: int = 0


@dataclass(slots=True)
class ProjectResource:
    record: ProjectFileRecord
    value: object = None


@dataclass(slots=True)
class ExecutableManifest:
    source_name: str
    relative_path: str


@dataclass(slots=True)
class ProjectManifest:
    name: str
    root_path: str
    version_prefix: str
    files: list[ProjectFileRecord] = field(default_factory=list)
    version: int = CURRENT_PROJECT_VERSION
    executable: ExecutableManifest | None = None
    game_files: dict[str, str] = field(default_factory=dict)
    icon_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.icon_path is None:
            value.pop("icon_path")
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectManifest:
        executable = value.get("executable")
        file_values = []
        for raw_item in value.get("files", []):
            item = dict(raw_item)
            item.pop("codec_name", None)
            item.pop("generator", None)
            file_values.append(item)
        for item in file_values:
            relative_path = str(item["relative_path"])
            validate_language_resource_path(relative_path)
            workspace_path = item.get("workspace_path")
            if workspace_path is not None:
                validate_language_resource_path(str(workspace_path))
        raw_version_prefix = value.get("version_prefix")
        manifest = cls(
            name=value["name"],
            root_path=value["root_path"],
            files=[ProjectFileRecord(**item) for item in file_values],
            version=int(value.get("version", 2)),
            version_prefix=(
                "" if raw_version_prefix is None else str(raw_version_prefix)
            ),
            executable=(
                ExecutableManifest(**executable) if executable is not None else None
            ),
            game_files={
                str(key): str(item) for key, item in value.get("game_files", {}).items()
            },
            icon_path=(
                None if value.get("icon_path") is None else str(value["icon_path"])
            ),
        )
        manifest.validate()
        return manifest

    @property
    def root(self) -> Path:
        return Path(self.root_path)

    def validate(self) -> None:
        if not self.version_prefix.strip():
            raise ValueError("Version prefix is required.")
        if not VERSION_PREFIX_PATTERN.fullmatch(self.version_prefix):
            raise ValueError(
                "Version prefix may contain only letters, numbers, "
                "underscores, and hyphens."
            )
        if not self.name.strip():
            raise ValueError("Project name must not be empty.")
        if self.icon_path is not None:
            self._validate_relative_path(self.icon_path, "project icon")

        normalized_logical_names: set[str] = set()
        normalized_game_files: set[str] = set()
        for logical_name, actual_name in self.game_files.items():
            if not logical_name.strip() or not actual_name.strip():
                raise ValueError("Game file names must not be empty.")
            logical_path = self._validate_relative_path(
                logical_name,
                "logical game file",
            )
            actual_path = self._validate_relative_path(
                actual_name,
                "game source file",
            )
            logical_key = logical_path.casefold()
            if logical_key in normalized_logical_names:
                raise ValueError(f"Duplicate logical game file: {logical_name}")
            normalized_logical_names.add(logical_key)
            actual_key = actual_path.casefold()
            if actual_key in normalized_game_files:
                raise ValueError(f"Duplicate game source file: {actual_name}")
            normalized_game_files.add(actual_key)

        resources: set[tuple[str, str]] = set()
        workspace_paths: set[str] = set()
        source_orders: dict[str, set[int]] = {}
        for record in self.files:
            if not record.source_file.strip():
                raise ValueError("Resource source file must not be empty.")
            self._validate_relative_path(
                record.source_file,
                "resource source file",
            )
            relative = self._validate_relative_path(
                record.relative_path,
                "resource",
            )
            validate_language_resource_path(relative)
            if record.language is not None:
                try:
                    normalize_language_code(record.language)
                except ValueError as error:
                    raise ValueError(
                        f"Unsupported language prefix {record.language!r}. "
                        f"Resource path: {relative!r}."
                    ) from error
            key = (record.source_file.casefold(), relative.casefold())
            if key in resources:
                raise ValueError(f"Duplicate project resource: {record.relative_path}")
            resources.add(key)

            if record.order < 0:
                raise ValueError(
                    f"Resource order must not be negative: {record.relative_path}"
                )
            source_key = record.source_file.casefold()
            orders = source_orders.setdefault(source_key, set())
            if record.order in orders:
                raise ValueError(
                    f"Duplicate resource order {record.order} in "
                    f"{record.source_file}: {record.relative_path}"
                )
            orders.add(record.order)

            if record.virtual:
                if record.workspace_path is not None:
                    raise ValueError(
                        "Virtual resources must not have a workspace path: "
                        f"{record.relative_path}"
                    )
                if not record.generated_on_pack:
                    raise ValueError(
                        "Virtual resources must be generated on pack: "
                        f"{record.relative_path}"
                    )
                if record.storage_format != "virtual":
                    raise ValueError(
                        "Virtual resources must use virtual storage: "
                        f"{record.relative_path}"
                    )
                if record.file_kind != "virtual":
                    raise ValueError(
                        "Virtual resources must use the virtual file kind: "
                        f"{record.relative_path}"
                    )
            else:
                if record.workspace_path is None:
                    raise ValueError(
                        "Physical resources require a workspace path: "
                        f"{record.relative_path}"
                    )
                workspace_path = self._validate_relative_path(
                    record.workspace_path,
                    "workspace",
                )
                workspace_key = workspace_path.casefold()
                if workspace_key in workspace_paths:
                    raise ValueError(
                        f"Duplicate project workspace path: {record.workspace_path}"
                    )
                workspace_paths.add(workspace_key)
                if record.generated_on_pack:
                    raise ValueError(
                        "Physical resources cannot be generated on pack: "
                        f"{record.relative_path}"
                    )
                if record.storage_format == "virtual" or record.file_kind == "virtual":
                    raise ValueError(
                        "Physical resources cannot use virtual metadata: "
                        f"{record.relative_path}"
                    )

        for source_key, orders in source_orders.items():
            expected = set(range(len(orders)))
            if orders != expected:
                display_name = next(
                    record.source_file
                    for record in self.files
                    if record.source_file.casefold() == source_key
                )
                raise ValueError(
                    f"Resource orders in {display_name} must be contiguous from "
                    f"0 through {len(orders) - 1}."
                )

        if self.executable is not None:
            if not self.executable.source_name.strip():
                raise ValueError("Executable source name must not be empty.")
            self._validate_relative_path(
                self.executable.source_name,
                "executable source",
            )
            self._validate_relative_path(
                self.executable.relative_path,
                "executable",
            )

    @staticmethod
    def _validate_relative_path(value: str, label: str) -> str:
        text = str(value).replace("\\", "/")
        if not text.strip():
            raise ValueError(f"{label.title()} path must not be empty.")
        windows_path = PureWindowsPath(text)
        if (
            PurePosixPath(text).is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
        ):
            raise ValueError(f"{label.title()} path must be relative: {value}")
        parts = [part for part in text.split("/") if part not in {"", "."}]
        if not parts or ".." in parts:
            raise ValueError(
                f"{label.title()} path contains invalid traversal: {value}"
            )
        return "/".join(parts)
