"""Blueprint 28 — post-assembly narration: cue placement, the duck, loud failures."""

import shutil
import subprocess
from pathlib import Path

import pytest

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    CostEstimateLine,
    DirectorOp,
    DirectorOpType,
)
from diony_director import narration

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _op(op_id: str, *, narr: str = "") -> DirectorOp:
    return DirectorOp(
        id=op_id,
        session_id="s-n",
        op=DirectorOpType.GENERATE,
        provider=CloudProvider.GROK,
        model="grok-imagine-video",
        mode=CloudGenerationMode.T2V,
        prompt="a held cinematic moment",
        narration=narr,
        duration_s=6,
        estimate=CostEstimate(
            id="est-n",
            workspace_id="ws",
            lines=[
                CostEstimateLine(
                    label="gen", provider_id="grok", model="grok-imagine-video",
                    unit="clip", usd_micros=1000,
                )
            ],
        ),
    )


class _Clip:
    """The two fields cue_sheet reads off a scripted ClipUnit."""

    def __init__(self, clip_id, line, speaker="ANA", on_camera=True):
        self.clip_id = clip_id
        self.clip_class = "sync-dialogue"

        class _Packet:
            script_excerpt = [{"speaker": speaker, "line": line, "parenthetical": "low"}]
            voice_ref = "voice-ana"
            delivery = {"pace": "unhurried"}

        self.audio_packet = _Packet()


def test_cue_sheet_places_lines_on_the_real_cut_timeline() -> None:
    entries = [
        {"op": _op("op-1"), "clip": _Clip("cl-1", "We finish this tonight."), "window": (0.0, 4.0)},
        {"op": _op("op-2"), "clip": None, "window": (0.5, 3.0)},
        {"op": _op("op-3"), "clip": _Clip("cl-3", "Then we are gone."), "window": (0.0, 5.0)},
    ]
    cues = narration.cue_sheet(entries)
    assert [c.clip_id for c in cues] == ["cl-1", "cl-3"]
    assert cues[0].start_s == pytest.approx(narration.CUE_LEAD_IN_S)
    # the second line lands after BOTH earlier windows — placement is the
    # assembled cut's arithmetic, never the provider's booked lengths
    assert cues[1].start_s == pytest.approx(4.0 + 3.0 + narration.CUE_LEAD_IN_S)
    assert cues[0].voice_ref == "voice-ana"
    assert cues[0].line == "We finish this tonight."


def test_chain_links_contribute_their_split_narration() -> None:
    # a chained one-shot has no ClipUnit — its narration rides the op,
    # split there by the master derivation (Blueprint 28)
    entries = [
        {"op": _op("op-a", narr="The city sleeps."), "clip": None, "window": (0.0, 8.0)},
        {"op": _op("op-b"), "clip": None, "window": (0.0, 8.0)},
        {"op": _op("op-c", narr="It will not wake."), "clip": None, "window": (0.0, 4.0)},
    ]
    cues = narration.cue_sheet(entries)
    assert [c.clip_id for c in cues] == ["op-a", "op-c"]
    assert cues[1].start_s == pytest.approx(16.0 + narration.CUE_LEAD_IN_S)
    assert cues[1].speaker == ""


def test_build_filter_ducks_the_location_track_under_the_voice() -> None:
    chains = narration.build_filter(
        2, [150, 4150], film_duration_s=12.0, loudness_target="streaming"
    )
    graph = ";".join(chains)
    assert "sidechaincompress" in graph
    assert "adelay=150:all=1" in graph and "adelay=4150:all=1" in graph
    assert f"loudnorm=I={narration.NARRATION_LOUDNESS}" in graph
    # the program relocks to the delivery target AFTER the voice joins
    assert graph.rstrip("[aout]").endswith("loudnorm=I=-16:TP=-1.0:LRA=11,aresample=48000")
    assert "amix=inputs=2:duration=first:normalize=0" in graph


