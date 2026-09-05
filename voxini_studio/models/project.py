"""Core data models for a VOXini Video Studio project.

These models are the single source of truth for a project: they are what
gets saved to / loaded from the .voxproj JSON file, and what every other
module (audio analysis, scene planning, providers, ffmpeg assembly) reads
from and writes to. Kept as plain Pydantic models with no Qt dependency so
they can be unit-tested headlessly and reused by any future UI.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class AspectRatio(str, Enum):
    WIDESCREEN = "16:9"
    VERTICAL = "9:16"
    SQUARE = "1:1"


class SceneStatus(str, Enum):
    PLANNED = "planned"          # scene exists in the storyboard, no clip yet
    QUEUED = "queued"            # user confirmed generation, waiting on provider
    GENERATING = "generating"    # provider job in flight
    DONE = "done"                # clip generated and accepted
    FAILED = "failed"            # last generation attempt failed
    REJECTED = "rejected"        # user marked the result unusable, needs regen
    NEEDS_REVIEW = "needs_review"
    """Set by the (future, not-yet-implemented) automatic face-verification
    check when a generated clip's face similarity to the character's
    reference falls below the configured threshold - see
    docs/Referenzbindung_Luecken_und_Plan.md, Punkt 7. A scene in this
    status counts as neither DONE nor exportable: ffmpeg_assembly must
    refuse to include it in the final export until Neon68 explicitly
    re-accepts it (e.g. by re-running generate_scene() successfully, which
    moves it to DONE). Distinct from FAILED (provider/network error) and
    REJECTED (user's own manual judgement) - NEEDS_REVIEW is specifically
    "the software itself is not confident this is the right face"."""


# View angles a character can have a dedicated freigegeben reference photo
# for - matches the "preferred_view_role" vocabulary used by the ChatGPT
# scene-plan handoff (Szenenplan_VOXini_v4_FINAL_mit_Mikrotiming.json,
# reference_requirements[].preferred_view_role), so imported scenes can
# pick the angle that actually matches the camera direction instead of
# always sending the same frontal photo regardless of shot type.
CharacterViewRole = Literal["front", "three_quarter", "profile_left", "profile_right", "full_body"]


class Character(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    """Free-text identity block (age, hair, eyes, build, wardrobe notes,
    anything that must stay consistent). This text is injected verbatim
    into every scene prompt that references this character."""
    reference_image_paths: list[str] = Field(default_factory=list)
    """The primary/default identity anchor photo(s) - used whenever a scene
    does not specify a more specific view or wardrobe state below, and kept
    for backward compatibility with projects created before views/
    wardrobe_states existed."""

    views: dict[str, str] = Field(default_factory=dict)
    """Additional per-angle reference photos, keyed by CharacterViewRole
    (e.g. "front", "profile_left"), so a scene whose camera direction calls
    for a side profile doesn't have to reuse an unrelated frontal identity
    photo. Empty by default - existing projects/characters keep working
    unchanged via reference_image_paths alone. See resolve_reference_image()."""

    wardrobe_states: dict[str, str] = Field(default_factory=dict)
    """Per-outfit reference photos, keyed by a free-text wardrobe state name
    the project defines for itself (e.g. "EMMA_WEDDING_INTERIOR",
    "EMMA_RAIN_FINALE" - matches Scene.character_wardrobe_states below and
    the wardrobe_states vocabulary in the ChatGPT scene-plan handoff).
    Takes priority over views/reference_image_paths when a scene names a
    wardrobe state for this character, since the correct outfit matters
    more for continuity than the exact camera angle."""

    def prompt_block(self) -> str:
        """Text injected into a scene prompt to keep this character consistent."""
        if not self.description:
            return self.name
        return f"{self.name}: {self.description}"

    def resolve_reference_image(
        self, wardrobe_state: Optional[str] = None, view_role: Optional[str] = None
    ) -> Optional[str]:
        """Picks the single best reference photo for one scene appearance of
        this character, in priority order: (1) the wardrobe state the scene
        asked for, if this character actually has a photo for it, (2) the
        camera-angle view the scene asked for, if available, (3) the
        general/default identity anchor. Returns None only if the character
        has no reference photo at all yet (can't happen for a character the
        setup wizard/import actually bound an image to, but generation_service
        must still treat that as a hard error per the reference rule, not
        silently generate without any identity photo)."""
        if wardrobe_state and wardrobe_state in self.wardrobe_states:
            return self.wardrobe_states[wardrobe_state]
        if view_role and view_role in self.views:
            return self.views[view_role]
        if self.reference_image_paths:
            return self.reference_image_paths[0]
        return None


class ClipVersion(BaseModel):
    """One generation attempt for a scene. A scene can accumulate several
    of these across regenerations; `accepted` marks the one used in export."""
    id: str = Field(default_factory=_new_id)
    provider_id: str = ""
    model_id: str = ""
    file_path: str = ""
    created_at: str = Field(default_factory=_now_iso)
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    """Cost actually recorded for a paid provider job (0.0 for local/free
    providers). Used to track cumulative Runway spend against the budget
    limit."""
    accepted: bool = False
    error_message: str = ""
    reference_warning: str = ""
    """Set when this scene had more character reference images available
    than the provider actually used (every current provider only ever
    sends a single start/reference image per job - see generation_service.
    generate_scene()). Non-empty even on success, so multi-character or
    multi-reference-image scenes don't silently lose visual identity
    information without the user ever finding out."""
    quality_warning: str = ""
    """Set when a provider had to silently degrade quality to still produce
    a clip - currently: ComfyUIProvider skipping the local 1080p upscale
    because ffmpeg wasn't found (see ComfyUIProvider._upscale_local /
    GenerationResult.warning). Non-empty even though the clip generation
    itself succeeded, so a scene that's quietly stuck at raw Wan2.2
    resolution doesn't look identical to a properly upscaled one in the UI."""
    face_similarity: Optional[float] = None
    """Cosine similarity (roughly -1..1, higher = more similar) between the
    character's reference photo and the generated clip's face, the LOWER of
    the first-frame and last-frame measurements - see core/
    face_verification.py (task #638). None means the check did not run for
    this version at all (feature disabled, scene has no single-character
    identity reference to check against, or the .venv-faceverify
    environment isn't set up yet) - never a fabricated/assumed value."""
    face_warning: str = ""
    """German, user-facing explanation set by core/face_verification.py
    whenever the automatic face-drift check found a problem (similarity
    below Project.face_verification_threshold, or no face detected in a
    checked frame) OR whenever the check could not run despite being
    enabled (e.g. environment not set up yet) - empty when the check
    passed, is not applicable to this scene, or is disabled project-wide.
    Distinct from reference_warning/quality_warning above (different
    failure classes); see SceneStatus.NEEDS_REVIEW for how this feeds into
    the scene's overall status."""


