"""Mock provider for the MVP: no external API, no stock footage. Produces a
short procedurally generated placeholder clip (solid color + scene label,
color varied per scene) purely so the full pipeline - storyboard, cost
gate, generation, ffmpeg assembly, export - can be exercised end to end
without any paid API call. price_per_second is nonzero on purpose (a small
simulated rate) so the cost/confirmation gate has something real to show
and confirm, clearly labeled as simulated.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from voxini_studio.core import ffmpeg_locator
from voxini_studio.models.project import AspectRatio
from voxini_studio.providers.base import GenerationRequest, GenerationResult, Provider
from voxini_studio.ui.theme import resource_root

_ASPECT_DIMS = {
    AspectRatio.WIDESCREEN: (1280, 720),
    AspectRatio.VERTICAL: (720, 1280),
    AspectRatio.SQUARE: (960, 960),
}


def _font_path() -> Path:
    """Bundled DejaVu Sans Bold - see ffmpeg_assembly._title_font_path for
    why this must never be a hardcoded Linux-only path.

    Returns the Path itself (not an escaped string) - see generate() for why:
    the caller runs ffmpeg with this file's parent directory as the working
    directory and references it by bare filename only, exactly like
    ffmpeg_assembly.build_title_card() already does, instead of embedding an
    absolute Windows path (with its drive-letter colon) into the drawtext
    filter string."""
    return resource_root() / "fonts" / "DejaVuSans-Bold.ttf"


def _safe_text(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace(":", "")
        .replace("'", "")
        .replace('"', "")
        .replace("%", "")
    )[:70]


class MockProvider(Provider):
    id = "mock"
    display_name = "Mock (lokal, kein Stockmaterial - simulierte Kosten)"
    price_per_second = 0.05  # simulated rate, purely to exercise the cost gate
    max_clip_seconds = 10.0
    supported_aspect_ratios = [AspectRatio.WIDESCREEN, AspectRatio.VERTICAL, AspectRatio.SQUARE]
    requires_api_key = False

    def generate(self, request: GenerationRequest) -> GenerationResult:
        width, height = _ASPECT_DIMS.get(request.aspect_ratio, (1280, 720))
        duration = max(0.5, request.scene.duration)
        dest = Path(request.dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        seed = int(hashlib.md5(request.scene.id.encode()).hexdigest(), 16) % 360
        label = _safe_text(f"#{request.scene.order} {request.scene.label}")
        font_path = _font_path()
        drawtext = (
            f"drawtext=fontfile={font_path.name}:text='{label}':fontcolor=white:fontsize=26:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.45:boxborderw=12"
        )
        vf = f"hue=h={seed}:s=1,{drawtext}"
        # dest is passed as an absolute path, so it resolves correctly
        # regardless of the cwd set below for the fontfile lookup.
        cmd = [
            ffmpeg_locator.ffmpeg_path(), "-y",
            "-f", "lavfi", "-i", f"color=c=slateblue:s={width}x{height}:d={duration}:r=24",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(dest.resolve()), "-loglevel", "error",
        ]
        result = subprocess.run(cmd, capture_output=True, cwd=font_path.parent)
        if result.returncode != 0:
            return GenerationResult(success=False, error_message=result.stderr.decode()[-1000:])
        return GenerationResult(
            success=True,
            file_path=str(dest),
            actual_cost=self.estimate_cost(duration),
        )
