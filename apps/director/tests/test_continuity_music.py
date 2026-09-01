"""Regressions from production s-1b80548c4d: broken bridge chains, scene
resets at re-anchor clips, mixed rasters, and the per-clip ringtone score.

The doctrine under test:
- a scripted music cue is ONE piece carried verbatim by every clip in its
  scene, never a per-clip foley event sitting beside a "no music" directive;
- a re-anchor clip keeps the bridge frame (first) plus the plate — identity
  sheets alone made the model invent a brand-new room;
- every Grok video op pins the program aspect so the raster never wanders;
- the bridge frame is taken at the assembly CUT point, not the surplus tail.
"""

from pathlib import Path

import pytest

from diony_contracts.production import Scene, Script, ScriptLine
from diony_director import compiler, packagebuilder, screenwriter, splitter


def _draft():
    return screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )


def _script_with_music():
    lines = [
        ScriptLine(line_id="ln-1", kind="heading", text="INT. DINER - NIGHT", scene_id="sc-1"),
        ScriptLine(line_id="ln-2", kind="action", text="Mara waits by the counter.", scene_id="sc-1"),
        ScriptLine(
            line_id="ln-3",
            kind="sound",
            text="A catchy funky instrumental groove fades up through the diner speakers.",
            scene_id="sc-1",
        ),
        ScriptLine(line_id="ln-4", kind="action", text="Mara counts the till.", scene_id="sc-1"),
        ScriptLine(
            line_id="ln-5", kind="dialogue", text="We leave tonight.", speaker="MARA", scene_id="sc-1"
        ),
    ]
    return Script(
        id="scr-music",
        runtime_target_s=30,
        scenes=[
            Scene(
                scene_id="sc-1",
                heading="INT. DINER - NIGHT",
                setting_ref="set-primary",
                cast=["chr-lead"],
                line_ids=[l.line_id for l in lines],
            )
        ],
        lines=lines,
    )


def test_a_music_cue_scores_the_whole_scene_not_one_clip() -> None:
    draft = _draft()
    clips = splitter.split_script(_script_with_music(), draft.characters, draft.settings)
    assert clips
    for clip in clips:
        assert clip.audio_packet.score == "in"
        assert "funky instrumental groove" in clip.audio_packet.score_desc
        # the cue is the scene's score, never a one-clip foley event
        assert not any(
            "groove" in str(e.get("event", "")) for e in clip.audio_packet.foley
        )


def test_scene_openers_breathe_with_ambience_not_forced_silence() -> None:
    draft = _draft()
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    by_scene: dict[str, list] = {}
    for clip in clips:
        by_scene.setdefault(clip.scene_id, []).append(clip)
    for scene_clips in by_scene.values():
        opener = min(scene_clips, key=lambda c: c.order)
        assert opener.clip_class != "silent-picture"


def test_scored_prompt_never_contradicts_itself_with_no_music() -> None:
    draft = _draft()
    package = packagebuilder.build_package(
        "s-music", "a getaway", draft, provider_policy="grok"
    )
    scene = package.script.scenes[0]
    clips = splitter.split_script(_script_with_music(), draft.characters, draft.settings)
    # graft the music-scored clips onto the real package's scene/setting ids
    for clip in clips:
        clip.scene_id = scene.scene_id
        clip.continuity.setting_plate["setting_id"] = scene.setting_ref
    package.clips = clips
    for clip in clips:
        prompt = compiler.compile_clip_prompt(clip, package).prompt
        assert "no music" not in prompt
        assert "score:" in prompt
        assert "funky instrumental groove" in prompt


def test_legacy_music_foley_reads_as_score_not_beside_no_music() -> None:
    package = packagebuilder.build_package(
        "s-legacy", "a getaway", _draft(), provider_policy="grok"
    )
    clip = next(c for c in package.clips if c.clip_class not in {"sync-dialogue", "vo-over"})
    clip.audio_packet.score = "none"
    clip.audio_packet.score_desc = ""
    clip.audio_packet.foley.append(
        {"event": "A catchy funky instrumental groove fades up through the speakers."}
    )
    prompt = compiler.compile_clip_prompt(clip, package).prompt
    assert "no music" not in prompt
    assert "funky instrumental groove" in prompt


