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
from typing import NamedTuple
from pathlib import Path

from diony_contracts.enums import AssetKind
from diony_contracts.models import Asset, Project
from diony_contracts.voice import SpeechClipPackage
from diony_finish.pipeline import FinishError, finished_media_has_picture, verify_finished_media

from diony_producer.gallery_join import resolve_media_tool
from diony_producer.gallery import gallery_program_preview_path
from dataclasses import replace

from diony_contracts.montage import DecisionCategory, DecisionOption
from diony_contracts.score import AlignmentMode, ScoreAlignment, align_cue_to_cuts
from diony_producer.soundtrack import (
    PlannedCue,
    ensure_generated_cues,
    load_soundtrack_plan,
    soundtrack_plan_exists,
)
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


class VoiceRailRow(NamedTuple):
    audio: Path
    start_seconds: float
    package: SpeechClipPackage
    clip_seconds: float
    fade_in: float
    fade_out: float


def _locked_voice_sources(project: Project) -> list[VoiceRailRow]:
    """Resolve verified per-clip mixes at their frame-authoritative timeline starts.

    Each row carries junction fades sized to the picture's temporal blends, so
    audio crosses a cut exactly where picture dissolves instead of butt-cutting
    under a visible transition.
    """
    if project.timeline is None:
        raise MixError("Required dialogue needs a locked timeline")
    fps = project.timeline.fps or 23.976
    events = list(project.timeline.events)
    starts = {event.shot_id: event.start_frame / fps for event in events}
    fades_in = {event.shot_id: max(0, event.handle_in) / fps for event in events}
    fades_out: dict[str, float] = {}
    for current, following in zip(events, events[1:]):
        fades_out[current.shot_id] = max(0, following.handle_in) / fps
    sources: list[VoiceRailRow] = []
    for shot in project.shots:
        if not shot.audio_line and not shot.dialogue_turns:
            continue
        take = _selected_take_for_shot(project, shot.id)
        package = take.voice_package if take is not None else None
        if package is None:
            raise MixError(f"Required dialogue on {shot.id} has no Wan speech package")
        settled = take is not None and take.qc is not None and take.qc.settled_by_policy
        qc = package.qc
        if not package.verified:
            # A print settled by retake-cap policy may carry speech that
            # measured present but missed a loudness or tail gate; the stem
            # is still Wan's performance of the authored line.
            if not (settled and qc is not None and qc.audio_present and package.mix_stem_asset_id):
                raise MixError(f"Required dialogue on {shot.id} has an unverified speech package")
        if qc is None or qc.speech_presence_score is None or qc.speech_presence_score < qc.speech_presence_min:
            raise MixError(
                f"Required dialogue on {shot.id} has no measured speech-content evidence"
            )
        try:
            asset = project.asset(package.mix_stem_asset_id)
        except KeyError as exc:
            raise MixError(f"Required dialogue on {shot.id} has incomplete speech package assets") from exc
        audio = abs_path(asset.path)
        if not audio.is_file() or audio.stat().st_size <= 44:
            raise MixError(f"Required dialogue on {shot.id} has no dialogue stem bytes")
        sources.append(
            VoiceRailRow(
                audio=audio,
                start_seconds=max(0.0, starts.get(shot.id, 0.0)),
                package=package,
                clip_seconds=max(0.1, shot.duration_frames / (shot.fps or fps)),
                fade_in=round(fades_in.get(shot.id, 0.0), 6),
                fade_out=round(fades_out.get(shot.id, 0.0), 6),
            )
        )
    return sorted(sources, key=lambda row: (row.start_seconds, row.package.shot_id))


