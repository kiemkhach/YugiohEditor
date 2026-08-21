import struct
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from yugioh_editor.repositories.game import windows_icon_resources
from yugioh_editor.repositories.game.windows_icon_resources import (
    update_executable_icon,
    validate_icon_data,
)


class _FakeResourceApi:
    def __init__(
        self,
        *,
        groups=(),
        icon_ids=(),
        fail_update_at=None,
        fail_commit=False,
    ):
        self.inventory = windows_icon_resources._ResourceInventory(
            groups=tuple(
                windows_icon_resources._GroupResource(name, language)
                for name, language in groups
            ),
            icon_ids=frozenset(icon_ids),
        )
        self.fail_update_at = fail_update_at
        self.fail_commit = fail_commit
        self.inspected_paths = []
        self.begun_paths = []
        self.updates = []
        self.end_calls = []

    def inspect(self, path):
        self.inspected_paths.append(path)
        return self.inventory

    def begin_update(self, path):
        self.begun_paths.append(path)
        return "update-handle"

    def update_resource(
        self,
        handle,
        resource_type,
        name,
        language,
        data,
    ):
        if len(self.updates) == self.fail_update_at:
            raise OSError("injected update failure")
        self.updates.append(
            (handle, resource_type, name, language, data),
        )

    def end_update(self, handle, *, discard):
        self.end_calls.append((handle, discard))
        if not discard and self.fail_commit:
            raise OSError("injected commit failure")


def _build_icon(entries):
    directory_size = 6 + len(entries) * 16
    entry_data = bytearray()
    payload_data = bytearray()
    for entry in entries:
        payload = entry[6]
        entry_data.extend(
            struct.pack(
                "<BBBBHHII",
                *entry[:6],
                len(payload),
                directory_size + len(payload_data),
            )
        )
        payload_data.extend(payload)
    return (
        struct.pack("<HHH", 0, 1, len(entries))
        + bytes(entry_data)
        + bytes(payload_data)
    )


def _png_payload(width, height, color):
    output = BytesIO()
    Image.new("RGBA", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def _dib_icon(width, height, color):
    output = BytesIO()
    Image.new("RGBA", (width, height), color).save(
        output,
        format="ICO",
        sizes=[(width, height)],
        bitmap_format="bmp",
    )
    return output.getvalue()


class WindowsIconValidationTests(unittest.TestCase):
    def test_accepts_a_valid_icon(self):
        icon_data = _build_icon([(32, 32, 0, 0, 1, 32, _png_payload(32, 32, "red"))])

        self.assertIsNone(validate_icon_data(icon_data))

    def test_accepts_a_valid_dib_icon(self):
        self.assertIsNone(validate_icon_data(_dib_icon(32, 32, "blue")))

    def test_rejects_a_bounded_non_image_payload(self):
        icon_data = _build_icon([(32, 32, 0, 0, 1, 32, b"bounded-but-not-image-data")])

        with self.assertRaisesRegex(ValueError, "entry 0.*valid image data"):
            validate_icon_data(icon_data)

    def test_rejects_payload_dimensions_that_disagree_with_directory(self):
        icon_data = _build_icon([(32, 32, 0, 0, 1, 32, _png_payload(16, 16, "purple"))])

        with self.assertRaisesRegex(ValueError, "entry 0.*valid image data"):
            validate_icon_data(icon_data)

    def test_rejects_malformed_headers_and_entries(self):
        valid = _build_icon([(32, 32, 0, 0, 1, 32, _png_payload(32, 32, "green"))])
        malformed = {
            "truncated header": b"\0\0\1",
            "nonzero header reserved": b"\1\0" + valid[2:],
            "wrong type": valid[:2] + b"\2\0" + valid[4:],
            "zero image count": struct.pack("<HHH", 0, 1, 0),
            "truncated directory": struct.pack("<HHH", 0, 1, 1) + b"\0" * 15,
            "nonzero entry reserved": valid[:9] + b"\1" + valid[10:],
            "empty payload": valid[:14] + b"\0\0\0\0" + valid[18:],
            "payload overlaps directory": valid[:18]
            + struct.pack("<I", 6)
            + valid[22:],
            "payload out of bounds": valid[:14]
            + struct.pack("<I", len(valid))
            + valid[18:],
        }

        for label, icon_data in malformed.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_icon_data(icon_data)

    def test_rejects_overlapping_image_payloads(self):
        directory_end = 6 + 2 * 16
        icon_data = (
            struct.pack("<HHH", 0, 1, 2)
            + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 8, 4, directory_end)
            + struct.pack(
                "<BBBBHHII",
                32,
                32,
                0,
                0,
                1,
                32,
                3,
                directory_end + 2,
            )
            + b"abcdef"
        )

        with self.assertRaisesRegex(ValueError, "payloads overlap"):
            validate_icon_data(icon_data)


