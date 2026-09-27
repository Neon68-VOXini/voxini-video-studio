"""Abstract provider interface. Every external (or mock) video generation
backend implements this so the rest of the app - cost estimation, the
confirmation gate, generation, regeneration - never needs to know which
concrete provider is in use.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from voxini_studio.models.project import AspectRatio, Scene


@dataclass
class GenerationRequest:
    scene: Scene
    resolved_prompt: str
    character_reference_paths: list[str]
    aspect_ratio: AspectRatio
    dest_path: Path
    model_id: str = ""
    identity_stage_prompt: str = ""
    """Prompt for the identity-scene pipeline's stage-1 SDXL still image
    (see Scene.stage1_identity_prompt / ComfyUIProvider.
    _generate_identity_scene_image) - deliberately NOT the same text as
    resolved_prompt, which is written for a multi-frame video model and
    always includes "every frame"/continuity language a still-image model
    has no use for. Empty when the scene has no characters (nothing for
    ComfyUIProvider to fall back to resolved_prompt for, since identity mode
    only runs when there is a character reference photo to begin with)."""
    identity_character_prompts: list[str] = field(default_factory=list)
    """Task #637 (Mehrfigur-Workflow): per-character identity text blocks
    (Character.prompt_block()), in the SAME order as
    character_reference_paths - only populated (len > 1) when the scene has
    more than one character AND the resolved provider actually declared
    support for that many simultaneous references (see
    Provider.max_simultaneous_references / resolve_scene_references()).
    ComfyUIProvider._generate_multi_character_identity_scene_image() combines
    each entry with identity_scene_description below to build that
    character's own masked-region prompt. Empty (the normal case) for every
    single-character or no-character scene - those keep using
    identity_stage_prompt exactly as before, no behaviour change."""
    identity_scene_description: str = ""
    """Task #637: the scene's shared setting/action text with all character
    identity blocks and video-only continuity language stripped out (see
    Scene.stage1_scene_description()) - used together with
    identity_character_prompts above so every masked region of a
    multi-character composite still reflects the same background/setting,
    not just that region's own character. Empty unless the scene has more
    than one character."""


@dataclass
class GenerationResult:
    success: bool
    file_path: str = ""
    error_message: str = ""
    actual_cost: float = 0.0
    warning: str = ""
    """Non-fatal degradation notice for an otherwise-successful result (e.g.
    ComfyUIProvider skipping the 1080p upscale because ffmpeg wasn't found -
    see ComfyUIProvider._upscale_local). Empty on a clean success. Copied
    onto ClipVersion.quality_warning by generation_service.generate_scene()
    so the UI can surface it, instead of a lower-quality-than-requested clip
    silently looking identical to a normal one."""


@dataclass
class ProviderInfo:
    id: str
    display_name: str
    price_per_second: float
    max_clip_seconds: float
    supported_aspect_ratios: list[AspectRatio] = field(default_factory=list)
    requires_api_key: bool = False


class Provider(ABC):
    """Concrete providers set the class attributes below and implement
    generate(). estimate_cost() is provided so the cost/confirmation gate
    can call it uniformly before any paid provider is actually invoked."""

    id: str = "base"
    display_name: str = "Base Provider"
    price_per_second: float = 0.0
    max_clip_seconds: float = 10.0
    supported_aspect_ratios: list[AspectRatio] = [AspectRatio.WIDESCREEN]
    requires_api_key: bool = False
    max_simultaneous_references: int = 1
    """Task #637 (Mehrfigur-Workflow): how many character/continuity
    reference images this provider can actually use for ONE job at once.
    1 (the default, matching every provider before this feature existed) -
    a scene needing more than this is hard-blocked by generation_service.
    resolve_scene_references() rather than silently sending only the first
    reference and dropping the rest (binding reference rule). Only
    ComfyUIProvider currently ever reports more than 1, and only while its
    identity-scene pipeline is enabled - see its override below. A plain
    class attribute (not a method) since it does not depend on the request,
    only on which provider/mode is configured."""

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.id,
            display_name=self.display_name,
            price_per_second=self.price_per_second,
            max_clip_seconds=self.max_clip_seconds,
            supported_aspect_ratios=list(self.supported_aspect_ratios),
            requires_api_key=self.requires_api_key,
        )

    def estimate_cost(self, duration_seconds: float) -> float:
        return round(self.price_per_second * duration_seconds, 4)

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        ...
