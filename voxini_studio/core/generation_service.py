"""Headless generation orchestration - no Qt here, so it can be unit
tested directly. The UI (GenerationPanel) only gathers user choices and
calls into this module.

This is also where the hybrid local/cloud rules are actually enforced:
- provider resolution (scene override, else project default) via
  resolve_provider(), so "provider per project OR per scene" is a single
  choke point rather than something every caller re-implements;
- the per-provider budget gate (budget_check()), which refuses to submit a
  job that would exceed the project's configured limit for that provider
  (Runway/Kling/Seedance/Veo each have their own budget_limit/spent_total
  pair - see _BUDGET_TRACKED_PROVIDER_IDS below) WITHOUT ever calling the
  provider - a paid API call is never made once a budget is exceeded;
- actual_cost / {provider}_spent_total bookkeeping after a successful paid
  job, so the budget gate has real cumulative spend to compare against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from voxini_studio.core import face_verification
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


def resolve_scene_references(
    proj, scene: Scene, max_simultaneous_characters: int = 1
) -> tuple[list[str], str]:
    """Single choke point for turning a scene's character assignments into
    the actual reference image path(s) a provider job will use - and for
    enforcing the binding reference rule's hard-block requirements (see
    docs/Referenzbindung_Luecken_und_Plan.md, "Verbindliche Entscheidungen"
    Punkt 1-3):

    - a character assigned to the scene with no resolvable reference image
      (Character.resolve_reference_image() returned None) blocks the scene;
    - a scene needing more character references at once than the resolved
      provider actually declared support for (`max_simultaneous_characters`,
      see Provider.max_simultaneous_references) blocks it, because silently
      sending only some of them and dropping the rest is exactly what the
      binding rule forbids ("silent_reference_dropping_forbidden");
    - a character reference combined with a continuity "Anschlussbild" in
      the SAME job still always blocks, regardless of
      max_simultaneous_characters - task #637's multi-character compositing
      pipeline only handles multiple CHARACTER faces in one image, not a
      character-plus-continuity combination, so that combination is treated
      the same conservative way as before this feature existed.

    max_simultaneous_characters defaults to 1 (the pre-#637 behaviour) so
    any caller that hasn't been updated to look up the resolved provider's
    capability keeps the old, safe, single-reference-only behaviour - pass
    provider.max_simultaneous_references explicitly once a provider is
    known (see generate_scene()/build_cost_review() below).

    Returns (reference_paths, error_message). error_message is empty and
    reference_paths holds the (possibly empty, for a scene with no
    characters) list of project-relative paths to actually send whenever
    the scene is NOT blocked. error_message is a German, user-facing
    explanation and reference_paths is [] whenever the scene IS blocked -
    callers must treat any non-empty error_message as "do not call the
    provider", not merely as a warning to display alongside a result.

    Called both by generate_scene() below (so a direct/scripted call can
    never bypass the check) and by the UI before it even shows the cost-
    confirmation dialog (see ui/cost_confirm_dialog.py), so a blocked scene
    never reaches the point of a cost estimate being shown to Neon68 as if
    it were generatable.
    """
    missing: list[str] = []
    resolved_characters: list[tuple[str, str]] = []  # (name, project-relative path)
    for char_id in scene.character_ids:
        char = proj.characters.get(char_id)
        if char is None:
            missing.append(
                f"Charakter-ID {char_id} ist der Szene zugeordnet, existiert aber nicht mehr im Projekt."
            )
            continue
        wardrobe_state = scene.character_wardrobe_states.get(char_id)
        view_role = scene.character_view_roles.get(char_id)
        path = char.resolve_reference_image(wardrobe_state=wardrobe_state, view_role=view_role)
        if path is None:
            missing.append(f"Charakter '{char.name}' hat kein Referenzbild - Generierung gesperrt.")
        else:
            resolved_characters.append((char.name, path))

    if missing:
        return [], " ".join(missing)

    has_continuity = bool(scene.continuity_reference_path)

    if len(resolved_characters) > max_simultaneous_characters:
        names = ", ".join(name for name, _ in resolved_characters)
        return [], (
            f"Diese Szene benoetigt {len(resolved_characters)} gleichzeitige Charakter-Referenzbilder "
            f"({names}), aber der aktuelle Anbieter unterstuetzt aktuell nur bis zu "
            f"{max_simultaneous_characters}. Generierung gesperrt, um keine Referenz stillschweigend "
            "zu verwerfen - siehe docs/Referenzbindung_Luecken_und_Plan.md, Punkt 3 "
            "(mehrstufiger Schluesselbild-Workflow)."
        )

    if has_continuity and resolved_characters:
        names = ", ".join(name for name, _ in resolved_characters)
        return [], (
            f"Diese Szene braucht sowohl Charakter-Referenzbild(er) ({names}) als auch das "
            "Anschlussbild gleichzeitig - diese Kombination kann der aktuelle Anbieter noch nicht "
            "in einem Auftrag uebertragen. Generierung gesperrt, um keine Referenz stillschweigend "
            "zu verwerfen."
        )

    if has_continuity:
        return [scene.continuity_reference_path], ""

    return [path for _, path in resolved_characters], ""


@dataclass
class SceneReviewItem:
    """One scene's entry in the cost-confirmation review protocol (see
    ui/cost_confirm_dialog.py and docs/Referenzbindung_Luecken_und_Plan.md,
    "Verbindliche Entscheidungen" Punkt 9). Pure data, no Qt - built here so
    the review logic is unit-testable headlessly, same rationale as the
    rest of this module."""

    scene: Scene
    cost: float
    character_names: list[str] = field(default_factory=list)
    reference_paths: list[str] = field(default_factory=list)
    """Populated only when NOT blocked - the actual project-relative image
    path(s) this job would send, in scene.character_ids order."""
    wardrobe_states: dict[str, str] = field(default_factory=dict)
    """character name -> wardrobe state name, only entries that actually
    apply to this scene (see Scene.character_wardrobe_states)."""
    continuity_available: bool = False
    block_reason: str = ""
    """Non-empty means this scene is hard-blocked - see
    resolve_scene_references(). reference_paths/wardrobe_states are not
    populated for a blocked scene."""

    @property
    def blocked(self) -> bool:
        return bool(self.block_reason)


@dataclass
class CostReviewReport:
    items: list[SceneReviewItem]
    blocked_count: int
    allowed_cost_total: float


def build_cost_review(pm: ProjectManager, scenes_and_costs: list[tuple[Scene, float]]) -> CostReviewReport:
    """Builds the full reference-binding review protocol for a batch of
    scenes about to be shown in the cost-confirmation dialog - single choke
    point so the dialog only ever renders data, never recomputes the
    blocking decision itself (which must stay in perfect sync with what
    generate_scene() will actually do)."""
    proj = pm.project
    characters = proj.characters if proj is not None else {}
    items: list[SceneReviewItem] = []
    blocked_count = 0
    allowed_cost_total = 0.0

    for scene, cost in scenes_and_costs:
        char_names = [characters[cid].name for cid in scene.character_ids if cid in characters]
        max_refs = 1
        if proj is not None:
            # Resolve the SAME provider generate_scene() will actually use for
            # this scene, so the block/allow decision shown here can never
            # drift out of sync with what happens when generation actually
            # runs (task #637 - a provider's max_simultaneous_references can
            # differ, e.g. ComfyUIProvider only reports >1 while its
            # identity-scene pipeline is enabled).
            max_refs = resolve_provider(pm, scene).max_simultaneous_references
        ref_paths, block_reason = (
            resolve_scene_references(proj, scene, max_refs) if proj is not None else ([], "")
        )
        item = SceneReviewItem(scene=scene, cost=cost, character_names=char_names, block_reason=block_reason)
        if block_reason:
            blocked_count += 1
        else:
            allowed_cost_total += cost
            item.reference_paths = ref_paths
            item.wardrobe_states = {
                characters[cid].name: state
                for cid, state in scene.character_wardrobe_states.items()
                if cid in characters
            }
            item.continuity_available = bool(scene.continuity_reference_path)
        items.append(item)

    return CostReviewReport(items=items, blocked_count=blocked_count, allowed_cost_total=allowed_cost_total)


# Paid providers whose budget is tracked via a pair of project fields named
# "{provider_id}_budget_limit" / "{provider_id}_spent_total" (see
# models/project.py). Local/mock providers (e.g. "comfyui") are not listed
# here and are therefore always free and never blocked by this gate.
#
# FIX (2026-09-06, Entwicklungsvertrag Regel 8/11): this used to be
# hardcoded to check only proj.runway_budget_limit/runway_spent_total and
# `provider.id == "runway"`, so the kling_budget_limit/seedance_budget_limit/
# veo_budget_limit fields added this session for the three new cloud
# providers were silently NOT enforced - a real safety gap discovered right
# before the user's planned full-batch generation with a real, paid Veo key
# and a real $25 limit configured. Generalizing this to every paid provider
# id closes that gap without changing Runway's existing, already-verified
# behaviour (its check is byte-for-byte the same as before, just looked up
# generically instead of hardcoded).
_BUDGET_TRACKED_PROVIDER_IDS = ("runway", "kling", "seedance", "veo")


def budget_check(pm: ProjectManager, provider: Provider, estimated_cost: float) -> Optional[str]:
    """Returns a German user-facing error message if this job would exceed
    the project's configured budget limit for this provider, else None.
    Only applies to providers in _BUDGET_TRACKED_PROVIDER_IDS - local/mock
    jobs are always free and are never blocked by this gate."""
    proj = pm.project
    if proj is None or provider.id not in _BUDGET_TRACKED_PROVIDER_IDS:
        return None
    limit = getattr(proj, f"{provider.id}_budget_limit", 0.0)
    if limit <= 0:
        return None
    spent = getattr(proj, f"{provider.id}_spent_total", 0.0)
    if spent + estimated_cost > limit:
        return (
            f"Budget-Limit erreicht: bereits {spent:.2f} EUR ausgegeben, "
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

    # hard-block gate FIRST, per the binding reference rule - a scene with a
    # missing or non-transmittable reference must never reach a cost
    # estimate/budget check/provider call at all (see resolve_scene_
    # references() docstring above).
    reference_paths_rel, reference_error = resolve_scene_references(
        proj, scene, provider.max_simultaneous_references
    )
    if reference_error:
        version.error_message = reference_error
        scene.versions.append(version)
        scene.status = SceneStatus.FAILED
        return version

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
    reference_paths = [str(pm.resolve(p)) for p in reference_paths_rel]

    # Task #637 - only meaningful (and only ever non-empty) for a scene with
    # more than one character that a provider actually accepted (see
    # resolve_scene_references() above): per-character identity text +
    # the shared scene/setting text, for ComfyUIProvider's multi-character
    # identity pipeline to combine per masked region. A single-character or
    # no-character scene leaves both at their empty defaults, so
    # identity_stage_prompt (unchanged) keeps being what every existing,
    # already-verified single-character generation actually uses - no
    # behaviour change for those.
    identity_character_prompts: list[str] = []
    identity_scene_description = ""
    present_characters = [proj.characters[cid] for cid in scene.character_ids if cid in proj.characters]
    if len(present_characters) > 1:
        identity_character_prompts = [c.prompt_block() for c in present_characters]
        identity_scene_description = scene.stage1_scene_description()

    request = GenerationRequest(
        scene=scene,
        resolved_prompt=resolved_prompt,
        character_reference_paths=reference_paths,
        aspect_ratio=proj.default_aspect_ratio,
        dest_path=dest_path,
        model_id=resolved_model_id,
        identity_stage_prompt=scene.stage1_identity_prompt(proj.characters),
        identity_character_prompts=identity_character_prompts,
        identity_scene_description=identity_scene_description,
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

        # Task #638 - automatische Gesichtskontrolle: single choke point,
        # laeuft fuer JEDEN Provider (ComfyUI/Runway/Mock) identisch, direkt
        # nach einem erfolgreichen Ergebnis und VOR der endgueltigen
        # Status-Entscheidung. Siehe core/face_verification.py fuer die
        # genaue Anwendbarkeits-/Bewertungslogik - laeuft standardmaessig
        # gar nicht (Project.face_verification_enabled=False) und aendert
        # dann nichts am bisherigen Verhalten.
        face_result = face_verification.verify_generated_clip(pm, scene, Path(result.file_path))
        version.face_similarity = face_result.min_similarity
        version.face_warning = face_result.message
        if face_result.should_flag:
            scene.status = SceneStatus.NEEDS_REVIEW
        else:
            scene.status = SceneStatus.DONE

        if provider.id in _BUDGET_TRACKED_PROVIDER_IDS:
            attr = f"{provider.id}_spent_total"
            setattr(proj, attr, getattr(proj, attr, 0.0) + result.actual_cost)
    else:
        version.error_message = result.error_message
        scene.versions.append(version)
        scene.status = SceneStatus.FAILED

    return version


def generate_scenes(
    pm: ProjectManager, scenes: list[Scene], provider: Optional[Provider] = None, model_id: str = ""
) -> list[ClipVersion]:
    return [generate_scene(pm, scene, provider, model_id) for scene in scenes]