class WindowsIconUpdateTests(unittest.TestCase):
    def _update_with(self, api, icon_data):
        with patch.object(
            windows_icon_resources,
            "_create_resource_api",
            return_value=api,
        ):
            update_executable_icon("staging/game_pc.exe", icon_data)

    def test_generates_a_multi_image_group_and_no_group_fallback(self):
        small_payload = _png_payload(16, 16, "blue")
        large_payload = _png_payload(256, 256, "red")
        icon_data = _build_icon(
            [
                (16, 16, 0, 0, 1, 32, small_payload),
                (0, 0, 0, 0, 1, 32, large_payload),
            ]
        )
        api = _FakeResourceApi()

        self._update_with(api, icon_data)

        self.assertEqual(api.inspected_paths, [Path("staging/game_pc.exe")])
        self.assertEqual(api.begun_paths, [Path("staging/game_pc.exe")])
        self.assertEqual(api.end_calls, [("update-handle", False)])
        self.assertEqual(
            [(item[1], item[2], item[3], item[4]) for item in api.updates[:2]],
            [
                (3, 1, 0, small_payload),
                (3, 2, 0, large_payload),
            ],
        )
        expected_group = (
            struct.pack("<HHH", 0, 1, 2)
            + struct.pack("<BBBBHHIH", 16, 16, 0, 0, 1, 32, len(small_payload), 1)
            + struct.pack("<BBBBHHIH", 0, 0, 0, 0, 1, 32, len(large_payload), 2)
        )
        self.assertEqual(
            api.updates[2],
            ("update-handle", 14, 1, 0, expected_group),
        )

    def test_preserves_group_names_and_languages_and_avoids_icon_ids(self):
        icon_data = _build_icon(
            [
                (16, 16, 0, 0, 1, 32, _png_payload(16, 16, "red")),
                (32, 32, 0, 0, 1, 32, _png_payload(32, 32, "blue")),
            ]
        )
        groups = ((7, 1033), ("MAIN_ICON", 1041), ("MAIN_ICON", 1033))
        api = _FakeResourceApi(groups=groups, icon_ids=(1, 2, 4))

        self._update_with(api, icon_data)

        icon_updates = [item for item in api.updates if item[1] == 3]
        group_updates = [item for item in api.updates if item[1] == 14]
        self.assertEqual(
            [(item[2], item[3]) for item in icon_updates],
            [(3, 1033), (5, 1033), (3, 1041), (5, 1041)],
        )
        self.assertEqual(
            [(item[2], item[3]) for item in group_updates],
            list(groups),
        )
        self.assertEqual({item[1] for item in api.updates}, {3, 14})
        self.assertTrue(all(item[4] for item in api.updates))
        for group_update in group_updates:
            group_entries = group_update[4][6:]
            self.assertEqual(
                [
                    struct.unpack_from("<H", group_entries, 12)[0],
                    struct.unpack_from("<H", group_entries, 26)[0],
                ],
                [3, 5],
            )

    def test_update_failure_discards_without_deletion_calls(self):
        icon_data = _build_icon([(16, 16, 0, 0, 1, 32, _png_payload(16, 16, "red"))])
        api = _FakeResourceApi(groups=((1, 1033),), fail_update_at=1)

        with self.assertRaisesRegex(OSError, "injected update failure"):
            self._update_with(api, icon_data)

        self.assertEqual(api.end_calls, [("update-handle", True)])
        self.assertTrue(all(item[1] in {3, 14} for item in api.updates))
        self.assertTrue(
            all(isinstance(item[4], bytes) and item[4] for item in api.updates)
        )

    def test_commit_failure_is_followed_by_a_discard_attempt(self):
        icon_data = _build_icon([(16, 16, 0, 0, 1, 32, _png_payload(16, 16, "green"))])
        api = _FakeResourceApi(fail_commit=True)

        with self.assertRaisesRegex(OSError, "injected commit failure"):
            self._update_with(api, icon_data)

        self.assertEqual(
            api.end_calls,
            [("update-handle", False), ("update-handle", True)],
        )

    def test_non_windows_update_fails_clearly(self):
        icon_data = _build_icon([(16, 16, 0, 0, 1, 32, _png_payload(16, 16, "blue"))])

        with (
            patch.object(windows_icon_resources.sys, "platform", "linux"),
            self.assertRaisesRegex(OSError, "only supported on Windows"),
        ):
            update_executable_icon("game_pc.exe", icon_data)


if __name__ == "__main__":
    unittest.main()
