"""Resolves the ffmpeg/ffprobe executables to actually invoke.

Priority order:
1. A bundled binary shipped inside the app itself (`voxini_studio/resources/
   ffmpeg/win64/ffmpeg.exe` in source form, or the equivalent path inside a
   frozen PyInstaller build via `sys._MEIPASS`) - this is what makes the
   Windows .exe work out of the box with zero extra installation, since a
   real static Windows ffmpeg/ffprobe build (GPLv3, gyan.dev "essentials"
   build - see resources/ffmpeg/win64/LICENSE-FFmpeg.txt) is embedded.
2. Whatever `ffmpeg`/`ffprobe` is already on the system PATH (this is what
   the Linux/dev environment uses, and what a Windows user with their own
   FFmpeg install would use too).
3. As a last resort, the bare command name - preserves the previous
   behaviour (a clear "file not found" style error from subprocess) instead
   of silently doing nothing.

Only Windows ships a bundled binary today (that's the only platform this
app is actually distributed as a frozen .exe for); on Linux/macOS dev
environments step 2 always applies since ffmpeg is a normal dev dependency
there.
"""
from __future__ import annotations

import platform
import shutil
from functools import lru_cache
from pathlib import Path

from voxini_studio.ui.theme import resource_root


def _bundled_dir() -> Path:
    return resource_root() / "ffmpeg" / "win64"


@lru_cache(maxsize=None)
def ffmpeg_path() -> str:
    return _resolve("ffmpeg.exe", "ffmpeg")


@lru_cache(maxsize=None)
def ffprobe_path() -> str:
    return _resolve("ffprobe.exe", "ffprobe")


def _resolve(windows_exe_name: str, command_name: str) -> str:
    if platform.system() == "Windows":
        bundled = _bundled_dir() / windows_exe_name
        if bundled.is_file():
            return str(bundled)
    found = shutil.which(command_name)
    if found:
        return found
    return command_name


def is_available() -> bool:
    """True if ffmpeg can actually be invoked - either the bundled Windows
    binary was found, or something resolves on the system PATH. Tests use
    this (instead of a bare `shutil.which("ffmpeg")`) so they correctly
    detect the bundled binary too and don't spuriously self-skip on a
    machine that has no separately installed system ffmpeg."""
    resolved = ffmpeg_path()
    return resolved != "ffmpeg" or shutil.which("ffmpeg") is not None


def reset_cache_for_tests() -> None:
    """Test-only: clears the lru_cache so a test can monkeypatch resource_root
    or PATH and observe a fresh resolution."""
    ffmpeg_path.cache_clear()
    ffprobe_path.cache_clear()
