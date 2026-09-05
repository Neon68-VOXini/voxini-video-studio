"""Anschlussbild (continuity reference) support - see
docs/Referenzbindung_Luecken_und_Plan.md, "Verbindliche Entscheidungen"
Punkt 6.

Deliberately MANUAL, not automatic (Neon68's explicit decision,
2026-09-05): extracting the last frame of an accepted clip is automatic
and harmless (always kept ready as soon as a clip is accepted), but
actually attaching that frame to the NEXT scene as
Scene.continuity_reference_path is a separate, explicit per-scene action a
user takes - because a scene that already needs a character reference
would otherwise silently become blocked. See generation_service.
resolve_scene_references(): no current provider (ComfyUI/Wan2.2, Runway)
accepts two simultaneous reference images, so "character reference" +
"continuity reference" together is hard-blocked until the multi-stage
keyframe workflow exists (Punkt 3 / task #637). Automatically attaching
continuity to every consecutive scene would have started blocking scenes
that generate fine today, without Neon68 asking for that - hence the
explicit, opt-in design here instead.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from voxini_studio.core import ffmpeg_locator
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import Scene


def _lastframe_path(pm: ProjectManager, scene: Scene) -> Path:
    return pm.paths.clips_dir / scene.id / "lastframe.png"


def extract_last_frame(pm: ProjectManager, scene: Scene) -> Optional[str]:
    """Extracts the final frame of `scene`'s accepted clip as a PNG, stored
    alongside the clip (overwriting any previous extraction for the same
    scene, since only the CURRENTLY accepted version matters). Returns the
    project-relative path on success, or None (never raises) if there is no
    accepted clip, ffmpeg is not available, or extraction fails for any
    reason - callers must treat None as an honest "nicht verfuegbar", never
    retry silently in a loop or fabricate a placeholder image."""
    version = scene.accepted_version()
    if version is None or not version.file_path:
        return None
    if not ffmpeg_locator.is_available():
        return None
    clip_abs = pm.resolve(version.file_path)
    if not clip_abs.is_file():
        return None
    dest = _lastframe_path(pm, scene)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_locator.ffmpeg_path(), "-y", "-sseof", "-1", "-i", str(clip_abs),
        "-frames:v", "1", "-update", "1", str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not dest.is_file():
        return None
    return dest.relative_to(pm.paths.root).as_posix()


def continuity_candidate(pm: ProjectManager, scene: Scene) -> Optional[str]:
    """The project-relative last-frame path of the scene immediately BEFORE
    `scene` in storyboard order, extracting it on demand if not already
    extracted - or None if there is no previous scene, or the previous
    scene has no accepted clip yet (honest "nicht verfuegbar", never a
    placeholder). This is what the UI offers to attach; it does NOT write
    anything onto `scene` itself - see attach_continuity_reference()."""
    proj = pm.project
    if proj is None:
        return None
    ordered = proj.sorted_scenes()
    idx = next((i for i, s in enumerate(ordered) if s.id == scene.id), None)
    if idx is None or idx == 0:
        return None
    previous = ordered[idx - 1]
    return extract_last_frame(pm, previous)


def attach_continuity_reference(pm: ProjectManager, scene: Scene) -> tuple[bool, str]:
    """Explicit, user-triggered action: sets scene.continuity_reference_path
    to the previous scene's last frame. Returns (success, message) - message
    is a German, user-facing explanation either way (including WHY it
    failed, e.g. no accepted previous clip yet), never a silent no-op."""
    candidate = continuity_candidate(pm, scene)
    if candidate is None:
        return False, (
            "Kein Anschlussbild verfuegbar: die vorherige Szene hat noch keinen "
            "akzeptierten Clip, oder die Bildextraktion ist fehlgeschlagen (ffmpeg "
            "nicht verfuegbar oder Clip-Datei fehlt)."
        )
    scene.continuity_reference_path = candidate
    return True, f"Anschlussbild aus der vorherigen Szene uebernommen: {Path(candidate).name}"


def detach_continuity_reference(scene: Scene) -> None:
    scene.continuity_reference_path = None
