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


def _font_path() -> str:
    """Bundled DejaVu Sans Bold - see ffmpeg_assembly._title_font_path for
    why this must never be a hardcoded Linux-only path."""
    font = resource_root() / "fonts" / "DejaVuSans-Bold.ttf"
    return str(font).replace("\\", "/").replace(":", "\\:")


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
        drawtext = (
            f"drawtext=fontfile={_font_path()}:text='{label}':fontcolor=white:fontsize=26:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.45:boxborderw=12"
        )
        vf = f"hue=h={seed}:s=1,{drawtext}"
        cmd = [
            ffmpeg_locator.ffmpeg_path(), "-y",
            "-f", "lavfi", "-i", f"color=c=slateblue:s={width}x{height}:d={duration}:r=24",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(dest), "-loglevel", "error",
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            return GenerationResult(success=False, error_message=result.stderr.decode()[-1000:])
        return GenerationResult(
            success=True,
            file_path=str(dest),
            actual_cost=self.estimate_cost(duration),
        )
