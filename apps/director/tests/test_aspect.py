"""Program aspect: one session-level shape (16:9 default, 9:16 vertical)
carried by every generation, plate, bridge frame, and the final assembly."""

import pytest

from diony_director import assembly, session


@pytest.fixture(autouse=True)
def _clean_state():
    session.reset()
    yield
    session.reset()


def test_aspect_defaults_to_wide() -> None:
    assert session.aspect_for("s-x") == "16:9"
    assert session.program_raster("s-x") == (1280, 720)


def test_vertical_aspect_flips_the_program_raster() -> None:
    session.set_aspect("s-v", "9:16")
    assert session.aspect_for("s-v") == "9:16"
    assert session.program_raster("s-v") == (720, 1280)


def test_unknown_aspect_is_refused() -> None:
    with pytest.raises(ValueError):
        session.set_aspect("s-x", "4:3")


def test_pin_aspect_speaks_each_providers_dialect() -> None:
    from diony_contracts.director import CloudProvider
    from diony_director.app import _pin_aspect
    from tests.test_continuity_music import _gen_op

    session.set_aspect("s-pin", "9:16")
    grok_op = _gen_op("op-g", "s-pin", "cl-1")
    _pin_aspect(grok_op)
    assert grok_op.artifact_refs["aspect_ratio"] == "9:16"

    flow_op = _gen_op("op-f", "s-pin", "cl-2", provider=CloudProvider.GOOGLE_FLOW)
    _pin_aspect(flow_op)
    assert flow_op.artifact_refs["aspect_ratio"] == "portrait"


def test_aspect_locks_once_footage_exists() -> None:
    from fastapi import HTTPException

    from diony_director.app import _apply_aspect
    from tests.test_continuity_music import _gen_op

    sid = "s-lock"
    op = _gen_op("op-done", sid, "cl-1", status="completed")
    session._OPS[op.id] = op
    session._SESSIONS[sid] = session.SessionState(id=sid, op_ids=[op.id])

    _apply_aspect(sid, "16:9")  # same shape: a no-op, never an error
    with pytest.raises(HTTPException) as err:
        _apply_aspect(sid, "9:16")
    assert "locked" in err.value.detail


def test_assembly_filter_cuts_at_the_requested_raster() -> None:
    probes = [{"duration_s": 4.0, "has_audio": True}]
    chains = assembly.build_filter(
        probes, {}, look=False, loudness_target="streaming", width=720, height=1280
    )
    joined = ";".join(chains)
    assert "scale=720:1280" in joined
    assert "pad=720:1280" in joined


def test_grok_image_payload_carries_the_program_aspect(monkeypatch) -> None:
    from diony_director.providers import base, grok

    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"url": "https://cdn/img.png"}]}

    def fake_post(url, *, what, json, headers, timeout):
        seen.update(json)
        return _Resp()

    monkeypatch.setenv("XAI_API_KEY", "k")
    monkeypatch.setattr(base, "post_with_retries", fake_post)
    grok.submit_image("a plate", aspect_ratio="9:16")
    assert seen.get("aspect_ratio") == "9:16"
