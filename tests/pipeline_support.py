import pandas as pd

from yugioh_editor.models.entities import (
    ContainerArchive,
    ContainerEntry,
    ProjectFileRecord,
    ProjectResource,
)
from yugioh_editor.repositories.game.repository import GameRepository


def indexed_text_table(
    texts: list[str],
    *,
    reserved: set[int] | None = None,
) -> pd.DataFrame:
    reserved = reserved or set()
    return pd.DataFrame(
        {
            "text": texts,
            "is_reserved": [index in reserved for index in range(len(texts))],
        }
    )


def encode_indexed_text_resources(
    table: pd.DataFrame,
    language: str,
    *,
    text_stem: str,
    index_stem: str,
) -> tuple[bytes, bytes]:
    if "value" in table.columns and "text" not in table.columns:
        table = indexed_text_table(table["value"].astype(str).tolist())
    repository = GameRepository.from_root(".")
    text_path = f"bin#/{text_stem}{language}.bin"
    index_path = f"bin#/{index_stem}{language}.bin"
    archive = repository.encode_archive(
        "Data.dat",
        [
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    text_path,
                    f"data/{text_path}",
                    "table",
                    "table",
                    language=language,
                    order=0,
                ),
                table,
            ),
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    index_path,
                    None,
                    "virtual",
                    "virtual",
                    language=language,
                    generated_on_pack=True,
                    virtual=True,
                    order=1,
                )
            ),
        ],
    )
    payloads = {entry.relative_path: entry.data for entry in archive.entries}
    return payloads[text_path], payloads[index_path]


def encode_description_resources(
    table: pd.DataFrame,
    language: str,
) -> tuple[bytes, bytes]:
    return encode_indexed_text_resources(
        table,
        language,
        text_stem="card_desc",
        index_stem="card_indx",
    )


def encode_dialog_resources(
    table: pd.DataFrame,
    language: str,
) -> tuple[bytes, bytes]:
    return encode_indexed_text_resources(
        table,
        language,
        text_stem="dlg_text",
        index_stem="dlg_indx",
    )


def decode_description_resource(
    data: bytes,
    indexes: bytes,
    language: str,
) -> pd.DataFrame:
    count = len(indexes) // 4
    repository = GameRepository.from_root(".")
    resources = repository.decode_archive(
        ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry(
                    "bin#/card_id.bin",
                    data=b"\x00\x00" * count,
                    order=0,
                ),
                ContainerEntry(
                    f"bin#/card_desc{language}.bin",
                    data=data,
                    order=1,
                ),
                ContainerEntry(
                    f"bin#/card_indx{language}.bin",
                    data=indexes,
                    order=2,
                ),
            ],
        ),
        "data",
    )
    return next(
        resource.value
        for resource in resources
        if resource.record.relative_path.casefold().endswith(f"card_desc{language}.bin")
    )


def decode_dialog_resource(
    data: bytes,
    indexes: bytes,
    language: str,
) -> pd.DataFrame:
    repository = GameRepository.from_root(".")
    resources = repository.decode_archive(
        ContainerArchive(
            "Data.dat",
            entries=[
                ContainerEntry(
                    f"bin#/dlg_text{language}.bin",
                    data=data,
                    order=0,
                ),
                ContainerEntry(
                    f"bin#/dlg_indx{language}.bin",
                    data=indexes,
                    order=1,
                ),
            ],
        ),
        "data",
    )
    return next(
        resource.value
        for resource in resources
        if resource.record.relative_path.casefold().endswith(f"dlg_text{language}.bin")
    )


def encode_sort_resource(names: list[str], language: str) -> bytes:
    repository = GameRepository.from_root(".")
    id_path = "bin#/card_id.bin"
    reverse_path = "bin#/card_intid.bin"
    name_path = f"bin#/card_name{language}.bin"
    sort_path = f"bin#/card_sort{language}.bin"
    card_ids = list(range(max(1, len(names))))
    archive = repository.encode_archive(
        "Data.dat",
        [
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    id_path,
                    f"data/{id_path}",
                    "table",
                    "table",
                    order=0,
                ),
                pd.DataFrame({"value": card_ids}),
            ),
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    reverse_path,
                    None,
                    "virtual",
                    "virtual",
                    generated_on_pack=True,
                    virtual=True,
                    order=1,
                )
            ),
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    name_path,
                    f"data/{name_path}",
                    "table",
                    "table",
                    language=language,
                    order=2,
                ),
                pd.DataFrame({"value": names}),
            ),
            ProjectResource(
                ProjectFileRecord(
                    "Data.dat",
                    sort_path,
                    None,
                    "virtual",
                    "virtual",
                    language=language,
                    generated_on_pack=True,
                    virtual=True,
                    order=3,
                )
            ),
        ],
    )
    return next(
        entry.data
        for entry in archive.entries
        if entry.relative_path.casefold().endswith(f"card_sort{language}.bin")
    )
