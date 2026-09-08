"""GoogleFlowAdapter: POST /videos, poll /jobs, credit reconciliation from remainingCredits."""

import pytest

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    CostEstimateLine,
    DirectorOp,
    DirectorOpType,
)
from diony_director.providers import base, flow


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


def test_quota_429_raises_the_quota_error_with_the_providers_reset_time(monkeypatch) -> None:
    """The live 2026-09-02 shape: PUBLIC_ERROR_USER_QUOTA_REACHED with an ISO
    retryAfter — must surface as the transient quota flavor, never the fatal one."""
    from datetime import datetime, timezone

    body = {
        "error": "PUBLIC_ERROR_USER_QUOTA_REACHED (HTTP 429)",
        "code": 429,
        "retryAfter": "2026-09-02T22:33:11.316Z",
    }

    class _QuotaResponse(_Response):
        text = "PUBLIC_ERROR_USER_QUOTA_REACHED"
        headers: dict = {}

    monkeypatch.setattr(flow.httpx, "post", lambda url, **kw: _QuotaResponse(body, status_code=429))
    try:
        flow.submit(_op())
        raise AssertionError("expected CloudAdapterQuotaError")
    except base.CloudAdapterQuotaError as err:
        expected = datetime(2026, 9, 2, 22, 33, 11, 316000, tzinfo=timezone.utc).timestamp()
        assert err.retry_at == expected
        assert "429" in str(err)
        assert err.reason_code == "PUBLIC_ERROR_USER_QUOTA_REACHED"
        assert "overall quota is used up" in str(err)


def test_quota_reason_reads_the_live_envelope_never_dumps_it() -> None:
    """The 2026-09-03 room showed the whole JSON envelope (jobId, email,
    nested response…) as the hold reason. The reason is the documented
    token, read from any of the places useapi puts it, in plain words."""
    body = {
        "jobId": "j0903164445356080755v-u3057-email:someone@example.com-bot:google-flow",
        "error": "PUBLIC_ERROR_USER_QUOTA_REACHED (HTTP 429)",
        "code": 429,
        "retryAfter": "2026-09-03T17:14:52.956Z",
        "response": {
            "error": {
                "code": 429,
                "message": "Quota exceeded: PUBLIC_ERROR_USER_QUOTA_REACHED",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
    }
    res = _Response(body, status_code=429)
    res.text = __import__("json").dumps(body)
    code, why = base.quota_reason(res)
    assert code == "PUBLIC_ERROR_USER_QUOTA_REACHED"
    assert "quota is used up" in why and "jobId" not in why and "@" not in why
    throttled = _Response({"error": {"message": "PUBLIC_ERROR_USER_REQUESTS_THROTTLED"}}, status_code=429)
    throttled.text = ""
    assert base.quota_reason(throttled)[1].startswith("Flow is throttling")
    plain = _Response({"error": "slow down please"}, status_code=429)
    plain.text = ""
    assert base.quota_reason(plain) == ("", "slow down please")


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


# --- Request pacing: Flow is never hit in a burst -----------------------------


class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def _pacing_harness(monkeypatch, *, gap: str, start: float = 1000.0):
    clock = _FakeClock(start)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 6))
        clock.now += seconds

    monkeypatch.setenv("DIONY_FLOW_REQUEST_GAP_S", gap)
    monkeypatch.setattr(flow, "_clock", clock)
    monkeypatch.setattr(flow, "_sleep", fake_sleep)
    monkeypatch.setattr(flow, "_last_request_done_at", 0.0)
    return clock, sleeps


def test_consecutive_flow_requests_wait_out_the_gap_after_the_previous_one_finished(
    monkeypatch,
) -> None:
    clock, sleeps = _pacing_harness(monkeypatch, gap="2")

    def send():
        clock.now += 0.3  # the request itself takes 300 ms
        return "ok"

    assert flow.paced(send) == "ok"
    assert sleeps == []  # the first request never waits
    assert flow.paced(send) == "ok"
    assert sleeps == [2.0]  # a full gap, measured from the previous completion
    clock.now += 0.5  # half a second of unrelated work
    flow.paced(send)
    assert sleeps == [2.0, 1.5]  # only the remainder of the gap
    clock.now += 10
    flow.paced(send)
    assert sleeps == [2.0, 1.5]  # the gap already passed: no wait at all


def test_a_failed_flow_request_still_counts_toward_the_gap(monkeypatch) -> None:
    clock, sleeps = _pacing_harness(monkeypatch, gap="1.5")

    def boom():
        raise base.CloudAdapterTransientError("reset by peer")

    with pytest.raises(base.CloudAdapterTransientError):
        flow.paced(boom)
    flow.paced(lambda: "ok")
    assert sleeps == [1.5]


