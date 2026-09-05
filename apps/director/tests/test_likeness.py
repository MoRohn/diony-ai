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
    for kept in ("Volvo FM", "both Volvo trucks", "PRECISION and CONTROL", "Use the reference image"):
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


def test_nicknames_the_brain_lists_are_aliases_and_a_descriptor_naming_the_person_is_refused() -> None:
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
                return {"choices": [{"message": {"content": json.dumps(brain.audit_reply or {"people": []})}}]}

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
            {"prompt": LEAKED, "duration_s": 10, "narration": "", "scene_change": False, "opening": f"{ACTOR} in a split"},
            {"prompt": "The camera pulls back on Van Damme.", "duration_s": 10, "narration": "Van Damme holds.", "scene_change": False, "opening": ""},
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
        "a man in a full split between two trucks", target_duration_s=20,
        reference_images=[str(still)], clip_plan=[10, 10],
    )
    doc = json.dumps({k: v for k, v in master.model_dump(mode="json").items() if k != "real_people"})
    # the record keeps the name; nothing a generator sees does
    assert master.real_people and master.real_people[0]["name"] == ACTOR
    for field in likeness.master_texts(master):
        assert "Damme" not in field and "Jean-Claude" not in field, field
    assert master.prompt.startswith(f"Create a video of {DESCRIPTOR}")
    assert master.clips[1]["narration"] == "The broad-shouldered man in sunglasses and a leather vest holds."
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
        audit_reply={"people": [{"name": "Van Damme", "descriptor": DESCRIPTOR, "label": "The Splitter"}]},
    )
    monkeypatch.setattr(screenwriter.httpx, "post", brain)
    master = master_prompt.derive("a man in a full split", target_duration_s=20, clip_plan=[10, 10])
    assert brain.calls == ["master", "audit"]
    for field in likeness.master_texts(master):
        assert "Damme" not in field and "Jean-Claude" not in field, field
    assert ACTOR in master.real_people[0]["aliases"], "the full phrase joined the surname the audit named"


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
    assert master.prompt == master_prompt.optimize(
        master_prompt._parse(master_brain_body(), idea="a keeper waits", target_duration_s=8, has_reference=False)
    ).prompt
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
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60, master=master
    )
    package = packagebuilder.build_package("s-likeness", "a getaway driver leaves town", draft)
    clip = package.clips[0]
    clip.add_prompt(compiler.compile_clip_prompt(clip, package))
    revised = compiler.revise_prompt(clip, package, note=f"{ACTOR} should smile as Van Damme turns")
    assert "Damme" not in revised.prompt and "Jean-Claude" not in revised.prompt
    assert f"{DESCRIPTOR} should smile" in revised.prompt


# --- the screenplay -----------------------------------------------------------


def test_a_real_person_cast_as_a_character_is_relabeled_and_every_speaker_follows(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_REFINE", "0")
    body = {
        "logline": f"{ACTOR} holds a split between two trucks.",
        "beats": [{"beat": f"{ACTOR} holds", "story_time": "Day 1"}],
        "settings": [{"setting_id": "set-hwy", "name": "Sunlit Highway", "kind": "EXT", "room_tone": "wind"}],
        "characters": [
            {"char_id": "chr-jcvd", "name": "JEAN-CLAUDE VAN DAMME", "age_body_notes": "the actor's face"},
            {"char_id": "chr-mara", "name": "MARA"},
        ],
        "scenes": [
            {
                "heading": "EXT. SUNLIT HIGHWAY - DAY",
                "setting_ref": "set-hwy",
                "story_time": "Day 1",
                "cast": ["chr-jcvd", "chr-mara"],
                "lines": [
                    {"kind": "action", "text": "Van Damme lowers into the split.", "shot": "Wide on Van Damme."},
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
        "settings": [{"setting_id": "set-hwy", "name": "Highway", "kind": "EXT", "room_tone": "wind"}],
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
    assert draft.script.lines[1].text == "The broad-shouldered man in sunglasses and a leather vest waits."


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
