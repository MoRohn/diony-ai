"""An idea that contains the user's own scripted scene breakdown is AUTHORED
material: the enhancement realizes it — every scene covered, every narration
line verbatim — instead of re-deriving a smaller film from it (the 12-scene
"Companies That Never Get Built" collapse, 2026-09-02)."""

from __future__ import annotations

import json

from diony_director import master_prompt, screenwriter

AUTHORED_IDEA = (
    "Title: The Companies That Never Get Built\n"
    "Format: Short animation story\n"
    "Scene 1 — Quiet Success\n"
    "Visual: Night. You sit alone in a modest sedan at a red light in a clean business district.\n"
    "Narration: “You were told AI would create more opportunity. More products. More growth. More jobs.”\n"
    "Scene 2 — The Attractive Thesis\n"
    "Visual: Animated graphics appear above the skyline, boxes labeled growth and efficiency.\n"
    "Narration: “And on the surface, it makes sense. A company gets faster. Leaner. Smarter.”\n"
    "Narration (beat): “But abundance is not the same thing as access.”\n"
)


def test_authored_breakdown_parses_scenes_and_narration_in_order() -> None:
    authored = screenwriter.authored_breakdown(AUTHORED_IDEA)
    assert authored["is_authored"]
    assert authored["scene_count"] == 2
    assert authored["narration"] == [
        "You were told AI would create more opportunity. More products. More growth. More jobs.",
        "And on the surface, it makes sense. A company gets faster. Leaner. Smarter.",
        "But abundance is not the same thing as access.",
    ]
    blocks = authored["scenes"]
    assert len(blocks) == 2
    assert blocks[0]["title"] == "Quiet Success"
    assert blocks[0]["visual"].startswith("Night. You sit alone")
    assert len(blocks[1]["narration"]) == 2


def test_authored_breakdown_survives_a_collapsed_paste() -> None:
    # a chat paste can flatten newlines — numbered headers and quoted
    # narration still parse
    flattened = " ".join(AUTHORED_IDEA.split("\n"))
    authored = screenwriter.authored_breakdown(flattened)
    assert authored["is_authored"]
    assert authored["scene_count"] == 2
    assert len(authored["narration"]) == 3


def test_a_plain_idea_is_not_authored() -> None:
    authored = screenwriter.authored_breakdown(
        "a getaway driver leaves a small desert town at night, one last job behind her"
    )
    assert not authored["is_authored"]
    assert authored["narration"] == []