def _voice_rail_filters(sources: list[VoiceRailRow]) -> tuple[list[str], list[str]]:
    """Per-clip filter chains with picture-matched junction fades. Pure."""
    filters: list[str] = []
    labels: list[str] = []
    for index, row in enumerate(sources):
        chain = [f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo"]
        if row.fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={row.fade_in:.6f}")
        if row.fade_out > 0:
            start = max(0.0, row.clip_seconds - row.fade_out)
            chain.append(f"afade=t=out:st={start:.6f}:d={row.fade_out:.6f}")
        delay_ms = max(0, round(row.start_seconds * 1000))
        chain.append(f"adelay={delay_ms}|{delay_ms}")
        label = f"voice{index}"
        filters.append(",".join(chain) + f"[{label}]")
        labels.append(f"[{label}]")
    return filters, labels


def hero_cut_seconds(project: Project) -> list[float]:
    """Timeline instants where a hero shot's print begins (the cuts the score serves)."""
    timeline = project.timeline
    if timeline is None:
        return []
    fps = float(timeline.fps or 23.976)
    heroes = {shot.id for shot in project.shots if shot.hero}
    return [round(event.start_frame / fps, 6) for event in timeline.events if event.shot_id in heroes]


def align_score_cues(project: Project, cues: list[PlannedCue]) -> tuple[list[PlannedCue], list[ScoreAlignment]]:
    """Shift each score cue's in-point (< one bar) so downbeats land on hero cuts.

    Blueprint 24 §12. Only `score` cues move; ambience and foley are placed
    as planned. The Bible can switch the behaviour off; a cue without a
    stable pulse, or without a hero cut inside its span, stays phrase flow.
    Every decision is recorded on the MixLock and in the decision log.
    """
    from diony_producer.beat_grid import analyze_beat_grid
    from diony_producer.decisions import record_decision
    from diony_producer.speech_audio import media_duration_seconds

    cuts = hero_cut_seconds(project)
    enabled = bool(project.bible.score_cuts_on_downbeats)
    aligned: list[PlannedCue] = []
    alignments: list[ScoreAlignment] = []
    for cue in cues:
        if cue.role != "score" or not enabled:
            aligned.append(cue)
            continue
        grid = analyze_beat_grid(Path(cue.path))
        if grid is None:
            aligned.append(cue)
            alignments.append(
                ScoreAlignment(
                    cue_id=cue.cue_id,
                    mode=AlignmentMode.UNALIGNED,
                    cue_start_seconds=cue.start_seconds,
                    cue_duration_seconds=cue.duration_seconds,
                    reason="the cue could not be decoded for analysis",
                )
            )
            continue
        try:
            source_seconds: float | None = media_duration_seconds(Path(cue.path))
        except Exception:  # noqa: BLE001 - unknown source length caps the shift at one bar
            source_seconds = None
        alignment = align_cue_to_cuts(
            grid,
            cue_id=cue.cue_id,
            cue_start_seconds=cue.start_seconds,
            cue_duration_seconds=cue.duration_seconds,
            cut_seconds=cuts,
            source_duration_seconds=source_seconds,
        )
        alignments.append(alignment)
        aligned.append(replace(cue, source_offset_seconds=alignment.source_shift_seconds))
    if alignments:
        landed = [row for row in alignments if row.mode == AlignmentMode.BEAT_CUT]
        summary = "; ".join(f"{row.cue_id}: {row.reason}" for row in alignments)[:600]
        record_decision(
            project,
            stage="mix",
            category=DecisionCategory.SOUNDTRACK,
            subject="score alignment to hero cuts",
            options=[
                DecisionOption(id="beat_cut", label="shift the in-point so downbeats land on hero cuts", reason=summary if landed else "", rejected_because="" if landed else "no cue measured a usable pulse under a hero cut"),
                DecisionOption(id="phrase_flow", label="place the cue as planned", reason="" if landed else summary, rejected_because="a pulse served the cuts" if landed else ""),
            ],
            selected="beat_cut" if landed else "phrase_flow",
            reason=summary,
            confidence=0.7,
        )
        record_activity(
            "mix",
            "Score alignment: " + summary,
            level="live" if landed else "info",
        )
    return aligned, alignments


def _cue_rail_filters(cues: list[PlannedCue]) -> tuple[list[str], list[str]]:
    """Per-cue filter chains: licensed library bytes, trimmed, gained, placed. Pure."""
    filters: list[str] = []
    labels: list[str] = []
    for index, cue in enumerate(cues):
        delay_ms = max(0, round(cue.start_seconds * 1000))
        label = f"cue{index}"
        start = max(0.0, float(getattr(cue, "source_offset_seconds", 0.0) or 0.0))
        filters.append(
            f"[{index}:a]atrim={start:.6f}:{start + cue.duration_seconds:.6f},asetpts=PTS-STARTPTS,"
            "aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={cue.gain_db:g}dB,"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        labels.append(f"[{label}]")
    return filters, labels


def _write_rail(
    destination: Path,
    *,
    ffmpeg: str,
    inputs: list[Path],
    filters: list[str],
    labels: list[str],
    duration: float,
    label: str,
) -> None:
    """Assemble already-produced audio; nothing is synthesized at final mix."""
    if not labels:
        raise MixError(f"{label} has no audio sources")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part.wav")
    pending.unlink(missing_ok=True)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for audio in inputs:
        command.extend(["-i", str(audio)])
    graph = list(filters)
    graph.append(
        "".join(labels)
        + (f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0," if len(labels) > 1 else "")
        + f"apad,atrim=0:{duration:.6f}[out]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(graph),
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
    _run(command, label=label)
    if not pending.is_file() or pending.stat().st_size <= 44:
        raise MixError(f"{label} returned no audio bytes")
    os.replace(pending, destination)


def _loudness_full_report(stem: Path, *, ffmpeg: str, target: float, peak: float) -> dict:
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
        label="program loudness measurement",
    )
    return _json_report(result.stderr or result.stdout)


def _write_program_mixdown(
    destination: Path,
    *,
    ffmpeg: str,
    rails: dict[str, Path],
    duration: float,
    target: float,
    peak: float,
) -> None:
    """Combine rails with dialogue-priority ducking and two-pass linear loudnorm."""
    order = [role for role in ("dialogue", "ambience", "foley", "score") if role in rails]
    if not order:
        raise MixError("program mixdown has no rails")
    destination.parent.mkdir(parents=True, exist_ok=True)
    premix = destination.with_name(f".{destination.stem}.premix.wav")
    pending = destination.with_name(f".{destination.stem}.part.wav")
    premix.unlink(missing_ok=True)
    pending.unlink(missing_ok=True)
    ducked_roles = [role for role in ("ambience", "score") if role in rails and "dialogue" in rails]
    filters: list[str] = []
    mix_labels: list[str] = []
    index_of = {role: index for index, role in enumerate(order)}
    if "dialogue" in rails:
        keys = "".join(f"[key{role}]" for role in ducked_roles)
        filters.append(
            f"[{index_of['dialogue']}:a]asplit={1 + len(ducked_roles)}[dialoguemain]{keys}"
        )
        mix_labels.append("[dialoguemain]")
    for role in order:
        if role == "dialogue":
            continue
        source = f"[{index_of[role]}:a]"
        if role in ducked_roles:
            filters.append(
                f"{source}[key{role}]sidechaincompress="
                f"threshold=0.02:ratio=6:attack=25:release=350[{role}duck]"
            )
            mix_labels.append(f"[{role}duck]")
        else:
            mix_labels.append(source)
    filters.append(
        "".join(mix_labels)
        + (
            f"amix=inputs={len(mix_labels)}:normalize=0:dropout_transition=0,"
            if len(mix_labels) > 1
            else ""
        )
        + f"apad,atrim=0:{duration:.6f}[out]"
    )
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for role in order:
        command.extend(["-i", str(rails[role])])
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
            str(premix),
        ]
    )
    try:
        _run(command, label="program mixdown")
        if not premix.is_file() or premix.stat().st_size <= 44:
            raise MixError("program mixdown returned no audio bytes")
        measured = _loudness_full_report(premix, ffmpeg=ffmpeg, target=target, peak=peak)
        try:
            two_pass = (
                f"loudnorm=I={target:g}:TP={peak:g}:LRA=7:"
                f"measured_I={float(measured['input_i']):g}:"
                f"measured_LRA={float(measured['input_lra']):g}:"
                f"measured_TP={float(measured['input_tp']):g}:"
                f"measured_thresh={float(measured['input_thresh']):g}:"
                f"offset={float(measured['target_offset']):g}:linear=true"
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MixError("program loudness measurement is incomplete") from exc
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(premix),
                "-af",
                two_pass,
                "-t",
                f"{duration:.6f}",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s24le",
                str(pending),
            ],
            label="program loudness normalization",
        )
    finally:
        premix.unlink(missing_ok=True)
    if not pending.is_file() or pending.stat().st_size <= 44:
        raise MixError("program loudness normalization returned no audio bytes")
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


