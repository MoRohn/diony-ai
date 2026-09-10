"""The likeness guard — a real person is described, never named.

2026-09-05: a truck-poster reference still; the idea named nobody; the brain
recognized the actor in the still and wrote his name into the master prompt
and all 26 clip prompts. Google Flow's prominent-people filter refuses that
before render. These tests pin the three layers: the brain is told, the
system enforces the brain's own confession, and an audit catches what the
brain wrote but did not confess — while brands, titles, and the user's own
invented characters are never touched.
"""

from __future__ import annotations

import json

import pytest
from master_brain_fakes import fast_master_derive, is_master_call, master_brain_body

from diony_contracts.director import CloudProvider
from diony_director import compiler, likeness, master_prompt, packagebuilder, screenwriter, session

ACTOR = "Jean-Claude Van Damme"
DESCRIPTOR = "the broad-shouldered man in sunglasses and a leather vest"
PERSON = {"name": ACTOR, "descriptor": DESCRIPTOR, "label": "The Splitter"}

# the post-split clip prompt from the screenshot, as the brain wrote it
LEAKED = (
    f"Create a video of {ACTOR} locked in a full split between two dark Volvo FM "
    "trucks on a sunlit highway. Use the reference image as the exact visual setting "
    "and starting composition. The main subject remains Van Damme, arms crossed, full "
    "side split. Small annotations PRECISION and CONTROL draw toward each truck. "
    "Maintain strict continuity throughout: Van Damme's face, vest, trousers, boots, "
    "sunglasses, crossed-arm split, both Volvo trucks, and left-side titles remain "
    "unchanged. Van Damme holds. The clip ends with a stable wide hold of Van Damme in "
    "full split."
)


def _people(*entries: dict) -> list[dict]:
    return likeness.normalize_people(list(entries), has_reference=True)


# --- the scrub -------------------------------------------------------------


def test_every_form_of_the_name_becomes_the_descriptor_and_nothing_else_moves() -> None:
    text, hits = likeness.scrub(LEAKED, _people(PERSON))
    assert "Van Damme" not in text and "Jean-Claude" not in text
    assert hits == 5
    assert text.startswith(f"Create a video of {DESCRIPTOR} locked in a full split")
    # the possessive keeps its 's
    assert f"{DESCRIPTOR}'s face" in text
    # a mention that opens a sentence is capitalized
    assert "The broad-shouldered man in sunglasses and a leather vest holds." in text
    # brands, on-screen titles, wardrobe, and the reference opener are untouched
    for kept in (
        "Volvo FM",
        "both Volvo trucks",
        "PRECISION and CONTROL",
        "Use the reference image",
    ):
        assert kept in text


def test_the_scrub_is_idempotent() -> None:
    once, _ = likeness.scrub(LEAKED, _people(PERSON))
    twice, hits = likeness.scrub(once, _people(PERSON))
    assert twice == once and hits == 0


def test_a_short_first_name_that_is_also_a_word_is_never_touched() -> None:
    people = _people({"name": "Will Smith", "descriptor": "the man in the grey suit"})
    text, hits = likeness.scrub(
        "Will Smith waits. Will he cross? He will. Smith turns. The smith hammers.", people
    )
    assert hits == 2
    assert text == (
        "The man in the grey suit waits. Will he cross? He will. The man in the grey "
        "suit turns. The smith hammers."
    )


def test_a_cue_line_in_caps_becomes_the_label_in_caps() -> None:
    text, _ = likeness.scrub("VAN DAMME\nHold it.", _people(PERSON))
    assert text == "THE SPLITTER\nHold it."
    label, changed = likeness.scrub_label("JEAN-CLAUDE VAN DAMME", _people(PERSON))
    assert changed and label == "THE SPLITTER"
    label, changed = likeness.scrub_label("MARA", _people(PERSON))
    assert not changed and label == "MARA"


