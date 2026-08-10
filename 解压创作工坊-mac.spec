# -*- mode: python ; coding: utf-8 -*-

import os


mediainfo_library = os.environ.get("CREATOR_MEDIAINFO_LIB", "")
if not mediainfo_library or not os.path.isfile(mediainfo_library):
    raise SystemExit("CREATOR_MEDIAINFO_LIB 未指向有效的 libmediainfo.dylib")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[(mediainfo_library, "pymediainfo")],
    datas=[("./vendor/pyJianYingDraft/assets", "pyJianYingDraft/assets")],
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
    name="漫画推文",
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
    name="漫画推文",
)

app = BUNDLE(
    collect,
    name="漫画推文-v1.0.app",
    icon=None,
    bundle_identifier="com.comicpost.studio",
    info_plist={
        "CFBundleDisplayName": "漫画推文",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Copyright © 2026 Comic Post Studio",
    },
)