_STEM_ROLES = {
    "room_tone_stem",
    "dialogue_stem",
    "ambience_stem",
    "foley_stem",
    "score_stem",
    "program_mix_stem",
}


def _mix_revision(project: Project) -> int:
    revisions = [
        int(asset.metadata.get("mix_revision") or 0)
        for asset in project.assets
        if asset.role in _STEM_ROLES and isinstance(asset.metadata, dict)
    ]
    return 1 + max(revisions, default=0)


def _stem_asset(
    project: Project,
    *,
    rel: str,
    role: str,
    revision: int,
    metadata: dict,
) -> Asset:
    path = abs_path(rel)
    digest = _sha256(path)
    asset = Asset(
        id=f"ast-{digest[:12]}-{role.replace('_', '-')}-v{revision}",
        kind=AssetKind.AUDIO,
        filename=path.name,
        mime="audio/wav",
        bytes=path.stat().st_size,
        path=rel,
        sha256=digest,
        role=role,
        metadata={"mix_revision": revision, **metadata},
    )
    project.assets.append(asset)
    return asset


def bounce_mix(project: Project) -> None:
    """Assemble verified content audio; generated noise is never a soundtrack.

    Content is verified per-clip dialogue and/or licensed library cues from the
    digest-protected soundtrack plan. Rails mix with dialogue-priority ducking
    and land on the Bible loudness target through two-pass linear loudnorm.
    """
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
    ensure_generated_cues(project)
    planned_cues = load_soundtrack_plan(project)
    if planned_cues is None and soundtrack_plan_exists(project):
        # A planned soundtrack that fails verification is a hold, never a
        # silently stripped master ("visibly, never silently").
        raise MixError(
            "The soundtrack plan failed verification: a planned library cue is "
            "missing or its bytes changed. Restore the library or re-plan before Mix."
        )
    planned_cues = planned_cues or []
    duration = _duration_seconds(master, ffprobe)
    if not script and not planned_cues:
        # Mute clips are allowed. An image-only Idea authors no dialogue and
        # this site planned no cues, so the program delivers picture-only:
        # digital silence keeps an audio stream in the master, nothing is
        # manufactured and presented as a soundtrack, and the policy is
        # visible on the receipt and in the log.
        _bounce_mute_program(
            project,
            master=master,
            ffmpeg=ffmpeg,
            duration=duration,
            revision=revision,
        )
        return
    planned_cues, alignments = align_score_cues(project, planned_cues)
    project.mix.score_alignments = alignments
    stems_root = f"projects/{project.id}/program/{project.timeline.id}/stems"
    rails: dict[str, Path] = {}
    rail_assets: list[Asset] = []
    voice_sources: list[VoiceRailRow] = []
    if script:
        voice_sources = _locked_voice_sources(project)
        dialogue_rel = f"{stems_root}/dialogue-v{revision}.wav"
        dialogue_stem = abs_path(dialogue_rel)
        filters, labels = _voice_rail_filters(voice_sources)
        _write_rail(
            dialogue_stem,
            ffmpeg=ffmpeg,
            inputs=[row.audio for row in voice_sources],
            filters=filters,
            labels=labels,
            duration=duration,
            label="locked voice stem assembly",
        )
        rails["dialogue"] = dialogue_stem
        rail_assets.append(
            _stem_asset(
                project,
                rel=dialogue_rel,
                role="dialogue_stem",
                revision=revision,
                metadata={
                    "timeline_id": project.timeline.id,
                    "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
                    "voice_package_count": len(voice_sources),
                    "voice_pipeline": "wan22_native_speech_per_clip",
                    "voice_package_sha256": hashlib.sha256(
                        "|".join(
                            f"{row.package.request.content_sha256}:{row.package.video_sha256}"
                            for row in voice_sources
                        ).encode("utf-8")
                    ).hexdigest(),
                },
            )
        )
    for role in ("ambience", "foley", "score"):
        cues = [cue for cue in planned_cues if cue.role == role]
        if not cues:
            continue
        rail_rel = f"{stems_root}/{role}-v{revision}.wav"
        rail_stem = abs_path(rail_rel)
        filters, labels = _cue_rail_filters(cues)
        _write_rail(
            rail_stem,
            ffmpeg=ffmpeg,
            inputs=[Path(cue.path) for cue in cues],
            filters=filters,
            labels=labels,
            duration=duration,
            label=f"{role} stem assembly",
        )
        rails[role] = rail_stem
        rail_assets.append(
            _stem_asset(
                project,
                rel=rail_rel,
                role=f"{role}_stem",
                revision=revision,
                metadata={
                    "timeline_id": project.timeline.id,
                    "cue_count": len(cues),
                    "cue_ids": ",".join(cue.cue_id for cue in cues),
                },
            )
        )
    program_rel = f"{stems_root}/program-mix-v{revision}.wav"
    program_stem = abs_path(program_rel)
    _write_program_mixdown(
        program_stem,
        ffmpeg=ffmpeg,
        rails=rails,
        duration=duration,
        target=target,
        peak=peak,
    )
    measured, true_peak = _measure_stem(program_stem, ffmpeg=ffmpeg, target=target, peak=peak)
    if abs(measured - target) > 1.0:
        raise MixError(f"measured loudness {measured:g} LUFS missed the {target:g} LUFS target")
    if true_peak > peak:
        raise MixError(f"measured true peak {true_peak:g} dBTP exceeded the {peak:g} dBTP ceiling")
    _mux_program(master, program_stem, ffmpeg=ffmpeg)
    if _visual_qc_required():
        try:
            verify_finished_media(master, label="mixed 1080 program master")
        except FinishError as exc:
            raise MixError(str(exc)) from exc
    if not program_master_has_audio(project):
        raise MixError("the mixed 1080 program master has no verified audio stream")

    rail_assets.append(
        _stem_asset(
            project,
            rel=program_rel,
            role="program_mix_stem",
            revision=revision,
            metadata={
                "timeline_id": project.timeline.id,
                "measured_lufs": measured,
                "true_peak_dbtp": true_peak,
                "rails": ",".join(sorted(rails)),
                "planned_cue_count": len(planned_cues),
            },
        )
    )
    project.mix.stem_asset_ids = [asset.id for asset in rail_assets]
    project.mix.measured_lufs = measured
    project.mix.true_peak_dbtp = true_peak
    project.mix.picture_lock_timeline_id = project.timeline.id
    project.timeline.program_sha256 = _sha256(master)
    # The audio-bearing master is a new Gallery version. A removal of the
    # earlier picture-only file must not suppress the completed program.
    project.timeline.gallery_removed_at = None
    gallery_program_preview_path(project, project.id)
    persist_project(project)


