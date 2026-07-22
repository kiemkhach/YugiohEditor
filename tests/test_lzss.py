import os
import unittest

from yugioh_editor.repositories.game.codecs.lzss import PowerOfChaosLzssCodec


class LzssTests(unittest.TestCase):
    def setUp(self):
        self.codec = PowerOfChaosLzssCodec()

    def test_literal_vector(self):
        self.assertEqual(self.codec.compress(b"ABC"), b"\x07ABC")
        self.assertEqual(self.codec.decompress(b"\x07ABC", 3), b"ABC")

    def test_empty_one_byte_and_zero_payloads_terminate(self):
        for source in (b"", b"\x7f", b"\x00" * 16_384):
            with self.subTest(length=len(source)):
                packed = self.codec.compress(source)
                self.assertEqual(self.codec.decompress(packed, len(source)), source)

    def test_repeated_round_trip(self):
        source = (b"POWER OF CHAOS " * 500) + bytes(range(128))
        packed = self.codec.compress(source)
        self.assertEqual(self.codec.decompress(packed, len(source)), source)
        self.assertLess(len(packed), len(source))

    def test_random_round_trip(self):
        source = os.urandom(8192)
        self.assertEqual(
            self.codec.decompress(self.codec.compress(source), len(source)),
            source,
        )
