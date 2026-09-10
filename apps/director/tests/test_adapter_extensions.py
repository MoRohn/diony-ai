"""M2/M3/M5 adapter surface: images, characters, voices, edit, extend, upscale."""

from diony_director.providers import flow, grok


class _Response:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise flow.httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self) -> dict:
        return self._body


def test_flow_image_submit_targets_the_images_endpoint(monkeypatch) -> None:
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        return _Response({"jobId": "j-img-1"}, status_code=201)

    monkeypatch.setattr(flow.httpx, "post", fake_post)
    handle = flow.submit_image("a neon diner plate, wide", model="nano-banana-2")
    assert handle.external_id == "j-img-1"
    assert seen["url"].endswith("/images")
    assert seen["json"]["model"] == "nano-banana-2"


def test_flow_poll_surfaces_media_generation_id_and_image_urls(monkeypatch) -> None:
    body = {
        "status": "completed",
        "media": [
            {"mediaGenerationId": "user:img9", "imageUrl": "https://flow-content.google/p.png"}
        ],
    }
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body))
    handle = flow.submit_handle("j-img-1", model="nano-banana-2", duration_s=0)
    status = flow.poll(handle)
    assert status.state == "done"
    assert status.result_url.endswith("p.png")
    assert status.metadata["media_generation_id"] == "user:img9"


def test_flow_character_and_voice_registration(monkeypatch) -> None:
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs.get("json")))
        if url.endswith("/characters"):
            return _Response({"ref": "char-ref-7"}, status_code=201)
        return _Response({"ref": "voice-ref-3"}, status_code=201)

    monkeypatch.setattr(flow.httpx, "post", fake_post)
    char_ref = flow.register_character("Mara", ["user:img1", "user:img2"])
    voice_ref = flow.register_voice("Mara", "user:aud1")
    assert char_ref == "char-ref-7"
    assert voice_ref == "voice-ref-3"
    assert calls[0][0].endswith("/characters")
    assert calls[1][0].endswith("/voices")


def test_flow_extend_and_upscale_endpoints(monkeypatch) -> None:
    seen = []

    def fake_post(url, **kwargs):
        seen.append((url, kwargs.get("json")))
        return _Response({"jobId": f"j-{len(seen)}"}, status_code=201)

    monkeypatch.setattr(flow.httpx, "post", fake_post)
    ext = flow.submit_extend("user:vid1", prompt="keep driving into the dark", duration_s=8)
    ups = flow.submit_upscale("user:vid1")
    assert ext.external_id == "j-1" and ups.external_id == "j-2"
    assert seen[0][0].endswith("/videos/extend")
    assert seen[1][0].endswith("/videos/upscale")


def test_grok_edit_and_extension_endpoints(monkeypatch) -> None:
    seen = []

    def fake_post(url, **kwargs):
        seen.append((url, kwargs.get("json")))
        return _Response({"request_id": f"req-{len(seen)}"})

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    edit = grok.submit_edit(
        "https://vault/clip.mp4",
        prompt="remove the earrings, keep everything else",
        model="grok-imagine-video",
    )
    ext = grok.submit_extension(
        "https://vault/clip.mp4",
        prompt="continue the push-in and the wind",
        model="grok-imagine-video",
        duration_s=6,
    )
    assert edit.external_id == "req-1" and ext.external_id == "req-2"
    assert seen[0][0].endswith("/v1/videos/edits")
    assert seen[1][0].endswith("/v1/videos/extensions")
    assert seen[1][1]["duration"] == 6


# --- POST /videos/upscale contract (useapi.net docs, verified 2026-09-03) ----


def test_flow_upscale_payload_is_async_and_never_carries_email(monkeypatch) -> None:
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        # async mode answers 201 with a LOWERCASE jobid
        return _Response({"jobid": "j-up-1", "status": "created"}, status_code=201)

    monkeypatch.setenv("DIONY_FLOW_EMAIL", "studio@example.com")
    monkeypatch.setattr(flow.httpx, "post", fake_post)
    handle = flow.submit_upscale("user:12345-video:abc", resolution="1080p")
    assert seen["url"].endswith("/videos/upscale")
    assert seen["json"] == {
        "mediaGenerationId": "user:12345-video:abc",
        "resolution": "1080p",
        "async": True,
    }
    assert "email" not in seen["json"]
    assert handle.external_id == "j-up-1"
    assert handle.model == flow.UPSCALE_MODEL
    assert handle.resolution == "1080p"
    assert handle.metadata["kind"] == "upscale"
    assert handle.metadata["source_media_generation_id"] == "user:12345-video:abc"
    assert "sync_result_url" not in handle.metadata


