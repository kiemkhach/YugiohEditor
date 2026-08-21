from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from yugioh_editor.infrastructure.windows_game_install_locator import (
    WindowsGameInstallLocator,
)


class WindowsGameInstallLocatorTests(unittest.TestCase):
    @staticmethod
    def fake_winreg(value: object = r"C:\Games\Joey") -> SimpleNamespace:
        key = MagicMock()
        key.__enter__.return_value = key
        return SimpleNamespace(
            HKEY_LOCAL_MACHINE=object(),
            KEY_QUERY_VALUE=0x0001,
            KEY_WOW64_32KEY=0x0200,
            OpenKey=Mock(return_value=key),
            QueryValueEx=Mock(return_value=(value, 1)),
            key=key,
        )

    def test_finds_install_dir_in_logical_key_and_32_bit_registry_view(self):
        winreg = self.fake_winreg("  C:\\Games\\Joey  ")

        with patch.dict(sys.modules, {"winreg": winreg}):
            result = WindowsGameInstallLocator.find_game_folder()

        self.assertEqual(result, r"C:\Games\Joey")
        winreg.OpenKey.assert_called_once_with(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\KONAMI\Yu-Gi-Oh! Power Of Chaos\system",
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_32KEY,
        )
        winreg.QueryValueEx.assert_called_once_with(
            winreg.key,
            "InstallDirJ",
        )

    def test_missing_registry_key_or_value_is_a_normal_case(self):
        for operation in ("OpenKey", "QueryValueEx"):
            with self.subTest(operation=operation):
                winreg = self.fake_winreg()
                getattr(winreg, operation).side_effect = FileNotFoundError(
                    "not installed"
                )
                with patch.dict(sys.modules, {"winreg": winreg}):
                    self.assertIsNone(WindowsGameInstallLocator.find_game_folder())

    def test_non_windows_environment_fails_gracefully(self):
        with (
            patch.dict(sys.modules, {"winreg": None}),
            self.assertLogs(level="DEBUG") as logs,
        ):
            result = WindowsGameInstallLocator.find_game_folder()

        self.assertIsNone(result)
        self.assertIn("registry support is unavailable", "\n".join(logs.output))

    def test_registry_access_error_is_logged_and_ignored(self):
        winreg = self.fake_winreg()
        winreg.OpenKey.side_effect = PermissionError("registry denied")

        with (
            patch.dict(sys.modules, {"winreg": winreg}),
            self.assertLogs(level="WARNING") as logs,
        ):
            result = WindowsGameInstallLocator.find_game_folder()

        self.assertIsNone(result)
        self.assertIn(
            "Unable to read the registered Joey installation",
            "\n".join(logs.output),
        )

    def test_malformed_registry_values_are_logged_and_ignored(self):
        for value in (None, 123, "", "   ", "bad\x00path"):
            with self.subTest(value=value):
                winreg = self.fake_winreg(value)
                with (
                    patch.dict(sys.modules, {"winreg": winreg}),
                    self.assertLogs(level="WARNING") as logs,
                ):
                    result = WindowsGameInstallLocator.find_game_folder()
                self.assertIsNone(result)
                self.assertIn(
                    "Ignoring malformed registry value InstallDirJ",
                    "\n".join(logs.output),
                )


if __name__ == "__main__":
    unittest.main()
