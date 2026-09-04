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
from typing import Optional

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


class Character(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    """Free-text identity block (age, hair, eyes, build, wardrobe notes,
    anything that must stay consistent). This text is injected verbatim
    into every scene prompt that references this character."""
    reference_image_paths: list[str] = Field(default_factory=list)

    def prompt_block(self) -> str:
        """Text injected into a scene prompt to keep this character consistent."""
        if not self.description:
            return self.name
        return f"{self.name}: {self.description}"


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
        """Prompt text with character identity blocks prepended."""
        blocks = [characters[cid].prompt_block() for cid in self.character_ids if cid in characters]
        if not blocks:
            return self.prompt_text
        header = "CHARACTERS:\n" + "\n".join(f"- {b}" for b in blocks)
        return f"{header}\n\nSCENE:\n{self.prompt_text}"


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