_IDENTITY_LOCK_TEMPLATE = (
    "IDENTITY REFERENCE — ABSOLUTE CONTINUITY REQUIREMENT\n"
    "The attached character reference image for {name} is the binding identity source for this "
    "shot, not merely a style reference. Use that exact same person in every frame. Do not "
    "redesign, reinterpret, beautify, age, de-age or replace the character. Preserve exactly the "
    "reference face geometry, eye shape and color, eyebrows, nose, lips, teeth, jawline, "
    "cheekbones, ears, skin tone and texture, hairline, hair color, hair length, body proportions "
    "and apparent age. The character must remain instantly recognizable as the same individual "
    "from the first frame to the last frame."
)
"""Verbatim (English, since it goes into the same prompt sent to the video
model) anti-drift block per the binding reference rule - see
Verbindliche_Referenzregel_VOXini_Runway_v2.md (ChatGPT/Neon68 handoff) and
docs/Referenzbindung_Luecken_und_Plan.md, Punkt 5. Auto-inserted by
Scene.resolved_prompt() below for every character in a scene, UNLESS the
scene's prompt_text already contains an equivalent, specifically-approved
block (see identity_lock_prompt_included)."""

_MULTI_CHARACTER_SEPARATION_TEMPLATE = (
    "MULTI-CHARACTER SEPARATION\n"
    "Each attached reference belongs only to its named character: {names}. Keep them as separate, "
    "stable identities. Never blend or exchange their facial features, hair, clothing, body "
    "proportions or age."
)
"""Added on top of _IDENTITY_LOCK_TEMPLATE whenever a scene has more than one
character, per the same binding reference rule."""


