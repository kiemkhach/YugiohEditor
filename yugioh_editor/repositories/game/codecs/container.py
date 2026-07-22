from __future__ import annotations

import logging
import struct
from collections.abc import Callable

from yugioh_editor.common.constants import (
    CONTAINER_ENTRY_HEADER_SIZE,
    CONTAINER_HEADER_SIZE,
    CONTAINER_PATH_SIZE,
    CONTAINER_SIGNATURE,
)
from yugioh_editor.common.errors import InvalidFileFormatError
from yugioh_editor.models.entities import ContainerArchive, ContainerEntry
from yugioh_editor.repositories.game.codecs.lzss import PowerOfChaosLzssCodec


class ContainerCodec:
    """Read and write KCEJYUGI archive containers."""

    _entry_numbers = struct.Struct("<III")

    def __init__(self, lzss: PowerOfChaosLzssCodec | None = None) -> None:
        self._lzss = lzss or PowerOfChaosLzssCodec()
        self._compression_selectors: dict[
            str, Callable[[ContainerEntry, bytes, bytes], bytes]
        ] = {
            "auto": self._select_auto,
            "always": self._select_compressed,
            "never": self._select_uncompressed,
            "preserve": self._select_preserved,
        }

    def decode(self, data: bytes) -> ContainerArchive:
        if len(data) < CONTAINER_HEADER_SIZE:
            raise InvalidFileFormatError("Container is smaller than its header.")
        if data[:8] != CONTAINER_SIGNATURE:
            raise InvalidFileFormatError("Invalid KCEJYUGI container signature.")

        entry_count = struct.unpack_from("<I", data, 8)[0]
        header_end = CONTAINER_HEADER_SIZE + entry_count * CONTAINER_ENTRY_HEADER_SIZE
        if header_end > len(data):
            raise InvalidFileFormatError(
                "Container entry headers exceed the file size."
            )

        entries: list[ContainerEntry] = []
        normalized_paths: set[str] = set()
        payload_ranges: list[tuple[int, int, str]] = []
        for order in range(entry_count):
            header_offset = CONTAINER_HEADER_SIZE + order * CONTAINER_ENTRY_HEADER_SIZE
            raw_path = data[header_offset : header_offset + CONTAINER_PATH_SIZE]
            relative_path = self._decode_path(raw_path)
            if not relative_path.strip():
                raise InvalidFileFormatError("Container entry path must not be empty.")
            normalized_path = self._normalize_path(relative_path)
            if normalized_path in normalized_paths:
                raise InvalidFileFormatError(
                    f"Duplicate container entry path: {relative_path}"
                )
            normalized_paths.add(normalized_path)
            offset, full_size, stored_size = self._entry_numbers.unpack_from(
                data, header_offset + CONTAINER_PATH_SIZE
            )
            end = offset + stored_size
            if offset < header_end or end > len(data):
                raise InvalidFileFormatError(
                    f"Invalid payload range for subfile: {relative_path}"
                )
            for other_start, other_end, other_path in payload_ranges:
                if offset < other_end and other_start < end:
                    raise InvalidFileFormatError(
                        "Overlapping payload ranges for subfiles "
                        f"'{other_path}' and '{relative_path}'."
                    )
            payload_ranges.append((offset, end, relative_path))
            stored_data = data[offset:end]
            compressed = stored_size != full_size
            if compressed and full_size == 0:
                raise InvalidFileFormatError(
                    f"Invalid compressed payload for subfile: {relative_path}"
                )
            payload = (
                self._lzss.decompress(stored_data, expected_size=full_size)
                if compressed
                else stored_data
            )
            if len(payload) != full_size:
                raise InvalidFileFormatError(
                    f"Payload size mismatch for subfile: {relative_path}"
                )
            entries.append(
                ContainerEntry(
                    relative_path=relative_path,
                    offset=offset,
                    full_size=full_size,
                    stored_size=stored_size,
                    data=payload,
                    compressed=compressed,
                    order=order,
                )
            )
        return ContainerArchive(entries=entries)

    def encode(self, archive: ContainerArchive, compression: str = "preserve") -> bytes:
        selector = self._compression_selectors.get(compression)
        if selector is None:
            supported = ", ".join(self._compression_selectors)
            raise ValueError(
                f"Unsupported compression mode '{compression}'. Use: {supported}."
            )

        self._validate_entries(archive.entries)
        ordered_entries = sorted(archive.entries, key=lambda item: item.order)
        compression_count = sum(
            bool(entry.data) and self._requires_compression(entry, compression)
            for entry in ordered_entries
        )
        logging.info(
            "Container encoding started entry_count=%d compression_count=%d mode=%s",
            len(ordered_entries),
            compression_count,
            compression,
        )
        payloads: list[bytes] = []
        compressed_so_far = 0
        for index, entry in enumerate(ordered_entries, start=1):
            raw = bytes(entry.data)
            should_compress = bool(raw) and self._requires_compression(
                entry,
                compression,
            )
            compressed = self._lzss.compress(raw) if should_compress else b""
            if should_compress:
                compressed_so_far += 1
                if (
                    compressed_so_far % 25 == 0
                    or compressed_so_far == compression_count
                ):
                    logging.info(
                        "Container compression progress compressed=%d/%d "
                        "entry=%d/%d path=%s",
                        compressed_so_far,
                        compression_count,
                        index,
                        len(ordered_entries),
                        entry.relative_path,
                    )
            payloads.append(selector(entry, raw, compressed))

        payload_offset = (
            CONTAINER_HEADER_SIZE + len(ordered_entries) * CONTAINER_ENTRY_HEADER_SIZE
        )
        headers = bytearray()
        current_offset = payload_offset
        for entry, payload in zip(ordered_entries, payloads):
            headers.extend(self._encode_path(entry.relative_path))
            headers.extend(
                self._entry_numbers.pack(current_offset, len(entry.data), len(payload))
            )
            current_offset += len(payload)

        output = bytearray(CONTAINER_SIGNATURE)
        output.extend(struct.pack("<I", len(ordered_entries)))
        output.extend(headers)
        output.extend(b"".join(payloads))
        logging.info(
            "Container encoding completed entry_count=%d compression_count=%d "
            "output_bytes=%d",
            len(ordered_entries),
            compression_count,
            len(output),
        )
        return bytes(output)

    @staticmethod
    def _requires_compression(entry: ContainerEntry, mode: str) -> bool:
        if mode == "never":
            return False
        if mode == "preserve":
            return entry.compressed
        return True

    @staticmethod
    def _select_auto(entry: ContainerEntry, raw: bytes, compressed: bytes) -> bytes:
        return compressed if compressed and len(compressed) < len(raw) else raw

    @staticmethod
    def _select_compressed(
        entry: ContainerEntry, raw: bytes, compressed: bytes
    ) -> bytes:
        return compressed

    @staticmethod
    def _select_uncompressed(
        entry: ContainerEntry, raw: bytes, compressed: bytes
    ) -> bytes:
        return raw

    @staticmethod
    def _select_preserved(
        entry: ContainerEntry, raw: bytes, compressed: bytes
    ) -> bytes:
        return compressed if entry.compressed else raw

    @staticmethod
    def _swap_nibbles(data: bytes) -> bytes:
        return bytes(((value & 0x0F) << 4) | ((value & 0xF0) >> 4) for value in data)

    def _decode_path(self, value: bytes) -> str:
        raw = self._swap_nibbles(value).split(b"\x00", 1)[0]
        for encoding in ("utf-8", "cp932"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise InvalidFileFormatError(
            "Container entry path uses an unsupported encoding."
        )

    def _encode_path(self, relative_path: str) -> bytes:
        normalized = relative_path.replace("/", "\\")
        if not normalized.strip():
            raise ValueError("Container entry path must not be empty.")
        try:
            encoded = normalized.encode("utf-8")
        except UnicodeEncodeError:
            encoded = normalized.encode("cp932")
        if len(encoded) >= CONTAINER_PATH_SIZE:
            raise ValueError(
                "Container path exceeds "
                f"{CONTAINER_PATH_SIZE - 1} bytes: {relative_path}"
            )
        return self._swap_nibbles(
            encoded + b"\x00" * (CONTAINER_PATH_SIZE - len(encoded))
        )

    @staticmethod
    def _normalize_path(relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/")
        if normalized.startswith("/") or (
            len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":"
        ):
            raise InvalidFileFormatError(
                f"Container entry path must be relative: {relative_path!r}"
            )
        parts = [part for part in normalized.split("/") if part not in {"", "."}]
        if not parts or ".." in parts:
            raise InvalidFileFormatError(
                f"Invalid container entry path: {relative_path!r}"
            )
        return "/".join(parts).casefold()

    @classmethod
    def _validate_entries(cls, entries: list[ContainerEntry]) -> None:
        paths: set[str] = set()
        orders: set[int] = set()
        for entry in entries:
            normalized = cls._normalize_path(entry.relative_path)
            if normalized in paths:
                raise ValueError(
                    f"Duplicate container entry path: {entry.relative_path}"
                )
            paths.add(normalized)
            if not isinstance(entry.order, int) or entry.order < 0:
                raise ValueError(f"Invalid container entry order: {entry.order!r}")
            if entry.order in orders:
                raise ValueError(f"Duplicate container entry order: {entry.order}")
            orders.add(entry.order)
