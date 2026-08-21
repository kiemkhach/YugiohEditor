from __future__ import annotations

import logging


class WindowsGameInstallLocator:
    """Locate the Joey installation through the 32-bit Windows registry view."""

    REGISTRY_KEY = r"SOFTWARE\KONAMI\Yu-Gi-Oh! Power Of Chaos\system"
    INSTALL_DIRECTORY_VALUE = "InstallDirJ"

    @staticmethod
    def find_game_folder() -> str | None:
        try:
            import winreg
        except ImportError:
            logging.debug("Windows registry support is unavailable on this platform.")
            return None

        access = winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_32KEY
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                WindowsGameInstallLocator.REGISTRY_KEY,
                0,
                access,
            ) as registry_key:
                value, _value_type = winreg.QueryValueEx(
                    registry_key,
                    WindowsGameInstallLocator.INSTALL_DIRECTORY_VALUE,
                )
        except FileNotFoundError:
            logging.debug("The registered Joey installation was not found.")
            return None
        except OSError:
            logging.warning(
                "Unable to read the registered Joey installation.",
                exc_info=True,
            )
            return None

        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            logging.warning(
                "Ignoring malformed registry value %s at HKLM\\%s.",
                WindowsGameInstallLocator.INSTALL_DIRECTORY_VALUE,
                WindowsGameInstallLocator.REGISTRY_KEY,
            )
            return None
        return value.strip()
