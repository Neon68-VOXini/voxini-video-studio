"""Provider registry. Two things live here:

1. A simple id -> default-configured-instance registry (register/get_provider/
   available_providers) so the UI can list every known provider (id, display
   name, indicative price, whether it needs an API key) without knowing the
   concrete classes. These default instances are fine for *listing* but are
   NOT what actual generation should use for comfyui/runway, since both need
   per-project settings (ComfyUI host/port, Runway model/budget).

2. build_provider(project, provider_id) - the factory actually used by
   generation_service - which constructs a fresh provider instance
   configured from the given Project's settings every time. This is what
   makes the hybrid workflow's "per project OR per scene" provider choice
   and the ComfyUI setup wizard's host/port changes actually take effect.
"""
from __future__ import annotations

from voxini_studio.providers.base import Provider
from voxini_studio.providers.comfyui_provider import ComfyUIProvider
from voxini_studio.providers.mock_provider import MockProvider
from voxini_studio.providers.runway_provider import RunwayProvider

_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _REGISTRY[provider.id] = provider


def get_provider(provider_id: str) -> Provider:
    if provider_id not in _REGISTRY:
        raise KeyError(f"Unknown provider: {provider_id!r}. Available: {list(_REGISTRY)}")
    return _REGISTRY[provider_id]


def available_providers() -> list[Provider]:
    return list(_REGISTRY.values())


def build_provider(project, provider_id: str) -> Provider:
    """Constructs a provider instance configured from the given project's
    settings - use this (not get_provider) for anything that will actually
    generate a clip. `project` is a voxini_studio.models.project.Project."""
    if provider_id == "mock":
        return MockProvider()
    if provider_id == "comfyui":
        return ComfyUIProvider(
            host=project.comfyui_host,
            port=project.comfyui_port,
            resolution=project.comfyui_resolution,
            upscale_to_1080p=project.comfyui_upscale_to_1080p,
            identity_scene_mode=project.comfyui_identity_scene_mode,
            identity_checkpoint=project.comfyui_identity_checkpoint,
            negative_prompt=project.comfyui_negative_prompt,
            identity_negative_prompt=project.comfyui_identity_negative_prompt,
        )
    if provider_id == "runway":
        return RunwayProvider(model_id=project.runway_model_id)
    raise KeyError(f"Unknown provider: {provider_id!r}")


register(MockProvider())
register(ComfyUIProvider())
register(RunwayProvider())
