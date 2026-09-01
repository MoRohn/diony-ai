"""Screenwriter: idea → real script documents, deterministic offline, LLM when configured."""

import json

from diony_director import screenwriter


def test_idea_becomes_a_structured_script_with_bibles() -> None:
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    script = draft.script
    assert script.runtime_target_s == 60
    assert script.logline
    assert script.beats and all("story_time" in beat for beat in script.beats)
    assert len(script.scenes) >= 2
    assert any(line.kind == "dialogue" for line in script.lines)
    assert draft.characters and draft.characters[0].voice is not None
    assert draft.settings and draft.settings[0].room_tone_id
    for scene in script.scenes:
        assert scene.setting_ref in {s.setting_id for s in draft.settings}


def test_offline_fallback_is_labeled_never_silent() -> None:
    draft = screenwriter.write_script("two rivals meet at dawn", runtime_target_s=45)
    assert any("template" in warning for warning in draft.warnings)


def test_scene_headings_follow_screenplay_form() -> None:
    draft = screenwriter.write_script("a chase through a desert town", runtime_target_s=60)
    for scene in draft.script.scenes:
        assert scene.heading.startswith(("INT.", "EXT."))
        assert " - " in scene.heading


def test_brain_hook_is_used_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")

    body = {
        "logline": "A driver leaves town.",
        "beats": [{"beat": "decision", "story_time": "Night 1"}],
        "settings": [{"setting_id": "set-diner", "name": "Neon Diner", "kind": "INT", "room_tone": "diner hum"}],
        "characters": [{"char_id": "chr-mara", "name": "MARA"}],
        "scenes": [
            {
                "heading": "INT. NEON DINER - NIGHT",
                "setting_ref": "set-diner",
                "story_time": "Night 1",
                "cast": ["chr-mara"],
                "lines": [
                    {"kind": "action", "text": "MARA counts her keys."},
                    {"kind": "dialogue", "text": "Tonight we drive.", "speaker": "MARA"},
                ],
            }
        ],
    }

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    draft = screenwriter.write_script("a getaway driver leaves town", runtime_target_s=60)
    assert draft.script.logline == "A driver leaves town."
    assert draft.script.scenes[0].heading == "INT. NEON DINER - NIGHT"
    assert not any("template" in w for w in draft.warnings)


def test_brain_failure_raises_never_templates(monkeypatch) -> None:
    import pytest as _pytest

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")

    def boom(*a, **k):
        raise screenwriter.httpx.HTTPError("down")

    monkeypatch.setattr(screenwriter.httpx, "post", boom)
    with _pytest.raises(screenwriter.BrainUnavailable) as err:
        screenwriter.write_script("a getaway driver leaves town", runtime_target_s=60)
    assert "grok-4.6" in str(err.value)
    assert "down" in str(err.value)


def test_the_template_scales_scenes_to_the_runtime_target() -> None:
    from diony_director import splitter

    draft = screenwriter.write_script("a heist across a sleeping city", runtime_target_s=300)
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    planned = sum(c.duration_s for c in clips)
    # planned footage lands near the target, not at a fixed 3-scene stub
    assert 210 <= planned <= 390, f"planned {planned}s for a 300s target"
    assert len(draft.script.scenes) >= 10


def test_short_targets_keep_the_compact_shape() -> None:
    draft = screenwriter.write_script("two rivals meet at dawn", runtime_target_s=45)
    assert 2 <= len(draft.script.scenes) <= 4


def test_dev_scene_dialogue_varies_not_repeats() -> None:
    draft = screenwriter.write_script("a heist across a sleeping city", runtime_target_s=240)
    lines = [l.text for l in draft.script.lines if l.kind == "dialogue"]
    assert len(lines) >= 6
    assert len(set(lines[:8])) == len(lines[:8]), "adjacent dev scenes must not repeat dialogue"


def test_no_length_guards_a_twenty_minute_target_plans_twenty_minutes() -> None:
    from diony_director import splitter

    draft = screenwriter.write_script("a night crossing of the whole city", runtime_target_s=1200)
    clips = splitter.split_script(draft.script, draft.characters, draft.settings)
    planned = sum(c.duration_s for c in clips)
    assert 840 <= planned <= 1560, f"planned {planned}s for a 1200s target"
    assert len(draft.script.scenes) > 60


def test_brain_defaults_to_grok_when_the_xai_key_exists(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_URL", raising=False)
    assert screenwriter.brain_url() == "https://api.x.ai"
    assert screenwriter.brain_model() == "grok-4.6"


def test_brain_off_when_url_is_explicitly_empty(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "")
    assert screenwriter.brain_url() == ""


def test_brain_call_carries_auth_and_json_mode(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_URL", raising=False)
    seen = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "not json at all"}}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["json"] = kwargs.get("json") or {}
        return _Response()

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    import pytest as _pytest

    with _pytest.raises(screenwriter.BrainUnavailable):
        screenwriter.write_script("a chase", runtime_target_s=60)
    assert seen["url"] == "https://api.x.ai/v1/chat/completions"
    assert seen["headers"].get("Authorization", "").startswith("Bearer ")
    assert seen["json"]["model"] == "grok-4.6"
    assert seen["json"]["response_format"] == {"type": "json_object"}


def test_brain_retries_once_before_falling_back(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    calls = {"n": 0}

    class _Response:
        status_code = 200

        def json(self):
            import json as _json

            body = {
                "logline": "second try wins",
                "beats": [{"beat": "b", "story_time": "Night 1"}],
                "settings": [{
                    "setting_id": "set-a", "name": "Pier", "kind": "EXT",
                    "room_tone": "waves",
                    "time_weather": "Night 1, sea fog",
                    "grade_language": "sodium lamps against black water",
                    "camera_kit": "28-40mm, waist height, slow drifts",
                }],
                "characters": [{
                    "char_id": "chr-a", "name": "AVA",
                    "wardrobe": "yellow oilskin over a navy sweater",
                }],
                "scenes": [{"heading": "EXT. PIER - NIGHT", "setting_ref": "set-a",
                            "story_time": "Night 1", "cast": ["chr-a"],
                            "lines": [{"kind": "action", "text": "AVA waits."}]}],
            }
            return {"choices": [{"message": {"content": _json.dumps(body)}}]}

    def flaky_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise screenwriter.httpx.HTTPError("transient timeout")
        return _Response()

    monkeypatch.setattr(screenwriter.httpx, "post", flaky_post)
    draft = screenwriter.write_script("a pier at night", runtime_target_s=45)
    assert calls["n"] == 2
    assert draft.script.logline == "second try wins"
    assert not draft.warnings


def test_template_only_exists_when_the_brain_is_off(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "")
    draft = screenwriter.write_script("a heist", runtime_target_s=60)
    assert draft.script.scenes
    assert any("template" in w for w in draft.warnings)


def test_brain_timeout_is_generous_and_overridable(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_TIMEOUT", raising=False)
    assert screenwriter.brain_timeout_s() == 600.0
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_TIMEOUT", "900")
    assert screenwriter.brain_timeout_s() == 900.0
