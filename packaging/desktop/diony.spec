# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve().parents[1]
PAYLOAD = Path(os.environ["DIONY_DESKTOP_PAYLOAD"]).resolve()
VERSION = os.environ.get("DIONY_DESKTOP_VERSION", "0.0.0")
TARGET_ARCH = os.environ.get("DIONY_MAC_ARCH", "universal2")
ICONS = ROOT / "packaging" / "desktop" / "icons"
MAC_ICON = ICONS / "diony-ai-app-icon.icns"
WINDOWS_ICON = ICONS / "diony-ai-app-icon.ico"
DESKTOP_ICON = MAC_ICON if sys.platform == "darwin" else WINDOWS_ICON
SOURCE_DIRS = [
    ROOT / "apps" / name / "src"
    for name in ("desk", "wizard", "wan", "finish", "producer", "director")
] + [ROOT / "packages" / "contracts" / "src"]
PACKAGES = (
    "diony_contracts",
    "diony_desk",
    "diony_wizard",
    "diony_wan",
    "diony_finish",
    "diony_producer",
    "diony_director",
)
hiddenimports = []
datas = [(str(PAYLOAD), "product")]
for package in PACKAGES:
    hiddenimports += collect_submodules(package)
    datas += collect_data_files(package)

a = Analysis(
    [str(ROOT / "packaging" / "desktop" / "entry.py")],
    pathex=[str(path) for path in SOURCE_DIRS],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe_options = {"target_arch": TARGET_ARCH} if sys.platform == "darwin" else {}
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Diony AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    icon=str(DESKTOP_ICON),
    **exe_options,
)
collection = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Diony AI",
)
if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="Diony.app",
        icon=str(MAC_ICON),
        bundle_identifier="ai.diony.desktop",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Diony",
            "CFBundleName": "Diony",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
        },
        target_arch=TARGET_ARCH,
    )
