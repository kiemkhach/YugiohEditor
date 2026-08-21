from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from uuid import uuid4

from yugioh_editor.common.card_name_normalization import CardNameNormalizer
from yugioh_editor.common.constants import (
    CONTAINER_LOGICAL_NAMES,
    EXECUTABLE_SUFFIX,
    LOGICAL_DAT_FILES,
    VERSION_PREFIX_PATTERN,
)
from yugioh_editor.common.errors import ProjectValidationError
from yugioh_editor.models.entities import (
    ContainerArchive,
    ExecutableManifest,
    ProjectFileRecord,
    ProjectManifest,
    ProjectResource,
)
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.project.repository import (
    ProjectRepository,
    normalize_project_path,
)
from yugioh_editor.services.card_reference_data_service import (
    CardReferenceDataService,
)


class ProjectService:
    """Coordinate project use cases through repository public APIs."""

    def __init__(
        self,
        card_reference_data_service: CardReferenceDataService | None = None,
    ) -> None:
        self._card_reference_data_service = (
            CardReferenceDataService()
            if card_reference_data_service is None
            else card_reference_data_service
        )
        self._card_name_normalizer = CardNameNormalizer(
            self._card_reference_data_service
        )

    def create_project(
        self,
        project_name: str,
        workspace_root: str | Path,
        game_root: str | Path,
        version_prefix: str,
        icon_source: str | Path | None = None,
    ) -> ProjectManifest:
        name = project_name.strip()
        if not name:
            raise ProjectValidationError("Project name is required.")
        prefix = self.validate_version_prefix(version_prefix)
        game = GameRepository.from_root(game_root, self._card_name_normalizer)
        logical_dat = game.find_logical_dat_files()
        deck_path = game.find_file("deck.ydc")
        missing = [
            logical_name
            for logical_name in ("data.dat", "voice.dat", "region.dat")
            if logical_name not in logical_dat
        ]
        if deck_path is None:
            missing.append("deck.ydc")
        if missing:
            raise ProjectValidationError(
                f"Required game files are missing: {', '.join(missing)}"
            )

        project_root = Path(workspace_root).expanduser().resolve() / name
        manifest = ProjectManifest(
            name=name,
            root_path=str(project_root),
            version_prefix=prefix,
            game_files={
                **{
                    logical_name: path.name
                    for logical_name, path in logical_dat.items()
                },
                "deck.ydc": deck_path.name,
            },
        )
        destination = ProjectRepository(manifest)
        staging = destination.begin_create()
        try:
            for logical_name in ("data.dat", "voice.dat"):
                source_name = logical_dat[logical_name].name
                archive = game.read_container(source_name)
                resources = game.decode_archive(
                    archive,
                    CONTAINER_LOGICAL_NAMES[logical_name],
                )
                manifest.files.extend(staging.import_resources(resources))

            deck_resource = game.read_deck_resource(
                deck_path.name,
                "deck/deck.ydc",
            )
            manifest.files.extend(staging.import_resources([deck_resource]))
            region_name = logical_dat["region.dat"].name
            region_resource = game.read_raw_resource(
                region_name,
                f"region/{region_name}",
            )
            manifest.files.extend(staging.import_resources([region_resource]))

            executable = game.find_game_executable()
            if executable is not None:
                output_name = f"{prefix}{EXECUTABLE_SUFFIX}"
                workspace_path = f"{prefix}/{output_name}"
                executable_resource = game.read_executable_resource(
                    executable.name,
                    workspace_path,
                )
                manifest.files.extend(staging.import_resources([executable_resource]))
                manifest.executable = ExecutableManifest(
                    source_name=executable.name,
                    relative_path=workspace_path,
                )

            selected_icon = "" if icon_source is None else str(icon_source).strip()
            if selected_icon:
                manifest.icon_path = staging.import_project_icon(selected_icon)
                icon_data = staging.read_project_icon()
                if icon_data is None:
                    raise ProjectValidationError(
                        "The selected project icon could not be imported."
                    )
                game.validate_executable_icon(icon_data)

            staging.save(manifest)
            destination.commit_create(staging)
            return manifest
        except Exception:
            staging.discard()
            raise

    def load_project(self, project_root: str | Path) -> ProjectManifest:
        return ProjectRepository(project_root).load()

    def list_visible_resources(
        self,
        manifest: ProjectManifest,
    ) -> list[ProjectFileRecord]:
        return ProjectRepository.list_visible_resources(manifest)

    @staticmethod
    def tree_resource_parts(
        manifest: ProjectManifest,
        resource: ProjectFileRecord,
    ) -> tuple[str, tuple[str, ...]]:
        source_key = resource.source_file.casefold()
        if resource.file_kind == "exe":
            return (
                manifest.version_prefix,
                (Path(resource.relative_path).name,),
            )
        root_name = LOGICAL_DAT_FILES.get(source_key)
        if root_name is not None:
            parts = normalize_project_path(resource.relative_path).parts
            if parts and parts[0].casefold() == root_name.casefold():
                parts = parts[1:]
            return (
                root_name,
                (Path(resource.relative_path).name,)
                if source_key == "region.dat"
                else parts,
            )
        if source_key == "deck.ydc":
            return "deck", (Path(resource.relative_path).name,)
        return (
            Path(resource.source_file).stem,
            normalize_project_path(resource.relative_path).parts,
        )

    @staticmethod
    def read_project_text(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
    ) -> str:
        value = ProjectRepository(manifest).get_resource(resource)
        if not isinstance(value, str):
            raise TypeError("The selected project resource is not text.")
        return value

    @staticmethod
    def write_project_text(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
        value: str,
    ) -> None:
        ProjectRepository(manifest).save_resource(resource, value)

    @staticmethod
    def read_project_table(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
    ):
        value = ProjectRepository(manifest).get_resource(resource)
        if value.__class__.__name__ != "DataFrame":
            raise TypeError("The selected project resource is not a table.")
        return value

    @staticmethod
    def write_project_table(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
        table,
    ) -> None:
        ProjectRepository(manifest).save_resource(resource, table)

    @staticmethod
    def project_table_editor_columns(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
    ) -> tuple[str, ...]:
        return ProjectRepository(manifest).get_resource_editor_columns(resource)

    @staticmethod
    def read_project_binary(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
    ) -> bytes:
        value = ProjectRepository(manifest).get_resource(resource)
        if not isinstance(value, bytes):
            raise TypeError("The selected project resource is not binary.")
        return value

    @staticmethod
    def read_project_binary_preview(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
        limit: int,
    ) -> tuple[bytes, int]:
        return ProjectRepository(manifest).get_binary_preview(
            resource,
            limit,
        )

    @staticmethod
    def write_project_binary(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
        data: bytes,
    ) -> None:
        ProjectRepository(manifest).save_resource(resource, data)

    @staticmethod
    def replace_project_file(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
        source: str | Path,
    ) -> None:
        ProjectRepository(manifest).replace_resource(resource, source)

    @staticmethod
    def replace_project_image(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
        source: str | Path,
    ) -> None:
        ProjectRepository(manifest).replace_image_resource(
            resource,
            source,
        )

    @staticmethod
    def project_resource_path(
        manifest: ProjectManifest,
        resource: ProjectFileRecord | str,
    ) -> Path:
        return ProjectRepository(manifest).resource_path(resource)

    def pack_project(self, manifest: ProjectManifest) -> Path:
        pack_id = uuid4().hex[:8]
        output_path = manifest.root / "bin"
        logging.info(
            "Pack started pack_id=%s project=%s output=%s",
            pack_id,
            manifest.name,
            output_path,
        )
        staging = None
        try:
            manifest.validate()
            project = ProjectRepository(manifest)
            icon_data = project.read_project_icon()
            if icon_data is not None and manifest.executable is None:
                raise ProjectValidationError(
                    "The project has a configured icon but no executable to update."
                )
            staging = project.begin_pack()
            output = GameRepository.from_root(
                staging.root,
                self._card_name_normalizer,
            )
            resources_to_pack, grouped = self._group_project_resources(manifest)
            physical_count = sum(not record.virtual for record in resources_to_pack)
            virtual_count = len(resources_to_pack) - physical_count
            logging.info(
                "Pack resources pack_id=%s source_count=%d physical_count=%d "
                "virtual_count=%d total_count=%d unique_path_count=%d",
                pack_id,
                len(grouped),
                physical_count,
                virtual_count,
                len(resources_to_pack),
                len(
                    {
                        (
                            record.source_file.casefold(),
                            record.relative_path.replace("\\", "/").casefold(),
                        )
                        for record in resources_to_pack
                    }
                ),
            )
            source_names = [
                project.get_game_file_name(logical_name)
                for logical_name in ("data.dat", "voice.dat", "deck.ydc", "region.dat")
            ]
            if manifest.executable is not None:
                source_names.append(manifest.executable.source_name)

            def log_source(source_file: str) -> None:
                logging.info(
                    "Packing source pack_id=%s source_index=%d/%d source=%s "
                    "resource_count=%d",
                    pack_id,
                    next(
                        index
                        for index, name in enumerate(source_names, start=1)
                        if name.casefold() == source_file.casefold()
                    ),
                    len(source_names),
                    source_file,
                    len(grouped.get(source_file.casefold(), [])),
                )

            for logical_name in ("data.dat", "voice.dat"):
                source_file, archive = self._reconstruct_container(
                    project,
                    output,
                    grouped,
                    logical_name,
                )
                log_source(source_file)
                output.write_container(
                    source_file,
                    archive,
                    compression="preserve",
                )
                output.read_container(source_file)

            deck_name, deck_resource = self._reconstruct_single_resource(
                project,
                grouped,
                "deck.ydc",
                "deck.ydc project data",
            )
            log_source(deck_name)
            output.write_binary(
                deck_name,
                output.encode_deck_resource(deck_resource),
            )

            region_name, region_resource = self._reconstruct_single_resource(
                project,
                grouped,
                "region.dat",
                "Region project data",
            )
            log_source(region_name)
            output.write_binary(
                region_name,
                output.encode_raw_resource(region_resource),
            )

            if manifest.executable is not None:
                log_source(manifest.executable.source_name)
                executable_records = grouped.get(
                    manifest.executable.source_name.casefold(),
                    [],
                )
                if len(executable_records) != 1:
                    raise ProjectValidationError(
                        "Executable project data is missing or duplicated."
                    )
                card_record_count = len(project.get_table("card_ids"))
                output.write_executable_resource(
                    Path(manifest.executable.relative_path).name,
                    project.export_resources(executable_records)[0],
                    metadata={"card_record_count": card_record_count},
                    icon_data=icon_data,
                )
            result = project.commit_pack(staging)
            logging.info(
                "Pack completed pack_id=%s output=%s",
                pack_id,
                result,
            )
            return result
        except Exception:
            logging.exception(
                "Pack failed pack_id=%s project=%s output=%s",
                pack_id,
                manifest.name,
                output_path,
            )
            if staging is not None:
                staging.discard()
            raise

    def export_project_files(
        self,
        manifest: ProjectManifest,
        destination_root: str | Path,
    ) -> Path:
        manifest.validate()
        project = ProjectRepository(manifest)
        destination = Path(destination_root).expanduser().resolve()
        if destination == project.root.resolve():
            raise ProjectValidationError(
                "Export destination must not be the editable project root."
            )

        _, grouped = self._group_project_resources(manifest)
        encoder = GameRepository.from_root(
            destination,
            self._card_name_normalizer,
        )

        for logical_name in ("data.dat", "voice.dat"):
            _source_file, archive = self._reconstruct_container(
                project,
                encoder,
                grouped,
                logical_name,
            )
            export_root = encoder.use_root(
                destination / CONTAINER_LOGICAL_NAMES[logical_name]
            )
            export_root.ensure_root()
            for entry in sorted(archive.entries, key=lambda item: item.order):
                export_root.write_binary(
                    normalize_project_path(entry.relative_path).as_posix(),
                    entry.data,
                )

        deck_name, deck_resource = self._reconstruct_single_resource(
            project,
            grouped,
            "deck.ydc",
            "deck.ydc project data",
        )
        deck_root = encoder.use_root(destination / "deck")
        deck_root.ensure_root()
        deck_root.write_binary(
            deck_name,
            encoder.encode_deck_resource(deck_resource),
        )

        region_name, region_resource = self._reconstruct_single_resource(
            project,
            grouped,
            "region.dat",
            "Region project data",
        )
        region_root = encoder.use_root(destination / "region")
        region_root.ensure_root()
        region_root.write_binary(
            region_name,
            encoder.encode_raw_resource(region_resource),
        )
        return destination

    @staticmethod
    def _group_project_resources(
        manifest: ProjectManifest,
    ) -> tuple[
        tuple[ProjectFileRecord, ...],
        dict[str, list[ProjectFileRecord]],
    ]:
        resources = tuple(
            ProjectRepository.list_resources(
                manifest,
                include_virtual=True,
            )
        )
        grouped: dict[str, list[ProjectFileRecord]] = {}
        for record in resources:
            grouped.setdefault(record.source_file.casefold(), []).append(record)
        return resources, grouped

    @staticmethod
    def _reconstruct_container(
        project: ProjectRepository,
        encoder: GameRepository,
        grouped: dict[str, list[ProjectFileRecord]],
        logical_name: str,
    ) -> tuple[str, ContainerArchive]:
        source_file = project.get_game_file_name(logical_name)
        resources = project.export_resources(grouped.get(source_file.casefold(), ()))
        return source_file, encoder.encode_archive(source_file, resources)

    @staticmethod
    def _reconstruct_single_resource(
        project: ProjectRepository,
        grouped: dict[str, list[ProjectFileRecord]],
        logical_name: str,
        label: str,
    ) -> tuple[str, ProjectResource]:
        source_file = project.get_game_file_name(logical_name)
        records = grouped.get(source_file.casefold(), ())
        if len(records) != 1:
            raise ProjectValidationError(f"{label} is missing or duplicated.")
        return source_file, project.export_resources(records)[0]

    @staticmethod
    def validate_version_prefix(value: str) -> str:
        prefix = value.strip()
        if not prefix:
            raise ProjectValidationError("Version prefix is required.")
        if not VERSION_PREFIX_PATTERN.fullmatch(prefix):
            raise ProjectValidationError(
                "Version prefix may contain only letters, "
                "numbers, underscores, and hyphens."
            )
        if prefix.casefold().endswith(EXECUTABLE_SUFFIX):
            raise ProjectValidationError(
                f"Version prefix must not include '{EXECUTABLE_SUFFIX}'."
            )
        return prefix

    def run_packed_game(
        self,
        manifest: ProjectManifest,
    ) -> subprocess.Popen:
        if manifest.executable is None:
            raise FileNotFoundError("The project does not contain an executable.")
        output = GameRepository.from_root(ProjectRepository(manifest).root / "bin")
        executable = output.require_file_path(
            Path(manifest.executable.relative_path).name
        )
        return subprocess.Popen(
            [str(executable), "-full", "-speedy"],
            cwd=str(executable.parent),
        )

    @staticmethod
    def find_registered_game_folder() -> str | None:
        from yugioh_editor.infrastructure.windows_game_install_locator import (
            WindowsGameInstallLocator,
        )

        return WindowsGameInstallLocator.find_game_folder()
