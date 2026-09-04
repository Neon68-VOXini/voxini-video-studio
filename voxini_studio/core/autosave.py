"""Autosave, crash recovery, and rolling manual-save backups - no Qt here,
so it's fully unit-testable headlessly. The UI only wires a timer to
`ProjectManager.autosave()` and asks the user a yes/no question when
`has_pending_recovery()` is true right after opening a project.

Design:
- `project.voxproj` is only ever written by an explicit user Save action
  (ProjectManager.save()) - autosave never overwrites it.
- `project.autosave.voxproj` is written periodically (e.g. every 60s from a
  UI timer) with the in-memory project state, whether or not the user has
  saved manually. If the app crashes or is closed without saving, this
  snapshot survives.
- On the next open, if the autosave snapshot is newer than the manually
  saved file (or the manual file doesn't even exist), that's unsaved-but-
  autosaved work worth offering back to the user - `has_pending_recovery()`
  detects this, `load_autosave()` reads it, `discard_autosave()` clears it
  once the user has decided (recovered into a real save, or dismissed it).
- Every successful manual save additionally rotates a timestamped backup
  copy of the previous project.voxproj into backups/ (capped at
  MAX_BACKUPS), so a bad edit can be recovered even after saving over it.
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from voxini_studio.models.project import Project, ProjectPaths

MAX_BACKUPS = 10


# -- ordering markers ---------------------------------------------------------
# Recovery ordering is decided by comparing two tiny marker files holding
# nanosecond wall-clock timestamps, NOT raw filesystem mtimes - some
# filesystems only report mtime with coarse (e.g. whole-second) resolution,
# which would make two rapid saves indistinguishable and either miss real
# recoverable work or falsely offer to "recover" data that's actually
# older. time.time_ns() gives ample resolution to order even two writes
# that happen back-to-back in the same process.

def _write_marker(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="utf-8")


def _read_marker(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return -1


def mark_manual_save(paths: ProjectPaths) -> None:
    _write_marker(paths.save_marker_file, time.time_ns())


# -- autosave / recovery -----------------------------------------------------

def write_autosave(project: Project, paths: ProjectPaths) -> Path:
    path = paths.autosave_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(project.model_dump_json(indent=2), encoding="utf-8")
    _write_marker(paths.autosave_marker_file, time.time_ns())
    return path


def has_pending_recovery(paths: ProjectPaths) -> bool:
    """True if there is autosaved work newer than the last manual save (or
    the manual save is missing entirely, e.g. the project file was lost)."""
    auto = paths.autosave_file
    if not auto.exists():
        return False
    if not paths.project_file.exists():
        return True
    return _read_marker(paths.autosave_marker_file) > _read_marker(paths.save_marker_file)


def load_autosave(paths: ProjectPaths) -> Optional[Project]:
    auto = paths.autosave_file
    if not auto.exists():
        return None
    import json

    data = json.loads(auto.read_text(encoding="utf-8"))
    return Project.model_validate(data)


def discard_autosave(paths: ProjectPaths) -> None:
    auto = paths.autosave_file
    if auto.exists():
        auto.unlink()
    if paths.autosave_marker_file.exists():
        paths.autosave_marker_file.unlink()


# -- rolling manual-save backups ---------------------------------------------

def make_backup(paths: ProjectPaths) -> Optional[Path]:
    """Copies the CURRENT on-disk project.voxproj (i.e. the state before an
    imminent overwrite) into backups/, timestamped, then prunes old backups
    beyond MAX_BACKUPS. Returns None if there was nothing on disk yet to
    back up (e.g. the very first save of a brand new project)."""
    if not paths.project_file.exists():
        return None
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    # microsecond-resolution, fixed-width timestamp so filenames sort into
    # correct chronological order lexicographically even for two backups
    # made within the same second (list_backups relies on this rather than
    # raw filesystem mtime, which can have coarse resolution on some
    # filesystems and make rapid saves indistinguishable)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = paths.backups_dir / f"project_{stamp}.voxproj"
    counter = 1
    while dest.exists():  # extremely unlikely, but keep backups unique
        dest = paths.backups_dir / f"project_{stamp}_{counter}.voxproj"
        counter += 1
    shutil.copy2(paths.project_file, dest)
    _prune_backups(paths)
    return dest


def _prune_backups(paths: ProjectPaths) -> None:
    if not paths.backups_dir.exists():
        return
    backups = sorted(paths.backups_dir.glob("project_*.voxproj"), key=lambda p: p.name)
    excess = len(backups) - MAX_BACKUPS
    for old in backups[:max(0, excess)]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups(paths: ProjectPaths) -> list[Path]:
    if not paths.backups_dir.exists():
        return []
    # sorted by filename (which embeds a microsecond-resolution timestamp),
    # not raw filesystem mtime - see the comment in make_backup()
    return sorted(paths.backups_dir.glob("project_*.voxproj"), key=lambda p: p.name, reverse=True)


def load_backup(path: str | Path) -> Project:
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Project.model_validate(data)
