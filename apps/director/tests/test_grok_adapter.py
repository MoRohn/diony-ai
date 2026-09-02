"""GrokImagineAdapter: async submit/poll, and immediate persistence of ephemeral URLs."""

import hashlib
import json

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    CostEstimateLine,
    DirectorOp,
    DirectorOpType,
)
from diony_director.providers import base, grok


def _op(**overrides) -> DirectorOp:
    estimate = CostEstimate(
        id="est-1",
        workspace_id="local-workspace",
        lines=[
            CostEstimateLine(
                label="video seconds · 720p",
                provider_id="grok",
                model="grok-imagine-video",
                unit="second:720p",
                unit_rate_usd_micros=70_000,
                quantity_microunits=6_000_000,
                usd_micros=420_000,
            )
        ],
    )
    payload = {
        "id": "op-1",
        "session_id": "s-1",
        "op": DirectorOpType.GENERATE,
        "provider": CloudProvider.GROK,
        "model": "grok-imagine-video",
        "mode": CloudGenerationMode.T2V,
        "prompt": "a dusty truck at dusk, slow push-in, engine idle and wind",
        "duration_s": 6,
        "resolution": "720p",
        "estimate": estimate,
    }
    payload.update(overrides)
    return DirectorOp(**payload)


class _Response:
    def __init__(self, body: dict, status_code: int = 200, content: bytes = b""):
        self._body = body
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise base.httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self) -> dict:
        return self._body


def test_submit_posts_the_generation_and_returns_the_request_id(monkeypatch) -> None:
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        seen["headers"] = kwargs.get("headers")
        return _Response({"request_id": "req-abc123"})

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    handle = grok.submit(_op())
    assert handle.external_id == "req-abc123"
    assert handle.provider_id == "grok"
    assert seen["url"].endswith("/v1/videos/generations")
    assert seen["json"]["model"] == "grok-imagine-video"
    assert seen["json"]["duration"] == 6
    assert "test-xai-key" in seen["headers"]["Authorization"]


def test_poll_maps_done_and_carries_the_result_url(monkeypatch) -> None:
    def fake_get(url, **kwargs):
        assert url.endswith("/v1/videos/req-abc123")
        return _Response({"status": "done", "video": {"url": "https://cdn.x.ai/clip.mp4", "duration": 6}})

    monkeypatch.setattr(grok.httpx, "get", fake_get)
    handle = grok.submit_handle("req-abc123", model="grok-imagine-video", duration_s=6)
    status = grok.poll(handle)
    assert status.state == "done"
    assert status.result_url == "https://cdn.x.ai/clip.mp4"


def test_poll_maps_failed_moderated_and_expired(monkeypatch) -> None:
    responses = iter([
        _Response({"status": "failed", "error": {"code": "internal", "message": "GPU exploded"}}),
        _Response({"status": "failed", "error": {"code": "content_moderation", "message": "refused"}}),
        _Response({"status": "expired"}),
    ])
    monkeypatch.setattr(grok.httpx, "get", lambda url, **kw: next(responses))
    handle = grok.submit_handle("req-x", model="grok-imagine-video", duration_s=6)
    first = grok.poll(handle)
    assert first.state == "failed"
    assert "GPU exploded" in first.detail and "internal" in first.detail
    assert grok.poll(handle).state == "moderated"
    assert grok.poll(handle).state == "expired"


def test_reference_images_are_url_objects_capped_at_three(monkeypatch) -> None:
    seen = {}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs.get("json")
        return _Response({"request_id": "req-r2v"})

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    op = _op(
        model="grok-imagine-video-1.5",
        mode=CloudGenerationMode.R2V,
        artifact_refs={"references": ["u1", "u2", "u3", "u4", "u5"]},
    )
    grok.submit(op)
    assert seen["json"]["reference_images"] == [{"url": "u1"}, {"url": "u2"}, {"url": "u3"}]
    assert "image" not in seen["json"]


def test_silent_picture_clips_disable_native_audio(monkeypatch) -> None:
    seen = {}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs.get("json")
        return _Response({"request_id": "req-silent"})

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    grok.submit(_op(artifact_refs={"generate_audio": False}))
    assert seen["json"]["generate_audio"] is False


def test_extension_and_edit_send_video_input_objects(monkeypatch) -> None:
    seen = []

    def fake_post(url, **kwargs):
        seen.append(kwargs.get("json"))
        return _Response({"request_id": f"req-{len(seen)}"})

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    grok.submit_extension(
        "https://vault/clip.mp4", prompt="continue", model="grok-imagine-video-1.5", duration_s=6
    )
    grok.submit_edit("data:video/mp4;base64,AAAA", prompt="remove the earrings")
    assert seen[0]["video"] == {"url": "https://vault/clip.mp4"}
    assert seen[1]["video"] == {"base64": "AAAA"}
    assert seen[1]["model"] == "grok-imagine-video"  # editing model per docs


def test_fetch_persists_the_ephemeral_url_with_a_hash(monkeypatch, tmp_path) -> None:
    payload = b"grok-clip-bytes"
    monkeypatch.setattr(base.httpx, "get", lambda url, **kw: _Response({}, content=payload))
    clip = base.download("https://cdn.x.ai/clip.mp4", tmp_path / "take.mp4")
    assert clip.path.read_bytes() == payload
    assert clip.sha256 == hashlib.sha256(payload).hexdigest()
    assert clip.bytes == len(payload)


def test_api_rejections_surface_the_providers_own_words(monkeypatch) -> None:
    class Reject:
        status_code = 400
        text = '{"error":"duration must be between 1 and 15 seconds"}'

        def json(self):
            return {}

    monkeypatch.setattr(grok.httpx, "post", lambda *a, **k: Reject())
    try:
        grok.submit(_op())
    except Exception as err:
        assert "400" in str(err)
        assert "duration must be between" in str(err)
        from diony_director.providers.base import CloudAdapterError

        assert isinstance(err, CloudAdapterError)
    else:  # pragma: no cover
        raise AssertionError("expected CloudAdapterError")


def test_i2v_image_is_a_typed_url_object(monkeypatch, tmp_path) -> None:
    """Verified live: a bare string image is rejected 422 — the REST field is
    an object like the extension's video input."""
    seen = {}

    def fake_post(url, **kwargs):
        seen["json"] = kwargs.get("json")
        return _Response({"request_id": "req-i2v"})

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    still = tmp_path / "plate.jpg"
    still.write_bytes(b"jpegbytes")
    grok.submit(_op(mode=CloudGenerationMode.I2V, artifact_refs={"start_image": str(still)}))
    image = seen["json"]["image"]
    assert isinstance(image, dict)
    assert image["url"].startswith("data:image/jpg;base64,")


def test_dropped_reference_images_are_recorded_not_silent(monkeypatch, tmp_path) -> None:
    """Image and reference_images are exclusive on the wire; when the
    still-lock wins, the degradation must live in the handle's record."""
    monkeypatch.setattr(grok.httpx, "post", lambda url, **kw: _Response({"request_id": "req-d"}))
    still = tmp_path / "plate.jpg"
    still.write_bytes(b"jpegbytes")
    handle = grok.submit(
        _op(
            mode=CloudGenerationMode.I2V,
            artifact_refs={"start_image": str(still), "references": ["u1", "u2"]},
        )
    )
    assert handle.metadata["references_dropped"] == 2
    clean = grok.submit(_op(mode=CloudGenerationMode.I2V, artifact_refs={"start_image": str(still)}))
    assert "references_dropped" not in clean.metadata
