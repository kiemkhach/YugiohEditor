import struct
import unittest
from unittest.mock import Mock

from yugioh_editor.common.errors import InvalidFileFormatError
from yugioh_editor.models.entities import ContainerArchive, ContainerEntry, DeckFile
from yugioh_editor.repositories.game.codecs.container import ContainerCodec
from yugioh_editor.repositories.game.codecs.deck import DeckCodec


class ContainerAndDeckTests(unittest.TestCase):
    def test_container_round_trip_with_compression(self):
        archive = ContainerArchive(
            source_name="data.dat",
            entries=[
                ContainerEntry(
                    "bin#/sample.bin", data=b"A" * 1000, compressed=True, order=0
                ),
                ContainerEntry(
                    "card/sample.bmp", data=bytes(range(255)), compressed=False, order=1
                ),
            ],
        )
        codec = ContainerCodec()
        rebuilt = codec.decode(codec.encode(archive, "preserve"))
        self.assertEqual(
            [item.relative_path.replace("\\", "/") for item in rebuilt.entries],
            [item.relative_path.replace("\\", "/") for item in archive.entries],
        )
        self.assertEqual(
            [item.data for item in rebuilt.entries],
            [item.data for item in archive.entries],
        )

    def test_preserve_compresses_only_entries_marked_compressed(self):
        lzss = Mock()
        lzss.compress.return_value = b"compressed"
        archive = ContainerArchive(
            source_name="data.dat",
            entries=[
                ContainerEntry(
                    "large-uncompressed.bin",
                    data=b"U" * 1_000_000,
                    compressed=False,
                    order=0,
                ),
                ContainerEntry(
                    "small-compressed.bin",
                    data=b"C" * 100,
                    compressed=True,
                    order=1,
                ),
            ],
        )

        ContainerCodec(lzss=lzss).encode(archive, "preserve")

        lzss.compress.assert_called_once_with(b"C" * 100)

    def test_never_mode_does_not_invoke_lzss(self):
        lzss = Mock()
        archive = ContainerArchive(
            source_name="voice.dat",
            entries=[
                ContainerEntry(
                    "large.wav",
                    data=b"W" * 1_000_000,
                    compressed=True,
                    order=0,
                )
            ],
        )

        ContainerCodec(lzss=lzss).encode(archive, "never")

        lzss.compress.assert_not_called()

    def test_deck_round_trip(self):
        codec = DeckCodec()
        deck = DeckFile(card_ids=[1, 2, 2, 3])
        self.assertEqual(codec.decode(codec.encode(deck)).card_ids, deck.card_ids)

    def test_container_rejects_empty_duplicate_paths_and_orders(self):
        codec = ContainerCodec()
        invalid_archives = (
            ContainerArchive(
                "data.dat",
                entries=[ContainerEntry("", data=b"x", order=0)],
            ),
            ContainerArchive(
                "data.dat",
                entries=[
                    ContainerEntry("A/B.bin", data=b"x", order=0),
                    ContainerEntry("a\\b.BIN", data=b"y", order=1),
                ],
            ),
            ContainerArchive(
                "data.dat",
                entries=[
                    ContainerEntry("a.bin", data=b"x", order=0),
                    ContainerEntry("b.bin", data=b"y", order=0),
                ],
            ),
            ContainerArchive(
                "data.dat",
                entries=[ContainerEntry("/absolute.bin", data=b"x", order=0)],
            ),
            ContainerArchive(
                "data.dat",
                entries=[ContainerEntry("C:/absolute.bin", data=b"x", order=0)],
            ),
            ContainerArchive(
                "data.dat",
                entries=[ContainerEntry("../escape.bin", data=b"x", order=0)],
            ),
        )
        for archive in invalid_archives:
            with self.subTest(entries=archive.entries):
                with self.assertRaises((ValueError, InvalidFileFormatError)):
                    codec.encode(archive, "never")

    def test_container_rejects_overlapping_payload_ranges(self):
        codec = ContainerCodec()
        encoded = bytearray(
            codec.encode(
                ContainerArchive(
                    "data.dat",
                    entries=[
                        ContainerEntry("a.bin", data=b"AAAA", order=0),
                        ContainerEntry("b.bin", data=b"BBBB", order=1),
                    ],
                ),
                "never",
            )
        )
        first_offset = struct.unpack_from("<I", encoded, 12 + 256)[0]
        struct.pack_into("<I", encoded, 12 + 268 + 256, first_offset + 2)
        with self.assertRaisesRegex(
            InvalidFileFormatError,
            "Overlapping payload",
        ):
            codec.decode(bytes(encoded))

    def test_container_rejects_nonempty_compressed_payload_for_empty_entry(self):
        codec = ContainerCodec()
        encoded = bytearray(
            codec.encode(
                ContainerArchive(
                    "data.dat",
                    entries=[ContainerEntry("empty.bin", data=b"", order=0)],
                ),
                "never",
            )
        )
        encoded.append(0)
        struct.pack_into("<I", encoded, 12 + 256 + 8, 1)
        with self.assertRaisesRegex(
            InvalidFileFormatError,
            "Invalid compressed payload",
        ):
            codec.decode(bytes(encoded))
