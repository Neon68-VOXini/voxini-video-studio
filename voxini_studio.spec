# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for VOXini Video Studio 1.0 - a self-contained
Windows application folder (PyInstaller "onedir" mode, since v1.1.0). Run
via BUILD_WINDOWS.bat - do not run pyinstaller directly against this file
without first activating the app's own (not ComfyUI's!) Python venv, since
PyInstaller bundles whatever environment it's invoked from.

WARUM ONEDIR STATT ONEFILE (Wechsel in v1.1.0): im vorherigen Onefile-Modus
entpackte sich die .exe bei JEDEM Start neu in einen temporaeren Ordner
(zwei interne Prozesse: der aeussere Bootloader-Prozess extrahiert, startet
den eigentlichen App-Prozess als Kind und prueft dabei per eingebauter
PyInstaller-Sicherheitsfunktion, dass der Pfad des Elternprozesses zur
eigenen .exe passt). Wurde die frisch entpackte Datei just in diesem
Moment von einem Antivirus-Programm gescannt/kurz gesperrt, schlug diese
Pruefung gelegentlich fehl - auch bei ganz normalem Doppelklick-Start, ohne
jeden Zusammenhang mit dem Auto-Update ("Security validation failure:
parent process has different executable!", siehe app_update.py und
Windows_Testprotokoll.md). Onedir-Builds entpacken sich nicht bei jedem
Start neu, das Problem entfaellt strukturell.

Onedir mode (EXE receives exclude_binaries=True, a.binaries/a.zipfiles/
a.datas go to a separate COLLECT step) produces an application FOLDER
containing the .exe plus an `_internal` subfolder with all bundled
dependencies - no more single-file distribution. BUILD_WINDOWS.bat points
--distpath directly at the project root, so the result lands at exactly:

    VOXini Video Studio\VOXini Video Studio.exe   (inside a folder of the
                                                     same name, alongside
                                                     `_internal\`)

The whole `VOXini Video Studio\` folder must be kept together (moved/
zipped/distributed as one unit) - see app_update.py for how the in-app
auto-updater now swaps this entire folder instead of a single file.

Bundles as data files (all reliably embedded and, at runtime, resolved via
sys._MEIPASS - in onedir mode this simply points at the app folder itself/
its `_internal` subfolder, no temp-dir extraction happens):
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
    [],
    exclude_binaries=True,
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

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="VOXini Video Studio",
)
