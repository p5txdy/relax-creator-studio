# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files


mediainfo_library = os.environ.get("CREATOR_MEDIAINFO_LIB", "")
if not mediainfo_library or not os.path.isfile(mediainfo_library):
    raise SystemExit("CREATOR_MEDIAINFO_LIB 未指向有效的 libmediainfo.dylib")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[(mediainfo_library, "pymediainfo")],
    datas=collect_data_files("pyJianYingDraft"),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["uiautomation", "comtypes", "imageio", "numpy"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="解压创作工坊",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collect = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="解压创作工坊",
)

app = BUNDLE(
    collect,
    name="解压创作工坊-v0.2.3.app",
    icon=None,
    bundle_identifier="com.relaxcreator.studio",
    info_plist={
        "CFBundleDisplayName": "解压创作工坊",
        "CFBundleShortVersionString": "0.2.3",
        "CFBundleVersion": "0.2.3",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Copyright © 2026 Relax Creator Studio",
    },
)
