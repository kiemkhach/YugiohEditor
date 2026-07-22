from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from yugioh_editor.common.constants import CODEC_OPERATIONS
from yugioh_editor.models.entities import ContainerArchive, ContainerEntry, DeckFile
from yugioh_editor.repositories.game.codecs.card import (
    FixedHexListCodec,
    FixedStringListCodec,
    IntegerListCodec,
    NibbleStatisticsCodec,
    OffsetStringTableCodec,
    RecordTableCodec,
    RegexRecordCodec,
    TerminatedStringListCodec,
)
from yugioh_editor.repositories.game.codecs.container import ContainerCodec
from yugioh_editor.repositories.game.codecs.deck import DeckCodec
from yugioh_editor.repositories.game.codecs.text import TextCodec


class GameFolderConnection:
    """Storage-oriented connection to an original or packed game folder.

    It handles paths, raw bytes, containers, and storage-level formats without
    assigning business meaning to individual binary files.
    """

    def __init__(
        self,
        root: str | Path,
        container_codec: ContainerCodec | None = None,
        deck_codec: DeckCodec | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self._container = container_codec or ContainerCodec()
        self._deck = deck_codec or DeckCodec()
        self._integer_list = IntegerListCodec()
        self._fixed_hex = FixedHexListCodec()
        self._fixed_strings = FixedStringListCodec()
        self._offset_strings = OffsetStringTableCodec()
        self._terminated_strings = TerminatedStringListCodec()
        self._record_table = RecordTableCodec()
        self._nibble_records = NibbleStatisticsCodec()
        self._regex_records = RegexRecordCodec()
        self._text = TextCodec()
        self._decoders = {
            "container": self._decode_container_data,
            "binary": self._decode_binary_data,
            "text": self._decode_text_resource,
            "integer_list": self.read_integer_list,
            "fixed_hex_list": self.read_fixed_hex_list,
            "fixed_string_list": self.read_fixed_string_list,
            "offset_string_table": self._decode_offset_string_resource,
            "record_table": self._decode_record_resource,
            "regex_record_table": self.read_regex_record_table,
            "image": self._decode_binary_data,
            "audio": self._decode_binary_data,
        }
        self._encoders = {
            "container": self._encode_container_data,
            "binary": self._encode_binary_data,
            "text": self._encode_text_resource,
            "integer_list": self.write_integer_list,
            "fixed_hex_list": self.write_fixed_hex_list,
            "fixed_string_list": self.write_fixed_string_list,
            "offset_string_table": self._encode_offset_string_resource,
            "record_table": self._encode_record_resource,
            "regex_record_table": self.write_regex_record_table,
            "image": self._encode_binary_data,
            "audio": self._encode_binary_data,
        }
        self._validate_codec_operations()

    def use_root(self, root: str | Path) -> GameFolderConnection:
        return GameFolderConnection(root, self._container, self._deck)

    def resolve(self, relative_path: str | Path) -> Path:
        path = (self.root / relative_path).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("The requested path is outside the game folder.")
        return path

    def list_files(self, recursive: bool = False) -> list[Path]:
        if not self.root.exists():
            return []
        iterator = self.root.rglob("*") if recursive else self.root.glob("*")
        return sorted(
            (path for path in iterator if path.is_file()),
            key=lambda item: str(item).casefold(),
        )

    def list_binary_files(self) -> list[Path]:
        return [
            path
            for path in self.list_files(recursive=False)
            if path.suffix.casefold() == ".bin"
        ]

    def list_container_files(self) -> list[Path]:
        return [
            path
            for path in self.list_files(recursive=False)
            if path.read_bytes().startswith(b"KCEJYUGI")
        ]

    def read_bytes(self, relative_path: str | Path) -> bytes:
        return self.resolve(relative_path).read_bytes()

    def write_bytes(self, relative_path: str | Path, data: bytes) -> Path:
        destination = self.resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(destination, bytes(data))
        return destination

    def read_container(self, relative_path: str | Path) -> ContainerArchive:
        path = self.resolve(relative_path)
        archive = self._container.decode(path.read_bytes())
        archive.source_name = path.name
        return archive

    def write_container(
        self,
        relative_path: str | Path,
        archive: ContainerArchive,
        compression: str = "preserve",
    ) -> Path:
        return self.write_bytes(
            relative_path, self._container.encode(archive, compression)
        )

    @staticmethod
    def list_container_subfiles(archive: ContainerArchive) -> list[str]:
        return [
            entry.relative_path
            for entry in sorted(archive.entries, key=lambda item: item.order)
        ]

    @staticmethod
    def read_container_subfile(archive: ContainerArchive, relative_path: str) -> bytes:
        return GameFolderConnection._entry_map(archive)[
            GameFolderConnection._normalize(relative_path)
        ].data

    @staticmethod
    def replace_container_subfile(
        archive: ContainerArchive,
        relative_path: str,
        data: bytes,
    ) -> None:
        entry = GameFolderConnection._entry_map(archive)[
            GameFolderConnection._normalize(relative_path)
        ]
        entry.data = bytes(data)
        entry.full_size = len(data)

    @staticmethod
    def add_container_subfile(
        archive: ContainerArchive,
        relative_path: str,
        data: bytes,
        *,
        compressed: bool = True,
    ) -> ContainerEntry:
        key = GameFolderConnection._normalize(relative_path)
        if key in GameFolderConnection._entry_map(archive):
            raise ValueError(f"Container subfile already exists: {relative_path}")
        entry = ContainerEntry(
            relative_path=relative_path,
            data=bytes(data),
            full_size=len(data),
            stored_size=len(data),
            compressed=compressed,
            order=max((item.order for item in archive.entries), default=-1) + 1,
        )
        archive.entries.append(entry)
        return entry

    @staticmethod
    def delete_container_subfile(archive: ContainerArchive, relative_path: str) -> None:
        key = GameFolderConnection._normalize(relative_path)
        archive.entries[:] = [
            item
            for item in archive.entries
            if GameFolderConnection._normalize(item.relative_path) != key
        ]
        for order, item in enumerate(
            sorted(archive.entries, key=lambda value: value.order)
        ):
            item.order = order

    def read_deck(self, relative_path: str | Path) -> DeckFile:
        return self._deck.decode(self.read_bytes(relative_path))

    def write_deck(self, relative_path: str | Path, deck: DeckFile) -> Path:
        return self.write_bytes(relative_path, self._deck.encode(deck))

    def read_executable(self, relative_path: str | Path) -> bytes:
        return self.read_bytes(relative_path)

    def write_executable(self, relative_path: str | Path, data: bytes) -> Path:
        return self.write_bytes(relative_path, data)

    def read_binary(self, relative_path: str | Path) -> bytes:
        return self.read_bytes(relative_path)

    def write_binary(self, relative_path: str | Path, data: bytes) -> Path:
        return self.write_bytes(relative_path, data)

    def read_binary_file(self, relative_path: str | Path) -> bytes:
        return self.read_bytes(relative_path)

    def write_binary_file(
        self,
        relative_path: str | Path,
        data: bytes,
    ) -> Path:
        return self.write_bytes(relative_path, data)

    def decode_resource(
        self,
        codec_name: str,
        data: bytes,
        **parameters: object,
    ) -> object:
        try:
            decoder = self._decoders[codec_name]
        except KeyError as error:
            available = ", ".join(sorted(self._decoders))
            raise ValueError(
                f"Unknown codec operation '{codec_name}'. "
                f"Available operations: {available}."
            ) from error
        return decoder(data, **parameters)

    def encode_resource(
        self,
        codec_name: str,
        value: object,
        **parameters: object,
    ) -> bytes:
        try:
            encoder = self._encoders[codec_name]
        except KeyError as error:
            available = ", ".join(sorted(self._encoders))
            raise ValueError(
                f"Unknown codec operation '{codec_name}'. "
                f"Available operations: {available}."
            ) from error
        encoded = encoder(value, **parameters)
        if not isinstance(encoded, bytes):
            raise TypeError(f"Codec operation '{codec_name}' did not return bytes.")
        return encoded

    def read_integer_list(
        self,
        data: bytes,
        *,
        byte_width: int,
        signed: bool = False,
        byte_order: str = "little",
    ) -> list[int]:
        return self._integer_list.decode(
            data,
            byte_width=byte_width,
            signed=signed,
            byte_order=byte_order,
        )

    def write_integer_list(
        self,
        values: Iterable[int],
        *,
        byte_width: int,
        signed: bool = False,
        byte_order: str = "little",
    ) -> bytes:
        return self._integer_list.encode(
            values,
            byte_width=byte_width,
            signed=signed,
            byte_order=byte_order,
        )

    def read_fixed_hex_list(
        self,
        data: bytes,
        *,
        byte_width: int,
    ) -> list[str]:
        return self._fixed_hex.decode(data, byte_width=byte_width)

    def write_fixed_hex_list(
        self,
        values: Iterable[str],
        *,
        byte_width: int,
    ) -> bytes:
        return self._fixed_hex.encode(values, byte_width=byte_width)

    def read_fixed_string_list(
        self,
        data: bytes,
        *,
        record_size: int,
        encoding: str,
        terminator: bytes = b"\x00",
    ) -> list[str]:
        return self._fixed_strings.decode(
            data,
            record_size=record_size,
            encoding=encoding,
            terminator=terminator,
        )

    def write_fixed_string_list(
        self,
        values: Iterable[str],
        *,
        record_size: int,
        encoding: str,
        terminator: bytes = b"\x00",
    ) -> bytes:
        return self._fixed_strings.encode(
            values,
            record_size=record_size,
            encoding=encoding,
            terminator=terminator,
        )

    def read_offset_string_table(
        self,
        data: bytes,
        offsets: Sequence[int],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
        input_padding_policy: str = "canonical_zero",
    ) -> list[dict[str, object]]:
        return self._offset_strings.decode(
            data,
            offsets,
            encoding=encoding,
            terminator=terminator,
            alignment=alignment,
            minimum_padding=minimum_padding,
            input_padding_policy=input_padding_policy,
        )

    def write_offset_string_table(
        self,
        values: Sequence[Mapping[str, object]],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
    ) -> bytes:
        encoded, _ = self._offset_strings.encode(
            values,
            encoding=encoding,
            terminator=terminator,
            alignment=alignment,
            minimum_padding=minimum_padding,
        )
        return encoded

    def calculate_offset_string_positions(
        self,
        values: Sequence[Mapping[str, object]],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
    ) -> list[int]:
        return self._offset_strings.calculate_offsets(
            values,
            encoding=encoding,
            terminator=terminator,
            alignment=alignment,
            minimum_padding=minimum_padding,
        )

    def read_terminated_string_list(
        self,
        data: bytes,
        *,
        encoding: str,
        terminator: bytes = b"\x00",
    ) -> list[str]:
        return self._terminated_strings.decode(
            data,
            encoding=encoding,
            terminator=terminator,
        )

    def write_terminated_string_list(
        self,
        values: Iterable[str],
        *,
        encoding: str,
        terminator: bytes = b"\x00",
    ) -> bytes:
        return self._terminated_strings.encode(
            values,
            encoding=encoding,
            terminator=terminator,
        )

    def read_record_table(
        self,
        data: bytes,
        *,
        record_size: int,
        row_decoder: str,
    ) -> list[dict[str, object]]:
        decoders = {
            "nibble_statistics": self._nibble_records.decode_record,
        }
        try:
            decoder = decoders[row_decoder]
        except KeyError as error:
            raise ValueError(f"Unknown row decoder '{row_decoder}'.") from error
        return self._record_table.decode(
            data,
            record_size=record_size,
            row_decoder=decoder,
        )

    def write_record_table(
        self,
        rows: Iterable[Mapping[str, object]],
        *,
        record_size: int,
        row_encoder: str,
    ) -> bytes:
        encoders = {
            "nibble_statistics": self._nibble_records.encode_record,
        }
        try:
            encoder = encoders[row_encoder]
        except KeyError as error:
            raise ValueError(f"Unknown row encoder '{row_encoder}'.") from error
        return self._record_table.encode(
            rows,
            record_size=record_size,
            row_encoder=encoder,
        )

    def read_regex_record_table(
        self,
        data: bytes,
        *,
        pattern,
        encoding: str,
    ) -> list[dict[str, str]]:
        return self._regex_records.decode(
            data,
            pattern=pattern,
            encoding=encoding,
        )

    def write_regex_record_table(
        self,
        rows: Iterable[Mapping[str, object]],
        *,
        template: str,
        encoding: str,
    ) -> bytes:
        return self._regex_records.encode(
            rows,
            template=template,
            encoding=encoding,
        )

    def decode_text_data(
        self,
        data: bytes,
        *,
        encoding: str,
        errors: str = "strict",
    ) -> str:
        return self._text.decode(
            data,
            encoding=encoding,
            errors=errors,
        )

    def encode_text_data(
        self,
        value: str,
        *,
        encoding: str,
        errors: str = "strict",
    ) -> bytes:
        return self._text.encode(
            value,
            encoding=encoding,
            errors=errors,
        )

    def _decode_container_data(self, data: bytes) -> ContainerArchive:
        return self._container.decode(data)

    def _encode_container_data(
        self,
        value: object,
        *,
        compression: str = "preserve",
    ) -> bytes:
        if not isinstance(value, ContainerArchive):
            raise TypeError("The container operation requires a ContainerArchive.")
        return self._container.encode(value, compression)

    @staticmethod
    def _decode_binary_data(data: bytes) -> bytes:
        return bytes(data)

    @staticmethod
    def _encode_binary_data(value: object) -> bytes:
        return bytes(value)

    def _decode_text_resource(
        self,
        data: bytes,
        *,
        encoding: str,
        errors: str = "strict",
    ) -> str:
        return self.decode_text_data(data, encoding=encoding, errors=errors)

    def _encode_text_resource(
        self,
        value: object,
        *,
        encoding: str,
        errors: str = "strict",
    ) -> bytes:
        return self.encode_text_data(
            str(value),
            encoding=encoding,
            errors=errors,
        )

    def _decode_offset_string_resource(
        self,
        data: bytes,
        *,
        offsets: Sequence[int],
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
        input_padding_policy: str = "canonical_zero",
    ) -> list[str]:
        return self.read_offset_string_table(
            data,
            offsets,
            encoding=encoding,
            terminator=terminator,
            alignment=alignment,
            minimum_padding=minimum_padding,
            input_padding_policy=input_padding_policy,
        )

    def _encode_offset_string_resource(
        self,
        value: object,
        *,
        encoding: str,
        terminator: bytes = b"\x00",
        alignment: int = 2,
        minimum_padding: int = 2,
    ) -> bytes:
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise TypeError("The offset-string operation requires a sequence.")
        return self.write_offset_string_table(
            list(value),
            encoding=encoding,
            terminator=terminator,
            alignment=alignment,
            minimum_padding=minimum_padding,
        )

    def _decode_record_resource(
        self,
        data: bytes,
        *,
        record_size: int,
        row_codec: str,
    ) -> list[dict[str, object]]:
        return self.read_record_table(
            data,
            record_size=record_size,
            row_decoder=row_codec,
        )

    def _encode_record_resource(
        self,
        value: object,
        *,
        record_size: int,
        row_codec: str,
    ) -> bytes:
        if not isinstance(value, Iterable):
            raise TypeError("The record-table operation requires iterable rows.")
        return self.write_record_table(
            value,
            record_size=record_size,
            row_encoder=row_codec,
        )

    def _validate_codec_operations(self) -> None:
        for label, operations in (
            ("decoder", self._decoders),
            ("encoder", self._encoders),
        ):
            names = frozenset(operations)
            if names != CODEC_OPERATIONS:
                missing = ", ".join(sorted(CODEC_OPERATIONS.difference(names)))
                extra = ", ".join(sorted(names.difference(CODEC_OPERATIONS)))
                raise RuntimeError(
                    f"Invalid {label} codec registry; missing=[{missing}], "
                    f"extra=[{extra}]."
                )

    @staticmethod
    def _entry_map(archive: ContainerArchive) -> dict[str, ContainerEntry]:
        return {
            GameFolderConnection._normalize(item.relative_path): item
            for item in archive.entries
        }

    @staticmethod
    def _normalize(path: str) -> str:
        return path.replace("/", "\\").casefold()

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
