# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_data_files

# PyInstaller resolves relative source paths from the directory containing the
# spec file. Resolve the repository root explicitly so packaging works the same
# locally and on GitHub-hosted Windows runners.
project_root = os.path.abspath(os.path.join(SPECPATH, os.pardir))
entry_point = os.path.join(project_root, "main.py")

# Bundle all non-Python runtime data owned by the application package.
# This includes resources/*.csv, resources/*.icon, and ui/*.ui.
datas = collect_data_files("yugioh_editor")

a = Analysis(
    [entry_point],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YugiohEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="YugiohEditor",
)
