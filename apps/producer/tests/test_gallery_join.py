from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import importlib
import time

from diony_contracts.enums import (
    ArtifactKind,
    AssetKind,
    CustomerKind,
    GalleryCompositionStatus,
    ProductionKind,
)
from diony_contracts.fixture import demo_project
from diony_contracts.models import Artifact, Asset, Brief
from diony_contracts.stitch import assemble_program
from diony_producer.app import app
from diony_producer.gallery_join import (
    JoinManager,
    JoinRequest,
    create_join_plan,
    preview_window,
    render_and_commit,
)
from diony_producer.gallery import ensure_browser_preview, ensure_first_frame, gallery_payload
from diony_producer.vault import abs_path, persist_project
import diony_producer.gallery_join as gallery_join


def finished_project(project_id: str, title: str) -> object:
    project = demo_project()
    project.id = project_id
    project.bible.title = title
    assemble_program(project)
    assert project.timeline
    project.timeline.id = f"tl-{project_id}"
    project.timeline.preview_path = (
        f"projects/{project_id}/program/tl-{project_id}/master_1080.mov"
    )
    source_hashes = []
    for event in project.timeline.events:
        take = project.take(event.take_id)
        shot = project.shot(event.shot_id)
        negative = next(art for art in take.artifacts if art.kind == ArtifactKind.NEGATIVE_720)
        negative.path = f"projects/{project_id}/takes/{take.id}/negative_720.mp4"
        negative.project_id = project_id
        negative.shot_id = shot.id
        negative.sha256 = f"negative-{shot.id}"
        negative.prompt_sha256 = hashlib.sha256(shot.prompt.encode()).hexdigest()
        negative.reference_asset_ids = list(shot.keyframe_asset_ids)
        reference_rows = []
        for asset_id in shot.keyframe_asset_ids:
            try:
                asset = project.asset(asset_id)
                reference_rows.append(f"{asset.id}:{asset.sha256}")
            except KeyError:
                reference_rows.append(f"{asset_id}:missing")
        negative.reference_sha256 = (
            hashlib.sha256("|".join(reference_rows).encode()).hexdigest()
            if reference_rows
            else ""
        )
        finish = next(
            (art for art in take.artifacts if art.kind == ArtifactKind.FINISH_1080),
            None,
        )
        if finish is None:
            finish = Artifact(
                id=f"art-{take.id}-1080",
                take_id=take.id,
                kind=ArtifactKind.FINISH_1080,
                path="pending",
            )
            take.artifacts.append(finish)
        finish.path = f"projects/{project_id}/takes/{take.id}/finish_1080.mov"
        finish.project_id = project_id
        finish.shot_id = shot.id
        finish.sha256 = f"finish-{shot.id}"
        finish.source_sha256 = negative.sha256
        finish.prompt_sha256 = negative.prompt_sha256
        finish.reference_asset_ids = list(negative.reference_asset_ids)
        finish.reference_sha256 = negative.reference_sha256
        finish.generation_task = "diony_sr_v1"
        source_hashes.append(finish.sha256)
    project.timeline.picture_sha256 = "picture-sha"
    project.timeline.source_artifact_sha256s = source_hashes
    path = abs_path(project.timeline.preview_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"x" * 256)
    project.timeline.program_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    persist_project(project)
    return project


def test_gallery_inventory_survives_when_a_blank_project_becomes_current() -> None:
    finished_project("project-finished", "Finished Program")
    blank = demo_project()
    blank.id = "studio-blank"
    blank.shots = []
    blank.takes = []
    blank.timeline = None
    persist_project(blank)

    payload = gallery_payload(blank)

    assert payload["project_id"] == "studio-blank"
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "program-project-finished"


def test_gallery_rejects_a_master_replaced_after_mix() -> None:
    project = finished_project("project-tampered", "Tampered")
    assert gallery_payload(project)["count"] == 1
    master = abs_path(project.timeline.preview_path)
    master.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"wrong-run" * 64)

    assert gallery_payload(project)["items"] == []