def test_grok_reference_packs_always_carry_the_setting_plate() -> None:
    package = packagebuilder.build_package(
        "s-plate", "a getaway", _draft(), provider_policy="grok"
    )
    clip = next(
        c for c in package.clips
        if c.continuity.lock_recipe == "ref" and c.continuity.character_packs
    )
    setting_id = str(clip.continuity.setting_plate["setting_id"])
    setting = package.setting(setting_id)
    setting.plate_asset_id = "/vault/plates/diner.jpg"
    for char_id in [str(p["char_id"]) for p in clip.continuity.character_packs]:
        try:
            char = package.character(char_id)
        except KeyError:
            continue
        char.grok_ref_pack = ["/vault/sheets/a.jpg", "/vault/sheets/b.jpg", "/vault/sheets/c.jpg"]
    refs = compiler.provider_artifact_refs(clip, package)
    references = refs.get("references") or []
    assert "/vault/plates/diner.jpg" in references, (
        "identity sheets alone let the model invent a new room — the plate must ride"
    )
    assert len(references) <= 3


def _gen_op(op_id: str, sid: str, clip_id: str, **overrides):
    from diony_contracts.director import (
        CloudGenerationMode,
        CloudProvider,
        CostEstimate,
        CostEstimateLine,
        DirectorOp,
        DirectorOpType,
    )

    estimate = CostEstimate(
        id=f"est-{op_id}",
        workspace_id="local-workspace",
        lines=[
            CostEstimateLine(
                label="video seconds · 720p",
                provider_id="grok",
                model="grok-imagine-video-1.5",
                unit="second:720p",
                unit_rate_usd_micros=140_000,
                quantity_microunits=4_000_000,
                usd_micros=560_000,
            )
        ],
    )
    payload = {
        "id": op_id,
        "session_id": sid,
        "op": DirectorOpType.GENERATE,
        "clip_id": clip_id,
        "provider": CloudProvider.GROK,
        "model": "grok-imagine-video-1.5",
        "mode": CloudGenerationMode.T2V,
        "prompt": "p",
        "duration_s": 4.0,
        "estimate": estimate,
    }
    payload.update(overrides)
    return DirectorOp(**payload)


def test_re_anchor_keeps_the_bridge_frame_first(monkeypatch, tmp_path) -> None:
    """Position-4 re-anchor must continue the place: bridge frame first, then
    the plate, then identity — never identity sheets alone."""
    from types import SimpleNamespace

    from diony_director import app as app_module, session

    package = packagebuilder.build_package(
        "s-anchor", "a getaway", _draft(), provider_policy="grok"
    )
    clip = next(
        c for c in package.clips
        if c.continuity.lock_recipe == "ref" and c.continuity.character_packs
        and c.provider is not None and c.provider.value == "grok"
    )
    setting = package.setting(str(clip.continuity.setting_plate["setting_id"]))
    setting.plate_asset_id = "/vault/plates/diner.jpg"

    op = _gen_op("op-anchor", "s-anchor", clip.clip_id)
    prev = _gen_op("op-prev", "s-anchor", "cl-prev", status="completed")
    frame_path = tmp_path / "frames" / "op-prev.jpg"

    monkeypatch.setattr(session, "clip_for_op", lambda _op: clip)
    monkeypatch.setattr(session, "package_for", lambda _sid: package)
    monkeypatch.setattr(
        session, "apply_uploads_to_refs", lambda _sid, _clip, _pkg, refs: refs
    )
    monkeypatch.setattr(session, "chain_position", lambda _sid, _oid: 4)
    monkeypatch.setattr(session, "previous_completed_clip_op", lambda _sid, _op: prev)
    monkeypatch.setattr(
        session,
        "runtime",
        lambda _oid: {"clip": SimpleNamespace(path=str(tmp_path / "prev.mp4"))},
    )
    monkeypatch.setattr(
        app_module, "_last_frame", lambda _p, _d, at_s=None, sid="": str(frame_path)
    )

    app_module._late_bind_locks(op)
    app_module._pin_aspect(op)  # dispatch order: locks first, then the aspect pin

    references = op.artifact_refs.get("references") or []
    assert references, "a re-anchor still carries references"
    assert references[0] == str(frame_path), "the bridge frame leads the pack"
    assert "/vault/plates/diner.jpg" in references, "the plate rides the re-anchor"
    assert "start_image" not in op.artifact_refs
    assert op.artifact_refs.get("aspect_ratio") == "16:9"


