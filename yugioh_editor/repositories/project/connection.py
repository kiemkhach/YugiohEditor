from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path

import pandas as pd
from PIL import Image

from yugioh_editor.common.constants import PROJECT_FILE_NAME
from yugioh_editor.models.entities import ProjectManifest


class ProjectFolderConnection:
    """Typed connection to an analyzed project folder.

    Structured lists and tables are persisted as CSV content while preserving
    the original game file name and extension recorded by the manifest.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def use_root(self, root: str | Path) -> ProjectFolderConnection:
        return ProjectFolderConnection(root)

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def create_staging_sibling(self, label: str) -> ProjectFolderConnection:
        self.root.parent.mkdir(parents=True, exist_ok=True)
        path = Path(
            tempfile.mkdtemp(
                prefix=f".{self.root.name}.{label}.",
                suffix=".tmp",
                dir=str(self.root.parent),
            )
        )
        return ProjectFolderConnection(path)

    def create_staging_clone(self, label: str) -> ProjectFolderConnection:
        staging = self.create_staging_sibling(label)
        if self.root.exists():
            for source_directory, directory_names, file_names in os.walk(self.root):
                relative = Path(source_directory).relative_to(self.root)
                destination_directory = staging.root / relative
                destination_directory.mkdir(parents=True, exist_ok=True)
                for directory_name in directory_names:
                    (destination_directory / directory_name).mkdir(exist_ok=True)
                for file_name in file_names:
                    source = Path(source_directory) / file_name
                    destination = destination_directory / file_name
                    try:
                        os.link(source, destination)
                    except OSError:
                        shutil.copy2(source, destination)
        return staging

    def commit_staging_root(
        self,
        staging: ProjectFolderConnection,
    ) -> None:
        if self.root.exists():
            raise FileExistsError(f"Project directory already exists: {self.root}")
        os.replace(staging.root, self.root)

    def discard_root(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def replace_directory(
        self,
        staging: ProjectFolderConnection,
        relative_path: str | Path,
    ) -> Path:
        destination = self.resolve(relative_path)
        backup = destination.with_name(f".{destination.name}.backup")
        if backup.exists():
            shutil.rmtree(backup)
        moved_old = False
        try:
            if destination.exists():
                os.replace(destination, backup)
                moved_old = True
            os.replace(staging.root, destination)
            if backup.exists():
                shutil.rmtree(backup)
            return destination
        except Exception:
            if destination.exists() and moved_old:
                shutil.rmtree(destination)
            if backup.exists():
                os.replace(backup, destination)
            raise

    def resolve(self, relative_path: str | Path) -> Path:
        path = (self.root / relative_path).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("The requested path is outside the project folder.")
        return path

    def exists(self, relative_path: str | Path) -> bool:
        return self.resolve(relative_path).exists()

    def list_files(
        self, relative_path: str | Path = ".", recursive: bool = True
    ) -> list[Path]:
        directory = self.resolve(relative_path)
        if not directory.exists():
            return []
        iterator = directory.rglob("*") if recursive else directory.glob("*")
        return sorted(
            (path for path in iterator if path.is_file()),
            key=lambda item: str(item).casefold(),
        )

    def list_directories(self, relative_path: str | Path = ".") -> list[Path]:
        directory = self.resolve(relative_path)
        if not directory.exists():
            return []
        return sorted(
            (path for path in directory.iterdir() if path.is_dir()),
            key=lambda item: str(item).casefold(),
        )

    def read_bytes(self, relative_path: str | Path) -> bytes:
        return self.resolve(relative_path).read_bytes()

    def read_bytes_preview(
        self,
        relative_path: str | Path,
        limit: int,
    ) -> tuple[bytes, int]:
        if limit <= 0:
            raise ValueError("Preview limit must be positive.")
        path = self.resolve(relative_path)
        size = path.stat().st_size
        with path.open("rb") as stream:
            return stream.read(limit), size

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        destination = self.resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(destination, bytes(data))
        return destination

    def copy_file(self, source: str | Path, relative_path: str | Path) -> Path:
        destination = self.resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        os.close(handle)
        try:
            shutil.copy2(Path(source), temporary_name)
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return destination

    def delete_file(self, relative_path: str | Path) -> None:
        path = self.resolve(relative_path)
        if path.exists():
            path.unlink()

    def read_text(self, relative_path: str | Path, encoding: str = "utf-8") -> str:
        path = self.resolve(relative_path)
        with path.open("r", encoding=encoding, newline="") as stream:
            return stream.read()

    def write_text(
        self,
        relative_path: str | Path,
        value: str,
        encoding: str = "utf-8",
    ) -> Path:
        return self.write_bytes(relative_path, value.encode(encoding))

    def read_manifest(self) -> ProjectManifest:
        value = json.loads(self.read_text(PROJECT_FILE_NAME, "utf-8"))
        return ProjectManifest.from_dict(value)

    def write_manifest(self, manifest: ProjectManifest) -> Path:
        text = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
        return self.write_text(PROJECT_FILE_NAME, text, "utf-8")

    def read_table(self, relative_path: str | Path) -> pd.DataFrame:
        path = self.resolve(relative_path)
        if path.stat().st_size == 0:
            return pd.DataFrame()
        return pd.read_csv(path, dtype=object, keep_default_na=False)

    def write_table(
        self,
        relative_path: str | Path,
        table: pd.DataFrame,
        columns: Iterable[str] | None = None,
    ) -> Path:
        destination = self.resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame = table.copy()
        if columns is not None:
            for column in columns:
                if column not in frame.columns:
                    frame[column] = ""
            frame = frame[list(columns)]
        text = frame.to_csv(index=False, lineterminator="\n")
        self._atomic_write(destination, text.encode("utf-8-sig"))
        return destination

    @staticmethod
    def read_external_table(path: str | Path) -> pd.DataFrame:
        source = Path(path).expanduser().resolve()
        return pd.read_csv(
            source,
            dtype=object,
            keep_default_na=False,
            encoding="utf-8-sig",
        )

    @staticmethod
    def write_external_table(
        path: str | Path,
        table: pd.DataFrame,
        columns: Iterable[str],
    ) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame = table.copy()
        for column in columns:
            if column not in frame.columns:
                frame[column] = ""
        text = frame[list(columns)].to_csv(index=False, lineterminator="\n")
        ProjectFolderConnection._atomic_write(
            destination,
            text.encode("utf-8-sig"),
        )
        return destination

    def read_int_list(
        self, relative_path: str | Path, column: str = "value"
    ) -> list[int]:
        table = self.read_table(relative_path)
        if column not in table.columns:
            return []
        return [int(value) for value in table[column].tolist()]

    def write_int_list(
        self,
        relative_path: str | Path,
        values: Iterable[int],
        column: str = "value",
    ) -> Path:
        return self.write_table(relative_path, pd.DataFrame({column: list(values)}))

    def read_string_list(
        self, relative_path: str | Path, column: str = "value"
    ) -> list[str]:
        table = self.read_table(relative_path)
        if column not in table.columns:
            return []
        return table[column].astype(str).tolist()

    def write_string_list(
        self,
        relative_path: str | Path,
        values: Iterable[str],
        column: str = "value",
    ) -> Path:
        return self.write_table(relative_path, pd.DataFrame({column: list(values)}))

    def read_fixed_string_list(self, relative_path: str | Path) -> list[str]:
        return self.read_string_list(relative_path)

    def write_fixed_string_list(
        self,
        relative_path: str | Path,
        values: Iterable[str],
    ) -> Path:
        return self.write_string_list(relative_path, values)

    def read_image(self, relative_path: str | Path) -> bytes:
        return self.read_bytes(relative_path)

    def write_image(self, relative_path: str | Path, data: bytes) -> Path:
        return self.write_bytes(relative_path, data)

    def convert_image_to_bmp(
        self,
        source: str | Path | bytes,
        relative_path: str | Path,
        *,
        size: tuple[int, int] | None = None,
    ) -> Path:
        return self.write_image(
            relative_path,
            self.convert_image_to_bmp_bytes(source, size=size),
        )

    @staticmethod
    def convert_image_to_bmp_bytes(
        source: str | Path | bytes,
        *,
        size: tuple[int, int] | None = None,
    ) -> bytes:
        output = BytesIO()
        image_source = BytesIO(source) if isinstance(source, bytes) else source
        with Image.open(image_source) as image:
            image.load()
            if (
                isinstance(source, bytes)
                and image.format == "BMP"
                and (size is None or image.size == size)
            ):
                return source
            converted = image.convert("RGB")
            if size is not None:
                converted = converted.resize(size, Image.Resampling.LANCZOS)
            converted.save(output, format="BMP")
        return output.getvalue()

    def image_size(
        self,
        relative_path: str | Path,
    ) -> tuple[int, int]:
        with Image.open(BytesIO(self.read_image(relative_path))) as image:
            return image.size

    def read_audio(self, relative_path: str | Path) -> bytes:
        return self.read_bytes(relative_path)

    def write_audio(self, relative_path: str | Path, data: bytes) -> Path:
        return self.write_bytes(relative_path, data)

    def read_executable(self, relative_path: str | Path) -> bytes:
        return self.read_bytes(relative_path)

    def write_executable(self, relative_path: str | Path, data: bytes) -> Path:
        return self.write_bytes(relative_path, data)

    def read_binary(self, relative_path: str | Path) -> bytes:
        return self.read_bytes(relative_path)

    def write_binary(self, relative_path: str | Path, data: bytes) -> Path:
        return self.write_bytes(relative_path, data)

    @staticmethod
    def _atomic_write(destination: Path, data: bytes) -> None:
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
        )
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
