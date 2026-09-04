"""Headless generation orchestration - no Qt here, so it can be unit
tested directly. The UI (GenerationPanel) only gathers user choices and
calls into this module.

This is also where the hybrid local/cloud rules are actually enforced:
- provider resolution (scene override, else project default) via
  resolve_provider(), so "provider per project OR per scene" is a single
  choke point rather than something every caller re-implements;
- the Runway budget gate (budget_check()), which refuses to submit a job
  that would exceed the project's configured limit WITHOUT ever calling the
  provider - a paid API call is never made once a budget is exceeded;
- actual_cost / runway_spent_total bookkeeping after a successful paid job,
  so the budget gate has real cumulative spend to compare against.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import ClipVersion, Scene, SceneStatus
from voxini_studio.providers import registry
from voxini_studio.providers.base import GenerationRequest, Provider


def resolve_provider(pm: ProjectManager, scene: Scene) -> Provider:
    """Resolves the actual provider instance to use for this scene, per the
    project's hybrid provider settings (scene override, else project
    default), freshly configured from the project's current settings
    (ComfyUI host/port, Runway model) - not a shared default instance."""
    proj = pm.project
    if proj is None:
        raise RuntimeError("No project is open")
    provider_id = proj.provider_for_scene(scene)
    return registry.build_provider(proj, provider_id)


def estimate_costs(scenes: list[Scene], provider: Provider) -> list[tuple[Scene, float]]:
    return [(scene, provider.estimate_cost(scene.duration)) for scene in scenes]


def budget_check(pm: ProjectManager, provider: Provider, estimated_cost: float) -> Optional[str]:
    """Returns a German user-facing error message if this job would exceed
    the project's configured Runway budget limit, else None. The limit only
    ever applies to the 'runway' provider id - local/mock jobs are always
    free and are never blocked by this gate."""
    proj = pm.project
    if proj is None or provider.id != "runway":
        return None
    limit = proj.runway_budget_limit
    if limit <= 0:
        return None
    if proj.runway_spent_total + estimated_cost > limit:
        return (
            f"Budget-Limit erreicht: bereits {proj.runway_spent_total:.2f} EUR ausgegeben, "
            f"dieser Auftrag wuerde {estimated_cost:.2f} EUR kosten, Limit ist {limit:.2f} EUR. "
            "Bitte das Limit in den Projekteinstellungen erhoehen, um fortzufahren."
        )
    return None


def generate_scene(
    pm: ProjectManager, scene: Scene, provider: Optional[Provider] = None, model_id: str = ""
) -> ClipVersion:
    """Run one scene through a provider and record the resulting ClipVersion
    on the scene. If `provider` is not given, it is resolved automatically
    per the project's hybrid settings (scene override, else project
    default) - this is what makes "provider per project OR per scene"
    actually take effect without every caller re-implementing the lookup.
    Always returns the version, whether it succeeded, failed, or was
    blocked by the budget gate - callers inspect scene.status afterwards."""
    proj = pm.project
    if proj is None or pm.paths is None:
        raise RuntimeError("No project is open")

    if provider is None:
        provider = resolve_provider(pm, scene)

    resolved_model_id = model_id or getattr(provider, "model_id", "")
    version = ClipVersion(provider_id=provider.id, model_id=resolved_model_id)
    estimated_cost = provider.estimate_cost(scene.duration)
    version.estimated_cost = estimated_cost

    budget_error = budget_check(pm, provider, estimated_cost)
    if budget_error:
        version.error_message = budget_error
        scene.versions.append(version)
        scene.status = SceneStatus.FAILED
        return version

    scene.status = SceneStatus.GENERATING
    dest_dir = pm.paths.clips_dir / scene.id
    dest_path = dest_dir / f"{version.id}.mp4"

    resolved_prompt = scene.resolved_prompt(proj.characters)
    reference_paths: list[str] = []
    chars_with_images = 0
    for char_id in scene.character_ids:
        char = proj.characters.get(char_id)
        if char and char.reference_image_paths:
            chars_with_images += 1
            reference_paths.extend(str(pm.resolve(p)) for p in char.reference_image_paths)

    # every current provider (ComfyUI/Wan2.2, Runway) only ever sends
    # reference_paths[0] to the actual generation job - a scene with more
    # than one usable reference image (several characters, or one character
    # with several reference photos) silently loses the rest. Record that
    # here so the UI can warn the user instead of the identity information
    # just disappearing without a trace.
    if len(reference_paths) > 1:
        if chars_with_images > 1:
            version.reference_warning = (
                f"Szene hat {chars_with_images} Charaktere mit Referenzbildern, aber es wird "
                "nur das erste Referenzbild des zuerst zugeordneten Charakters verwendet."
            )
        else:
            version.reference_warning = (
                f"Charakter hat {len(reference_paths)} Referenzbilder, aber es wird nur das "
                "erste verwendet."
            )

    request = GenerationRequest(
        scene=scene,
        resolved_prompt=resolved_prompt,
        character_reference_paths=reference_paths,
        aspect_ratio=proj.default_aspect_ratio,
        dest_path=dest_path,
        model_id=resolved_model_id,
    )
    result = provider.generate(request)

    if result.success:
        version.file_path = Path(result.file_path).relative_to(pm.paths.root).as_posix()
        version.actual_cost = result.actual_cost
        version.quality_warning = result.warning
        version.accepted = True
        for existing in scene.versions:
            existing.accepted = False
        scene.versions.append(version)
        scene.status = SceneStatus.DONE
        if provider.id == "runway":
            proj.runway_spent_total += result.actual_cost
    else:
        version.error_message = result.error_message
        scene.versions.append(version)
        scene.status = SceneStatus.FAILED

    return version


def generate_scenes(
    pm: ProjectManager, scenes: list[Scene], provider: Optional[Provider] = None, model_id: str = ""
) -> list[ClipVersion]:
    return [generate_scene(pm, scene, provider, model_id) for scene in scenes]
