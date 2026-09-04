# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for VOXini Video Studio 1.0 - a single,
self-contained Windows .exe (PyInstaller "onefile" mode). Run via
BUILD_WINDOWS.bat - do not run pyinstaller directly against this file
without first activating the app's own (not ComfyUI's!) Python venv, since
PyInstaller bundles whatever environment it's invoked from.

Onefile mode (EXE receives a.binaries/a.zipfiles/a.datas directly, with
exclude_binaries=False, and there is deliberately no COLLECT step) produces
ONE executable file with everything embedded - no separate `dist\VOXini
Video Studio\` folder, no `_internal` subfolder, no separate "portable"
zip/folder distribution. BUILD_WINDOWS.bat points --distpath directly at
the project root, so the result lands at exactly:

    VOXini Video Studio.exe   (in this project's own root folder)

Bundles as data files (all reliably embedded and extracted at runtime to
the onefile temp dir, sys._MEIPASS):
  - voxini_studio/resources/icons  (SVG icons + logo)
  - voxini_studio/resources/fonts  (bundled DejaVu Sans Bold, used for
    title cards so ffmpeg's drawtext filter never depends on a font being
    pre-installed on the user's system - see core/ffmpeg_assembly.py)
  - voxini_studio/resources/ffmpeg/win64  (bundled static ffmpeg.exe /
    ffprobe.exe, GPLv3, official gyan.dev "essentials" Windows build - see
    LICENSE-FFmpeg.txt alongside them - so the .exe never depends on the
    user having installed FFmpeg separately; see core/ffmpeg_locator.py)
  - voxini_studio/providers/workflows  (Wan2.2 ComfyUI workflow JSON
    templates)

Model weights (.safetensors) are explicitly NEVER bundled - those live in
the user-chosen ComfyUI models folder set up by INSTALL_LOCAL_AI.bat / the
in-app setup wizard, since they are tens of gigabytes and are downloaded
(with explicit per-file size/destination confirmation) only when the user
actually wants local generation.
"""
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden_imports = (
    collect_submodules("voxini_studio")
    + ["keyring.backends.Windows", "PySide6.QtSvg", "PySide6.QtSvgWidgets"]
)

a = Analysis(
    ["voxini_studio/main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("voxini_studio/resources", "voxini_studio/resources"),
        ("voxini_studio/providers/workflows", "voxini_studio/providers/workflows"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name="VOXini Video Studio",
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
    icon=None,
)
