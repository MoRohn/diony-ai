"""Production Mix receipts and media verification.

Mix is a machine quality gate, not a timer. A live run cannot close it until
audio stems, measured loudness, true peak, and an audio-bearing program master
all exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from diony_contracts.enums import AssetKind
from diony_contracts.models import Asset, Project
from diony_contracts.voice import VoiceClipPackage
from diony_finish.pipeline import FinishError, finished_media_has_picture, verify_finished_media

from diony_producer.gallery_join import resolve_media_tool
from diony_producer.gallery import gallery_program_preview_path
from diony_producer.vault import abs_path, persist_project, record_activity, vault_root


class MixError(RuntimeError):
    """A mix could not produce verified media evidence."""


_lock = threading.Lock()
_busy = False
_detail = ""
_error = ""
_generation = 0


def _visual_qc_required() -> bool:
    disabled = {"1", "true", "yes", "on"}
    return not (
        os.environ.get("DIONY_DRY_RUN", "").strip().lower() in disabled
        or os.environ.get("DIONY_FINISH_FAKE", "").strip().lower() in disabled
    )


def mix_status() -> dict:
    with _lock:
        return {"busy": _busy, "detail": _detail, "error": _error}


def reset_mix() -> None:
    global _busy, _detail, _error, _generation
    with _lock:
        _generation += 1
        _busy = False
        _detail = ""
        _error = ""


def abort_mix() -> None:
    reset_mix()


def _tool(name: str) -> str:
    resolved = resolve_media_tool(name)
    if not resolved:
        raise MixError(f"{name} is required for the production mix")
    return resolved


def _run(command: list[str], *, label: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MixError(f"{label} failed: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"exit {result.returncode}"
        raise MixError(f"{label} failed: {message[:500]}")
    return result


def _duration_seconds(master: Path, ffprobe: str) -> float:
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(master),
        ],
        label="program duration probe",
        timeout=30,
    )
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MixError("program duration evidence is missing") from exc
    if duration <= 0:
        raise MixError("program duration evidence is invalid")
    return duration


def _json_report(blob: str) -> dict:
    start = blob.rfind("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        raise MixError("FFmpeg did not return measured loudness evidence")
    try:
        return json.loads(blob[start : end + 1])
    except json.JSONDecodeError as exc:
        raise MixError("FFmpeg returned unreadable loudness evidence") from exc


def _measure_stem(stem: Path, *, ffmpeg: str, target: float, peak: float) -> tuple[float, float]:
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(stem),
            "-af",
            f"loudnorm=I={target:g}:TP={peak:g}:LRA=7:print_format=json",
            "-f",
            "null",
            "-",
        ],
        label="mix measurement",
    )
    report = _json_report(result.stderr or result.stdout)
    try:
        measured = float(report["input_i"])
        true_peak = float(report["input_tp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MixError("measured LUFS or true-peak evidence is missing") from exc
    if not (-100 < measured < 10) or not (-100 < true_peak < 10):
        raise MixError("measured LUFS or true-peak evidence is invalid")
    return round(measured, 2), round(true_peak, 2)


def _write_room_tone(
    destination: Path,
    *,
    ffmpeg: str,
    duration: float,
    target: float,
    peak: float,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part.wav")
    pending.unlink(missing_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=pink:amplitude=0.08:sample_rate=48000:seed=10401",
            "-t",
            f"{duration:.6f}",
            "-af",
            f"highpass=f=80,lowpass=f=1200,loudnorm=I={target:g}:TP={peak:g}:LRA=7",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(pending),
        ],
        label="room-tone stem bounce",
    )
    if not pending.is_file() or pending.stat().st_size <= 44:
        raise MixError("room-tone stem bytes are missing")
    os.replace(pending, destination)


def _dialogue_script(project: Project) -> str:
    lines: list[str] = []
    for shot in project.shots:
        candidates = [turn.text for turn in shot.dialogue_turns]
        if shot.audio_line:
            candidates.insert(0, shot.audio_line)
        for candidate in candidates:
            line = " ".join(str(candidate or "").split()).strip()
            if line and line not in lines:
                lines.append(line)
    return " ".join(lines)


def _selected_take_for_shot(project: Project, shot_id: str):
    shot = project.shot(shot_id)
    if shot.selected_take_id:
        try:
            return project.take(shot.selected_take_id)
        except KeyError:
            return None
    takes = project.takes_for(shot_id)
    return takes[-1] if takes else None


def _locked_voice_sources(
    project: Project,
) -> list[tuple[Path, float, VoiceClipPackage]]:
    """Resolve verified per-clip mixes at their frame-authoritative timeline starts."""
    if project.timeline is None:
        raise MixError("Required dialogue needs a locked timeline")
    starts = {event.shot_id: event.start_frame / project.timeline.fps for event in project.timeline.events}
    sources: list[tuple[Path, float, VoiceClipPackage]] = []
    for shot in project.shots:
        if not shot.audio_line and not shot.dialogue_turns:
            continue
        take = _selected_take_for_shot(project, shot.id)
        package = take.voice_package if take is not None else None
        if package is None:
            raise MixError(f"Required dialogue on {shot.id} has no locked voice package")
        if package.state.value != "verified" or not package.qc.passed:
            raise MixError(f"Required dialogue on {shot.id} has an unverified voice package")
        try:
            asset = project.asset(package.final_mix_asset_id)
            metadata = project.asset(package.performance_metadata_asset_id)
        except KeyError as exc:
            raise MixError(f"Required dialogue on {shot.id} has incomplete voice package assets") from exc
        audio = abs_path(asset.path)
        timing = abs_path(metadata.path)
        if not audio.is_file() or audio.stat().st_size <= 44:
            raise MixError(f"Required dialogue on {shot.id} has no final mixed audio bytes")
        if not timing.is_file() or timing.stat().st_size <= 32:
            raise MixError(f"Required dialogue on {shot.id} has no performance timing metadata")
        sources.append((audio, max(0.0, starts.get(shot.id, 0.0)), package))
    return sorted(sources, key=lambda row: (row[1], row[2].shot_id))


def _write_locked_voice_stem(
    destination: Path,
    *,
    ffmpeg: str,
    sources: list[tuple[Path, float, VoiceClipPackage]],
    duration: float,
    target: float,
    peak: float,
) -> None:
    """Assemble already-produced clip mixes; synthesis never occurs at final mix."""
    if not sources:
        raise MixError("Required dialogue has no verified per-clip voice packages")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part.wav")
    pending.unlink(missing_ok=True)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for audio, _start, _package in sources:
        command.extend(["-i", str(audio)])
    filters: list[str] = []
    labels: list[str] = []
    for index, (_audio, start_seconds, _package) in enumerate(sources):
        delay_ms = max(0, round(start_seconds * 1000))
        label = f"voice{index}"
        filters.append(
            f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,"
        + f"apad,atrim=0:{duration:.6f},"
        + f"loudnorm=I={target:g}:TP={peak:g}:LRA=7[out]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(pending),
        ]
    )
    _run(command, label="locked voice stem assembly")
    if not pending.is_file() or pending.stat().st_size <= 44:
        raise MixError("Locked per-clip voice assembly returned no audio bytes")
    os.replace(pending, destination)


def _mux_program(master: Path, stem: Path, *, ffmpeg: str) -> None:
    pending = master.with_name(f".{master.stem}.mix.part{master.suffix}")
    pending.unlink(missing_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(master),
            "-i",
            str(stem),
            "-filter_complex",
            "[1:a]apad[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-map_metadata",
            "0",
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s24le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            str(pending),
        ],
        label="program mix mux",
    )
    if not pending.is_file() or pending.stat().st_size <= 32:
        raise MixError("audio-bearing program master bytes are missing")
    os.replace(pending, master)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mix_revision(project: Project) -> int:
    return 1 + sum(
        asset.role in {"room_tone_stem", "dialogue_stem"}
        for asset in project.assets
    )


def bounce_mix(project: Project) -> None:
    """Assemble locked per-clip voice mixes or an honest picture-only room-tone rail."""
    if project.mix is None or project.timeline is None or not project.timeline.preview_path:
        raise MixError("Mix needs a finished program timeline")
    master = abs_path(project.timeline.preview_path)
    if not master.is_file() or master.stat().st_size <= 32:
        raise MixError("Mix needs the finished 1080 program master")
    if _visual_qc_required():
        try:
            verify_finished_media(master, label="1080 program master")
        except FinishError as exc:
            raise MixError(str(exc)) from exc
    ffmpeg = _tool("ffmpeg")
    ffprobe = _tool("ffprobe")
    target = -24.0 if project.bible.loudness.value == "broadcast" else -16.0
    peak = project.bible.true_peak_dbtp
    revision = _mix_revision(project)
    script = _dialogue_script(project)
    voice_sources = _locked_voice_sources(project) if script else []
    role = "dialogue_stem" if script else "room_tone_stem"
    stem_rel = (
        f"projects/{project.id}/program/{project.timeline.id}/stems/"
        f"{'dialogue' if script else 'room-tone'}-v{revision}.wav"
    )
    stem = abs_path(stem_rel)
    duration = _duration_seconds(master, ffprobe)
    if script:
        _write_locked_voice_stem(
            stem,
            ffmpeg=ffmpeg,
            sources=voice_sources,
            duration=duration,
            target=target,
            peak=peak,
        )
    else:
        _write_room_tone(stem, ffmpeg=ffmpeg, duration=duration, target=target, peak=peak)
    measured, true_peak = _measure_stem(stem, ffmpeg=ffmpeg, target=target, peak=peak)
    if abs(measured - target) > 1.0:
        raise MixError(f"measured loudness {measured:g} LUFS missed the {target:g} LUFS target")
    if true_peak > peak:
        raise MixError(f"measured true peak {true_peak:g} dBTP exceeded the {peak:g} dBTP ceiling")
    _mux_program(master, stem, ffmpeg=ffmpeg)
    if _visual_qc_required():
        try:
            verify_finished_media(master, label="mixed 1080 program master")
        except FinishError as exc:
            raise MixError(str(exc)) from exc
    if not program_master_has_audio(project):
        raise MixError("the mixed 1080 program master has no verified audio stream")

    digest = _sha256(stem)
    asset = Asset(
        id=f"ast-{digest[:12]}-mix-v{revision}",
        kind=AssetKind.AUDIO,
        filename=stem.name,
        mime="audio/wav",
        bytes=stem.stat().st_size,
        path=stem_rel,
        sha256=digest,
        role=role,
        metadata={
            "mix_revision": revision,
            "timeline_id": project.timeline.id,
            "measured_lufs": measured,
            "true_peak_dbtp": true_peak,
            "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest() if script else "",
            "voice_package_count": len(voice_sources),
            "voice_pipeline": "locked_per_clip" if voice_sources else "none",
            "voice_package_sha256": hashlib.sha256(
                "|".join(package.cache_key for _, _, package in voice_sources).encode("utf-8")
            ).hexdigest() if voice_sources else "",
        },
    )
    project.assets.append(asset)
    project.mix.stem_asset_ids = [asset.id]
    project.mix.measured_lufs = measured
    project.mix.true_peak_dbtp = true_peak
    project.mix.picture_lock_timeline_id = project.timeline.id
    project.timeline.program_sha256 = _sha256(master)
    # The audio-bearing master is a new Gallery version. A removal of the
    # earlier picture-only file must not suppress the completed program.
    project.timeline.gallery_removed_at = None
    gallery_program_preview_path(project, project.id)
    persist_project(project)


def start_mix(project: Project) -> bool:
    """Start one idempotent background bounce. Return True when work is active."""
    global _busy, _detail, _error
    with _lock:
        if _busy:
            return True
        _busy = True
        _detail = "Mixing to the locked loudness target"
        _error = ""
        token = _generation
    record_activity("mix", _detail, level="live")

    def loop() -> None:
        global _busy, _detail, _error
        try:
            bounce_mix(project)
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip()[:800] or "production mix failed"
            with _lock:
                if token != _generation:
                    return
                _busy = False
                _detail = message
                _error = message
            persist_project(project)
            record_activity("mix", message, level="fail")
            return
        with _lock:
            if token != _generation:
                return
            _busy = False
            _detail = "Mix measured and verified"
            _error = ""
        record_activity("mix", _detail)
        from diony_producer.advance import maybe_advance_run

        maybe_advance_run(project)

    threading.Thread(target=loop, name="diony-mix", daemon=True).start()
    return True


def mix_evidence_blockers(project: Project, *, verify_program: bool = False) -> list[str]:
    mix = project.mix
    if mix is None:
        return ["Mix receipt is missing."]

    blockers: list[str] = []
    if not mix.stem_asset_ids:
        blockers.append("Audio stems are missing.")
    elif not _stem_assets_valid(project):
        blockers.append("Audio stem evidence is invalid.")
    if mix.measured_lufs is None:
        blockers.append("Measured loudness evidence is missing.")
    else:
        target = -24.0 if project.bible.loudness.value == "broadcast" else -16.0
        if abs(mix.measured_lufs - target) > 1.0:
            blockers.append(
                f"Measured loudness {mix.measured_lufs:g} LUFS is outside the {target:g} LUFS target."
            )
    if mix.true_peak_dbtp is None:
        blockers.append("Measured true-peak evidence is missing.")
    elif mix.true_peak_dbtp > project.bible.true_peak_dbtp:
        blockers.append(
            f"Measured true peak {mix.true_peak_dbtp:g} dBTP exceeds "
            f"the {project.bible.true_peak_dbtp:g} dBTP ceiling."
        )
    if verify_program and not program_master_has_audio(project):
        blockers.append("The 1080 program master has no verified audio stream.")
    if verify_program and _visual_qc_required():
        master = (
            vault_root() / project.timeline.preview_path
            if project.timeline and project.timeline.preview_path
            else None
        )
        if master is None or not finished_media_has_picture(master):
            blockers.append("The 1080 program master failed visual QC with empty or flat-color frames.")
    return blockers


def _stem_assets_valid(project: Project) -> bool:
    if project.mix is None:
        return False
    for asset_id in project.mix.stem_asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError:
            return False
        if asset.kind != AssetKind.AUDIO:
            return False
        try:
            path = abs_path(asset.path)
        except ValueError:
            return False
        if not path.is_file() or path.stat().st_size <= 44:
            return False
    return bool(project.mix.stem_asset_ids)


def program_master_has_audio(project: Project) -> bool:
    if not project.timeline or not project.timeline.preview_path:
        return False
    master = vault_root() / project.timeline.preview_path
    ffprobe = resolve_media_tool("ffprobe") or shutil.which("ffprobe")
    if not master.is_file() or not ffprobe:
        return False
    try:
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(master),
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0 and bool(probe.stdout.strip())