def test_request_gap_env_override_and_fallbacks(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_FLOW_REQUEST_GAP_S", raising=False)
    assert flow.request_gap_s() == flow.DEFAULT_REQUEST_GAP_S
    monkeypatch.setenv("DIONY_FLOW_REQUEST_GAP_S", "0.25")
    assert flow.request_gap_s() == 0.25
    monkeypatch.setenv("DIONY_FLOW_REQUEST_GAP_S", "-3")
    assert flow.request_gap_s() == 0.0
    monkeypatch.setenv("DIONY_FLOW_REQUEST_GAP_S", "soon")
    assert flow.request_gap_s() == flow.DEFAULT_REQUEST_GAP_S


def test_a_zero_gap_never_sleeps(monkeypatch) -> None:
    _clock, sleeps = _pacing_harness(monkeypatch, gap="0")
    for _ in range(3):
        flow.paced(lambda: "ok")
    assert sleeps == []


def test_concurrent_flow_requests_never_overlap(monkeypatch) -> None:
    import threading
    import time as time_mod

    monkeypatch.setenv("DIONY_FLOW_REQUEST_GAP_S", "0")
    monkeypatch.setattr(flow, "_last_request_done_at", 0.0)
    state = {"in_flight": 0, "peak": 0}
    guard = threading.Lock()

    def send():
        with guard:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        time_mod.sleep(0.005)
        with guard:
            state["in_flight"] -= 1
        return "ok"

    workers = [threading.Thread(target=flow.paced, args=(send,)) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert state["peak"] == 1
    assert state["in_flight"] == 0


def test_every_flow_api_call_passes_through_the_pacing_gate(monkeypatch, tmp_path) -> None:
    gated: list[str] = []
    real_paced = flow.paced

    def counting_paced(send, *, what="request"):
        gated.append(what)
        return real_paced(send, what=what)

    monkeypatch.setattr(flow, "paced", counting_paced)
    monkeypatch.setattr(flow.httpx, "post", lambda url, **kw: _Response(
        {"jobId": "j-1", "mediaGenerationId": "m-1", "entityId": "c-1", "ref": "v-1"}
    ))
    monkeypatch.setattr(
        flow.httpx, "get", lambda url, **kw: _Response({"status": "running"}, status_code=200)
    )

    video = flow.submit(_op())
    flow.poll(video)
    flow.submit_image("a plate")
    flow.submit_extend("m-1", prompt="keep rolling", duration_s=8)
    flow.submit_upscale("m-1", resolution="1080p")
    flow.register_character("Mara", ["img-1"])
    flow.register_voice("Mara", "aud-1")
    plate = tmp_path / "plate.png"
    plate.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    flow.upload_asset(plate)

    assert gated == [
        "video submit",
        "job poll",
        "image submit",
        "extend submit",
        "upscale submit",
        "character register",
        "voice register",
        "asset upload",
    ]


def _live_filtered_job_body() -> dict:
    """Shape of a real Flow job (2026-09-04) refused by Google's prominent-people
    filter: the top-level error is the generic "All operations failed"; the
    reason lives in response.failureReasons and the media item's mediaStatus."""
    return {
        "jobid": "j0904-filtered",
        "status": "failed",
        "code": 400,
        "error": "All operations failed",
        "response": {
            "failureReasons": ["PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED", "PROMINENT_PERSON"],
            "media": [
                {
                    "mediaGenerationId": "user:1-video:abc",
                    "mediaMetadata": {
                        "visibility": "FILTERED",
                        "mediaStatus": {
                            "error": {"message": "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED", "code": 3},
                            "failureReasons": ["PROMINENT_PERSON"],
                            "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED",
                        },
                    },
                    "name": "abc",
                }
            ],
            "operations": [],
            "remainingCredits": 10000,
        },
    }


def test_poll_names_the_filter_behind_all_operations_failed(monkeypatch) -> None:
    """"All operations failed" is Flow's generic wrapper; the director must
    read the nested reason — a prominent-person refusal — and land it as
    moderated (nothing rendered, nothing burned) with actionable words."""
    monkeypatch.setattr(
        flow.httpx, "get", lambda url, **kw: _Response(_live_filtered_job_body(), status_code=200)
    )
    handle = flow.submit_handle("j0904-filtered", model="omni-flash", duration_s=10)
    status = flow.poll(handle)
    assert status.state == "moderated"
    assert status.terminal
    assert "All operations failed" not in status.detail
    assert "prominent-people filter" in status.detail
    assert "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED" in status.detail
    # the verified doctrine (2026-09-07): a name beside a picture, real or
    # invented; the words are scrubbed at submit, Retry sets the picture aside
    assert "any named character" in status.detail and "sets the picture aside" in status.detail
    assert status.metadata["credits_after"] == 10000


def test_poll_keeps_a_specific_top_level_error_when_nothing_is_nested(monkeypatch) -> None:
    body = {"status": "failed", "error": "captcha required", "response": {"media": []}}
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body, status_code=200))
    status = flow.poll(flow.submit_handle("j-175", model="omni-flash", duration_s=10))
    assert status.state == "failed"
    assert status.detail == "captcha required"


def test_poll_reports_a_non_filter_nested_reason_as_failed(monkeypatch) -> None:
    body = {
        "status": "failed",
        "error": "All operations failed",
        "response": {"failureReasons": ["INTERNAL_ERROR"], "media": []},
    }
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body, status_code=200))
    status = flow.poll(flow.submit_handle("j-176", model="omni-flash", duration_s=10))
    assert status.state == "failed"
    assert status.detail == "Google Flow reported INTERNAL_ERROR"


def test_completed_job_whose_media_was_filtered_lands_moderated(monkeypatch) -> None:
    """A "completed" job with no link and a FILTERED media item is a refusal,
    not Google's link throttle — it must not re-poll forever."""
    body = _live_filtered_job_body()
    body["status"] = "completed"
    body["error"] = ""
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body, status_code=200))
    status = flow.poll(flow.submit_handle("j-177", model="omni-flash", duration_s=10))
    assert status.state == "moderated"
    assert "prominent-people filter" in status.detail