def test_the_narrator_is_cast_from_tone_and_character(monkeypatch) -> None:
    monkeypatch.delenv(narration.GROK_VOICE_ENV, raising=False)
    monkeypatch.delenv(narration.GROK_VOICE_ROSTER_ENV, raising=False)
    # a tense noir production spoken by a man reads as Sal, not the old
    # hardcoded default
    voice, why = narration.cast_voice(
        speaker="ELIAS",
        tone_text="tense noir, night streets, cinematic realism",
        character_notes="a weathered man in his fifties",
    )
    assert voice == "sal"
    assert "male read" in why and "noir" in why
    # a warm playful piece with a female lead reads as Ara
    voice, why = narration.cast_voice(
        speaker="MARA",
        tone_text="playful upbeat morning energy",
        character_notes="a young woman, quick to smile",
    )
    assert voice == "ara"
    assert "female read" in why


def test_casting_is_stable_per_speaker_and_respects_the_roster(monkeypatch) -> None:
    monkeypatch.delenv(narration.GROK_VOICE_ROSTER_ENV, raising=False)
    first, _ = narration.cast_voice(speaker="NARRATOR", tone_text="", character_notes="")
    second, _ = narration.cast_voice(speaker="NARRATOR", tone_text="", character_notes="")
    assert first == second, "one production casts one voice per speaker"
    assert first in narration.DEFAULT_ROSTER
    # the live roster is env-overridable without a code change
    monkeypatch.setenv(narration.GROK_VOICE_ROSTER_ENV, '{"Nova": ["calm"]}')
    voice, _ = narration.cast_voice(speaker="NARRATOR", tone_text="calm", character_notes="")
    assert voice == "Nova"


def test_locks_and_the_site_pin_outrank_casting(monkeypatch) -> None:
    monkeypatch.delenv(narration.GROK_VOICE_ROSTER_ENV, raising=False)
    locked = narration.NarrationCue(
        order=0, clip_id="cl-1", speaker="ANA", line="hello", voice_ref="voice-ana"
    )
    open_cue = narration.NarrationCue(order=1, clip_id="cl-2", speaker="BEN", line="hi")
    monkeypatch.setenv(narration.GROK_VOICE_ENV, "Rex")
    narration.cast_cues([locked, open_cue], tone_text="tense")
    assert locked.voice_ref == "voice-ana" and locked.cast_note == "authored voice lock"
    assert open_cue.voice_ref == "Rex" and "site-pinned" in open_cue.cast_note

    monkeypatch.delenv(narration.GROK_VOICE_ENV, raising=False)
    fresh = narration.NarrationCue(order=2, clip_id="cl-3", speaker="BEN", line="hi")
    narration.cast_cues(
        [fresh], tone_text="tense noir", character_notes={"Ben": "a quiet man"}
    )
    assert fresh.voice_ref == "sal"
    assert fresh.cast_note.startswith("cast for:")


def test_direction_becomes_performed_speech_tags(monkeypatch) -> None:
    monkeypatch.delenv(narration.NARRATOR_TAGS_ENV, raising=False)
    # a whisper wraps the verbatim words; a beat and a laugh lead them
    assert (
        narration.perform_line("It is a secret.", "whispering")
        == "<whisper>It is a secret.</whisper>"
    )
    performed = narration.perform_line("I could not believe it.", "a beat, laughing")
    assert performed == "[pause] [laugh] I could not believe it."
    # 'a long pause' wins over the bare 'pause' keyword
    assert narration.perform_line("Go.", "a long pause").startswith("[long-pause]")
    # an unmapped direction performs nothing — it already shaped the casting
    assert narration.perform_line("We drive at dawn.", "cold; urgent") == "We drive at dawn."
    # the map extends by env without a code change
    monkeypatch.setenv(narration.NARRATOR_TAGS_ENV, '{"shouting": "[shout]"}')
    assert narration.perform_line("Now!", "shouting") == "[shout] Now!"


