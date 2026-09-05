"""Imports a creative scene-plan handoff (the "voxini_creative_scene_plan"
JSON schema produced by the ChatGPT/Neon68 creative process, together with
its matching "voxini_character_reference_catalog" JSON) into VOXini
Character/Scene objects - see docs/Referenzbindung_Luecken_und_Plan.md,
"Verbindliche Entscheidungen", Punkt 1.

Hard rules this module enforces (per the binding decisions and per
Charakterreferenzen_VOXini_v1.json's own "policy" block):

- validate_scene_plan_package() checks BOTH files and every file path they
  reference BEFORE anything is created or copied. import_scene_plan() calls
  it first and raises ImportValidationError, touching nothing, if any check
  fails - "missing_reference_is_hard_block" / "silent_reference_dropping_
  forbidden" apply to the import step itself, not only to later generation.
- A scene's character_names and its reference_requirements must name exactly
  the same characters (order-independent). A mismatch would mean the import
  silently drops a character's reference, which is exactly what the binding
  rule forbids - so it is a hard validation error, not a warning.
- Every path a reference_requirements entry marks hard_block_if_unavailable
  (currently always true) must exist on disk, for every character named in
  the scene, no matter how minor that character's on-screen visibility_role
  is (e.g. "distant_silhouette") - the source package itself already made
  that judgement call, this importer does not second-guess it.

What this module deliberately does NOT do yet (left to later tasks, per
docs/Referenzbindung_Luecken_und_Plan.md's Umsetzungsreihenfolge):

- It does not call generation_service or touch any provider - it only
  populates Character/Scene data.
- It does not resolve continuity_reference_path ("Anschlussbild") - that is
  computed after a scene is generated and accepted (core/continuity.py,
  Punkt 6), not at import time; the source plan has no such field per scene
  anyway (continuity is only expressed as prose in `transition`/
  `continuity_notes`, which this importer preserves into Scene.notes so the
  information is not lost, just not yet machine-actionable).
- It does not decide whether to import into a brand-new project or merge
  into an existing one - see docs/Referenzbindung_Luecken_und_Plan.md,
  Punkt 640: that is an explicit choice for the caller (UI) to get from
  Neon68, this module only ever returns a populated Project instance without
  writing project.voxproj itself.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from voxini_studio.models.project import Character, Project, ProjectPaths, Scene

_CATALOG_SCHEMA = "voxini_character_reference_catalog"
_SCENE_PLAN_SCHEMA = "voxini_creative_scene_plan"


class ImportValidationError(Exception):
    """Raised by import_scene_plan() when validate_scene_plan_package()
    found one or more problems. Nothing is created or copied when this is
    raised - see module docstring. `.errors` holds the full list (German,
    user-facing) so the caller can show every problem at once instead of
    making the user fix them one at a time."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class ImportReport:
    """Summary returned by import_scene_plan() on success, for the caller to
    show Neon68 before it actually gets saved as/into a project."""

    characters_created: list[str] = field(default_factory=list)
    scenes_created: int = 0
    images_copied: int = 0
    scenes_with_identity_lock: int = 0
    """Scenes that had at least one character, and therefore got the
    pre-approved reference_lock_prompt_prefix + global_negative_prompt
    embedded (identity_lock_prompt_included=True) instead of VOXini's own
    generic anti-drift block - see Scene.identity_lock_prompt_included."""
    scenes_without_characters: int = 0


def _resolve(base: Path, rel: str) -> Path:
    return (base / rel).resolve()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ImportValidationError([f"{label} nicht lesbar: {path} ({exc})"]) from exc
    except json.JSONDecodeError as exc:
        raise ImportValidationError([f"{label} ist kein gueltiges JSON: {path} ({exc})"]) from exc


