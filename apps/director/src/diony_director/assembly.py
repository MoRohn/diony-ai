"""Local assembly: the final program is always cut in Diony's own post house.

Normalizes every clip to one raster/rate, concatenates with real audio
handling (silent clips get a silent stereo bed instead of breaking the graph),
applies the default Diony look, and locks loudness — provider concatenate is
never the finish path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FPS = 24
WIDTH, HEIGHT = 1280, 720
LOUDNESS = {"streaming": "-16", "broadcast": "-24"}


class AssemblyUnavailable(RuntimeError):
    pass


class AssemblyFailed(RuntimeError):
    pass


@dataclass
class AssemblyResult:
    path: Path
    duration_s: float
    clip_count: int
    look: str
    loudness_target: str
    # per-clip (source_in_s, effective_duration_s) after trims — the cue and
    # EDL timing downstream deliverables are built from
    windows: list = None  # type: ignore[assignment]


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary is None:
        raise AssemblyUnavailable("ffmpeg is required for assembly and is not installed")
    return binary


def _probe(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise AssemblyUnavailable("ffprobe is required for assembly and is not installed")
    raw = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    body = json.loads(raw)
    return {
        "duration_s": float(body.get("format", {}).get("duration") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in body.get("streams", [])),
    }


DECLICK_FADE_S = 0.04  # a click guard, not an audible dip — the old 0.15s
# in/out pair dug a 300ms silence hole at every single cut
PER_CLIP_LOUDNESS = "-18"  # clips are level-matched BEFORE the join; the old
# post-concat dynaudnorm pumped between mismatched ambiences instead
ROOM_BED_AMPLITUDE = 0.006  # ≈ -44 dBFS brown noise under the whole program:
# continuity under every cut, so a join never falls into digital silence
MIN_CLIP_S = 0.2


def _default_look_chain() -> str:
    return "eq=saturation=1.06:contrast=1.04"


def resolve_look_chain(look: bool, look_profile: str) -> str:
    """The program look as one ffmpeg segment: the montage LookProfiles when
    named, the classic diony-default eq otherwise, empty when look is off."""
    if not look:
        return ""
    if look_profile and look_profile != "diony-default":
        try:
            from diony_contracts.montage import LookProfile, look_filter

            return look_filter(LookProfile(look_profile)) or ""
        except (ImportError, ValueError):
            return _default_look_chain()
    return _default_look_chain()


def effective_windows(probes: list[dict], specs: list[dict] | None) -> list[tuple[float, float]]:
    """Per-clip (start, duration) after handle/surplus trims.

    A spec's ``needed_s`` caps the clip at what the script actually needs
    (speech time + handles + safety): the provider grid's round-up surplus is
    trimmed off the tail instead of shipping as dead air. Explicit
    head/tail trims compose with it; a clip never shrinks below MIN_CLIP_S."""
    windows: list[tuple[float, float]] = []
    for index, probe in enumerate(probes):
        spec = (specs[index] if specs and index < len(specs) else {}) or {}
        duration = float(probe["duration_s"])
        head = max(0.0, float(spec.get("head_trim_s", 0.0)))
        tail = max(0.0, float(spec.get("tail_trim_s", 0.0)))
        effective = duration - head - tail
        needed = float(spec.get("needed_s", 0.0))
        if needed > 0:
            effective = min(effective, needed)
        effective = max(MIN_CLIP_S, effective)
        if head + effective > duration:
            head = max(0.0, duration - effective)
        windows.append((head, effective))
    return windows


def build_filter(
    probes: list[dict],
    silent_index: dict[int, int],
    *,
    look: bool,
    loudness_target: str,
    specs: list[dict] | None = None,
    look_profile: str = "diony-default",
) -> list[str]:
    """The join chain: per-clip trim → normalize → declick, one concat, a
    room-tone bed under the whole program, then the loudness lock."""
    chains = []
    concat_inputs = []
    windows = effective_windows(probes, specs)
    for index, _probe in enumerate(probes):
        head, effective = windows[index]
        chains.append(
            f"[{index}:v]trim=start={head:.3f}:end={head + effective:.3f},setpts=PTS-STARTPTS,"
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{index}]"
        )
        fade_out_start = max(0.0, effective - DECLICK_FADE_S)
        if index in silent_index:
            # silent bed inputs are generated at the trimmed length already
            chains.append(
                f"[{silent_index[index]}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"afade=t=in:st=0:d={DECLICK_FADE_S},"
                f"afade=t=out:st={fade_out_start:.3f}:d={DECLICK_FADE_S}[a{index}]"
            )
        else:
            chains.append(
                f"[{index}:a]atrim=start={head:.3f}:end={head + effective:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I={PER_CLIP_LOUDNESS}:TP=-2.0:LRA=11,aresample=48000,"
                f"afade=t=in:st=0:d={DECLICK_FADE_S},"
                f"afade=t=out:st={fade_out_start:.3f}:d={DECLICK_FADE_S}[a{index}]"
            )
        concat_inputs.append(f"[v{index}][a{index}]")
    chains.append(f"{''.join(concat_inputs)}concat=n={len(probes)}:v=1:a=1[vc][ac]")
    look_chain = resolve_look_chain(look, look_profile)
    if look_chain:
        chains.append(f"[vc]{look_chain}[vlook]")
    total = sum(effective for _, effective in windows)
    chains.append(
        f"anoisesrc=r=48000:colour=brown:amplitude={ROOM_BED_AMPLITUDE}:d={total:.3f},"
        f"aformat=channel_layouts=stereo[bed]"
    )
    target = LOUDNESS.get(loudness_target, LOUDNESS["streaming"])
    chains.append(
        f"[ac][bed]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I={target}:TP=-1.0:LRA=11,aresample=48000[aout]"
    )
    return chains


def assemble(
    clip_paths: list[Path],
    dest: Path,
    *,
    look: bool = True,
    loudness_target: str = "streaming",
    specs: list[dict] | None = None,
    look_profile: str = "diony-default",
) -> AssemblyResult:
    if not clip_paths:
        raise AssemblyFailed("nothing to assemble: no completed clips")
    ffmpeg = _ffmpeg()
    probes = [_probe(p) for p in clip_paths]
    dead = [
        clip_paths[i].name for i, probe in enumerate(probes) if probe["duration_s"] <= 0.05
    ]
    if dead:
        # name the truncated clip instead of letting ffmpeg fail the whole
        # concat with an anonymous stderr tail
        raise AssemblyFailed(
            f"clip(s) probe as empty ({', '.join(dead)}) — likely truncated downloads; "
            "re-run those clips before assembling"
        )
    windows = effective_windows(probes, specs)

    cmd: list[str] = [ffmpeg, "-y"]
    for path in clip_paths:
        cmd += ["-i", str(path)]
    silent_index: dict[int, int] = {}
    next_input = len(clip_paths)
    for index, probe in enumerate(probes):
        if not probe["has_audio"]:
            cmd += [
                "-f", "lavfi", "-t", f"{max(windows[index][1], 0.1):.3f}",
                "-i", "anullsrc=r=48000:cl=stereo",
            ]
            silent_index[index] = next_input
            next_input += 1

    chains = build_filter(
        probes,
        silent_index,
        look=look,
        loudness_target=loudness_target,
        specs=specs,
        look_profile=look_profile,
    )
    video_out = "[vlook]" if resolve_look_chain(look, look_profile) else "[vc]"

    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", video_out, "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=1800)
    if result.returncode != 0 or not dest.is_file():
        tail = result.stderr.decode(errors="replace")[-800:]
        raise AssemblyFailed(f"ffmpeg assembly failed: {tail}")

    final = _probe(dest)
    return AssemblyResult(
        path=dest,
        duration_s=final["duration_s"],
        clip_count=len(clip_paths),
        look=(look_profile or "diony-default") if look else "none",
        loudness_target=loudness_target,
        windows=windows,
    )