def _picture_only_policy(project: Project) -> str:
    brief = project.brief
    idea = " ".join(str(getattr(brief, "idea", "") or "").split()) if brief else ""
    attachments = list(getattr(brief, "attachment_ids", []) or []) if brief else []
    if not idea and attachments:
        return "mute: image-only Idea (no text), no dialogue authored, no soundtrack cues planned"
    return "mute: no dialogue authored and no soundtrack cues planned"


def _write_silent_stem(destination: Path, *, ffmpeg: str, duration: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
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
            "anullsrc=r=48000:cl=stereo",
            "-t",
            f"{max(0.1, duration):.3f}",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ],
        label="mute program stem",
    )


def _bounce_mute_program(
    project: Project,
    *,
    master: Path,
    ffmpeg: str,
    duration: float,
    revision: int,
) -> None:
    """Deliver a picture-only program with a mute audio stream and a visible policy."""
    assert project.mix is not None and project.timeline is not None
    policy = _picture_only_policy(project)
    stems_root = f"projects/{project.id}/program/{project.timeline.id}/stems"
    program_rel = f"{stems_root}/program-mix-v{revision}.wav"
    program_stem = abs_path(program_rel)
    _write_silent_stem(program_stem, ffmpeg=ffmpeg, duration=duration)
    _mux_program(master, program_stem, ffmpeg=ffmpeg)
    if _visual_qc_required():
        try:
            verify_finished_media(master, label="mute 1080 program master")
        except FinishError as exc:
            raise MixError(str(exc)) from exc
    if not program_master_has_audio(project):
        raise MixError("the mute 1080 program master has no audio stream")
    asset = _stem_asset(
        project,
        rel=program_rel,
        role="program_mix_stem",
        revision=revision,
        metadata={
            "timeline_id": project.timeline.id,
            "soundtrack": "mute",
            "policy": policy,
            "rails": "",
            "planned_cue_count": 0,
        },
    )
    project.mix.stem_asset_ids = [asset.id]
    project.mix.measured_lufs = None
    project.mix.true_peak_dbtp = None
    project.mix.picture_only = True
    project.mix.policy = policy
    project.mix.picture_lock_timeline_id = project.timeline.id
    project.timeline.program_sha256 = _sha256(master)
    project.timeline.gallery_removed_at = None
    gallery_program_preview_path(project, project.id)
    persist_project(project)
    record_activity("mix", f"Mute program delivered · {policy}", level="warn")


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
    if mix.picture_only:
        # Mute by policy: there is no loudness to measure. The stream itself
        # and the picture are still verified below.
        pass
    elif mix.measured_lufs is None:
        blockers.append("Measured loudness evidence is missing.")
    else:
        target = -24.0 if project.bible.loudness.value == "broadcast" else -16.0
        if abs(mix.measured_lufs - target) > 1.0:
            blockers.append(
                f"Measured loudness {mix.measured_lufs:g} LUFS is outside the {target:g} LUFS target."
            )
    if mix.picture_only:
        pass
    elif mix.true_peak_dbtp is None:
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
