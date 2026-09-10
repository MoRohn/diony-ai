"""s-22c294097d (2026-09-05): a second script pasted into a room that already
held a chained one-shot was sequenced BESIDE the first. Run All rolled the
old chain's leftover links, the cut concatenated the old film before the new
one (63 subtitle cues: the previous script first), and the room kept
resuming on the old chain's dead links. A room holds ONE film: a new chained
one-shot retires the earlier ones — off the cut, off the batch, out of the
narration count and the resume slot; their prints stay on the ledger."""

from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from diony_director import review, session


def _sid() -> str:
    return f"s-test-{uuid.uuid4().hex[:8]}"


def _print(op, tmp_path) -> None:
    from diony_director.providers import base

    op.status = "completed"
    clip = tmp_path / f"{op.id}.mp4"
    clip.write_bytes(b"clip" * 64)
    session.runtime(op.id)["clip"] = base.FetchedClip(
        path=clip, sha256="x", bytes=clip.stat().st_size
    )


def test_a_new_script_retires_the_rooms_earlier_film(tmp_path) -> None:
    sid = _sid()
    first = session.propose(
        sid, "a keeper crosses the lamp room and opens the iron door", duration_s=45
    )
    for op in first.ops[:2]:
        _print(op, tmp_path)
    first.ops[2].status = "failed"  # a dead link: the room would resume on it forever
    session.set_batch_targets(sid, {op.id for op in first.ops})
    assert [op.id for op in session.completed_clip_ops(sid)] == [op.id for op in first.ops[:2]]
    assert any(row["session_id"] == sid for row in session.resumable_sessions())

    second = session.propose(
        sid, "a diver descends a flooded stairwell and finds the lantern", duration_s=45
    )
    assert "retired" in second.reply and "2 printed clips of 3" in second.reply
    old_ids = {op.id for op in first.ops}
    assert session.retired_op_ids(sid) == old_ids
    assert session.retired_scenes(sid) == {f"oneshot-{first.ops[0].id}"}

    # the cut follows the new script alone — the old prints never lead the film
    assert session.completed_clip_ops(sid) == []
    _print(second.ops[0], tmp_path)
    assert [op.id for op in session.completed_clip_ops(sid)] == [second.ops[0].id]
    assert [[op.id for op in chain] for chain in session.oneshot_chain_ops(sid)] == [
        [op.id for op in second.ops]
    ]
    assert len(session.oneshot_chain_ops(sid, include_retired=True)) == 2

    # the old chain left the batch when the new one landed, and never re-enters
    assert not (session.batch_targets(sid) & old_ids)
    targets = session.set_batch_targets(sid, old_ids | {second.ops[1].id})
    assert targets == {second.ops[1].id}
    held = session.drain_held(sid)
    assert held and "earlier script" in held[0]["reason"]
    assert all(op.id not in old_ids for op in session.ready_ops(sid))
    assert [op.id for op in session.ready_ops(sid)] == [second.ops[1].id]

    # the old chain's dead link is no longer unfinished business for the room
    assert not any(row["session_id"] == sid for row in session.resumable_sessions())

    # a restart keeps the retirement
    session.persist(sid)
    doc = json.loads((tmp_path / "director" / sid / "state.json").read_text())
    assert doc["sequence"]["retired_scenes"] == [f"oneshot-{first.ops[0].id}"]
    session.reset()
    session._load_state(sid, doc)
    assert session.retired_op_ids(sid) == old_ids
    assert [op.id for op in session.completed_clip_ops(sid)] == [second.ops[0].id]


def test_authored_lines_count_only_the_living_script() -> None:
    sid = _sid()
    session.propose(
        sid,
        'Scene 1 — Dawn\nVisual: a founder at a desk.\nNarration: "Nobody remembers the first sale."\n'
        'Scene 2 — Noon\nVisual: the desk buried in orders.\nNarration: "Everyone remembers the phones."\n',
        duration_s=45,
    )
    assert session.authored_lines_expected(sid) == 2
    session.propose(
        sid,
        'Scene 1 — Night\nVisual: a diver on a flooded stair.\nNarration: "The lantern was still lit."\n',
        duration_s=45,
    )
    assert session.authored_lines_expected(sid) == 1


def test_run_batch_names_a_retired_chains_links_instead_of_rolling_them(tmp_path) -> None:
    from diony_director import app as app_module

    client = TestClient(app_module.app)
    sid = _sid()
    first = session.propose(sid, "a keeper crosses the lamp room", duration_s=45)
    second = session.propose(sid, "a diver descends a flooded stairwell", duration_s=45)
    # the old card's Run All asks for the old links: named, not rolled
    res = client.post(
        f"/api/director/sessions/{sid}/run_batch",
        json={"op_ids": [op.id for op in first.ops]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["targeted"] == []
    assert body["held"] and "earlier script" in body["held"][0]["reason"]
    # a plain Run All (no ids) rolls the living chain only
    res = client.post(f"/api/director/sessions/{sid}/run_batch", json={})
    assert res.status_code == 200, res.text
    assert set(res.json()["targeted"]) == {op.id for op in second.ops}


# --- a scripted black frame is the script, not a dead picture ----------------


def test_a_scripted_black_open_is_advisory_not_a_hold(monkeypatch, tmp_path) -> None:
    import types

    assert review.scripts_black("[VISUAL — black screen.] A soft startup chime.")
    assert review.scripts_black("A black frame holds, then ceiling slots rise.")
    assert review.scripts_black("Max looks into camera. Cut to black.")
    assert not review.scripts_black("Max, in a black suit over a black shirt, at the window.")

    receipt = types.SimpleNamespace(
        measured=True, black_frames=2, sharpness_min=200.0, exposure_ok=True
    )
    import diony_producer.quality as quality

    monkeypatch.setattr(quality, "measure_frame_quality", lambda path, frame_count: receipt)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")

    families: dict[str, str] = {}
    issues: list[str] = []
    review._measure_frames_for(clip, 10, families, {}, issues, scripted_black=True)
    assert families["frames"] == "pass"
    assert "advisory" in issues[0]

    families, issues = {}, []
    review._measure_frames_for(clip, 10, families, {}, issues)
    assert families["frames"] == "fail"
    assert "died mid-clip" in issues[0]