def test_chained_clip_bridges_from_the_frame_with_aspect_pinned(
    monkeypatch, tmp_path
) -> None:
    from types import SimpleNamespace

    from diony_director import app as app_module, session

    package = packagebuilder.build_package(
        "s-bridge", "a getaway", _draft(), provider_policy="grok"
    )
    clip = next(
        c for c in package.clips
        if c.continuity.lock_recipe == "ref"
        and c.provider is not None and c.provider.value == "grok"
    )

    op = _gen_op("op-b", "s-bridge", clip.clip_id)
    prev = _gen_op("op-a", "s-bridge", "cl-a", status="completed")
    frame_path = tmp_path / "frames" / "op-a.jpg"

    monkeypatch.setattr(session, "clip_for_op", lambda _op: clip)
    monkeypatch.setattr(session, "package_for", lambda _sid: package)
    monkeypatch.setattr(
        session, "apply_uploads_to_refs", lambda _sid, _clip, _pkg, refs: refs
    )
    monkeypatch.setattr(session, "chain_position", lambda _sid, _oid: 2)
    monkeypatch.setattr(session, "previous_completed_clip_op", lambda _sid, _op: prev)
    monkeypatch.setattr(
        session,
        "runtime",
        lambda _oid: {"clip": SimpleNamespace(path=str(tmp_path / "prev.mp4"))},
    )
    monkeypatch.setattr(
        app_module, "_last_frame", lambda _p, _d, at_s=None, sid="": str(frame_path)
    )

    app_module._late_bind_locks(op)
    app_module._pin_aspect(op)  # dispatch order: locks first, then the aspect pin

    assert op.artifact_refs.get("start_image") == str(frame_path)
    assert "references" not in op.artifact_refs
    assert op.artifact_refs.get("aspect_ratio") == "16:9"


HAVE_FFMPEG = True
try:  # mirror the production_flow guard
    from diony_producer.gallery_join import resolve_media_tool

    HAVE_FFMPEG = bool(resolve_media_tool("ffmpeg") and resolve_media_tool("ffprobe"))
except ImportError:
    HAVE_FFMPEG = False


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_bridge_frame_is_cut_point_conformed_to_program_raster(tmp_path) -> None:
    """A 2.35:1 clip with a trimmed tail bridges from the CUT frame at the
    program raster — not from the surplus tail at whatever shape the provider
    chose."""
    import subprocess

    from diony_director import app as app_module, assembly
    from diony_producer.gallery_join import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    src = tmp_path / "wide.mp4"
    # 4s clip whose hue flips at 2s: green first half, red second half
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=green:s=1456x624:d=2:r=24",
         "-f", "lavfi", "-i", "color=c=red:s=1456x624:d=2:r=24",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
         "-map", "[v]", str(src)],
        check=True, capture_output=True, timeout=120,
    )
    dest = tmp_path / "bridge.jpg"
    # cut point inside the green half — the bridge must be green, not tail-red
    frame = app_module._last_frame(src, dest, at_s=1.5)
    assert frame and dest.is_file()
    probe = subprocess.run(
        [resolve_media_tool("ffprobe"), "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(dest)],
        check=True, capture_output=True, timeout=60,
    ).stdout.decode().strip()
    assert probe == f"{assembly.WIDTH},{assembly.HEIGHT}"
    raw = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(dest),
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=60,
    ).stdout
    red, green = raw[0], raw[1]
    assert green > red, f"expected the green cut frame, got rgb {tuple(raw[:3])}"