def test_gallery_rejects_source_receipts_not_matching_selected_finishes() -> None:
    project = finished_project("project-wrong-source", "Wrong source")
    assert project.timeline
    project.timeline.source_artifact_sha256s[0] = "another-project-finish"

    assert gallery_payload(project)["items"] == []


def test_gallery_rejects_a_flat_green_live_master(monkeypatch) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    project = finished_project("project-flat-green", "Flat green")
    master = abs_path(project.timeline.preview_path)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=1920x1080:r=24:d=1",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            str(master),
        ],
        check=True,
    )
    project.timeline.program_sha256 = hashlib.sha256(master.read_bytes()).hexdigest()

    assert gallery_payload(project)["items"] == []


def test_join_plan_resolves_gallery_clips_in_requested_order_with_attribution() -> None:
    current = finished_project("project-current", "Current")
    finished_project("project-second", "Second")
    request = JoinRequest.model_validate(
        {
            "clip_ids": ["program-project-second", "program-project-current"],
            "trims": {
                "program-project-second": {"in_frame": 4, "out_frame": 260},
                "program-project-current": {"in_frame": 8, "out_frame": 250},
            },
            "junctions": [
                {
                    "transition": "cross_dissolve",
                    "duration_frames": 12,
                }
            ],
        }
    )

    plan = create_join_plan(
        current,
        request,
        profile_name="Local maker",
        cost_center_ids=["local-production"],
    )

    assert [source.project_id for source in plan.sources] == ["project-second", "project-current"]
    assert plan.profile_name == "Local maker"
    assert plan.cost_center_ids == ["local-production"]
    assert plan.width == 1920 and plan.height == 1080
    assert plan.source_asset_ids == ["program-project-second", "program-project-current"]


def test_render_commit_mints_asset_and_manifest_without_overwriting_sources(monkeypatch) -> None:
    current = finished_project("project-current", "Current")
    finished_project("project-second", "Second")
    request = JoinRequest(
        clip_ids=["program-project-current", "program-project-second"],
        junctions=[{"transition": "cut", "duration_frames": 0}],
    )
    plan = create_join_plan(current, request, profile_name="Maker", cost_center_ids=["cc-1"])

    def fake_render(composition, output, proxy, *, preview_only=False, cancel_event=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"m" * 512)
        proxy.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"p" * 512)

    monkeypatch.setattr("diony_producer.gallery_join.render_composition", fake_render)
    source_paths = [source.path for source in plan.sources]

    asset = render_and_commit(current, plan)

    assert plan.status == GalleryCompositionStatus.COMPLETE
    assert asset.id == plan.asset_id
    assert asset.source_asset_ids == plan.source_asset_ids
    assert asset.metadata["profile_name"] == "Maker"
    assert all(abs_path(path).is_file() for path in source_paths)
    manifest = abs_path(plan.manifest_path)
    payload = json.loads(manifest.read_text())
    assert payload["status"] == "complete"
    assert payload["sources"][0]["path"] == source_paths[0]


def test_gallery_join_api_validates_selection_and_returns_frame_summary() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    response = client.post("/api/gallery/joins/plan", json={"clip_ids": ["only-one"]})
    assert response.status_code == 422
    assert "at least 2" in response.text


def test_gallery_join_api_reports_unknown_or_unfinished_clip() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    response = client.post(
        "/api/gallery/joins/plan",
        json={
            "clip_ids": ["program-missing-a", "program-missing-b"],
            "junctions": [{"transition": "cut", "duration_frames": 0}],
        },
    )
    assert response.status_code == 400
    assert "not in this Gallery" in response.json()["detail"]


def test_join_manifest_remains_exportable_project_lineage() -> None:
    current = finished_project("project-current", "Current")
    finished_project("project-second", "Second")
    plan = create_join_plan(
        current,
        JoinRequest(
            clip_ids=["program-project-current", "program-project-second"],
            junctions=[{"transition": "fade_through_black", "duration_frames": 12}],
        ),
        profile_name="Maker",
        cost_center_ids=["cc-1"],
    )
    assert plan.manifest_path.startswith(f"projects/{current.id}/compositions/")
    assert plan.output_path.endswith("master_1080.mov")
    assert plan.proxy_path.endswith("preview_1080.mp4")