def validate_scene_plan_package(catalog_path: Path, scene_plan_path: Path) -> list[str]:
    """Returns a list of German, user-facing validation errors (empty list =
    the package is safe to import). Never raises for a malformed-but-
    readable package - malformed JSON/unreadable files are the only cases
    that short-circuit with a single-item list instead of accumulating,
    since nothing else can be checked without them."""
    errors: list[str] = []
    try:
        catalog = _load_json(catalog_path, "Charakterreferenz-Datei")
    except ImportValidationError as exc:
        return exc.errors
    try:
        plan = _load_json(scene_plan_path, "Szenenplan-Datei")
    except ImportValidationError as exc:
        return exc.errors

    if catalog.get("schema") != _CATALOG_SCHEMA:
        errors.append(
            f"Unerwartetes Schema in Charakterreferenz-Datei: {catalog.get('schema')!r} "
            f"(erwartet: {_CATALOG_SCHEMA!r})"
        )
    if plan.get("schema") != _SCENE_PLAN_SCHEMA:
        errors.append(
            f"Unerwartetes Schema in Szenenplan-Datei: {plan.get('schema')!r} "
            f"(erwartet: {_SCENE_PLAN_SCHEMA!r})"
        )

    scenes = plan.get("scenes") or []
    declared_count = plan.get("scene_count")
    if declared_count is not None and declared_count != len(scenes):
        errors.append(
            f"scene_count ({declared_count}) stimmt nicht mit der tatsaechlichen "
            f"Anzahl an Szenen ({len(scenes)}) im 'scenes'-Array ueberein."
        )

    characters = catalog.get("characters") or {}
    catalog_base = catalog_path.parent
    plan_base = scene_plan_path.parent

    for name, data in characters.items():
        if not isinstance(data, dict):
            errors.append(f"Charakter '{name}': ungueltiger Eintrag im Katalog (kein Objekt).")
            continue
        anchor = data.get("identity_anchor")
        if not anchor:
            errors.append(f"Charakter '{name}': kein 'identity_anchor' im Katalog angegeben.")
        elif not _resolve(catalog_base, anchor).is_file():
            errors.append(f"Charakter '{name}': identity_anchor-Datei fehlt: {anchor}")
        for view_role, rel in (data.get("identity_views") or {}).items():
            if not _resolve(catalog_base, rel).is_file():
                errors.append(f"Charakter '{name}', Ansicht '{view_role}': Datei fehlt: {rel}")
        for wardrobe_name, rel in (data.get("wardrobe_states") or {}).items():
            if not _resolve(catalog_base, rel).is_file():
                errors.append(
                    f"Charakter '{name}', Kleidungszustand '{wardrobe_name}': Datei fehlt: {rel}"
                )

    for sd in scenes:
        order = sd.get("order")
        label = sd.get("label", "")
        scene_ref = f"Szene {order} ('{label}')"

        char_names = sd.get("character_names") or []
        for cname in char_names:
            if cname not in characters:
                errors.append(
                    f"{scene_ref}: Charakter '{cname}' kommt im Szenenplan vor, fehlt aber im "
                    "Charakterreferenz-Katalog."
                )

        requirements = sd.get("reference_requirements") or []
        req_names = [r.get("character_name") for r in requirements]
        if sorted(n for n in char_names if n) != sorted(n for n in req_names if n):
            errors.append(
                f"{scene_ref}: character_names {char_names} stimmt nicht mit den Charakteren in "
                f"reference_requirements {req_names} ueberein - ein Import wuerde sonst eine "
                "Referenz stillschweigend verlieren oder erfinden."
            )

        for req in requirements:
            cname = req.get("character_name", "?")
            if not req.get("hard_block_if_unavailable"):
                # Per the source package's own policy this is currently
                # always true; if a future package ever sets it false for a
                # given character, this importer still requires the file to
                # exist for the identity_anchor field specifically, since an
                # imported Character always needs at least one image - but
                # does not hard-fail on the other, genuinely-optional
                # fields for that character.
                anchor = req.get("identity_anchor")
                if anchor and not _resolve(plan_base, anchor).is_file():
                    errors.append(
                        f"{scene_ref}, Charakter '{cname}': identity_anchor-Datei fehlt: {anchor}"
                    )
                continue
            for field_name in ("identity_anchor", "perspective_reference", "primary_generation_reference"):
                rel = req.get(field_name)
                if rel and not _resolve(plan_base, rel).is_file():
                    errors.append(
                        f"{scene_ref}, Charakter '{cname}': Pflichtreferenz '{field_name}' fehlt: {rel}"
                    )
            for rel in req.get("wardrobe_references") or []:
                if not _resolve(plan_base, rel).is_file():
                    errors.append(
                        f"{scene_ref}, Charakter '{cname}': Kleidungsreferenz fehlt: {rel}"
                    )

    return errors


