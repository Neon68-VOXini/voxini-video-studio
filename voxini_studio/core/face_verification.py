"""Automatische Gesichtskontrolle nach der Generierung (Task #638 - siehe
docs/Referenzbindung_Luecken_und_Plan.md, Punkt 7).

Ziel: verhindern, dass VOXini Video Studio die gleichen Identitaets-Drift-
Probleme bekommt wie beim realen "Willkommen bei Neon68"-Video, wo sich ein
Charaktergesicht von Szene zu Szene leicht gegenueber dem freigegebenen
Referenzbild veraendert hat, ohne dass es automatisch aufgefallen waere.

Orchestriert den isolierten Gesichtskontroll-Worker-Prozess (scripts/
face_verify_worker.py, laeuft im ".venv-faceverify"-Environment - siehe
core/face_verify_env.py) vom Haupt-Environment aus: extrahiert das erste und
letzte Frame des generierten Clips (ffmpeg), vergleicht beide gegen das
Charakter-Referenzbild und liefert ein FaceVerificationResult zurueck.

Single choke point: aufgerufen ausschliesslich von generation_service.
generate_scene() direkt nach einem erfolgreichen Provider-Ergebnis, BEVOR
die Szene als DONE markiert wird - siehe dortige Verdrahtung fuer die
genaue Statuslogik (NEEDS_REVIEW bei erkanntem Problem).

Dieses Modul importiert insightface/onnxruntime zu keinem Zeitpunkt selbst
- nur der Worker-Subprozess (im isolierten Environment) tut das.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from voxini_studio.core import face_verify_env, ffmpeg_locator
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import Scene

WORKER_SCRIPT = face_verify_env.APP_ROOT / "scripts" / "face_verify_worker.py"


@dataclass
class FaceVerificationResult:
    """applicable=False means the check simply doesn't apply to this scene
    (feature disabled, or no single-character identity reference to check
    against) - passed defaults to True in that case so callers never have
    to special-case "not applicable" separately from "passed". env_ready
    distinguishes "we actually compared faces" from "we couldn't check yet"
    (missing environment/ffmpeg failure/worker crash) - in the latter case
    passed is also True (never block/flag generation merely because the
    check itself couldn't run), but `message` carries an honest note."""
    applicable: bool
    env_ready: bool
    passed: bool
    similarity_first: Optional[float] = None
    similarity_last: Optional[float] = None
    min_similarity: Optional[float] = None
    message: str = ""

    @property
    def should_flag(self) -> bool:
        """True only when the check actually ran to completion and found a
        genuine problem - this is what generation_service.generate_scene()
        uses to decide whether to downgrade a scene from DONE to
        NEEDS_REVIEW. Never true merely because the check could not run."""
        return self.applicable and self.env_ready and not self.passed


def identity_reference_for_scene(proj, scene: Scene) -> Optional[tuple[str, str]]:
    """Returns (character_name, project-relative reference image path) if,
    and only if, `scene` has exactly one character with a resolvable
    reference image AND no continuity reference set - the only case where
    comparing the generated clip's face against a single, unambiguous
    identity photo is meaningful. Returns None for: no characters
    (establishing/background shots), a continuity-only scene (comparing
    against the previous clip's last frame would test continuity, not
    identity), or - should not happen once resolve_scene_references()
    already allowed the scene through - more than one resolvable character.
    Mirrors, but deliberately does NOT reuse or replace, generation_service.
    resolve_scene_references()'s own resolution - kept as a separate,
    read-only lookup so this module has no hard-blocking authority over
    generation itself, only an after-the-fact review authority."""
    if scene.continuity_reference_path:
        return None
    resolved: list[tuple[str, str]] = []
    for char_id in scene.character_ids:
        char = proj.characters.get(char_id)
        if char is None:
            continue
        wardrobe_state = scene.character_wardrobe_states.get(char_id)
        view_role = scene.character_view_roles.get(char_id)
        path = char.resolve_reference_image(wardrobe_state=wardrobe_state, view_role=view_role)
        if path:
            resolved.append((char.name, path))
    if len(resolved) != 1:
        return None
    return resolved[0]


def _extract_frame(ffmpeg_exe: str, clip_path: Path, dest: Path, *, from_start: bool) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if from_start:
        cmd = [ffmpeg_exe, "-y", "-i", str(clip_path), "-frames:v", "1", str(dest)]
    else:
        # Gleicher Befehl wie core/continuity.py::extract_last_frame().
        cmd = [
            ffmpeg_exe, "-y", "-sseof", "-1", "-i", str(clip_path),
            "-frames:v", "1", "-update", "1", str(dest),
        ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and dest.is_file()


def verify_generated_clip(pm: ProjectManager, scene: Scene, clip_abs_path: Path) -> FaceVerificationResult:
    """Fuehrt (falls anwendbar/aktiviert/eingerichtet) die Gesichtskontrolle
    fuer einen frisch generierten Clip durch. Wirft absichtlich NIE fuer
    eine gewoehnliche "nichts zu pruefen"/"noch nicht eingerichtet"-
    Situation - nur ein wirklich unerwarteter interner Fehler wuerde
    durchschlagen, und selbst dann sollten Aufrufer das wie "konnte nicht
    geprueft werden" behandeln, niemals wie einen Generierungsfehler."""
    proj = pm.project
    if proj is None or not proj.face_verification_enabled:
        return FaceVerificationResult(applicable=False, env_ready=False, passed=True)

    identity = identity_reference_for_scene(proj, scene)
    if identity is None:
        return FaceVerificationResult(applicable=False, env_ready=False, passed=True)
    character_name, reference_rel_path = identity
    reference_abs_path = pm.resolve(reference_rel_path)

    if not face_verify_env.is_ready():
        return FaceVerificationResult(
            applicable=True, env_ready=False, passed=True,
            message=(
                "Gesichtskontrolle ist aktiviert, aber das dafuer noetige isolierte "
                "Environment ist noch nicht eingerichtet - dieser Clip wurde OHNE "
                "automatische Gesichtspruefung akzeptiert. Bitte in den "
                "Projekteinstellungen einrichten, damit kuenftige Szenen tatsaechlich "
                "geprueft werden."
            ),
        )

    if not ffmpeg_locator.is_available():
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=True,
            message=(
                "Gesichtskontrolle konnte nicht durchgefuehrt werden: ffmpeg ist nicht "
                "verfuegbar, es liessen sich keine Frames aus dem Clip extrahieren. "
                "Dieser Clip wurde OHNE automatische Gesichtspruefung akzeptiert."
            ),
        )

    work_dir = pm.paths.clips_dir / scene.id
    ffmpeg_exe = ffmpeg_locator.ffmpeg_path()
    candidates = [
        ("first", work_dir / "faceverify_first.png", True),
        ("last", work_dir / "faceverify_last.png", False),
    ]
    frame_entries: list[tuple[str, Path]] = []
    for label, dest, from_start in candidates:
        if _extract_frame(ffmpeg_exe, clip_abs_path, dest, from_start=from_start):
            frame_entries.append((label, dest))

    if not frame_entries:
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=True,
            message=(
                "Gesichtskontrolle konnte nicht durchgefuehrt werden: es liess sich "
                "kein Frame aus dem generierten Clip extrahieren (ffmpeg-Fehler). "
                "Dieser Clip wurde OHNE automatische Gesichtspruefung akzeptiert."
            ),
        )

    output_json = work_dir / "faceverify_result.json"
    command = [
        str(face_verify_env.venv_python_path()), "-u", str(WORKER_SCRIPT),
        "--reference", str(reference_abs_path),
    ]
    for _, path in frame_entries:
        command += ["--frame", str(path)]
    command += ["--output-json", str(output_json)]

    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except Exception as exc:
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=True,
            message=f"Gesichtskontrolle konnte nicht gestartet werden ({exc}) - Clip OHNE Pruefung akzeptiert.",
        )

    if proc.returncode != 0 or not output_json.is_file():
        last_error = ""
        for line in (proc.stdout or "").splitlines():
            if line.startswith("ERROR "):
                last_error = line[len("ERROR "):]
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=True,
            message=(
                f"Gesichtskontrolle ist fehlgeschlagen ({last_error or 'unbekannter Fehler'}) "
                "- Clip OHNE Pruefung akzeptiert."
            ),
        )

    try:
        data = json.loads(output_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=True,
            message=f"Ergebnis der Gesichtskontrolle konnte nicht gelesen werden ({exc}) - Clip OHNE Pruefung akzeptiert.",
        )

    if not data.get("reference", {}).get("detected"):
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=False,
            message=(
                f"Im Referenzbild von '{character_name}' wurde kein Gesicht erkannt - "
                "Gesichtskontrolle konnte nicht durchgefuehrt werden. Bitte Referenzbild pruefen."
            ),
        )

    frames = data.get("frames", [])
    similarities: dict[str, Optional[float]] = {"first": None, "last": None}
    undetected: list[str] = []
    for (label, _), entry in zip(frame_entries, frames):
        if not entry.get("detected"):
            undetected.append(label)
        else:
            similarities[label] = entry.get("similarity")

    if undetected:
        which = ", ".join("erstes Frame" if l == "first" else "letztes Frame" for l in undetected)
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=False,
            similarity_first=similarities["first"], similarity_last=similarities["last"],
            message=(
                f"Kein Gesicht erkannt: {which}. Charakter '{character_name}' ist im generierten "
                "Clip nicht erkennbar - Szene zur Pruefung markiert."
            ),
        )

    values = [v for v in similarities.values() if v is not None]
    min_similarity = min(values) if values else None
    threshold = proj.face_verification_threshold
    passed = min_similarity is not None and min_similarity >= threshold

    if passed:
        return FaceVerificationResult(
            applicable=True, env_ready=True, passed=True,
            similarity_first=similarities["first"], similarity_last=similarities["last"],
            min_similarity=min_similarity,
        )
    return FaceVerificationResult(
        applicable=True, env_ready=True, passed=False,
        similarity_first=similarities["first"], similarity_last=similarities["last"],
        min_similarity=min_similarity,
        message=(
            f"Gesichtsaehnlichkeit zu '{character_name}' liegt bei {min_similarity:.2f} "
            f"(Schwelle: {threshold:.2f}) - moeglicher Identitaets-Drift, Szene zur Pruefung markiert."
        ),
    )
