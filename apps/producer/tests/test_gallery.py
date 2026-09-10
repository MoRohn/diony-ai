"""The Finished Clips inventory lists what the Director printed, from its own records."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diony_contracts.cluster import cluster_root
from diony_producer.app import app

HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


def _tiny_mp4(path: Path, seconds: float = 0.4) -> None:
    if HAVE_FFMPEG:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=orange:s=64x36:r=12:d={seconds}",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    else:
        path.write_bytes(b"\x00" * 4096)


def _session(sid: str, *, clips: int, film: bool, idea: str = "a lighthouse keeper") -> Path:
    root = cluster_root() / "director" / sid
    root.mkdir(parents=True, exist_ok=True)
    ops = []
    for n in range(1, clips + 1):
        job = f"job-{sid[-4:]}{n:02d}"
        _tiny_mp4(root / f"{job}.mp4")
        ops.append(
            {
                "id": f"op-{n}",
                "op": "generate",
                "status": "completed",
                "job_id": job,
                "provider": "google-flow",
                "model": "omni-flash",
                "mode": "t2v",
                "prompt": f"clip {n}: {idea}",
                "rationale": f"clip {n}/{clips}",
                "duration_s": 10.0,
                "resolution": "720p",
                "estimate": {"total_usd_micros": 302_850},
                "created_at": f"2026-09-10T15:0{n}:00Z",
            }
        )
    # a failed op with no media never lists
    ops.append({"id": "op-x", "op": "generate", "status": "failed", "job_id": "job-nope"})
    if film:
        _tiny_mp4(root / "film.mp4")
        (root / "film.srt").write_text("1\n00:00:00,150 --> 00:00:01,000\nhello\n")
    (root / "state.json").write_text(
        json.dumps({"session_id": sid, "ops": ops, "package": {"idea": idea}, "uploads": []})
    )
    return root


def test_the_gallery_lists_every_clip_and_film_with_its_record() -> None:
    _session("s-aaaa1111", clips=2, film=True)
    _session("s-bbbb2222", clips=1, film=False, idea="a getaway driver")
    client = TestClient(app)
    payload = client.get("/api/gallery").json()
    assert payload["count"] == 4 and payload["films"] == 1 and payload["clips"] == 3
    kinds = {item["id"]: item["kind"] for item in payload["items"]}
    assert kinds["s-aaaa1111:film"] == "film"
    clip = next(item for item in payload["items"] if item["id"] == "s-aaaa1111:job-111101")
    assert clip["prompt"] == "clip 1: a lighthouse keeper"
    assert clip["plate"] == "google-flow · omni-flash"
    assert clip["duration_s"] == 10.0 and clip["resolution"] == "720p"
    assert clip["estimate_usd_micros"] == 302_850
    assert clip["file_url"].startswith("/api/gallery/items/s-aaaa1111%3Ajob-111101/file?v=")
    assert clip["download_url"].endswith("&v=" + clip["file_url"].rsplit("v=", 1)[1])
    assert "spent_usd_micros" in clip and "printed" in clip  # the session receipt rides every card
    assert all("job-nope" not in item["id"] for item in payload["items"])
    film = next(item for item in payload["items"] if item["kind"] == "film")
    assert film["deliverables"] == ["film.srt"]
    assert film["prompt"] == "a lighthouse keeper"


def test_media_details_and_delete_stay_inside_the_vault() -> None:
    _session("s-cccc3333", clips=1, film=False)
    client = TestClient(app)
    item_id = "s-cccc3333:job-333301"
    media = client.get(f"/api/gallery/items/{item_id}/file")
    assert media.status_code == 200 and media.headers["content-type"] == "video/mp4"
    assert "inline" in media.headers.get("content-disposition", "inline")
    download = client.get(f"/api/gallery/items/{item_id}/file?download=true")
    assert "attachment" in download.headers["content-disposition"]
    details = client.get(f"/api/gallery/items/{item_id}/details").json()
    assert details["prompt"] == "clip 1: a lighthouse keeper"
    assert details["model"] == "omni-flash" and details["provider"] == "google-flow"
    assert details["idea"] == "a lighthouse keeper"
    # nothing outside the vault, nothing unnamed
    assert client.get("/api/gallery/items/..%3Afilm/file").status_code == 404
    assert client.get("/api/gallery/items/s-cccc3333%3A..%2F..%2Fetc/file").status_code == 404
    assert client.get("/api/gallery/items/s-nope%3Ajob-1/details").status_code == 404
    removed = client.delete(f"/api/gallery/items/{item_id}").json()
    assert removed["removed"] == item_id
    assert not (cluster_root() / "director" / "s-cccc3333" / "job-333301.mp4").exists()
    assert (cluster_root() / "director" / "s-cccc3333" / "state.json").exists(), "the record stays"
    assert client.get("/api/gallery").json()["count"] == 0


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_thumbnails_are_first_frames_cached_beside_the_media() -> None:
    root = _session("s-dddd4444", clips=1, film=False)
    client = TestClient(app)
    res = client.get("/api/gallery/items/s-dddd4444%3Ajob-444401/thumbnail")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert res.content[:2] == b"\xff\xd8"
    cached = list((root / "thumbs").glob("job-444401-*.jpg"))
    assert len(cached) == 1


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_stitching_selected_clips_makes_a_new_film_session() -> None:
    _session("s-eeee5555", clips=2, film=False)
    client = TestClient(app)
    res = client.post(
        "/api/gallery/stitch",
        json={"items": ["s-eeee5555:job-555502", "s-eeee5555:job-555501"], "title": "two clips"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["session"].startswith("join-") and body["joined_from"] == [
        "s-eeee5555:job-555502",
        "s-eeee5555:job-555501",
    ]
    joined = cluster_root() / "director" / body["session"]
    assert (joined / "film.mp4").is_file() and not (joined / "inputs.txt").exists()
    payload = client.get("/api/gallery").json()
    film = next(item for item in payload["items"] if item["session"] == body["session"])
    assert film["kind"] == "film" and film["prompt"] == "two clips"
    assert (
        client.post("/api/gallery/stitch", json={"items": ["s-eeee5555:job-555501"]}).status_code
        == 422
    )


def test_ffmpeg_is_found_in_the_windows_homes_too(tmp_path, monkeypatch) -> None:
    import importlib

    from diony_producer import media_tools

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ChocolateyInstall", str(tmp_path / "choco"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    module = importlib.reload(media_tools)
    homes = {str(p) for p in module.MEDIA_TOOL_DIRS}
    assert str(tmp_path / "local" / "Microsoft" / "WinGet" / "Links") in homes
    assert str(tmp_path / "pf" / "ffmpeg" / "bin") in homes
    assert str(tmp_path / "choco" / "bin") in homes
    assert str(tmp_path / "home" / "scoop" / "shims") in homes
    assert "/opt/homebrew/bin" in homes  # the Mac homes stay
    importlib.reload(media_tools)


def test_the_missing_ffmpeg_hint_follows_the_platform_choice(monkeypatch) -> None:
    from diony_contracts.cluster import write_platform_choice
    from diony_producer import gallery

    monkeypatch.setattr(gallery, "resolve_media_tool", lambda name: "")
    write_platform_choice("windows")
    with pytest.raises(RuntimeError, match="winget install Gyan.FFmpeg"):
        gallery.stitch_items(["s-a:job-1", "s-a:job-2"])
    write_platform_choice("mac")
    with pytest.raises(RuntimeError, match="brew install ffmpeg"):
        gallery.stitch_items(["s-a:job-1", "s-a:job-2"])
