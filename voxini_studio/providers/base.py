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


@dataclass
class GenerationResult:
    success: bool
    file_path: str = ""
    error_message: str = ""
    actual_cost: float = 0.0


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
