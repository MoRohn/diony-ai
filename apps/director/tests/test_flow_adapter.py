"""GoogleFlowAdapter: POST /videos, poll /jobs, credit reconciliation from remainingCredits."""

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    CostEstimateLine,
    DirectorOp,
    DirectorOpType,
)
from diony_director.providers import flow


def _op(**overrides) -> DirectorOp:
    estimate = CostEstimate(
        id="est-2",
        workspace_id="local-workspace",
        lines=[
            CostEstimateLine(
                label="veo-3.1-fast generation · 8s",
                provider_id="google-flow",
                model="veo-3.1-fast",
                unit="generation",
                unit_rate_usd_micros=399_800,
                quantity_microunits=1_000_000,
                internal_credits=20,
                usd_micros=399_800,
            )
        ],
    )
    payload = {
        "id": "op-2",
        "session_id": "s-1",
        "op": DirectorOpType.GENERATE,
        "provider": CloudProvider.GOOGLE_FLOW,
        "model": "veo-3.1-fast",
        "mode": CloudGenerationMode.T2V,
        "prompt": "diner at night, neon reflections, rain easing",
        "duration_s": 8,
        "resolution": "720p",
        "estimate": estimate,
    }
    payload.update(overrides)
    return DirectorOp(**payload)


class _Response:
    def __init__(self, body: dict, status_code: int = 201):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise flow.httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self) -> dict:
        return self._body


def test_submit_posts_async_and_returns_the_job_id(monkeypatch) -> None:
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        seen["headers"] = kwargs.get("headers")
        return _Response({"jobId": "j-171", "remainingCredits": 980})

    monkeypatch.setattr(flow.httpx, "post", fake_post)
    handle = flow.submit(_op())
    assert handle.external_id == "j-171"
    assert handle.metadata["credits_before"] == 980
    assert seen["url"].endswith("/videos")
    assert seen["json"]["model"] == "veo-3.1-fast"
    assert seen["json"]["duration"] == 8
    assert seen["json"]["async"] is True
    assert "test-useapi-token" in seen["headers"]["Authorization"]


def test_poll_completed_extracts_video_url_and_credits(monkeypatch) -> None:
    body = {
        "status": "completed",
        "remainingCredits": 960,
        "media": [{"mediaGenerationId": "user:abc", "videoUrl": "https://flow-content.google/clip.mp4"}],
    }
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body, status_code=200))
    handle = flow.submit_handle("j-171", model="veo-3.1-fast", duration_s=8, credits_before=980)
    status = flow.poll(handle)
    assert status.state == "done"
    assert status.result_url == "https://flow-content.google/clip.mp4"
    assert flow.credits_used(handle, status) == 20


def test_credits_fall_back_to_the_rate_card_when_the_meter_is_silent(monkeypatch) -> None:
    body = {"status": "completed", "media": [{"videoUrl": "https://flow-content.google/c.mp4"}]}
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body, status_code=200))
    handle = flow.submit_handle("j-172", model="veo-3.1-fast", duration_s=8)
    status = flow.poll(handle)
    assert flow.credits_used(handle, status) == 20  # from the packaged rate card


def test_poll_maps_a_failed_job_with_its_error(monkeypatch) -> None:
    body = {"status": "failed", "error": "captcha required", "code": 429}
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body, status_code=200))
    handle = flow.submit_handle("j-173", model="veo-3.1-fast", duration_s=8)
    status = flow.poll(handle)
    assert status.state == "failed"
    assert "captcha" in status.detail


def test_poll_lands_off_vocabulary_terminal_states_as_terminal(monkeypatch) -> None:
    """A moderated/cancelled Flow job must never poll as "running" forever —
    an unmapped terminal status wedges the whole paid batch behind it."""
    handle = flow.submit_handle("j-174", model="veo-3.1-fast", duration_s=8)
    expectations = {
        "moderated": "moderated",
        "blocked": "moderated",
        "rejected": "moderated",
        "content_violation": "moderated",
        "cancelled": "failed",
        "canceled": "failed",
        "timeout": "failed",
        "expired": "expired",
    }
    for raw, expected in expectations.items():
        body = {"status": raw}
        monkeypatch.setattr(
            flow.httpx, "get", lambda url, _body=body, **kw: _Response(_body, status_code=200)
        )
        status = flow.poll(handle)
        assert status.state == expected, raw
        assert status.terminal, raw
    # a genuinely unknown transient word still polls as running
    monkeypatch.setattr(
        flow.httpx,
        "get",
        lambda url, **kw: _Response({"status": "warming_up"}, status_code=200),
    )
    assert flow.poll(handle).state == "running"