def test_incompatible_sources_are_conformed_to_the_project_finish(monkeypatch) -> None:
    current = finished_project("project-current", "Current")
    finished_project("project-second", "Second")

    monkeypatch.setattr(
        gallery_join,
        "probe_media",
        lambda path, **kwargs: {
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "frames": 300,
            "codec": "vp9",
            "has_audio": True,
        },
    )
    plan = create_join_plan(
        current,
        JoinRequest(
            clip_ids=["program-project-current", "program-project-second"],
            junctions=[{"transition": "cut", "duration_frames": 0}],
        ),
        profile_name="Maker",
        cost_center_ids=["cc-1"],
    )

    assert all(source.normalized for source in plan.sources)
    assert all(source.fps == current.bible.fps for source in plan.sources)
    assert "scaled to 1920×1080" in plan.sources[0].normalization_note
    assert "conformed to" in plan.sources[0].normalization_note
    assert "decoded and normalized" in plan.sources[0].normalization_note


def test_gallery_card_uses_encoded_media_bounds_for_frame_controls(monkeypatch) -> None:
    current = finished_project("project-current", "Current")
    monkeypatch.setattr(
        "diony_producer.gallery.probe_media",
        lambda path, **kwargs: {
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "frames": 90,
            "codec": "h264",
            "has_audio": True,
        },
    )

    card = next(item for item in gallery_payload(current)["items"] if item["id"] == "program-project-current")

    assert card["duration_frames"] == round(90 * current.bible.fps / 30.0)
    assert card["fps"] == current.bible.fps
    assert card["source_fps"] == 30.0
    assert (card["width"], card["height"], card["codec"]) == (1280, 720, "h264")


def test_gallery_card_publishes_first_frame_download_and_remove_actions() -> None:
    current = finished_project("project-current", "Current")

    card = next(item for item in gallery_payload(current)["items"] if item["id"] == "program-project-current")
    version = current.timeline.program_sha256[:16]

    assert card["thumbnail_url"] == f"/api/gallery/items/program-project-current/thumbnail?v={version}"
    assert card["file_url"] == f"/api/projects/project-current/program/preview?v={version}"
    assert card["download_url"] == f"/api/projects/project-current/program/file?download=true&v={version}"
    assert card["remove_url"] == "/api/gallery/items/program-project-current"


def test_replaced_program_master_gets_new_gallery_media_urls() -> None:
    current = finished_project("project-current", "Current")
    first = gallery_payload(current)["items"][0]
    master = abs_path(current.timeline.preview_path)
    master.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"replacement" * 64)
    current.timeline.program_sha256 = hashlib.sha256(master.read_bytes()).hexdigest()

    replacement = gallery_payload(current)["items"][0]

    assert replacement["file_url"] != first["file_url"]
    assert replacement["thumbnail_url"] != first["thumbnail_url"]