def test_the_authored_brief_reaches_the_brain_with_verbatim_narration(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")
    captured: dict = {}

    body = {
        "logline": "x",
        "beats": [{"beat": "b", "story_time": "Night 1"}],
        "settings": [{"setting_id": "set-1", "name": "Sedan", "kind": "INT", "room_tone": "idle"}],
        "characters": [{"char_id": "chr-n", "name": "NARRATOR"}],
        "scenes": [
            {
                "heading": "INT. SEDAN - NIGHT",
                "setting_ref": "set-1",
                "story_time": "Night 1",
                "cast": ["chr-n"],
                "lines": [
                    {"kind": "action", "text": "The light holds red.", "shot": "Wide"},
                    {
                        "kind": "vo",
                        "text": "You were told AI would create more opportunity. More products. More growth. More jobs.",
                        "speaker": "NARRATOR",
                        "shot": "Wide",
                    },
                    {
                        "kind": "vo",
                        "text": "And on the surface, it makes sense. A company gets faster. Leaner. Smarter.",
                        "speaker": "NARRATOR",
                        "shot": "Wide",
                    },
                    {
                        "kind": "vo",
                        "text": "But abundance is not the same thing as access.",
                        "speaker": "NARRATOR",
                        "shot": "Wide",
                    },
                ],
            }
        ],
    }

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _Response()

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    screenwriter.write_script(AUTHORED_IDEA, runtime_target_s=90)
    user_text = captured["json"]["messages"][1]["content"]
    assert "AUTHORED" in user_text
    assert "AUTHORED NARRATION" in user_text
    assert "But abundance is not the same thing as access." in user_text
    assert "VERBATIM" in user_text
    assert "Write approximately" not in user_text
    # the scene budget is explicit: 2 authored scenes over 90s
    assert "2 scenes" in user_text


def test_paraphrased_and_dropped_narration_is_restored_deterministically(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")

    body = {
        "logline": "x",
        "beats": [{"beat": "b", "story_time": "Night 1"}],
        "settings": [{"setting_id": "set-1", "name": "Sedan", "kind": "INT", "room_tone": "idle"}],
        "characters": [{"char_id": "chr-n", "name": "NARRATOR"}],
        "scenes": [
            {
                "heading": "INT. SEDAN - NIGHT",
                "setting_ref": "set-1",
                "story_time": "Night 1",
                "cast": ["chr-n"],
                "lines": [
                    {"kind": "action", "text": "The light holds red.", "shot": "Wide"},
                    # line 1 paraphrased, line 2 carried, line 3 dropped
                    {
                        "kind": "vo",
                        "text": "You were told AI would create more opportunity and more jobs.",
                        "speaker": "NARRATOR",
                        "shot": "Wide",
                    },
                    {
                        "kind": "vo",
                        "text": "And on the surface, it makes sense. A company gets faster. Leaner. Smarter.",
                        "speaker": "NARRATOR",
                        "shot": "Wide",
                    },
                ],
            }
        ],
    }

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    draft = screenwriter.write_script(AUTHORED_IDEA, runtime_target_s=90)
    spoken = [line.text for line in draft.script.lines if line.kind in {"vo", "dialogue"}]
    assert (
        "You were told AI would create more opportunity. More products. More growth. More jobs."
        in spoken
    )
    assert "But abundance is not the same thing as access." in spoken
    # the dropped line lands AFTER the line it follows in the authored order
    carried = spoken.index("And on the surface, it makes sense. A company gets faster. Leaner. Smarter.")
    assert spoken.index("But abundance is not the same thing as access.") == carried + 1
    assert any("restored" in w for w in draft.warnings)
    assert any("inserted" in w for w in draft.warnings)


def test_the_template_path_realizes_the_authored_breakdown() -> None:
    draft = screenwriter.write_script(AUTHORED_IDEA, runtime_target_s=90)
    assert len(draft.script.scenes) == 2
    spoken = [line.text for line in draft.script.lines if line.kind == "vo"]
    assert spoken == [
        "You were told AI would create more opportunity. More products. More growth. More jobs.",
        "And on the surface, it makes sense. A company gets faster. Leaner. Smarter.",
        "But abundance is not the same thing as access.",
    ]
    # the canned getaway template never overwrites a written film
    assert not any("we drive" in line.text.lower() for line in draft.script.lines)
    assert any("authored breakdown" in w for w in draft.warnings)


def test_the_master_derivation_names_the_authored_material() -> None:
    text = master_prompt.user_message(AUTHORED_IDEA, 90)
    assert "established material" in text
    plain = master_prompt.user_message("a man waits by a harbor", 6)
    assert "established material" not in plain


def test_the_restore_pass_never_speaks_a_line_twice(monkeypatch) -> None:
    """Live-brain evidence (2026-09-02): the brain split one authored line
    across two consecutive vo lines and left a paraphrase beside a fragment
    of another; the first restore pass overwrote one half and shipped the
    other, so the film spoke both sentences twice."""
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")

    body = {
        "logline": "x",
        "beats": [{"beat": "b", "story_time": "Night 1"}],
        "settings": [{"setting_id": "set-1", "name": "Sedan", "kind": "INT", "room_tone": "idle"}],
        "characters": [
            {"char_id": "chr-n", "name": "NARRATOR"},
            {"char_id": "chr-d", "name": "DRIVER"},
        ],
        "scenes": [
            {
                "heading": "INT. SEDAN - NIGHT",
                "setting_ref": "set-1",
                "story_time": "Night 1",
                "cast": ["chr-n", "chr-d"],
                "lines": [
                    {"kind": "action", "text": "The light holds red.", "shot": "Wide"},
                    # authored line 1 split across two consecutive vo lines
                    {"kind": "vo", "text": "You were told AI would create more opportunity.", "speaker": "NARRATOR", "shot": "Wide"},
                    {"kind": "vo", "text": "More products. More growth. More jobs.", "speaker": "NARRATOR", "shot": "Close"},
                    # authored line 2 paraphrased, then a fragment of it
                    {"kind": "vo", "text": "On the surface it makes sense.", "speaker": "NARRATOR", "shot": "Wide"},
                    {"kind": "vo", "text": "A company gets faster. Leaner. Smarter.", "speaker": "NARRATOR", "shot": "Medium"},
                    # a character's line that echoes the narration is the writer's — untouched
                    {"kind": "dialogue", "text": "Abundance is not access.", "speaker": "DRIVER", "shot": "Close"},
                    # authored line 3 dropped entirely
                ],
            }
        ],
    }

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    draft = screenwriter.write_script(AUTHORED_IDEA, runtime_target_s=90)
    vo = [line.text for line in draft.script.lines if line.kind == "vo"]
    assert vo == [
        "You were told AI would create more opportunity. More products. More growth. More jobs.",
        "And on the surface, it makes sense. A company gets faster. Leaner. Smarter.",
        "But abundance is not the same thing as access.",
    ]
    # the merged line keeps the first fragment's shot
    merged = next(l for l in draft.script.lines if l.text.startswith("You were told"))
    assert merged.shot == "Wide"
    dialogue = [line.text for line in draft.script.lines if line.kind == "dialogue"]
    assert dialogue == ["Abundance is not access."]
    assert any("dropped 1 duplicate" in w for w in draft.warnings)