def test_nicknames_the_brain_lists_are_aliases_and_a_descriptor_naming_the_person_is_refused() -> (
    None
):
    people = _people({**PERSON, "aliases": ["JCVD", "the Muscles from Brussels"]})
    text, hits = likeness.scrub("JCVD, the Muscles from Brussels, grins.", people)
    assert hits == 2 and "JCVD" not in text and "Brussels" not in text
    bad = _people({"name": ACTOR, "descriptor": "Van Damme in a vest"})
    assert bad[0]["descriptor"] == "the person from the reference image"
    assert likeness.default_label(DESCRIPTOR) == "The Broad-Shouldered Man"
    assert bad[0]["label"] == "The Person"


def test_the_same_person_from_two_brains_is_one_entry() -> None:
    people = likeness.merge(
        _people(PERSON), _people({"name": "Van Damme", "descriptor": "the man in the vest"})
    )
    assert len(people) == 1 and people[0]["name"] == ACTOR


# --- candidates ---------------------------------------------------------------


def test_candidates_are_capitalized_phrases_never_titles_or_sentence_openers() -> None:
    found = likeness.candidates([LEAKED])
    assert ACTOR in found and "Van Damme" in found and "Volvo FM" in found
    assert "Create" not in found and "PRECISION" not in found and "CONTROL" not in found
    # a known person's forms are no longer candidates
    assert not any("Damme" in c for c in likeness.candidates([LEAKED], _people(PERSON)))


# --- the master derivation ----------------------------------------------------


class _Brain:
    """A fake brain: the master reply names the actor everywhere; the audit
    reply is whatever the test hands it."""

    def __init__(self, master_body: dict, audit_reply: dict | Exception | None = None) -> None:
        self.master_body = master_body
        self.audit_reply = audit_reply
        self.calls: list[str] = []

    def __call__(self, url, **kwargs):
        payload = kwargs.get("json") or {}
        system = str(payload["messages"][0]["content"])
        brain = self

        class _Response:
            status_code = 200
            text = ""

            def json(self_inner) -> dict:
                if is_master_call(payload):
                    brain.calls.append("master")
                    return {"choices": [{"message": {"content": json.dumps(brain.master_body)}}]}
                assert system.startswith("LIKENESS AUDIT")
                brain.calls.append("audit")
                if isinstance(brain.audit_reply, Exception):
                    raise brain.audit_reply
                return {
                    "choices": [
                        {"message": {"content": json.dumps(brain.audit_reply or {"people": []})}}
                    ]
                }

        return _Response()


def _leaky_body(**overrides) -> dict:
    return master_brain_body(
        primary_subject=f"{ACTOR} in a full split",
        beats=[
            {"stage": "initial_state", "text": f"{ACTOR} holds the split between the cabs"},
            {"stage": "payoff", "text": "the camera pulls back to the full split"},
        ],
        continuity_anchors=["Van Damme's sunglasses", "both Volvo trucks"],
        ending=f"a stable wide hold of {ACTOR} in full split",
        prompt=LEAKED,
        clips=[
            {
                "prompt": LEAKED,
                "duration_s": 10,
                "narration": "",
                "scene_change": False,
                "opening": f"{ACTOR} in a split",
            },
            {
                "prompt": "The camera pulls back on Van Damme.",
                "duration_s": 10,
                "narration": "Van Damme holds.",
                "scene_change": False,
                "opening": "",
            },
        ],
        **overrides,
    )


def test_the_brain_is_told_the_law_and_asked_to_confess() -> None:
    text = master_prompt.system_prompt()
    assert "LIKENESS LAW" in text and "real_people" in text
    assert text.startswith("MASTER AI VIDEO PROMPT TEMPLATE")
    assert "LIKENESS LAW" in screenwriter._BRAIN_INSTRUCTIONS


