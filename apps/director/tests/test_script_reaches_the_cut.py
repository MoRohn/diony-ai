"""s-16d2e6e5e0 (2026-09-03): a 12-clip chain's master derivation died on a
brain reply cut off at 8192 tokens, the chain was released as runnable with
the raw-idea prompt (the pasted template's first 400 characters — the script
never reached Grok), every link's narration was empty, and the cut rendered
with no lines and Grok's invented audio. Four guards, one per layer."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from diony_director import master_prompt, screenwriter, session
from master_brain_fakes import master_brain_body

SCRIPT = (
    "Scene 1 — Quiet Success\n"
    "Visual: a founder at a desk at dawn.\n"
    "Narration: \"Nobody remembers the first sale.\"\n"
    "Scene 2 — The Turn\n"
    "Visual: the same desk, buried in orders.\n"
    "Narration: \"But everyone remembers the day the phones would not stop.\"\n"
)


def _sid() -> str:
    return f"s-test-{uuid.uuid4().hex[:8]}"


class _Resp:
    status_code = 200

    def __init__(self, content: str, finish_reason: str = "stop", completion_tokens: int = 300):
        self._content = content
        self._finish = finish_reason
        self._tokens = completion_tokens

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": self._content}, "finish_reason": self._finish}],
            "usage": {"prompt_tokens": 900, "completion_tokens": self._tokens},
        }


def _wait_not_deriving(op, tries: int = 200) -> None:
    for _ in range(tries):
        if op.status != "deriving":
            return
        time.sleep(0.05)


# --- 1. a truncated brain reply is named, and never retried blind ------------


def test_a_truncated_brain_reply_is_a_named_failure_not_a_json_riddle(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    calls: list[dict] = []
    full = json.dumps(master_brain_body(clips=[{"prompt": "a"}, {"prompt": "b"}]))

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        # the provider stopped mid-object: what json.loads sees ends at the
        # last '}' and fails with "Expecting ',' delimiter"
        return _Resp(full[: len(full) // 2], finish_reason="length", completion_tokens=8192)

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    with pytest.raises(master_prompt.MasterUnavailable) as err:
        master_prompt.derive("a keeper waits", target_duration_s=30, clip_plan=[15, 15])
    text = str(err.value)
    assert "truncated" in text and "8192" in text, text
    assert "Expecting" not in text, "the JSON riddle is not the reason"
    assert len(calls) == 1, "a reply cut off by the token cap is not retried blind"
    assert calls[0]["max_tokens"] >= 32768, "the 12-clip split needs the screenwriter's headroom"


# --- 2. a chain whose master never derived cannot roll; it can retry ---------


def _propose_chain_with_failing_brain(monkeypatch, text: str = "a city splits in two"):
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    brain = {"healthy": False}
    gate = threading.Event()
    gate.set()

    def fake_post(url, **kwargs):
        gate.wait(timeout=10)
        if not brain["healthy"]:
            raise RuntimeError("brain melted")
        count = 3
        body = master_brain_body(
            clips=[
                {"prompt": f"clip {i} prose", "duration_s": 15, "narration": f"line {i}"}
                for i in range(count)
            ]
        )
        return _Resp(json.dumps(body))

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    sid = session.create_session()
    proposal = session.propose(sid, text, provider="grok", duration_s=45)
    assert len(proposal.ops) == 3
    for op in proposal.ops:
        _wait_not_deriving(op)
    return sid, proposal.ops, brain


def test_a_chain_whose_master_failed_is_held_not_rolled(monkeypatch) -> None:
    from diony_director import app as app_module

    sid, ops, brain = _propose_chain_with_failing_brain(monkeypatch)
    assert all(op.status == "proposed" for op in ops)
    assert all(op.prompt_version == "v0-provisional" for op in ops)
    assert "master derivation failed" in ops[0].rationale

    client = TestClient(app_module.app)
    run = client.post(f"/api/director/ops/{ops[0].id}/run")
    assert run.status_code == 409, run.text
    assert "never derived" in run.json()["detail"]
    batch = client.post(
        f"/api/director/sessions/{sid}/run_batch",
        json={"op_ids": [op.id for op in ops]},
    )
    assert batch.status_code == 409, batch.text
    assert "never derived" in batch.json()["detail"]
    assert all(op.status == "proposed" for op in ops), "nothing was claimed"

    # the derivation retries in place — no re-paste, no new op ids
    brain["healthy"] = True
    res = client.post(f"/api/director/ops/{ops[1].id}/derive")
    assert res.status_code == 200, res.text
    assert sorted(res.json()["op_ids"]) == sorted(op.id for op in ops)
    for op in ops:
        _wait_not_deriving(op)
    assert [op.prompt_version for op in ops] == ["v1"] * 3
    assert [op.narration for op in ops] == ["line 0", "line 1", "line 2"]
    assert "clip 2 prose" in ops[2].prompt
    assert "Master prompt derived" in ops[0].rationale
    assert session.master_for_op(ops[0].id) is not None
    assert client.post(f"/api/director/ops/{ops[0].id}/derive").status_code == 409, (
        "a derived chain has nothing to retry"
    )


def test_the_derivation_inputs_survive_a_restart(monkeypatch) -> None:
    sid, ops, brain = _propose_chain_with_failing_brain(monkeypatch)
    doc = json.loads(session._state_path(sid).read_text())
    assert doc["derivations"], "the idea is persisted beside the ops it priced"
    session.reset()
    session.rehydrate_from_vault()
    brain["healthy"] = True
    restored = session.retry_derivation(ops[1].id)
    for op in restored:
        _wait_not_deriving(op)
    assert [op.prompt_version for op in restored] == ["v1"] * 3


# --- 3. the authored lines ride the provisional ops ---------------------------


def test_authored_narration_lands_on_the_links_before_the_master_does(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    gate = threading.Event()

    def fake_post(url, **kwargs):
        gate.wait(timeout=10)
        raise RuntimeError("brain melted")

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    sid = session.create_session()
    proposal = session.propose(sid, SCRIPT, provider="grok", duration_s=30)
    ops = proposal.ops
    assert len(ops) == 2
    assert ops[0].narration == "Nobody remembers the first sale."
    assert ops[1].narration == "But everyone remembers the day the phones would not stop."
    gate.set()
    for op in ops:
        _wait_not_deriving(op)
    # the failed derivation never wiped the words
    assert ops[0].narration == "Nobody remembers the first sale."
    assert ops[1].narration.startswith("But everyone remembers")


def test_more_authored_scenes_than_links_spread_in_order() -> None:
    lines = [f"line {i}" for i in range(5)]
    scenes = [{"narration": [line]} for line in lines]
    plan = session.authored_narration_plan({"narration": lines, "scenes": scenes}, 2)
    assert plan == ["line 0 line 1 line 2", "line 3 line 4"]
    assert session.authored_narration_plan({"narration": [], "scenes": []}, 3) == ["", "", ""]


# --- 4. a scriptless cut never renders silently ------------------------------


def _print(op, tmp_path, real: bool = False) -> None:
    from diony_director.providers import base

    op.status = "completed"
    clip = tmp_path / f"{op.id}.mp4"
    if real:
        subprocess.run(
            [
                shutil.which("ffmpeg"), "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=110:duration=2",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", str(clip),
            ],
            check=True,
            capture_output=True,
        )
    else:
        clip.write_bytes(b"clip" * 64)
    session.runtime(op.id)["clip"] = base.FetchedClip(path=clip, sha256="x", bytes=clip.stat().st_size)


def test_a_cut_with_a_cast_narrator_but_no_lines_refuses_before_ffmpeg(tmp_path) -> None:
    from diony_director import app as app_module

    sid = _sid()
    session.set_voice(sid, "leo")
    proposal = session.propose(sid, "a city splits in two", provider="grok", duration_s=30)
    for op in proposal.ops:
        op.narration = ""
        _print(op, tmp_path)
    client = TestClient(app_module.app)
    res = client.post(f"/api/director/sessions/{sid}/assemble", json={})
    assert res.status_code == 409, res.text
    assert "no clip carries a line" in res.json()["detail"]
    assert "allow_silent" in res.json()["detail"]


def test_an_authored_script_that_reached_no_clip_refuses_the_cut(tmp_path, monkeypatch) -> None:
    from diony_director import app as app_module

    sid = _sid()
    monkeypatch.setattr(session.master_mod, "derive", _wipe_narration(session.master_mod.derive))
    proposal = session.propose(sid, SCRIPT, provider="grok", duration_s=30)
    for op in proposal.ops:
        op.narration = ""
        _print(op, tmp_path)
    client = TestClient(app_module.app)
    res = client.post(f"/api/director/sessions/{sid}/assemble", json={})
    assert res.status_code == 409, res.text
    assert "authored" in res.json()["detail"]


def _wipe_narration(inner):
    def _derive(*a, **k):
        master = inner(*a, **k)
        master.narration = ""
        for clip in master.clips:
            clip["narration"] = ""
        return master

    return _derive


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_allow_silent_ships_a_deliberately_mute_cut(tmp_path) -> None:
    from diony_director import app as app_module

    sid = _sid()
    session.set_voice(sid, "leo")
    proposal = session.propose(sid, "a city splits in two", provider="grok", duration_s=30)
    for op in proposal.ops:
        op.narration = ""
        _print(op, tmp_path, real=True)
    client = TestClient(app_module.app)
    res = client.post(f"/api/director/sessions/{sid}/assemble", json={"allow_silent": True})
    assert res.status_code == 200, res.text
    assert res.json()["cue_count"] == 0
