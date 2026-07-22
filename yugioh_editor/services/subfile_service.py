from __future__ import annotations

from yugioh_editor.common.card_name_normalization import CardNameNormalizer
from yugioh_editor.models.entities import (
    ContainerArchive,
    ProjectFileRecord,
)
from yugioh_editor.repositories.game.repository import GameRepository
from yugioh_editor.repositories.project.repository import ProjectRepository
from yugioh_editor.services.card_reference_data_service import (
    CardReferenceDataService,
)


class SubfileService:
    """Coordinate archive resources through repository public APIs."""

    def __init__(
        self,
        game_repository: GameRepository | None = None,
    ) -> None:
        self._game_repository = game_repository or GameRepository.from_root(
            ".",
            CardNameNormalizer(CardReferenceDataService()),
        )

    def unpack_archive(
        self,
        archive: ContainerArchive,
        project: ProjectRepository,
        output_directory: str,
    ) -> list[ProjectFileRecord]:
        resources = self._game_repository.decode_archive(
            archive,
            output_directory,
        )
        return project.import_resources(resources)

    def pack_archive(
        self,
        source_file: str,
        records: list[ProjectFileRecord],
        project: ProjectRepository,
    ) -> ContainerArchive:
        resources = project.export_resources(records)
        return self._game_repository.encode_archive(
            source_file,
            resources,
        )