def test_a_confessed_person_is_described_never_named_in_every_field(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    still = tmp_path / "poster.png"
    still.write_bytes(b"\x89PNG\r\n\x1a\n")
    brain = _Brain(_leaky_body(real_people=[PERSON]))
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive(
        "a man in a full split between two trucks",
        target_duration_s=20,
        reference_images=[str(still)],
        clip_plan=[10, 10],
    )
    doc = json.dumps(
        {k: v for k, v in master.model_dump(mode="json").items() if k != "real_people"}
    )
    # the record keeps the name; nothing a generator sees does
    assert master.real_people and master.real_people[0]["name"] == ACTOR
    for field in likeness.master_texts(master):
        assert "Damme" not in field and "Jean-Claude" not in field, field
    assert master.prompt.startswith(f"Create a video of {DESCRIPTOR}")
    assert (
        master.clips[1]["narration"]
        == "The broad-shouldered man in sunglasses and a leather vest holds."
    )
    assert master.clips[0]["opening"] == f"{DESCRIPTOR[:1].upper()}{DESCRIPTOR[1:]} in a split"
    assert f"{DESCRIPTOR}'s sunglasses" in master.continuity_anchors
    assert any("likeness guard" in w and "never named" in w for w in master.warnings)
    assert not any(ACTOR in w for w in master.warnings), "the note never repeats the name"
    # the audit still ran on what was left (Volvo FM) and found no one
    assert brain.calls == ["master", "audit"]
    assert "Damme" not in doc, "the name survives only in the guard's record"


def test_a_name_the_brain_wrote_but_did_not_confess_is_caught_by_the_audit(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    brain = _Brain(
        _leaky_body(),  # no real_people key at all
        audit_reply={
            "people": [{"name": "Van Damme", "descriptor": DESCRIPTOR, "label": "The Splitter"}]
        },
    )
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive("a man in a full split", target_duration_s=20, clip_plan=[10, 10])
    assert brain.calls == ["master", "audit"]
    for field in likeness.master_texts(master):
        assert "Damme" not in field and "Jean-Claude" not in field, field
    assert ACTOR in master.real_people[0]["aliases"], (
        "the full phrase joined the surname the audit named"
    )


def test_an_audit_that_fails_is_a_warning_never_a_failed_derivation(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    brain = _Brain(_leaky_body(real_people=[PERSON]), audit_reply=RuntimeError("boom"))
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive("a man in a full split", target_duration_s=20, clip_plan=[10, 10])
    assert "Damme" not in master.prompt
    assert any("likeness audit unavailable" in w for w in master.warnings)


def test_the_audit_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_LIKENESS_AUDIT", "0")
    brain = _Brain(_leaky_body(real_people=[PERSON]))
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive("a man in a full split", target_duration_s=20, clip_plan=[10, 10])
    assert brain.calls == ["master"] and "Damme" not in master.prompt


def test_a_film_with_no_real_people_is_byte_identical_and_names_no_guard(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    brain = _Brain(master_brain_body(real_people=[]))
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive("a keeper waits", target_duration_s=8)
    assert (
        master.prompt
        == master_prompt.optimize(
            master_prompt._parse(
                master_brain_body(), idea="a keeper waits", target_duration_s=8, has_reference=False
            )
        ).prompt
    )
    assert not master.real_people
    assert not any("likeness" in w for w in master.warnings)


# --- the compiled prompts -----------------------------------------------------


def test_the_shippable_body_and_a_chained_clip_hold_the_law(monkeypatch) -> None:
    people = _people(PERSON)
    for provider in (CloudProvider.GROK, CloudProvider.GOOGLE_FLOW):
        prompt = session.compile_master_body(
            LEAKED, "a man in a split", provider, "", narration="Van Damme holds.", people=people
        )
        assert "Damme" not in prompt and DESCRIPTOR in prompt


def test_a_revision_note_naming_the_person_reaches_the_provider_described() -> None:
    master = master_prompt.derive("a getaway driver leaves a neon diner", target_duration_s=60)
    master.real_people = _people(PERSON)
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner",
        runtime_target_s=60,
        master=master,
    )
    package = packagebuilder.build_package("s-likeness", "a getaway driver leaves town", draft)
    clip = package.clips[0]
    clip.add_prompt(compiler.compile_clip_prompt(clip, package))
    revised = compiler.revise_prompt(clip, package, note=f"{ACTOR} should smile as Van Damme turns")
    assert "Damme" not in revised.prompt and "Jean-Claude" not in revised.prompt
    assert f"{DESCRIPTOR} should smile" in revised.prompt


# --- the screenplay -----------------------------------------------------------


def test_a_real_person_cast_as_a_character_is_relabeled_and_every_speaker_follows(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")
    body = {
        "logline": f"{ACTOR} holds a split between two trucks.",
        "beats": [{"beat": f"{ACTOR} holds", "story_time": "Day 1"}],
        "settings": [
            {"setting_id": "set-hwy", "name": "Sunlit Highway", "kind": "EXT", "room_tone": "wind"}
        ],
        "characters": [
            {
                "char_id": "chr-jcvd",
                "name": "JEAN-CLAUDE VAN DAMME",
                "age_body_notes": "the actor's face",
            },
            {"char_id": "chr-mara", "name": "MARA"},
        ],
        "scenes": [
            {
                "heading": "EXT. SUNLIT HIGHWAY - DAY",
                "setting_ref": "set-hwy",
                "story_time": "Day 1",
                "cast": ["chr-jcvd", "chr-mara"],
                "lines": [
                    {
                        "kind": "action",
                        "text": "Van Damme lowers into the split.",
                        "shot": "Wide on Van Damme.",
                    },
                    {"kind": "dialogue", "text": "Hold it.", "speaker": "JEAN-CLAUDE VAN DAMME"},
                    {"kind": "dialogue", "text": "Holding.", "speaker": "MARA"},
                ],
            }
        ],
        "real_people": [PERSON],
    }

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    draft = screenwriter.write_script("a man in a split between two trucks", runtime_target_s=20)
    names = {c.name for c in draft.characters}
    assert names == {"THE SPLITTER", "MARA"}
    assert "Damme" not in draft.script.logline
    speakers = {line.speaker for line in draft.script.lines if line.speaker}
    assert speakers == {"THE SPLITTER", "MARA"}
    texts = " ".join(line.text + " " + line.shot for line in draft.script.lines)
    assert "Damme" not in texts and DESCRIPTOR in texts
    assert any("screenwriter: likeness guard" in w for w in draft.warnings)


def test_the_screenplay_inherits_the_master_derivations_people(monkeypatch) -> None:
    """The master saw the still and confessed; the screenwriter brain named
    the actor anyway and confessed nobody. The master's record still rules."""
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")
    monkeypatch.setenv("DIONY_LIKENESS_AUDIT", "0")
    master = fast_master_derive("a man in a split", target_duration_s=20)
    master.real_people = _people(PERSON)
    body = {
        "logline": "A split.",
        "beats": [{"beat": "hold", "story_time": "Day 1"}],
        "settings": [
            {"setting_id": "set-hwy", "name": "Highway", "kind": "EXT", "room_tone": "wind"}
        ],
        "characters": [{"char_id": "chr-1", "name": "VAN DAMME"}],
        "scenes": [
            {
                "heading": "EXT. HIGHWAY - DAY",
                "setting_ref": "set-hwy",
                "story_time": "Day 1",
                "cast": ["chr-1"],
                "lines": [{"kind": "action", "text": f"{ACTOR} waits."}],
            }
        ],
    }

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    draft = screenwriter.write_script("a man in a split", runtime_target_s=20, master=master)
    assert draft.characters[0].name == "THE SPLITTER"
    assert (
        draft.script.lines[1].text
        == "The broad-shouldered man in sunglasses and a leather vest waits."
    )


@pytest.mark.parametrize(
    "name,surname,first",
    [
        ("Jean-Claude Van Damme", "Van Damme", "Jean-Claude"),
        ("Robert De Niro", "De Niro", "Robert"),
        ("Dwayne Johnson", "Johnson", "Dwayne"),
        ("Will Smith", "Smith", None),
    ],
)
def test_surname_groups_keep_their_particles(name, surname, first) -> None:
    aliases = likeness.normalize_people([{"name": name, "descriptor": "the man"}])[0]["aliases"]
    assert name in aliases and surname in aliases
    if first:
        assert first in aliases
    else:
        assert "Will" not in aliases


# --- names beside a picture (2026-09-07) -----------------------------------------
#
# s-9c2f04d993: a fictional candidate, "Grant Merriweather", and an AI-drawn
# poster of him as the reference still — nobody real, real_people empty — and
# Google Flow refused clip 1 three times (PROMINENT_PERSON). Five live probes
# on omni-flash: the prompt alone rendered; the poster with a neutral prompt
# rendered; the prompt naming him WITH the poster was refused; the same prompt
# with "the candidate" in place of the name rendered beside the poster; and a
# spoken line saying his name beside the poster rendered. So the rule under
# test: on the Google lane a prompt that rides beside a picture names no one —
# real or invented — outside quoted speech; without a picture the names stay.

CANDIDATE = {
    "name": "Grant Merriweather",
    "descriptor": "the silver-haired candidate in the pale blue Oxford",
    "label": "The Candidate",
}
PICTURED_PROMPT = (
    "Use the reference image as the exact visual setting. Preserve Grant Merriweather's "
    "face, silver-brown hair, and pale blue Oxford. Grant Merriweather, 58, athletic build, "
    "stands at the fence at sunrise. Merriweather holds the rail. Spoken aloud on this "
    "clip's track, exactly these words: \"I'm Grant Merriweather, and I approve this "
    'message." No on-screen text.'
)


def test_the_brain_is_asked_to_confess_its_named_characters_too() -> None:
    text = master_prompt.system_prompt()
    assert "NAMED CHARACTERS" in text and "characters (list of" in text
    assert "beside a reference picture" in likeness.BRAIN_LAW
    assert '"characters"' in likeness.AUDIT_SYSTEM


def test_a_pictured_prompt_describes_every_named_person_but_leaves_speech_verbatim() -> None:
    people = likeness.normalize_people([CANDIDATE], has_reference=True)
    out, hits = likeness.scrub_pictured(PICTURED_PROMPT, people)
    assert hits == 3
    assert "Preserve the silver-haired candidate in the pale blue Oxford's face" in out
    assert out.count("The silver-haired candidate in the pale blue Oxford, 58") == 1
    assert "The silver-haired candidate in the pale blue Oxford holds the rail." in out
    # the spoken line is the user's own words and rendered live beside the poster
    assert '"I\'m Grant Merriweather, and I approve this message."' in out
    assert "Merriweather" not in out.replace(
        "I'm Grant Merriweather, and I approve this message.", ""
    )
    again, more = likeness.scrub_pictured(out, people)
    assert again == out and more == 0
    note = likeness.pictured_note(hits, people)
    assert "3 mentions" in note and CANDIDATE["descriptor"] in note and "Merriweather" not in note


def test_a_confessed_character_is_kept_on_the_artifact_and_a_real_person_never_doubles(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    still = tmp_path / "poster.png"
    still.write_bytes(b"\x89PNG\r\n\x1a\n")
    body = master_brain_body(
        primary_subject="Grant Merriweather, 58, silver-brown hair, pale blue shirt",
        prompt="Create a video of Grant Merriweather at a fence at sunrise. The clip ends with him at the fence.",
        real_people=[PERSON],
        characters=[CANDIDATE, {"name": ACTOR, "descriptor": "a man"}],
    )
    brain = _Brain(body)
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive(
        "a campaign ad", target_duration_s=8, reference_images=[str(still)]
    )
    # the real person stays a real person; the invented one is a character
    assert [p["name"] for p in master.real_people] == [ACTOR]
    assert [c["name"] for c in master.characters] == ["Grant Merriweather"]
    assert master.characters[0]["descriptor"] == CANDIDATE["descriptor"]
    # names of characters are welcome on the artifact — nothing pictured yet
    assert "Grant Merriweather" in master.prompt
    # a confessed character is no candidate: nothing was left to audit
    assert brain.calls == ["master"]
    restored = master_prompt.from_doc(master_prompt.to_doc(master))
    assert restored.characters == master.characters


def test_the_audit_also_catches_a_named_character_the_brain_did_not_confess(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body(
        prompt="Create a video of Grant Merriweather at a fence at sunrise. The clip ends with him at the fence."
    )
    brain = _Brain(body, audit_reply={"people": [], "characters": [CANDIDATE]})
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive("a campaign ad", target_duration_s=8)
    assert brain.calls == ["master", "audit"]
    assert master.real_people == []
    assert [c["name"] for c in master.characters] == ["Grant Merriweather"]
    assert "Grant Merriweather" in master.prompt  # unpictured: the name stays


def test_a_screenplays_cast_is_known_by_name_with_the_role_as_descriptor() -> None:
    from diony_contracts.production import CharacterProfile

    cast = [
        CharacterProfile(char_id="c1", name="Grant Merriweather", role="the candidate"),
        CharacterProfile(
            char_id="c2",
            name="Tom",
            role="Tom's neighbor",
            age_body_notes="a weathered farmer in his sixties, broad hands",
        ),
        CharacterProfile(char_id="c3", name="", role="nobody"),
    ]
    people = likeness.people_from_profiles(cast)
    assert [p["name"] for p in people] == ["Grant Merriweather", "Tom"]
    assert people[0]["descriptor"] == "the candidate"
    # a role that names the character falls back to the body notes, as a definite phrase
    assert people[1]["descriptor"] == "the weathered farmer in his sixties"


def _flow_lane(monkeypatch):
    """A Google lane that prints instantly and records every prompt it was
    sent with the refs beside it; jobs named in `refuse` are moderated."""
    import hashlib

    from diony_director.providers import base, flow

    sent: dict = {"videos": [], "refuse": set()}
    counter = {"n": 0}

    def fake_submit(op):
        counter["n"] += 1
        sent["videos"].append(
            {
                "op_id": op.id,
                "prompt": op.prompt,
                "refs": dict(op.artifact_refs),
                "ext": f"vid-{counter['n']}",
            }
        )
        return base.CloudJobHandle(
            provider_id="google-flow",
            external_id=f"vid-{counter['n']}",
            model=op.model,
            duration_s=int(op.duration_s),
            mode="t2v",
        )

    def fake_poll(handle):
        if handle.external_id in sent["refuse"]:
            from diony_director.providers import flow as flow_mod

            return base.CloudJobStatus(
                state="moderated",
                detail=flow_mod._FILTER_REASONS["PROMINENT_PERSON"] + " [PROMINENT_PERSON]",
            )
        return base.CloudJobStatus(
            state="done",
            result_url=f"https://flow/{handle.external_id}",
            metadata={"media_generation_id": f"user:{handle.external_id}"},
        )

    def fake_fetch(handle, status, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = b"mp4" * 2048
        dest.write_bytes(payload)
        return base.FetchedClip(
            path=dest,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            media_url=status.result_url,
        )

    monkeypatch.setattr(flow, "submit", fake_submit)
    monkeypatch.setattr(flow, "poll", fake_poll)
    monkeypatch.setattr(flow, "fetch", fake_fetch)
    return sent


def _candidate_brain(monkeypatch):
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body(
        primary_subject="Grant Merriweather, 58, silver-brown hair, pale blue Oxford",
        prompt=PICTURED_PROMPT,
        narration="",
        characters=[CANDIDATE],
    )
    brain = _Brain(body)
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    return brain


def _sync_submitter(monkeypatch):
    from diony_director import app as app_module

    def kick(sid):
        for _ in range(6):
            submitted, held = app_module._submit_ready(sid)
            session.log_held(sid, held)
            if not submitted:
                break

    monkeypatch.setattr(app_module, "_kick_submitter", kick)


def test_the_submit_gate_describes_names_beside_an_uploaded_still_and_keeps_them_without_one(
    monkeypatch,
) -> None:
    import base64

    from fastapi.testclient import TestClient

    from diony_director import app as app_module

    _candidate_brain(monkeypatch)
    sent = _flow_lane(monkeypatch)
    _sync_submitter(monkeypatch)
    monkeypatch.setattr(app_module, "_wait_for_reviews", lambda *a, **k: None, raising=False)
    client = TestClient(app_module.app)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24).decode()

    # pictured: the poster rides as a reference → the prompt names no one
    sid = client.post("/api/director/sessions").json()["session_id"]
    still = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "MERRIWEATHER.png", "data_base64": png},
    ).json()["upload"]
    body = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "a campaign ad", "provider": "google-flow", "duration_s": 8},
    ).json()
    op = session.get_op((body.get("ops") or [body["op"]])[0]["id"])
    for _ in range(200):
        if op.status != "deriving":
            break
        __import__("time").sleep(0.05)
    assert op.status == "proposed" and "Grant Merriweather" in op.prompt
    assert client.post(f"/api/director/ops/{op.id}/run").status_code == 200
    shipped = sent["videos"][-1]
    assert shipped["op_id"] == op.id and still["path"] in shipped["refs"]["references"]
    assert "Merriweather" not in shipped["prompt"].replace(
        "I'm Grant Merriweather, and I approve this message.", ""
    )
    assert CANDIDATE["descriptor"] in shipped["prompt"]
    assert '"I\'m Grant Merriweather, and I approve this message."' in shipped["prompt"]
    # the card shows what was sent, and says why
    assert session.get_op(op.id).prompt == shipped["prompt"]
    assert session.PICTURED_GUARD_MARK in session.get_op(op.id).rationale
    assert session.runtime(op.id)["pictured_guard"]["hits"] == 3

    # unpictured: the same film without a still keeps its character's name
    sid2 = client.post("/api/director/sessions").json()["session_id"]
    body = client.post(
        f"/api/director/sessions/{sid2}/message",
        json={"text": "a campaign ad", "provider": "google-flow", "duration_s": 8},
    ).json()
    op2 = session.get_op((body.get("ops") or [body["op"]])[0]["id"])
    for _ in range(200):
        if op2.status != "deriving":
            break
        __import__("time").sleep(0.05)
    assert client.post(f"/api/director/ops/{op2.id}/run").status_code == 200
    assert "Grant Merriweather" in sent["videos"][-1]["prompt"]
    assert session.PICTURED_GUARD_MARK not in session.get_op(op2.id).rationale


def test_retry_sets_the_picture_aside_when_a_name_clean_prompt_is_still_refused(
    monkeypatch,
) -> None:
    import base64

    from fastapi.testclient import TestClient

    from diony_director import app as app_module

    _candidate_brain(monkeypatch)
    sent = _flow_lane(monkeypatch)
    _sync_submitter(monkeypatch)
    client = TestClient(app_module.app)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24).decode()
    sid = client.post("/api/director/sessions").json()["session_id"]
    still = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "MERRIWEATHER.png", "data_base64": png},
    ).json()["upload"]
    body = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "a campaign ad", "provider": "google-flow", "duration_s": 8},
    ).json()
    op = session.get_op((body.get("ops") or [body["op"]])[0]["id"])
    for _ in range(200):
        if op.status != "deriving":
            break
        __import__("time").sleep(0.05)
    sent["refuse"].add("vid-1")
    assert client.post(f"/api/director/ops/{op.id}/run").status_code == 200
    assert client.get(f"/api/director/ops/{op.id}").json()["op"]["status"] == "failed"
    assert "Merriweather" not in sent["videos"][0]["prompt"].replace(
        "I'm Grant Merriweather, and I approve this message.", ""
    )
    # the opener has no frame to redraw; the picture is set aside instead
    assert app_module._plan_frame_redraw(op, None) == (False, False, "")
    res = client.post(f"/api/director/ops/{op.id}/retry")
    assert res.status_code == 200
    reply = res.json()
    assert reply["redraw"] is None
    assert reply["set_aside"]["pictures"] == [still["path"]]
    assert "named no one" in reply["set_aside"]["note"]
    assert client.get(f"/api/director/ops/{op.id}").json()["op"]["status"] == "completed"
    second = sent["videos"][1]
    assert "references" not in second["refs"]
    assert "picture set aside" in session.get_op(op.id).rationale
    # the set-aside survives a restart and still holds at the next submit
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    assert session.pictures_set_aside(session.get_op(op.id))
    # an op refused before the guard existed, in a room that knows names to
    # describe, retries through the guard first — not set aside yet
    fresh = session.get_op(op.id)
    fresh.status = "failed"
    session.runtime(fresh.id).pop("pictured_guard", None)
    session.runtime(fresh.id).pop("pictures_set_aside", None)
    fresh.artifact_refs["references"] = [still["path"]]
    assert session.pictured_people(fresh)
    assert app_module._plan_picture_setaside(fresh) == (False, "")
    # …but a room that knows no names has nothing to scrub: the picture is
    # the only lever, so the first Retry sets it aside (the s-9c2f04d993 case)
    session._MASTERS[fresh.id].characters = []
    assert not session.pictured_people(fresh)
    assert app_module._plan_picture_setaside(fresh)[0] is True


