"""Compare the legacy batch path with Card Detail's single-card fast path.

Run from the repository root, for example::

    python benchmarks/benchmark_card_detail_save.py --records 1115 --repeat 3

Fixture construction and verification are outside the timed section. Each run
uses a fresh copy of the same six-language project so the two strategies see
identical input bytes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.test_repository_tables import ProjectTableFixture  # noqa: E402
from yugioh_editor.common.constants import (  # noqa: E402
    DEFAULT_LANGUAGE,
    LANGUAGE_PREFIXES,
)
from yugioh_editor.models.entities import (  # noqa: E402
    CardImageVariant,
    ProjectFileRecord,
    ProjectResource,
)
from yugioh_editor.repositories.project.repository import (  # noqa: E402
    ProjectRepository,
)
from yugioh_editor.services.card_service import CardService  # noqa: E402


def _add_localized_resources(repository: ProjectRepository) -> None:
    manifest = repository._require_manifest()
    resources: list[ProjectResource] = []
    order = len(manifest.files)
    for language in LANGUAGE_PREFIXES:
        if language == DEFAULT_LANGUAGE:
            continue
        entries = (
            (
                f"bin#/card_name{language}.bin",
                f"data/bin#/card_name{language}.bin",
                "table",
                "table",
                pd.DataFrame({"value": ["", "Dragon"]}),
            ),
            (
                f"bin#/card_desc{language}.bin",
                f"data/bin#/card_desc{language}.bin",
                "table",
                "table",
                pd.DataFrame(
                    {
                        "text": ["Back", "Description"],
                        "is_reserved": [False, False],
                    }
                ),
            ),
            (
                f"bin#/card_sort{language}.bin",
                None,
                "virtual",
                "virtual",
                None,
            ),
            (
                f"bin#/card_indx{language}.bin",
                None,
                "virtual",
                "virtual",
                None,
            ),
        )
        for relative_path, workspace_path, file_kind, storage_format, value in entries:
            virtual = workspace_path is None
            resources.append(
                ProjectResource(
                    ProjectFileRecord(
                        source_file="Data.dat",
                        relative_path=relative_path,
                        workspace_path=workspace_path,
                        file_kind=file_kind,
                        storage_format=storage_format,
                        language=language,
                        generated_on_pack=virtual,
                        virtual=virtual,
                        order=order,
                    ),
                    value,
                )
            )
            order += 1
    manifest.files.extend(repository.import_resources(resources))
    repository.save(manifest)


def _resize_fixture(repository: ProjectRepository, record_count: int) -> None:
    if not 2 <= record_count <= 4095:
        raise ValueError("--records must be in the Joey range 2..4095.")

    indexes = list(range(record_count))
    card_ids = [-1, *range(record_count - 1)]
    english_names = ["Back", *(f"Card {index:04d}" for index in indexes[1:])]
    repository.save_table("card_ids", pd.DataFrame({"value": card_ids}))
    repository.save_table(
        "card_passcodes",
        pd.DataFrame({"value": ["FFFFFFFF"] * record_count}),
    )
    repository.save_table(
        "card_packs",
        pd.DataFrame({"value": ["disabled", *(["joey"] * (record_count - 1))]}),
    )
    repository.save_table(
        "card_properties",
        pd.DataFrame(
            {
                "attack": [1600] * record_count,
                "defense": [1200] * record_count,
                "monster_type_code": [1] * record_count,
                "monster_type": ["dragon"] * record_count,
                "card_category_code": [1] * record_count,
                "card_category": ["effect"] * record_count,
                "attribute_code": [2] * record_count,
                "attribute": ["dark"] * record_count,
                "level": [4] * record_count,
                "requires_two_tributes": [False] * record_count,
            }
        ),
    )
    for language in LANGUAGE_PREFIXES:
        names = (
            english_names
            if language == DEFAULT_LANGUAGE
            else ["Back", *(f"{language} Card {index:04d}" for index in indexes[1:])]
        )
        repository.save_table(
            "card_names",
            pd.DataFrame({"value": names}),
            language=language,
        )
        repository.save_table(
            "card_descriptions",
            pd.DataFrame(
                {
                    "text": [
                        "Back",
                        *(f"{language} description {index}" for index in indexes[1:]),
                    ],
                    "is_reserved": [False] * record_count,
                }
            ),
            language=language,
        )

    for variant in CardImageVariant:
        repository.save_table(
            "card_catalog",
            pd.DataFrame(
                {
                    "name": english_names,
                    "index": indexes,
                    "card_id": [0 if card_id < 0 else card_id for card_id in card_ids],
                    "image_name": [""] * record_count,
                    "note": [""] * record_count,
                }
            ),
            image_variant=variant,
        )
    repository.save()


def _instrument_method(
    owner: object,
    method_name: str,
    counters: Counter[str],
    counter_name: str,
) -> None:
    original = getattr(owner, method_name)

    def counted(*args: object, **kwargs: object) -> Any:
        counters[counter_name] += 1
        return original(*args, **kwargs)

    setattr(owner, method_name, counted)


def _instrument_repository(
    repository: ProjectRepository,
    counters: Counter[str],
) -> None:
    connection_methods = {
        "read_table": "dataframe_table_reads",
        "write_table": "dataframe_table_writes",
        "inspect_csv_table": "lightweight_csv_inspections",
        "rewrite_csv_rows": "csv_row_rewrites",
        "write_manifest": "manifest_writes",
    }
    for method_name, counter_name in connection_methods.items():
        _instrument_method(
            repository._connection,
            method_name,
            counters,
            counter_name,
        )

    original_get_table = repository.get_table
    original_save_table = repository.save_table

    def get_table(table_name: str, **parameters: object) -> pd.DataFrame:
        if table_name == "cards":
            counters["composite_card_reads"] += 1
        return original_get_table(table_name, **parameters)

    def save_table(
        table_name: str,
        table: pd.DataFrame,
        **parameters: object,
    ) -> None:
        if table_name == "cards":
            counters["composite_card_writes"] += 1
        original_save_table(table_name, table, **parameters)

    repository.get_table = get_table
    repository.save_table = save_table


def _measure_once(
    base_root: Path,
    run_root: Path,
    *,
    strategy: str,
    target_index: int,
) -> dict[str, object]:
    shutil.copytree(base_root, run_root)
    repository = ProjectRepository(run_root)
    manifest = repository.load()
    # The synthetic base is copied between runs; point its in-memory manifest
    # at the copy so CardService reuses this instrumented repository instance.
    manifest.root_path = str(run_root)
    service = CardService(repository)
    original = service.get_card_detail(manifest, target_index).to_draft()
    edited = original.clone()
    edited.attack = 1610
    edited.dirty = True
    edited.touched_fields.add("attack")

    counters: Counter[str] = Counter(
        {
            "dataframe_table_reads": 0,
            "dataframe_table_writes": 0,
            "lightweight_csv_inspections": 0,
            "csv_row_rewrites": 0,
            "manifest_writes": 0,
            "composite_card_reads": 0,
            "composite_card_writes": 0,
        }
    )
    _instrument_repository(repository, counters)
    original_begin_update: Callable[[], ProjectRepository] = repository.begin_update

    def begin_update() -> ProjectRepository:
        staging = original_begin_update()
        _instrument_repository(staging, counters)
        return staging

    repository.begin_update = begin_update
    started = perf_counter()
    if strategy == "batch":
        service.update_card(manifest, edited)
    elif strategy == "single":
        service.update_card(manifest, edited, original=original)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
    elapsed = perf_counter() - started
    measured_counts = dict(counters)

    saved_attack = int(
        repository.get_table("card_properties").iloc[target_index]["attack"]
    )
    if saved_attack != 1610:
        raise AssertionError(f"Saved attack is {saved_attack}, expected 1610.")
    return {
        "elapsed_seconds": elapsed,
        **measured_counts,
        "card_list_full_reloads": 0,
    }


def run_benchmark(record_count: int, repeat: int) -> dict[str, object]:
    if repeat < 1:
        raise ValueError("--repeat must be positive.")
    with tempfile.TemporaryDirectory(prefix="ygo-card-save-benchmark-") as directory:
        root = Path(directory)
        base_root = root / "base"
        _manifest, repository = ProjectTableFixture.build(base_root)
        _add_localized_resources(repository)
        _resize_fixture(repository, record_count)
        target_index = min(max(1, record_count // 2), record_count - 1)

        output: dict[str, object] = {
            "record_count": record_count,
            "languages": list(LANGUAGE_PREFIXES),
            "repeat": repeat,
            "target_index": target_index,
            "strategies": {},
        }
        strategies: dict[str, object] = output["strategies"]  # type: ignore[assignment]
        for strategy in ("batch", "single"):
            samples = [
                _measure_once(
                    base_root,
                    root / f"{strategy}-{iteration}",
                    strategy=strategy,
                    target_index=target_index,
                )
                for iteration in range(repeat)
            ]
            counts = {
                key: value
                for key, value in samples[-1].items()
                if key != "elapsed_seconds"
            }
            strategies[strategy] = {
                "median_elapsed_seconds": statistics.median(
                    float(sample["elapsed_seconds"]) for sample in samples
                ),
                "elapsed_samples_seconds": [
                    round(float(sample["elapsed_seconds"]), 6) for sample in samples
                ],
                **counts,
            }
        batch_elapsed = float(
            strategies["batch"]["median_elapsed_seconds"]  # type: ignore[index]
        )
        single_elapsed = float(
            strategies["single"]["median_elapsed_seconds"]  # type: ignore[index]
        )
        output["speedup"] = (
            None if single_elapsed == 0 else round(batch_elapsed / single_elapsed, 2)
        )
        return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=1115)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(args.records, args.repeat), indent=2))


if __name__ == "__main__":
    main()