def test_the_performed_line_reaches_grok_and_the_words_stay_verbatim(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv(narration.NARRATOR_ENV, raising=False)
    monkeypatch.delenv(narration.BACKEND_ENV, raising=False)
    monkeypatch.delenv(narration.NARRATOR_TAGS_ENV, raising=False)
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    seen: dict = {}

    class _Response:
        status_code = 200
        content = b"RIFFfakewav"
        text = ""
        headers = {"content-type": "audio/wav"}

    from diony_director.providers import base as provider_base

    monkeypatch.setattr(
        provider_base,
        "post_with_retries",
        lambda url, *, what, json, headers, timeout: seen.update({"json": json}) or _Response(),
    )
    cue = narration.NarrationCue(
        order=0, clip_id="cl-1", speaker="ANA", line="It is a secret.",
        direction="whispering", voice_ref="eve",
    )
    narration._synthesize_grok(cue, tmp_path / "cue.wav")
    assert seen["json"]["text"] == "<whisper>It is a secret.</whisper>"
    assert cue.performed == seen["json"]["text"]
    assert "It is a secret." in cue.performed, "the authored words ride verbatim"
    assert cue.line == "It is a secret.", "the line itself is never rewritten"


def test_no_backend_is_a_named_state(monkeypatch) -> None:
    monkeypatch.delenv(narration.NARRATOR_ENV, raising=False)
    monkeypatch.delenv(narration.BACKEND_ENV, raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert narration.backend() == ""
    assert narration.narrator_configured() is False
    cue = narration.NarrationCue(order=0, clip_id="cl-1", speaker="ANA", line="hello")
    with pytest.raises(narration.NarrationUnavailable):
        narration.synthesize(cue, Path("/tmp/never-written.wav"))


def test_grok_voice_is_the_default_backend_and_speaks_the_exact_words(
    tmp_path, monkeypatch
) -> None:
    # the xAI key alone selects the Grok voice API (the user's optimal
    # default); a cue's voice lock picks the voice, and the payload carries
    # the authored words verbatim
    monkeypatch.delenv(narration.NARRATOR_ENV, raising=False)
    monkeypatch.delenv(narration.BACKEND_ENV, raising=False)
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    assert narration.backend() == "grok"
    assert narration.narrator_configured() is True

    seen: dict = {}

    class _Response:
        status_code = 200
        content = b"RIFFfakewav"
        text = ""
        headers = {"content-type": "audio/wav"}

    from diony_director.providers import base as provider_base

    def _post(url, *, what, json, headers, timeout):
        seen.update({"url": url, "json": json, "headers": headers})
        return _Response()

    monkeypatch.setattr(provider_base, "post_with_retries", _post)
    cue = narration.NarrationCue(
        order=0, clip_id="cl-1", speaker="ANA", line="We finish this tonight.",
        direction="low", voice_ref="Sal",
    )
    out = tmp_path / "cue-00.wav"
    narration._synthesize_grok(cue, out)
    assert out.read_bytes() == b"RIFFfakewav"
    # the live /v1/tts contract, verified 2026-09-02: text + lowercase
    # voice_id + language + a WAV output_format at the mix's 48 kHz
    assert seen["url"].endswith(narration.GROK_VOICE_PATH_DEFAULT)
    assert seen["json"]["text"] == "We finish this tonight."
    assert seen["json"]["voice_id"] == "sal"
    assert seen["json"]["language"] == narration.GROK_VOICE_LANGUAGE_DEFAULT
    assert seen["json"]["output_format"] == {"codec": "wav", "sample_rate": 48000}
    assert seen["headers"]["Authorization"] == "Bearer test-xai-key"


def test_an_authored_cli_template_outranks_the_key_default(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-xai-key")
    monkeypatch.setenv(narration.NARRATOR_ENV, "say {text} -o {output}")
    monkeypatch.delenv(narration.BACKEND_ENV, raising=False)
    assert narration.backend() == "cli"
    monkeypatch.setenv(narration.BACKEND_ENV, "grok")
    assert narration.backend() == "grok"


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_narrate_mixes_the_voice_into_the_master(tmp_path, monkeypatch, fake_narrator) -> None:
    ffmpeg = shutil.which("ffmpeg")
    film = tmp_path / "film.mp4"
    subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=110:duration=3",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(film),
        ],
        check=True,
        capture_output=True,
    )
    cues = [
        narration.NarrationCue(
            order=0, clip_id="cl-1", speaker="ANA", line="We finish this tonight.",
            start_s=0.15, window_s=3.0,
        )
    ]
    dest = tmp_path / "film-narrated.mp4"
    report = narration.narrate(film, cues, dest, loudness_target="streaming")
    assert dest.is_file()
    assert report.overruns == []
    assert report.cues[0]["duration_s"] > 0.5
    from diony_director.assembly import _probe

    probe = _probe(dest)
    assert probe["has_audio"]
    assert probe["duration_s"] == pytest.approx(3.0, abs=0.4)


def test_an_overrunning_cue_never_overlaps_the_next_one() -> None:
    # cl-1's line speaks for 4.0s inside a 2.0s clip — without a layout pass
    # cl-2's cue starts while cl-1's is still talking: two voices at once
    cues = [
        narration.NarrationCue(order=0, clip_id="cl-1", speaker="ANA", line="a", start_s=0.15, window_s=2.0),
        narration.NarrationCue(order=1, clip_id="cl-2", speaker="BEN", line="b", start_s=2.15, window_s=2.0),
        narration.NarrationCue(order=2, clip_id="cl-3", speaker="ANA", line="c", start_s=4.15, window_s=2.0),
    ]
    shifts = narration.layout_cues(cues, [4.0, 1.0, 0.5])
    assert cues[0].start_s == pytest.approx(0.15)
    assert cues[1].start_s == pytest.approx(0.15 + 4.0 + narration.MIN_CUE_GAP_S)
    # the push cascades: cl-3 also waits for the shifted cl-2 to finish
    assert cues[2].start_s == pytest.approx(cues[1].start_s + 1.0 + narration.MIN_CUE_GAP_S)
    assert len(shifts) == 2
    assert "cl-2" in shifts[0] and "cl-3" in shifts[1]


def test_cues_that_fit_their_clips_hold_their_marks() -> None:
    cues = [
        narration.NarrationCue(order=0, clip_id="cl-1", speaker="ANA", line="a", start_s=0.15, window_s=3.0),
        narration.NarrationCue(order=1, clip_id="cl-2", speaker="BEN", line="b", start_s=3.15, window_s=3.0),
    ]
    shifts = narration.layout_cues(cues, [2.0, 2.0])
    assert [c.start_s for c in cues] == [0.15, 3.15]
    assert shifts == []


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_narrate_applies_the_no_overlap_layout(tmp_path, monkeypatch, fake_narrator) -> None:
    ffmpeg = shutil.which("ffmpeg")
    film = tmp_path / "film.mp4"
    subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=110:duration=4",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(film),
        ],
        check=True,
        capture_output=True,
    )
    # the fake narrator speaks 1.2s per cue; the second cue is scheduled only
    # 0.35s after the first — it must wait, never talk over it
    cues = [
        narration.NarrationCue(order=0, clip_id="cl-1", speaker="ANA", line="one", start_s=0.15, window_s=0.35),
        narration.NarrationCue(order=1, clip_id="cl-2", speaker="BEN", line="two", start_s=0.5, window_s=3.5),
    ]
    report = narration.narrate(film, cues, tmp_path / "out.mp4", loudness_target="streaming")
    first, second = report.cues
    assert second["start_s"] >= first["start_s"] + first["duration_s"]
    assert report.shifts and "cl-2" in report.shifts[0]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_a_line_too_long_for_its_clip_is_named_never_trimmed(
    tmp_path, monkeypatch, fake_narrator
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    film = tmp_path / "film.mp4"
    subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:duration=2",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(film),
        ],
        check=True,
        capture_output=True,
    )
    # the fake narrator speaks 1.2s; a 0.1s window + 1.0s tolerance is beaten
    cues = [
        narration.NarrationCue(
            order=0, clip_id="cl-9", speaker="", line="far too many words",
            start_s=0.15, window_s=0.1,
        )
    ]
    report = narration.narrate(film, cues, tmp_path / "out.mp4", loudness_target="streaming")
    assert report.overruns and "cl-9" in report.overruns[0]
    assert "book a longer clip" in report.overruns[0]
