"""Project lifecycle: create, open, save, and import source files into the
project's own media directory so a project folder is always self-contained
and portable.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from voxini_studio.core import autosave as autosave_module
from voxini_studio.models.project import Character, Project, ProjectPaths


class ProjectManager:
    def __init__(self) -> None:
        self.project: Project | None = None
        self.paths: ProjectPaths | None = None

    # -- lifecycle ---------------------------------------------------
    def create_new(self, project_dir: str | Path, name: str) -> Project:
        paths = ProjectPaths(project_dir)
        paths.ensure_layout()
        project = Project(name=name)
        self.project = project
        self.paths = paths
        self.save()
        return project

    def open(self, project_dir: str | Path) -> Project:
        paths = ProjectPaths(project_dir)
        if not paths.project_file.exists():
            raise FileNotFoundError(f"No project.voxproj found in {project_dir}")
        data = json.loads(paths.project_file.read_text(encoding="utf-8"))
        project = Project.model_validate(data)
        self.project = project
        self.paths = paths
        return project

    def save(self) -> None:
        if self.project is None or self.paths is None:
            raise RuntimeError("No project is open")
        self.project.touch()
        self.paths.root.mkdir(parents=True, exist_ok=True)
        # back up whatever is currently on disk BEFORE overwriting it, so a
        # bad edit is still recoverable even after the user hits Save
        autosave_module.make_backup(self.paths)
        self.paths.project_file.write_text(
            self.project.model_dump_json(indent=2), encoding="utf-8"
        )
        autosave_module.mark_manual_save(self.paths)
        # the manual save now supersedes any pending autosave snapshot
        autosave_module.discard_autosave(self.paths)

    # -- autosave / crash recovery -------------------------------------
    def autosave(self) -> Path | None:
        """Writes a periodic snapshot of the in-memory project WITHOUT
        touching project.voxproj (see core/autosave.py). Safe to call
        repeatedly from a UI timer; a no-op if no project is open."""
        if self.project is None or self.paths is None:
            return None
        return autosave_module.write_autosave(self.project, self.paths)

    def has_pending_recovery(self) -> bool:
        if self.paths is None:
            return False
        return autosave_module.has_pending_recovery(self.paths)

    def recover_from_autosave(self) -> Project:
        """Replaces the in-memory project with the autosaved snapshot. The
        caller (UI) decides whether to then call save() to make this the
        new canonical project.voxproj, or discard_recovery() to drop it."""
        if self.paths is None:
            raise RuntimeError("No project is open")
        recovered = autosave_module.load_autosave(self.paths)
        if recovered is None:
            raise RuntimeError("Keine automatische Sicherung gefunden")
        self.project = recovered
        return recovered

    def discard_recovery(self) -> None:
        if self.paths is not None:
            autosave_module.discard_autosave(self.paths)

    def list_backups(self) -> list[Path]:
        if self.paths is None:
            return []
        return autosave_module.list_backups(self.paths)

    def restore_backup(self, backup_path: Path) -> Project:
        """Replaces the in-memory project with a rolling backup snapshot
        (see core/autosave.py - written automatically on every manual
        save). Caller decides whether to save() immediately."""
        if self.paths is None:
            raise RuntimeError("No project is open")
        restored = autosave_module.load_backup(backup_path)
        self.project = restored
        return restored

    # -- importing source files --------------------------------------
    def import_audio(self, src_path: str | Path) -> str:
        return self._import_into(src_path, self.paths.audio_dir, "audio_path")

    def import_srt(self, src_path: str | Path) -> str:
        return self._import_into(src_path, self.paths.srt_dir, "srt_path")

    def import_prompt(self, src_path: str | Path) -> str:
        return self._import_into(src_path, self.paths.prompt_dir, "full_prompt_path")

    def clear_audio(self) -> None:
        self._clear_field("audio_path")

    def clear_srt(self) -> None:
        self._clear_field("srt_path")

    def clear_prompt(self) -> None:
        self._clear_field("full_prompt_path")

    def _clear_field(self, project_field: str) -> None:
        """Resets an imported-file field back to 'not set'. The copied file
        stays in media/ (harmless, and keeps backups/undo simple) - only the
        project's reference to it is removed."""
        if self.project is None:
            raise RuntimeError("No project is open")
        setattr(self.project, project_field, "")

    def import_character_reference(self, character_id: str, src_path: str | Path) -> str:
        if self.paths is None:
            raise RuntimeError("No project is open")
        dest_dir = self.paths.characters_dir / character_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        src = Path(src_path)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        # store project-relative paths with forward slashes ALWAYS, even on
        # Windows, so project.voxproj stays portable across OSes (Path()
        # accepts '/' as a separator on every platform when reading it back,
        # via resolve()/the '/' operator - see resolve() below)
        rel = dest.relative_to(self.paths.root).as_posix()
        char = self.project.characters.get(character_id)
        if char is not None:
            char.reference_image_paths.append(rel)
        return rel

    def _import_into(self, src_path: str | Path, dest_dir: Path, project_field: str) -> str:
        if self.project is None or self.paths is None:
            raise RuntimeError("No project is open")
        dest_dir.mkdir(parents=True, exist_ok=True)
        src = Path(src_path)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        rel = dest.relative_to(self.paths.root).as_posix()
        setattr(self.project, project_field, rel)
        return rel

    def resolve(self, relative_path: str) -> Path:
        """Turn a path stored in the project (relative to project root) into
        an absolute path for actually opening the file."""
        if self.paths is None:
            raise RuntimeError("No project is open")
        return self.paths.root / relative_path

    # -- characters -----------------------------------------------------
    def add_character(self, name: str, description: str = "") -> Character:
        if self.project is None:
            raise RuntimeError("No project is open")
        char = Character(name=name, description=description)
        self.project.add_character(char)
        return char