def _char_slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name).strip("_") or "char"


def import_scene_plan(
    catalog_path: str | Path,
    scene_plan_path: str | Path,
    dest_paths: ProjectPaths,
    *,
    project_name: str = "",
) -> tuple[Project, ImportReport]:
    """Parses the two JSON files and builds a fresh Project populated with
    Character and Scene objects, copying every referenced image into
    dest_paths.characters_dir. Raises ImportValidationError (see above) and
    creates/copies NOTHING if validate_scene_plan_package() finds any
    problem. Does not write project.voxproj - the caller decides when/
    whether to persist the returned Project (see module docstring)."""
    catalog_path = Path(catalog_path)
    scene_plan_path = Path(scene_plan_path)

    errors = validate_scene_plan_package(catalog_path, scene_plan_path)
    if errors:
        raise ImportValidationError(errors)

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    plan = json.loads(scene_plan_path.read_text(encoding="utf-8"))
    catalog_base = catalog_path.parent
    plan_base = scene_plan_path.parent

    dest_paths.characters_dir.mkdir(parents=True, exist_ok=True)
    report = ImportReport()

    def copy_image(src_base: Path, rel_src: str, char_id: str) -> str:
        src = _resolve(src_base, rel_src)
        dest_dir = dest_paths.characters_dir / char_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        report.images_copied += 1
        return dest.relative_to(dest_paths.root).as_posix()

    project = Project(name=project_name or plan.get("project_name") or "Imported Scene Plan")
    characters_by_name: dict[str, Character] = {}

    for name, data in (catalog.get("characters") or {}).items():
        char = Character(name=name)
        anchor_rel = copy_image(catalog_base, data["identity_anchor"], char.id)
        char.reference_image_paths = [anchor_rel]
        for view_role, rel in (data.get("identity_views") or {}).items():
            char.views[view_role] = copy_image(catalog_base, rel, char.id)
        for wardrobe_name, rel in (data.get("wardrobe_states") or {}).items():
            char.wardrobe_states[wardrobe_name] = copy_image(catalog_base, rel, char.id)
        project.add_character(char)
        characters_by_name[name] = char
        report.characters_created.append(name)

    global_negative = (plan.get("global_negative_prompt") or "").strip()

    scenes_sorted = sorted(plan.get("scenes") or [], key=lambda s: s.get("order", 0))
    for sd in scenes_sorted:
        scene = Scene(
            order=sd.get("order", 0),
            label=sd.get("label", ""),
            start_seconds=sd.get("start_seconds", 0.0),
            end_seconds=sd.get("end_seconds", 0.0),
        )

        char_ids: list[str] = []
        wardrobe_map: dict[str, str] = {}
        view_map: dict[str, str] = {}
        for req in sd.get("reference_requirements") or []:
            char = characters_by_name[req["character_name"]]
            char_ids.append(char.id)
            wardrobe_states = req.get("wardrobe_states") or []
            if wardrobe_states:
                wardrobe_map[char.id] = wardrobe_states[0]
            view_role = req.get("preferred_view_role")
            if view_role:
                view_map[char.id] = view_role

        scene.character_ids = char_ids
        scene.character_wardrobe_states = wardrobe_map
        scene.character_view_roles = view_map

        base_prompt = sd.get("final_generation_prompt") or sd.get("generation_prompt") or ""
        if char_ids:
            prefix = (sd.get("reference_lock_prompt_prefix") or "").strip()
            pieces = [p for p in (prefix, base_prompt) if p]
            if global_negative:
                pieces.append(f"AVOID: {global_negative}")
            scene.prompt_text = "\n\n".join(pieces)
            scene.identity_lock_prompt_included = True
            report.scenes_with_identity_lock += 1
        else:
            scene.prompt_text = base_prompt
            scene.identity_lock_prompt_included = False
            report.scenes_without_characters += 1

        notes_parts = [p for p in (sd.get("continuity_notes"), sd.get("transition")) if p]
        scene.notes = "\n".join(notes_parts)

        project.scenes.append(scene)
        report.scenes_created += 1

    return project, report
