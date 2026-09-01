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
        "media": [{"mediaGenerationId": "user:img9", "imageUrl": "https://flow-content.google/p.png"}],
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
        "https://vault/clip.mp4", prompt="remove the earrings, keep everything else",
        model="grok-imagine-video",
    )
    ext = grok.submit_extension(
        "https://vault/clip.mp4", prompt="continue the push-in and the wind",
        model="grok-imagine-video", duration_s=6,
    )
    assert edit.external_id == "req-1" and ext.external_id == "req-2"
    assert seen[0][0].endswith("/v1/videos/edits")
    assert seen[1][0].endswith("/v1/videos/extensions")
    assert seen[1][1]["duration"] == 6