def test_gallery_details_preserve_the_idea_prompt_and_downloadable_references(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")
    current = finished_project("project-source-slate", "Source Slate")
    reference_path = abs_path("assets/ast-source-slate/hero-frame.png")
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_bytes = b"\x89PNG\r\n\x1a\nsource-slate"
    reference_path.write_bytes(reference_bytes)
    reference = Asset(
        id="ast-source-slate",
        kind=AssetKind.IMAGE,
        filename="hero-frame.png",
        mime="image/png",
        bytes=len(reference_bytes),
        path="assets/ast-source-slate/hero-frame.png",
        sha256=hashlib.sha256(reference_bytes).hexdigest(),
    )
    current.brief = Brief(
        idea="A projectionist discovers that one lost frame remembers her.",
        customer_kind=CustomerKind.FEATURE,
        target_minutes=3,
        attachment_ids=[reference.id],
    )
    current.assets.append(reference)
    persist_project(current)
    monkeypatch.setattr(module, "_project", current)
    client = TestClient(app)

    gallery = client.get("/api/gallery").json()
    card = next(item for item in gallery["items"] if item["id"] == "program-project-source-slate")
    assert card["details_url"] == "/api/gallery/items/program-project-source-slate/details"

    response = client.get(card["details_url"])
    assert response.status_code == 200
    details = response.json()
    assert details["clip_id"] == card["id"]
    assert details["sources"] == [
        {
            "project_id": "project-source-slate",
            "title": "Source Slate",
            "idea_prompt": "A projectionist discovers that one lost frame remembers her.",
            "references": [
                {
                    "id": "ast-source-slate",
                    "filename": "hero-frame.png",
                    "kind": "image",
                    "mime": "image/png",
                    "bytes": len(reference_bytes),
                    "download_url": (
                        "/api/gallery/items/program-project-source-slate/"
                        "references/ast-source-slate/file"
                    ),
                }
            ],
        }
    ]

    downloaded = client.get(details["sources"][0]["references"][0]["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == reference_bytes
    assert "attachment" in downloaded.headers["content-disposition"]
    unrelated = client.get(
        "/api/gallery/items/program-project-source-slate/references/ast-not-in-lineage/file"
    )
    assert unrelated.status_code == 404


def test_joined_gallery_details_follow_each_source_project(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")
    current = finished_project("project-current", "Current")
    current.brief = Brief(
        idea="Current prompt with a midnight ferry.",
        customer_kind=CustomerKind.FEATURE,
        target_minutes=3,
    )
    second = finished_project("project-second", "Second")
    second.brief = Brief(
        idea="Second prompt with a daylight observatory.",
        customer_kind=CustomerKind.FEATURE,
        target_minutes=3,
    )
    persist_project(current)
    persist_project(second, set_current=False)
    plan = create_join_plan(
        current,
        JoinRequest(
            clip_ids=["program-project-second", "program-project-current"],
            junctions=[{"transition": "cut", "duration_frames": 0}],
        ),
        profile_name="Maker",
        cost_center_ids=["local-production"],
    )

    def fake_render(composition, output, proxy, *, preview_only=False, cancel_event=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"m" * 512)
        proxy.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"p" * 512)

    monkeypatch.setattr("diony_producer.gallery_join.render_composition", fake_render)
    joined = render_and_commit(current, plan)
    monkeypatch.setattr(module, "_project", current)
    client = TestClient(app)

    details = client.get(f"/api/gallery/items/{joined.id}/details")

    assert details.status_code == 200
    assert [source["project_id"] for source in details.json()["sources"]] == [
        "project-second",
        "project-current",
    ]
    assert [source["idea_prompt"] for source in details.json()["sources"]] == [
        "Second prompt with a daylight observatory.",
        "Current prompt with a midnight ferry.",
    ]


def test_gallery_identifies_a_finished_sample_without_weakening_master_lineage() -> None:
    current = finished_project("project-sample", "Tiny Sun")
    current.brief = Brief(
        idea="A glassblower shapes a tiny sun before dawn.",
        customer_kind=CustomerKind.FEATURE,
        production_kind=ProductionKind.SAMPLE,
        sample_seconds=10,
    )
    persist_project(current)

    card = next(item for item in gallery_payload(current)["items"] if item["id"] == "program-project-sample")

    assert card["production_kind"] == "sample"
    assert card["sample_seconds"] == 10
    assert card["plate"] == "1080 sample"
    assert card["finished"] is True


def test_gallery_card_carries_the_full_idea_prompt_for_the_inventory_description() -> None:
    current = finished_project("project-prompt-card", "The Night Press")
    current.brief = Brief(
        idea="A projectionist discovers that one lost frame remembers her.",
        customer_kind=CustomerKind.FEATURE,
        target_minutes=3,
    )

    card = next(
        item
        for item in gallery_payload(current)["items"]
        if item["id"] == "program-project-prompt-card"
    )

    assert card["prompt"] == "A projectionist discovers that one lost frame remembers her."


def test_first_frame_extraction_is_cached(tmp_path, monkeypatch) -> None:
    source = tmp_path / "finished.mov"
    source.write_bytes(b"video-source")
    thumbnail = tmp_path / "gallery" / "first-frame.jpg"
    commands: list[list[str]] = []

    monkeypatch.setattr("diony_producer.gallery.resolve_media_tool", lambda name: "/opt/diony/bin/ffmpeg")

    def fake_run(command, **kwargs):
        commands.append(command)
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\xff\xd8first-frame\xff\xd9")

    monkeypatch.setattr("diony_producer.gallery.subprocess.run", fake_run)

    assert ensure_first_frame(source, thumbnail) == thumbnail
    assert ensure_first_frame(source, thumbnail) == thumbnail
    assert thumbnail.read_bytes().startswith(b"\xff\xd8")
    assert len(commands) == 1
    assert commands[0][0] == "/opt/diony/bin/ffmpeg"
    assert "-frames:v" in commands[0]


def test_browser_preview_is_h264_aac_mp4_and_cached(tmp_path, monkeypatch) -> None:
    source = tmp_path / "broadcast-master.mov"
    source.write_bytes(b"video-source")
    preview = tmp_path / "gallery" / "browser-preview.mp4"
    commands: list[list[str]] = []

    monkeypatch.setattr("diony_producer.gallery.resolve_media_tool", lambda name: "/opt/diony/bin/ffmpeg")

    def fake_run(command, **kwargs):
        commands.append(command)
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"preview" * 16)

    monkeypatch.setattr("diony_producer.gallery.subprocess.run", fake_run)

    assert ensure_browser_preview(source, preview) == preview
    assert ensure_browser_preview(source, preview) == preview
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-c:a") + 1] == "aac"
    assert "+faststart" in command


def test_gallery_remove_soft_deletes_program_but_preserves_master(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")
    current = finished_project("project-current", "Current")
    monkeypatch.setattr(module, "_project", current)
    assert current.timeline
    master = abs_path(current.timeline.preview_path)
    client = TestClient(app)

    removed = client.delete("/api/gallery/items/program-project-current")

    assert removed.status_code == 200
    assert removed.json()["status"] == "removed"
    assert current.timeline.gallery_removed_at is not None
    assert master.is_file()
    assert client.get("/api/gallery").json()["items"] == []


def test_gallery_stream_is_inline_and_download_is_attachment(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")
    current = finished_project("project-current", "Current")
    monkeypatch.setattr(module, "_project", current)
    card = gallery_payload(current)["items"][0]
    preview = abs_path("gallery/previews/test-program.mp4")
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"preview" * 32)
    monkeypatch.setattr(module, "gallery_program_preview_path", lambda *args: preview)
    client = TestClient(app)

    streamed = client.get(card["file_url"])
    downloaded = client.get(card["download_url"])

    assert streamed.status_code == 200
    assert "content-disposition" not in streamed.headers
    assert downloaded.status_code == 200
    assert "attachment" in downloaded.headers["content-disposition"]


def test_media_tools_resolve_without_a_finder_path(tmp_path, monkeypatch) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"#!/bin/sh\n")
    ffmpeg.chmod(0o755)
    monkeypatch.delenv("DIONY_FFMPEG", raising=False)
    monkeypatch.setattr(gallery_join.shutil, "which", lambda value: None)
    monkeypatch.setattr(gallery_join, "MEDIA_TOOL_DIRS", (tmp_path,))

    assert gallery_join.resolve_media_tool("ffmpeg") == str(ffmpeg)


def test_probe_media_uses_ffmpeg_when_ffprobe_is_not_installed(tmp_path, monkeypatch) -> None:
    source = tmp_path / "wan-negative.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(
        gallery_join,
        "resolve_media_tool",
        lambda name: "" if name == "ffprobe" else "/usr/bin/ffmpeg",
    )

    class Result:
        returncode = 0
        stdout = ""
        stderr = """
Duration: 00:00:05.06, start: 0.000000, bitrate: 5892 kb/s
Stream #0:0: Video: h264 (High), yuv420p(progressive), 1280x720, 16 fps, 16 tbr
"""

    monkeypatch.setattr(gallery_join.subprocess, "run", lambda *args, **kwargs: Result())

    media = gallery_join.probe_media(
        source,
        fallback_fps=23.976,
        fallback_frames=120,
        fallback_width=0,
        fallback_height=0,
    )

    assert media["width"] == 1280
    assert media["height"] == 720
    assert media["fps"] == 16.0
    assert media["frames"] == 81
    assert media["codec"] == "h264"


def test_long_clips_preview_only_a_bounded_window_around_requested_junction() -> None:
    current = finished_project("project-current", "Current")
    finished_project("project-second", "Second")
    finished_project("project-third", "Third")
    plan = create_join_plan(
        current,
        JoinRequest(
            clip_ids=["program-project-current", "program-project-second", "program-project-third"],
            junctions=[
                {"transition": "cut", "duration_frames": 0},
                {"transition": "cross_dissolve", "duration_frames": 12},
            ],
        ),
        profile_name="Maker",
        cost_center_ids=["cc-1"],
    )

    preview = preview_window(plan, 1)

    assert [source.clip_id for source in preview.sources] == ["program-project-second", "program-project-third"]
    assert all(source.retained_frames <= round(plan.fps * 2) for source in preview.sources)
    assert preview.junctions[0].transition.value == "cross_dissolve"


def test_cancel_mid_process_preserves_sources_and_records_cancelled_manifest(monkeypatch) -> None:
    current = finished_project("project-current", "Current")
    finished_project("project-second", "Second")
    plan = create_join_plan(
        current,
        JoinRequest(
            clip_ids=["program-project-current", "program-project-second"],
            junctions=[{"transition": "cut", "duration_frames": 0}],
        ),
        profile_name="Maker",
        cost_center_ids=["cc-1"],
    )
    entered = __import__("threading").Event()

    def blocking_render(composition, output, proxy, *, preview_only=False, cancel_event=None):
        entered.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        raise InterruptedError("cancelled")

    monkeypatch.setattr("diony_producer.gallery_join.render_composition", blocking_render)
    manager = JoinManager()
    manager.start(current, plan)
    assert entered.wait(1)
    manager.cancel(plan.id)
    status = manager.wait(plan.id)

    assert status.status == GalleryCompositionStatus.CANCELLED
    assert current.assets == []
    assert all(abs_path(source.path).is_file() for source in plan.sources)
    assert json.loads(abs_path(plan.manifest_path).read_text())["status"] == "cancelled"


def test_join_api_previews_creates_polls_and_undoes_new_version(monkeypatch) -> None:
    module = importlib.import_module("diony_producer.app")
    current = finished_project("project-current", "Current")
    finished_project("project-second", "Second")
    monkeypatch.setattr(module, "_project", current)
    client = TestClient(app)
    payload = {
        "clip_ids": ["program-project-current", "program-project-second"],
        "junctions": [{"transition": "cross_dissolve", "duration_frames": 12}],
    }

    planned = client.post("/api/gallery/joins/plan", json=payload)
    assert planned.status_code == 200
    assert planned.json()["summary"]["source_count"] == 2
    preview = client.post("/api/gallery/joins/preview", json=payload)
    assert preview.status_code == 200
    assert client.get(preview.json()["preview_url"]).status_code == 200

    created = client.post("/api/gallery/joins", json=payload)
    assert created.status_code == 202
    composition_id = created.json()["id"]
    status = created.json()
    deadline = time.monotonic() + 3
    while status["status"] in {"queued", "processing"} and time.monotonic() < deadline:
        status = client.get(f"/api/gallery/joins/{composition_id}").json()
        time.sleep(0.02)
    assert status["status"] == "complete"
    gallery = client.get("/api/gallery").json()
    joined = next(item for item in gallery["items"] if item.get("composition_id") == composition_id)
    assert joined["finished"] is True
    assert client.get(joined["file_url"]).status_code == 200

    reedit = client.post(
        "/api/gallery/joins/plan",
        json={**payload, "parent_composition_id": composition_id},
    )
    assert reedit.status_code == 200
    assert reedit.json()["parent_composition_id"] == composition_id
    assert reedit.json()["version"] == 2

    undone = client.post(f"/api/gallery/joins/{composition_id}/undo")
    assert undone.status_code == 200
    assert undone.json()["status"] == "superseded"
    assert all(item.get("composition_id") != composition_id for item in client.get("/api/gallery").json()["items"])