class Scene(BaseModel):
    id: str = Field(default_factory=_new_id)
    order: int = 0
    label: str = ""
    """Short label from the script, e.g. 'INTRO' or 'FIRST CHORUS'."""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    prompt_text: str = ""
    """The raw directorial prompt text for this scene, as written by the user
    (or a sub-split of a longer script section)."""
    character_ids: list[str] = Field(default_factory=list)
    character_wardrobe_states: dict[str, str] = Field(default_factory=dict)
    """Maps a character_id present in character_ids to the wardrobe state
    name (see Character.wardrobe_states) that character wears in THIS scene
    specifically - e.g. the same "Emma" character might be
    "EMMA_WEDDING_INTERIOR" in one scene and "EMMA_RAIN_FINALE" in another.
    A character_id with no entry here falls back to Character.
    resolve_reference_image()'s default (a view, then the general identity
    anchor)."""
    character_view_roles: dict[str, str] = Field(default_factory=dict)
    """Maps a character_id present in character_ids to the camera-angle view
    (see CharacterViewRole/Character.views) this scene should use for that
    character when no wardrobe-state override above already determines the
    reference image - this is what implements binding decision Punkt 4
    ("pro Figur wird abhaengig von der Kamera-/Blickrichtung die passendste
    freigegebene Ansicht gewaehlt"). Populated by core/scene_plan_importer.py
    from the incoming plan's per-character preferred_view_role (already
    computed by the creative handoff, not re-derived by VOXini itself).
    A character_id with no entry here falls back to Character.
    resolve_reference_image()'s default identity anchor."""
    continuity_reference_path: Optional[str] = None
    """The "Anschlussbild": last frame of the immediately preceding scene's
    accepted clip, used as an additional continuity reference (matching
    wardrobe/wetness/prop/gaze/light state) where the chosen provider
    supports a second reference image. Set automatically after a scene is
    accepted (see core/continuity.py) - None means either "this is the
    first scene", "the previous scene has no accepted clip yet", or "frame
    extraction failed"; the UI must show this as a plain, honest
    "nicht verfügbar" rather than silently proceeding as if it were fine
    (per FINAL_ABNAHME_CHECKLISTE.md)."""
    identity_lock_prompt_included: bool = False
    """True for scenes imported from a creative handoff whose prompt_text
    already contains a specifically-approved, hand-written anti-drift block
    (see core/scene_plan_importer.py) - resolved_prompt() then skips
    auto-inserting its own generic _IDENTITY_LOCK_TEMPLATE/
    _MULTI_CHARACTER_SEPARATION_TEMPLATE to avoid duplicating/diluting the
    approved wording. False (default) for scenes authored directly in
    VOXini, where resolved_prompt() provides the automatic protection
    itself."""
    lyric_lines: list[str] = Field(default_factory=list)
    """SRT lines whose timing falls inside this scene, for reference."""
    status: SceneStatus = SceneStatus.PLANNED
    versions: list[ClipVersion] = Field(default_factory=list)
    notes: str = ""
    provider_override: Optional[str] = None
    """Provider id ('comfyui' | 'runway' | 'mock') to use for THIS scene
    specifically. None means "use the project's default provider" - this is
    how the hybrid workflow lets most scenes render locally/free while a
    handful of difficult scenes are sent to Runway on request."""

    @property
    def duration(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    def accepted_version(self) -> Optional[ClipVersion]:
        for v in reversed(self.versions):
            if v.accepted:
                return v
        return None

    def resolved_prompt(self, characters: dict[str, Character]) -> str:
        """Prompt text with character identity blocks prepended - see
        identity_lock_prompt_included above for why imported scenes skip the
        auto-inserted anti-drift text."""
        present = [characters[cid] for cid in self.character_ids if cid in characters]
        if not present:
            return self.prompt_text
        header = "CHARACTERS:\n" + "\n".join(f"- {c.prompt_block()}" for c in present)
        if self.identity_lock_prompt_included:
            return f"{header}\n\nSCENE:\n{self.prompt_text}"
        lock_blocks = "\n\n".join(_IDENTITY_LOCK_TEMPLATE.format(name=c.name) for c in present)
        parts = [header, lock_blocks]
        if len(present) > 1:
            names = ", ".join(c.name for c in present)
            parts.append(_MULTI_CHARACTER_SEPARATION_TEMPLATE.format(names=names))
        parts.append(f"SCENE:\n{self.prompt_text}")
        return "\n\n".join(parts)


class ProviderConfig(BaseModel):
    """Per-provider settings stored in the project (not secrets - API keys
    live outside the project file, see providers.base.ProviderCredentials)."""
    provider_id: str
    model_id: str = ""
    enabled: bool = True
    price_per_second: float = 0.0
    max_clip_seconds: float = 10.0
    supported_aspect_ratios: list[AspectRatio] = Field(
        default_factory=lambda: [AspectRatio.WIDESCREEN]
    )


class Project(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str = "Untitled Project"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    audio_path: str = ""
    srt_path: str = ""
    full_prompt_path: str = ""
    """Path to the original full timecode-based prompt script, kept for
    reference / re-parsing."""

    audio_duration_seconds: float = 0.0
    tempo_bpm: float = 0.0
    beat_times: list[float] = Field(default_factory=list)

    default_aspect_ratio: AspectRatio = AspectRatio.WIDESCREEN
    title_text: str = ""
    subtitle_mode: str = "soft"  # "soft" | "burned" | "none"

    characters: dict[str, Character] = Field(default_factory=dict)
    scenes: list[Scene] = Field(default_factory=list)
    provider_configs: dict[str, ProviderConfig] = Field(default_factory=dict)

    # -- hybrid local/cloud provider settings --------------------------
    default_provider_id: str = "comfyui"
    """Project-wide default provider. 'comfyui' (lokal, kostenlos) is the
    default so a fresh project never accidentally costs money. Individual
    scenes can override this via Scene.provider_override."""

    comfyui_host: str = "127.0.0.1"
    comfyui_port: int = 8188
    comfyui_install_dir: str = ""
    """Chosen by the user in the setup wizard on first run; empty until set."""
    comfyui_models_dir: str = ""

    comfyui_identity_scene_mode: bool = False
    """Off by default so existing projects/behaviour never change silently.
    When on, ComfyUIProvider generates each scene in two local/free steps
    instead of one: (1) SDXL + InstantID composes a brand-new scene image
    from the scene prompt, using the character's reference photo ONLY for
    facial identity (not composition/background/pose); (2) that freshly
    generated image is fed into the existing, unchanged Wan2.2
    image-to-video path. This is what actually fixes 'das Referenzbild
    darf nicht die ganze Szene sein' for the free/local provider - the
    plain single-step image-to-video path (used when this is off) always
    locks the whole first frame to the reference photo. Requires the user
    to install the ComfyUI_InstantID custom node + its model files once;
    VOXini does not bundle or auto-download them."""
    comfyui_identity_checkpoint: str = "sd_xl_base_1.0.safetensors"
    """SDXL checkpoint filename (must already exist in ComfyUI's
    models/checkpoints/) used for the identity-scene stage above. Only
    read when comfyui_identity_scene_mode is True."""

    comfyui_resolution: str = "720p"
    """'480p' or '720p' - passed straight to ComfyUIProvider(resolution=...).
    Exposed here (instead of only being a ComfyUIProvider constructor
    default nobody could reach) so the setup wizard can let the user trade
    generation speed against output resolution per project."""
    comfyui_upscale_to_1080p: bool = True
    """Whether ComfyUIProvider locally upscales the raw Wan2.2 output to
    1080p with ffmpeg after generation (see ComfyUIProvider._upscale_local).
    On by default to match the previous hard-coded behaviour."""
    comfyui_negative_prompt: str = ""
    """Empty string = use ComfyUIProvider.DEFAULT_NEGATIVE_PROMPT (the
    built-in default). Lets an advanced user override the negative prompt
    used for the main Wan2.2 image/text-to-video stage without editing the
    workflow JSON or the provider source."""
    comfyui_identity_negative_prompt: str = ""
    """Same as comfyui_negative_prompt above, but for the SDXL+InstantID
    identity-scene stage (see comfyui_identity_scene_mode /
    ComfyUIProvider.DEFAULT_IDENTITY_NEGATIVE_PROMPT). Empty = built-in
    default."""

    comfyui_restart_every_n_scenes: int = 0
    """0 = disabled (default, unchanged behaviour: only the per-job POST
    /free call in ComfyUIProvider.free_memory()). When > 0, the generation
    worker (GenerationPanel._GenerationWorker) fully kills and relaunches
    the ComfyUI process every N locally-generated scenes, instead of
    relying on /free alone. Root cause (confirmed live 2026-09-03 on an AMD
    RX 7600 XT via Task-Manager: 14.1/16 GB VRAM reserved at only 5% GPU
    load after a few scenes): ROCm's PyTorch build doesn't support CUDA's
    'expandable_segments' allocator feature, so cached-but-unused VRAM
    fragments are never fully returned to the driver within one long-running
    process, even though /free correctly unloads every model each time.
    A full process restart is the only way to guarantee a clean VRAM state,
    which is what makes an unattended overnight batch of many scenes
    practical instead of grinding to a near-halt partway through."""

    runway_model_id: str = "gen4_turbo"
    runway_budget_limit: float = 0.0
    """0.0 = no explicit limit configured. When > 0, the generation gate
    refuses to submit a new Runway job once runway_spent_total + the job's
    estimated cost would exceed this limit, without asking - the user must
    raise the limit first. This is a hard ceiling, separate from the
    per-job confirmation that is always required regardless of budget."""
    runway_spent_total: float = 0.0
    """Running total of actual_cost across all accepted Runway ClipVersions
    in this project - used to enforce runway_budget_limit."""

    face_verification_enabled: bool = False
    """Off by default so existing projects/behaviour never change silently
    and so an unattended overnight batch (see comfyui_restart_every_n_scenes
    above) never blocks on a heavy, not-yet-installed dependency - matches
    the established opt-in pattern of comfyui_identity_scene_mode. When on,
    generation_service.generate_scene() runs core/face_verification.py
    after every successful clip generation for a scene with exactly one
    identifiable character reference (see face_verification.py for the
    exact applicability rule), comparing the character's reference photo
    against the generated clip's first and last frame using InsightFace -
    this is task #638, the direct response to the real-world identity-drift
    problem observed in the 'Willkommen bei Neon68' video. Requires the
    separate, lazily-installed '.venv-faceverify' environment (see core/
    face_verify_env.py) to actually be set up; if enabled but not yet set
    up, generation still proceeds normally and only records an honest
    ClipVersion.face_warning note - it does NOT block or silently install
    anything mid-generation."""
    face_verification_threshold: float = 0.40
    """Minimum cosine similarity (InsightFace buffalo_l embeddings, roughly
    -1..1) between the character reference photo and a generated frame to
    count as 'still the same face'. 0.40 is a documented STARTING POINT,
    not an empirically measured value (no GPU/model available to calibrate
    it from this development environment, per Entwicklungsvertrag Regel 11
    - no fabricated test results) - Neon68 should treat this as adjustable
    per project once real comparisons are visible in the UI, lowering it if
    genuinely-matching faces get flagged too often, or raising it if
    drifted faces still pass."""
    social_outro_enabled: bool = False
    """Off by default so existing projects/exports never change silently.
    When on, ffmpeg_assembly.assemble_video() appends a short, statically
    rendered end card (real drawtext + bundled platform icon PNGs, NOT
    AI-generated) right before the final fade-out, listing where to find
    the artist on TikTok/YouTube/Instagram and the music on Spotify/Amazon
    Music/Apple Music, plus the website below. Built this way - instead of
    describing it in the AI video prompt - because AI video generators
    cannot reliably render legible, correctly spelled text or accurate
    platform logos; see build_social_outro_card() in core/ffmpeg_assembly.py."""
    social_outro_website: str = "Neon68.de"
    """Website shown on the social outro card above. Free text so this stays
    reusable beyond this one artist/project."""

    def add_character(self, character: Character) -> None:
        self.characters[character.id] = character

    def sorted_scenes(self) -> list[Scene]:
        return sorted(self.scenes, key=lambda s: s.start_seconds)

    def provider_for_scene(self, scene: Scene) -> str:
        return scene.provider_override or self.default_provider_id

    def touch(self) -> None:
        self.updated_at = _now_iso()


class ProjectPaths:
    """Filesystem layout for a project directory.

    project_dir/
      project.voxproj          <- the Project JSON (manually saved)
      project.autosave.voxproj <- periodic autosave snapshot (see core/autosave.py)
      backups/project_<timestamp>.voxproj  <- rolling backups made on manual save
      media/audio/...          <- imported audio, copied in
      media/srt/...
      media/prompt/...
      media/characters/<id>/...  <- reference images per character
      clips/<scene_id>/<version_id>.mp4  <- generated / mock clips
      export/                  <- final rendered outputs
    """

    def __init__(self, project_dir: str | Path):
        self.root = Path(project_dir)

    @property
    def project_file(self) -> Path:
        return self.root / "project.voxproj"

    @property
    def autosave_file(self) -> Path:
        return self.root / "project.autosave.voxproj"

    @property
    def save_marker_file(self) -> Path:
        """Tiny marker file holding a nanosecond timestamp of the last
        manual save - used instead of raw filesystem mtimes to detect
        pending autosave recovery, since some filesystems only report
        mtime with coarse (e.g. 1s) resolution which would make two
        rapid saves indistinguishable."""
        return self.root / "project.savemarker"

    @property
    def autosave_marker_file(self) -> Path:
        return self.root / "project.autosave.marker"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def media_dir(self) -> Path:
        return self.root / "media"

    @property
    def audio_dir(self) -> Path:
        return self.media_dir / "audio"

    @property
    def srt_dir(self) -> Path:
        return self.media_dir / "srt"

    @property
    def prompt_dir(self) -> Path:
        return self.media_dir / "prompt"

    @property
    def characters_dir(self) -> Path:
        return self.media_dir / "characters"

    @property
    def clips_dir(self) -> Path:
        return self.root / "clips"

    @property
    def export_dir(self) -> Path:
        return self.root / "export"

    def ensure_layout(self) -> None:
        for d in (
            self.audio_dir,
            self.srt_dir,
            self.prompt_dir,
            self.characters_dir,
            self.clips_dir,
            self.export_dir,
            self.backups_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
