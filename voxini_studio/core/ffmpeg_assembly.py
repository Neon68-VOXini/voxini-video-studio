"""FFmpeg assembly engine: takes every scene's accepted clip, in storyboard
order, and produces a finished MP4 - crossfade transitions, an optional
title card, subtitles (soft/burned/none), muxed against the project's song,
in 16:9, 9:16 or 1:1. Uses the same frame-accurate padded-xfade-chain
technique proven on the Universum project: each clip is rendered at its
nominal length plus a trailing pad equal to the next transition's frame
count, and xfade offsets are computed from cumulative *nominal* frame
counts so the final duration lines up exactly with the source audio.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from voxini_studio.core import ffmpeg_locator
from voxini_studio.core.project_manager import ProjectManager
from voxini_studio.models.project import AspectRatio, Scene, SceneStatus
from voxini_studio.ui.theme import resource_root

FPS = 24  # matches the "24 fps" spec in the directorial prompt format

_ASPECT_DIMS: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.WIDESCREEN: (1920, 1080),
    AspectRatio.VERTICAL: (1080, 1920),
    AspectRatio.SQUARE: (1080, 1080),
}


class AssemblyError(RuntimeError):
    pass


@dataclass
class ChainItem:
    label: str
    src_path: Path
    duration: float


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(cmd, capture_output=True, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        raise AssemblyError(result.stderr.decode(errors="replace")[-2000:])


def _probe_duration(path) -> float:
    result = subprocess.run(
        [
            ffmpeg_locator.ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise AssemblyError(f"ffprobe failed for {path}: {result.stderr}")
    return float(result.stdout.strip())


def _scale_crop_filter(width: int, height: int) -> str:
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def ready_scenes(pm: ProjectManager) -> list[tuple[Scene, str]]:
    """Scenes (in storyboard order) that have an accepted clip, paired with
    the accepted clip's project-relative file path. Scenes without an
    accepted version are skipped.

    A scene in SceneStatus.NEEDS_REVIEW is ALSO skipped, even though it has
    an accepted_version() - task #638's automatic face-drift check
    (core/face_verification.py) sets this status when the generated clip's
    face no longer matches the character's reference photo closely enough,
    and per SceneStatus.NEEDS_REVIEW's own contract (see models/project.py)
    the export must refuse to include it until Neon68 explicitly resolves
    that (re-running generation successfully, which moves it back to DONE,
    or manually overriding it back to DONE from the storyboard once
    satisfied it's actually fine) - silently exporting a flagged scene
    would defeat the entire point of the check."""
    proj = pm.project
    if proj is None:
        raise AssemblyError("No project is open")
    out = []
    for scene in proj.sorted_scenes():
        if scene.status == SceneStatus.NEEDS_REVIEW:
            continue
        version = scene.accepted_version()
        if version is not None and version.file_path:
            out.append((scene, version.file_path))
    return out


def _title_font_path() -> str:
    """Bundled DejaVu Sans Bold (see voxini_studio/resources/fonts), used for
    title cards so this never depends on a specific font being pre-installed
    on the user's system - the original hardcoded '/usr/share/fonts/...'
    path only ever existed on Linux dev machines and silently broke title
    cards on a real Windows install."""
    return str(resource_root() / "fonts" / "DejaVuSans-Bold.ttf")


_SOCIAL_FOLLOW_PLATFORMS: list[tuple[str, str]] = [
    ("tiktok.png", "TikTok"),
    ("youtube.png", "YouTube"),
    ("instagram.png", "Instagram"),
]
_SOCIAL_MUSIC_PLATFORMS: list[tuple[str, str]] = [
    ("spotify.png", "Spotify"),
    ("amazonmusic.png", "Amazon Music"),
    ("applemusic.png", "Apple Music"),
]


def _social_icon_path(filename: str) -> Path:
    return resource_root() / "icons" / "social" / filename


def _drawtext_escape(text: str) -> str:
    return text.replace("\\", "").replace(":", "").replace("'", "").replace('"', "")[:80]


def build_social_outro_card(
    work_dir: Path, website: str, width: int, height: int, duration: float = 6.0,
) -> Path:
    """Renders a short, statically composited end card listing where to find
    the artist (TikTok/YouTube/Instagram) and their music (Spotify/Amazon
    Music/Apple Music), plus a website line - built entirely from real text
    (ffmpeg drawtext) and bundled platform icon PNGs (ffmpeg overlay), NOT
    described in an AI video prompt. AI video generators cannot reliably
    render legible, correctly spelled text or accurate logos, so this is
    composited deterministically instead - see Project.social_outro_enabled.
    """
    # See build_title_card() below for why this uses a bare filename + cwd
    # instead of an escaped absolute Windows path.
    font_path = Path(_title_font_path())
    dest = work_dir / "social_outro_card.mp4"

    fade_expr = (
        f"if(lt(t,0.6),t/0.6,if(gt(t,{duration - 0.6}),({duration}-t)/0.6,1))"
    )

    # Sized off min(width, height) rather than width/height alone - an
    # earlier version sized fonts off height only, which rendered fine at
    # 16:9 but overflowed off-screen and collided horizontally at 9:16
    # (height >> width there). Confirmed by an actual test render before
    # this fix; see the sandbox verification note in the project history.
    scale_dim = min(width, height)
    icon_size = max(40, round(scale_dim * 0.14))
    row1_icon_y = round(height * 0.16)
    row1_label_y = round(height * 0.30)
    headline2_y = round(height * 0.40)
    row2_icon_y = round(height * 0.50)
    row2_label_y = round(height * 0.64)
    footer_y = round(height * 0.75)
    website_y = round(height * 0.86)

    def row_x_positions(n: int) -> list[int]:
        step = width / (n + 1)
        return [round(step * (i + 1) - icon_size / 2) for i in range(n)]

    xs1 = row_x_positions(len(_SOCIAL_FOLLOW_PLATFORMS))
    xs2 = row_x_positions(len(_SOCIAL_MUSIC_PLATFORMS))
    all_items = _SOCIAL_FOLLOW_PLATFORMS + _SOCIAL_MUSIC_PLATFORMS
    all_xs = xs1 + xs2
    all_icon_ys = [row1_icon_y] * len(xs1) + [row2_icon_y] * len(xs2)
    all_label_ys = [row1_label_y] * len(xs1) + [row2_label_y] * len(xs2)

    cmd = [ffmpeg_locator.ffmpeg_path(), "-y",
           "-f", "lavfi", "-i", f"color=c=0x0b0f1a:s={width}x{height}:d={duration}:r={FPS}"]
    for filename, _label in all_items:
        cmd += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(_social_icon_path(filename))]

    filt: list[str] = []
    for i in range(len(all_items)):
        # NOTE: colorchannelmixer's "aa" option does not support a
        # time-varying (t-based) expression the way drawtext's alpha= does
        # (confirmed by an actual failing ffmpeg run in testing) - icons are
        # therefore static/opaque; the card's own crossfade into the
        # previous scene already makes its appearance smooth, and the
        # drawtext labels/headlines below still fade individually.
        filt.append(f"[{i + 1}:v]scale={icon_size}:{icon_size},format=rgba[icon{i}]")

    last = "0:v"
    for i, (x, y) in enumerate(zip(all_xs, all_icon_ys)):
        out_lbl = f"ov{i}"
        filt.append(f"[{last}][icon{i}]overlay={x}:{y}[{out_lbl}]")
        last = out_lbl

    def drawtext_centered(text: str, y: int, fontsize: int) -> str:
        return (
            f"drawtext=fontfile={font_path.name}:text='{_drawtext_escape(text)}':"
            f"fontcolor=white:fontsize={fontsize}:x=(w-text_w)/2:y={y}:alpha='{fade_expr}'"
        )

    def drawtext_under_icon(text: str, x_center: int, y: int, fontsize: int) -> str:
        return (
            f"drawtext=fontfile={font_path.name}:text='{_drawtext_escape(text)}':"
            f"fontcolor=white:fontsize={fontsize}:x={x_center}-text_w/2:y={y}:alpha='{fade_expr}'"
        )

    text_filters = [
        drawtext_centered("Mich findet ihr bei:", round(height * 0.06), round(scale_dim * 0.042)),
        drawtext_centered("Meine Musik gibt es bei:", headline2_y, round(scale_dim * 0.042)),
        drawtext_centered(
            "und weiteren bekannten Musik-Plattformen", footer_y, round(scale_dim * 0.026)
        ),
        drawtext_centered(website, website_y, round(scale_dim * 0.048)),
    ]
    for (icon_x, label_y), (_filename, label) in zip(zip(all_xs, all_label_ys), all_items):
        center_x = icon_x + icon_size // 2
        text_filters.append(
            drawtext_under_icon(label, center_x, label_y, round(scale_dim * 0.024))
        )

    chained = last
    for i, tf in enumerate(text_filters):
        out_lbl = f"txt{i}"
        filt.append(f"[{chained}]{tf}[{out_lbl}]")
        chained = out_lbl

    cmd += [
        "-filter_complex", ";".join(filt), "-map", f"[{chained}]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(FPS), str(dest), "-loglevel", "error",
    ]
    _run(cmd, cwd=font_path.parent)
    return dest


def build_title_card(work_dir: Path, text: str, width: int, height: int, duration: float = 3.5) -> Path:
    font_path = Path(_title_font_path())
    safe_text = text.replace("\\", "").replace(":", "").replace("'", "").replace('"', "")[:80]
    dest = work_dir / "title_card.mp4"
    # Escaping the Windows drive-letter colon (C:\...) inside fontfile= with a
    # backslash is the commonly documented approach, but was confirmed by an
    # actual failing export on this machine to still break ffmpeg's filter
    # parser ("No option name near ..."). The robust fix that sidesteps the
    # ambiguity entirely: run ffmpeg with the font's own folder as the
    # working directory and reference it by bare filename only - then the
    # fontfile value never contains a colon or path separator at all, so
    # there is nothing left for the filter parser to trip over.
    drawtext = (
        f"drawtext=fontfile={font_path.name}:text='{safe_text}':fontcolor=white:fontsize=54:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:alpha='if(lt(t,0.6),t/0.6,if(gt(t,{duration-0.6}),"
        f"({duration}-t)/0.6,1))'"
    )
    cmd = [
        ffmpeg_locator.ffmpeg_path(), "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d={duration}:r={FPS}",
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(dest), "-loglevel", "error",
    ]
    _run(cmd, cwd=font_path.parent)
    return dest


def assemble_video(
    pm: ProjectManager,
    aspect_ratio: AspectRatio,
    output_path: Path,
    transition: str = "dissolve",
    transition_duration: float = 0.4,
    mux_audio: bool = True,
) -> Path:
    proj = pm.project
    if proj is None:
        raise AssemblyError("No project is open")

    width, height = _ASPECT_DIMS[aspect_ratio]
    scenes_and_paths = ready_scenes(pm)
    if not scenes_and_paths:
        raise AssemblyError("No scenes with an accepted clip to assemble.")

    output_path = Path(output_path)
    work_dir = output_path.parent / f"_assembly_{output_path.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)

    items: list[ChainItem] = []
    if proj.title_text:
        title_clip = build_title_card(work_dir, proj.title_text, width, height)
        items.append(ChainItem("__title__", title_clip, _probe_duration(title_clip)))
    for scene, rel_path in scenes_and_paths:
        items.append(ChainItem(scene.label, pm.resolve(rel_path), scene.duration))
    if proj.social_outro_enabled:
        card_clip = build_social_outro_card(work_dir, proj.social_outro_website, width, height)
        items.append(ChainItem("__social_outro__", card_clip, _probe_duration(card_clip)))

    n = len(items)
    D_frames = max(2, round(transition_duration * FPS))

    normalized_paths: list[Path] = []
    nominal_frames: list[int] = []
    for i, item in enumerate(items):
        nom = max(1, round(item.duration * FPS))
        pad = D_frames if i < n - 1 else 0
        total_frames = nom + pad
        out = work_dir / f"norm_{i:03d}.mp4"
        vf = f"{_scale_crop_filter(width, height)},fps={FPS},tpad=stop_mode=clone:stop_duration={pad / FPS:.4f}"
        cmd = [
            ffmpeg_locator.ffmpeg_path(), "-y", "-i", str(item.src_path),
            "-vf", vf, "-t", f"{total_frames / FPS:.4f}",
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", str(FPS), str(out), "-loglevel", "error",
        ]
        _run(cmd)
        normalized_paths.append(out)
        nominal_frames.append(nom)

    cum = [0]
    for nf in nominal_frames:
        cum.append(cum[-1] + nf)

    if n == 1:
        chain_out = normalized_paths[0]
    else:
        inputs: list[str] = []
        for p in normalized_paths:
            inputs += ["-i", str(p)]
        filt_parts = []
        last_label = "0:v"
        for i in range(n - 1):
            offset_s = cum[i + 1] / FPS
            out_label = f"v{i + 1}" if i < n - 2 else "vout"
            filt_parts.append(
                f"[{last_label}][{i + 1}:v]xfade=transition={transition}:"
                f"duration={transition_duration:.3f}:offset={offset_s:.3f}[{out_label}]"
            )
            last_label = out_label
        chain_out = work_dir / "video_chain.mp4"
        cmd = [ffmpeg_locator.ffmpeg_path(), "-y"] + inputs + [
            "-filter_complex", ";".join(filt_parts), "-map", f"[{last_label}]",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-crf", "20", str(chain_out), "-loglevel", "error",
        ]
        _run(cmd)

    video_source = chain_out

    if proj.subtitle_mode == "burned" and proj.srt_path:
        srt_abs = pm.resolve(proj.srt_path)
        burned = work_dir / "video_subtitled.mp4"
        srt_escaped = str(srt_abs).replace("\\", "/").replace(":", "\\:")
        cmd = [
            ffmpeg_locator.ffmpeg_path(), "-y", "-i", str(video_source),
            "-vf", f"subtitles='{srt_escaped}'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            str(burned), "-loglevel", "error",
        ]
        _run(cmd)
        video_source = burned

    if mux_audio and proj.audio_path:
        audio_abs = pm.resolve(proj.audio_path)
        audio_duration = _probe_duration(audio_abs)
        video_duration = _probe_duration(video_source)

        if video_duration < audio_duration - 0.05:
            pad_amount = audio_duration - video_duration
            fade_start = max(0.0, audio_duration - 0.6)
            padded = work_dir / "video_padded.mp4"
            cmd = [
                ffmpeg_locator.ffmpeg_path(), "-y", "-i", str(video_source),
                "-vf",
                f"tpad=stop_mode=clone:stop_duration={pad_amount:.3f},"
                f"fade=t=out:st={fade_start:.3f}:d=0.6",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-r", str(FPS), str(padded), "-loglevel", "error",
            ]
            _run(cmd)
            video_source = padded
            video_duration = audio_duration

        # final_duration is normally == audio_duration (the branch above
        # always pads video up to match it). It can now also be LONGER than
        # audio_duration when proj.social_outro_enabled added an end card
        # after the last scene - in that case the audio itself needs
        # padding (silence) instead of the video being cropped short and
        # cutting the card off mid-display.
        final_duration = max(video_duration, audio_duration)
        needs_audio_pad = audio_duration < final_duration - 0.05

        mux_cmd = [ffmpeg_locator.ffmpeg_path(), "-y", "-i", str(video_source), "-i", str(audio_abs)]
        maps = ["-map", "0:v", "-map", "1:a"]
        # NOTE: deliberately -t <final_duration> instead of -shortest. With a
        # soft subtitle track muxed in, -shortest truncates to the SHORTEST
        # STREAM - including the subtitle stream, whose own duration is
        # derived from its last cue's end time. A lyric SRT that (as is
        # normal) has no cue over a trailing instrumental section would
        # silently cut the whole export short. An explicit -t pinned to the
        # (audio or, if longer, video) duration is correct regardless of
        # subtitle coverage.
        codecs = ["-c:v", "copy", "-c:a", "aac", "-b:a", "256k"]
        if needs_audio_pad:
            codecs += ["-af", f"apad=whole_dur={final_duration:.3f}"]
        codecs += ["-t", f"{final_duration:.3f}"]

        if proj.subtitle_mode == "soft" and proj.srt_path:
            srt_abs = pm.resolve(proj.srt_path)
            mux_cmd += ["-i", str(srt_abs)]
            maps += ["-map", "2:s"]
            codecs += ["-c:s", "mov_text"]

        mux_cmd += maps + codecs + [str(output_path), "-loglevel", "error"]
        _run(mux_cmd)
    else:
        _run([ffmpeg_locator.ffmpeg_path(), "-y", "-i", str(video_source), "-c", "copy", str(output_path), "-loglevel", "error"])

    # Export succeeded - the assembly work_dir's per-scene/title-card/chain
    # intermediates are no longer needed. Deliberately only reached on the
    # success path (any _run() failure above raises AssemblyError before
    # this line), so a FAILED export's intermediates stay on disk for
    # debugging instead of vanishing along with the error. Previously this
    # cleanup didn't exist at all - every export left a permanent
    # "_assembly_<name>/" scratch folder next to the finished video.
    shutil.rmtree(work_dir, ignore_errors=True)

    return output_path