def test_flow_upscale_refuses_illegal_inputs_before_any_request(monkeypatch) -> None:
    import pytest

    from diony_director.providers import base

    def never(url, **kwargs):
        raise AssertionError("no request may leave for an illegal upscale")

    monkeypatch.setattr(flow.httpx, "post", never)
    with pytest.raises(base.CloudAdapterError, match="720p, 1080p, 4K"):
        flow.submit_upscale("user:vid", resolution="2K")
    with pytest.raises(base.CloudAdapterError, match="local file or URL"):
        flow.submit_upscale("https://cdn/clip.mp4")
    with pytest.raises(base.CloudAdapterError, match="mediaGenerationId"):
        flow.submit_upscale("")


def test_flow_upscale_cached_200_completes_without_a_poll(monkeypatch) -> None:
    # upscaling the same video twice returns the cached result synchronously
    body = {
        "jobId": "j-up-cached",
        "media": [
            {
                "mediaGenerationId": "user:vid_upsampled",
                "videoUrl": "https://flow-content.google/video/up.mp4",
            }
        ],
        "remainingCredits": 500,
    }
    monkeypatch.setattr(flow.httpx, "post", lambda url, **kw: _Response(body, status_code=200))

    def never_get(url, **kw):
        raise AssertionError("a cached upscale never polls")

    monkeypatch.setattr(flow.httpx, "get", never_get)
    handle = flow.submit_upscale("user:vid")
    status = flow.poll(handle)
    assert status.state == "done"
    assert status.result_url.endswith("/up.mp4")
    assert status.metadata["media_generation_id"] == "user:vid_upsampled"
    assert status.metadata["credits_after"] == 500


def test_flow_poll_treats_a_completed_job_without_a_link_as_pending(monkeypatch) -> None:
    # the docs: a missing videoUrl on a successful media item is Google's
    # link throttling — re-resolved by GET /jobs at most once a minute
    body = {
        "status": "completed",
        "response": {
            "media": [
                {
                    "mediaGenerationId": "user:vid_upsampled",
                    "mediaStatus": {"mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESSFUL"},
                }
            ],
            "remainingCredits": 12,
        },
    }
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(body))
    handle = flow.submit_handle("j-up-2", model=flow.UPSCALE_MODEL, duration_s=0, kind="upscale")
    status = flow.poll(handle)
    assert status.state == "running"
    assert status.terminal is False
    assert status.metadata["link_pending"] is True
    assert status.metadata["poll_after_s"] == flow.LINK_PENDING_POLL_S
    assert "link pending" in status.detail

    failed = {
        "status": "completed",
        "media": [{"mediaStatus": {"mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED"}}],
    }
    monkeypatch.setattr(flow.httpx, "get", lambda url, **kw: _Response(failed))
    status = flow.poll(handle)
    assert status.state == "failed"
    assert "FAILED" in status.detail


def test_flow_upscale_credits_follow_the_documented_resolution_prices() -> None:
    from diony_director.providers import base

    silent = base.CloudJobStatus(state="done", result_url="https://x/y.mp4")
    free = flow.submit_handle("j", model=flow.UPSCALE_MODEL, duration_s=0, resolution="1080p")
    assert flow.credits_used(free, silent) == 0
    ultra = flow.submit_handle("j", model=flow.UPSCALE_MODEL, duration_s=0, resolution="4K")
    assert flow.credits_used(ultra, silent) == 50
    metered = flow.submit_handle(
        "j", model=flow.UPSCALE_MODEL, duration_s=0, resolution="1080p", credits_before=100
    )
    assert (
        flow.credits_used(
            metered,
            base.CloudJobStatus(
                state="done", result_url="https://x/y.mp4", metadata={"credits_after": 97}
            ),
        )
        == 3
    )
