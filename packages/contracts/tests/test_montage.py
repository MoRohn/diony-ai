"""The montage contracts the Director cuts with: the program look and subtitles."""

from __future__ import annotations

from diony_contracts.montage import (
    LookProfile,
    SubtitleCue,
    look_filter,
    render_srt,
    render_vtt,
    wrap_cue_text,
)


def test_look_filter_blends_and_neutral_is_empty() -> None:
    assert look_filter(LookProfile.NEUTRAL, 0.6) == ""
    warm = look_filter(LookProfile.CINEMATIC_WARM, 0.6)
    assert warm.startswith("split=2[lookplate][looksrc];[looksrc]colorbalance")
    assert "all_opacity=0.600" in warm
    assert look_filter(LookProfile.MOODY_DARK, 1.0).startswith("curves=")
    lut = look_filter(LookProfile.NEUTRAL, 0.7, lut_path="/v/lo:ok.cube")
    assert "lut3d=file='/v/lo\\:ok.cube'" in lut and "all_opacity=0.700" in lut
    for profile in LookProfile:
        chain = look_filter(profile, 1.0)
        assert "saturation=1.2" not in chain and "saturation=1.3" not in chain


def test_subtitle_cues_render_as_srt_and_vtt() -> None:
    cues = [
        SubtitleCue(
            index=1, start_seconds=0.15, end_seconds=2.4, speaker="MARA", text="We leave at dawn."
        ),
        SubtitleCue(index=2, start_seconds=2.5, end_seconds=4.0, text="Then pack tonight."),
    ]
    srt = render_srt(cues)
    assert srt.startswith("1\n00:00:00,150 --> 00:00:02,400\nWe leave at dawn.\n")
    vtt = render_vtt(cues)
    assert vtt.startswith("WEBVTT") and "<v MARA>We leave at dawn." in vtt
    assert "00:00:02.500 --> 00:00:04.000" in vtt
    assert wrap_cue_text("word " * 30).count("\n") <= 1


def test_a_cue_must_end_after_it_starts() -> None:
    import pytest

    with pytest.raises(ValueError):
        SubtitleCue(index=1, start_seconds=2.0, end_seconds=1.0, text="backwards")