def test_an_artifact_derived_before_the_characters_contract_is_audited_once_at_the_first_pictured_submit(
    monkeypatch,
) -> None:
    """s-9c2f04d993's master knew no characters (an older build derived it).
    At the first pictured Flow submit the brain reads the artifact once, the
    character it finds is described in the prompt, and the artifact remembers
    it was audited — the next pictured submit asks nothing."""
    import base64

    from fastapi.testclient import TestClient

    from diony_director import app as app_module

    brain = _candidate_brain(monkeypatch)
    brain.master_body = master_brain_body(
        primary_subject="Grant Merriweather, 58, silver-brown hair, pale blue shirt",
        prompt=PICTURED_PROMPT,
        narration="",
    )  # no characters confessed
    brain.audit_reply = {"people": [], "characters": [CANDIDATE]}
    monkeypatch.setenv("DIONY_LIKENESS_AUDIT", "0")  # derivation audit off: the old-build shape
    sent = _flow_lane(monkeypatch)
    _sync_submitter(monkeypatch)
    client = TestClient(app_module.app)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24).decode()
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "MERRIWEATHER.png", "data_base64": png},
    )
    body = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": "a campaign ad", "provider": "google-flow", "duration_s": 8},
    ).json()
    op = session.get_op((body.get("ops") or [body["op"]])[0]["id"])
    for _ in range(200):
        if op.status != "deriving":
            break
        __import__("time").sleep(0.05)
    master = session.master_for_op(op.id)
    assert master.characters == [] and master.characters_audited is False
    assert brain.calls == ["master"]
    monkeypatch.setenv("DIONY_LIKENESS_AUDIT", "1")  # the new build boots with it on
    assert client.post(f"/api/director/ops/{op.id}/run").status_code == 200
    assert brain.calls == ["master", "audit"]
    shipped = sent["videos"][-1]["prompt"]
    assert "Merriweather" not in shipped.replace(
        "I'm Grant Merriweather, and I approve this message.", ""
    )
    assert CANDIDATE["descriptor"] in shipped
    assert master.characters_audited is True and [c["name"] for c in master.characters] == [
        "Grant Merriweather"
    ]
    # the audit survives a restart with the artifact
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    restored = session.master_for_op(op.id)
    assert (
        restored.characters_audited is True
        and restored.characters[0]["name"] == "Grant Merriweather"
    )
    # a brain that fails at the gate never blocks the submit
    restored.characters_audited = False
    restored.characters = []
    brain.audit_reply = RuntimeError("brain down")
    op2 = session.get_op(op.id)
    op2.status = "failed"
    session.record_failure(op2, "failed", "the provider job died mid-render")
    assert client.post(f"/api/director/ops/{op2.id}/retry").status_code == 200
    assert "audit unavailable" in session.get_op(op.id).rationale
    assert "Grant Merriweather" in sent["videos"][-1]["prompt"]
    assert restored.characters_audited is False
