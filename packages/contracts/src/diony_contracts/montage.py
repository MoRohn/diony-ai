"""Montage intelligence contracts: rundown variation review, decision log,
program final review, look profiles, subtitle sidecars, and production sizing.

Pure module — no I/O, no ffmpeg. Producers gather measurements and hand them
in; this module owns the thresholds, the verdict math, and the schemas so a
receipt written on the desk, on a GPU host, or in a test means the same thing.
Blueprint 24 records the studied production-craft sources and the policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable, Sequence

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from diony_contracts.models import Project, Shot


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- shot language


class ShotSize(StrEnum):
    WIDE = "wide"
    MEDIUM = "medium"
    MEDIUM_CLOSE_UP = "medium_close_up"
    CLOSE_UP = "close_up"


def shot_size_for(focal_mm: float) -> ShotSize:
    """The framing class the Wan camera layer books for a focal length.

    Mirrors the prompt compiler's framing vocabulary so a rundown review and a
    dispatched prompt never disagree about what a 40 mm shot is.
    """
    focal = float(focal_mm)
    if focal <= 24:
        return ShotSize.WIDE
    if focal <= 40:
        return ShotSize.MEDIUM
    if focal <= 65:
        return ShotSize.MEDIUM_CLOSE_UP
    return ShotSize.CLOSE_UP


class ReviewVerdict(StrEnum):
    STRONG = "strong"
    ACCEPTABLE = "acceptable"
    REVISE = "revise"
    FAIL = "fail"


def verdict_for_score(score: float) -> ReviewVerdict:
    """0–5 risk scale, lower is better: <2 strong, <3 acceptable, <4 revise, else fail."""
    if score < 2.0:
        return ReviewVerdict.STRONG
    if score < 3.0:
        return ReviewVerdict.ACCEPTABLE
    if score < 4.0:
        return ReviewVerdict.REVISE
    return ReviewVerdict.FAIL


# Phrases that constrain no pixels. A subject built from them reads as any
# other program's subject; the review flags a rundown that leans on them.
GENERIC_SUBJECT_PHRASES: tuple[str, ...] = (
    "a person",
    "a man",
    "a woman",
    "someone",
    "a beautiful",
    "modern",
    "futuristic",
    "cutting-edge",
    "seamless",
    "stunning",
    "innovative",
    "professional",
    "high quality",
)

RUNDOWN_REVIEW_VERSION = "diony-rundown-review-v1"
_DOMINANT_SIZE_RATIO = 0.5
_RUN_LENGTH_LIMIT = 3
_STATIC_RATIO_LIMIT = 0.6
_GENERIC_RATIO_LIMIT = 0.3
_MIN_SHOTS_FOR_STATS = 4
_VIOLATION_WEIGHT = 0.6


class ShotFacts(BaseModel):
    """The few facts a variation review needs, detached from the full Shot."""

    id: str
    shot_size: ShotSize
    static: bool
    lighting_key: str = ""
    hero: bool = False
    spoken: bool = False
    subject: str = ""
    locked_background: bool = False


def shot_facts(shot: "Shot") -> ShotFacts:
    from diony_contracts.enums import CameraMove, ReferenceLock

    plan = shot.prompt_plan
    return ShotFacts(
        id=shot.id,
        shot_size=shot_size_for(shot.focal_mm),
        static=shot.camera_move == CameraMove.STATIC,
        lighting_key=" ".join(str(shot.lighting_ratio or "").casefold().split()),
        hero=bool(shot.hero),
        spoken=bool(shot.dialogue_turns) or bool(" ".join(str(shot.audio_line or "").split())),
        subject=(plan.subject if plan is not None else shot.prompt) or "",
        locked_background=shot.reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND,
    )


class RundownReview(BaseModel):
    """Structural variety of a rundown before any GPU time is spent."""

    version: str = RUNDOWN_REVIEW_VERSION
    shot_count: int = Field(ge=0)
    score: float = Field(ge=0, le=5)
    verdict: ReviewVerdict
    violations: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    waived: list[str] = Field(default_factory=list)
    shot_sizes: dict[str, int] = Field(default_factory=dict)
    static_ratio: float = Field(default=0.0, ge=0, le=1)
    lighting_keys: int = Field(default=0, ge=0)
    hero_count: int = Field(default=0, ge=0)
    reviewed_at: datetime = Field(default_factory=_now)


def _longest_run(values: Sequence[str | None]) -> tuple[int, str]:
    best = 0
    best_value = ""
    run = 0
    current: str | None = None
    for value in values:
        if value is None:
            run = 0
            current = None
            continue
        if value == current:
            run += 1
        else:
            current = value
            run = 1
        if run > best:
            best = run
            best_value = value
    return best, best_value


def review_rundown(facts: Sequence[ShotFacts]) -> RundownReview:
    """Score the rundown's variety; every violation names its fix.

    Spoken shots are declared close-up cuts to their speaker (Blueprint 23), so
    they form their own class: they never count toward framing dominance or a
    framing run, and they are static by contract, so they never count toward
    the static ratio either. A locked background owns the frame, so the camera
    variety checks are waived and recorded as such.
    """
    facts = list(facts)
    count = len(facts)
    violations: list[str] = []
    suggestions: list[str] = []
    waived: list[str] = []
    picture = [item for item in facts if not item.spoken]
    sizes: dict[str, int] = {}
    for item in facts:
        sizes[item.shot_size.value] = sizes.get(item.shot_size.value, 0) + 1
    static_pool = picture
    static_ratio = (
        sum(1 for item in static_pool if item.static) / len(static_pool) if static_pool else 0.0
    )
    lighting_keys = len({item.lighting_key for item in facts if item.lighting_key})
    hero_count = sum(1 for item in facts if item.hero)
    locked = bool(facts) and all(item.locked_background for item in facts)
    enough = count >= _MIN_SHOTS_FOR_STATS
    if locked:
        waived.append("locked background owns framing, movement, and lighting; camera variety checks waived")
    elif enough:
        if len(picture) >= _MIN_SHOTS_FOR_STATS:
            picture_sizes: dict[str, int] = {}
            for item in picture:
                picture_sizes[item.shot_size.value] = picture_sizes.get(item.shot_size.value, 0) + 1
            size, dominant = max(picture_sizes.items(), key=lambda pair: pair[1])
            if dominant / len(picture) > _DOMINANT_SIZE_RATIO:
                violations.append(
                    f"{size.replace('_', ' ')} framing books {dominant} of {len(picture)} picture shots"
                )
                suggestions.append("vary focal lengths so no framing class exceeds half the coverage")
        run, run_size = _longest_run(
            [None if item.spoken else item.shot_size.value for item in facts]
        )
        if run >= _RUN_LENGTH_LIMIT:
            violations.append(
                f"{run} consecutive {run_size.replace('_', ' ')} shots without a change of framing"
            )
            suggestions.append("cut to a different framing at least every third shot")
        if len(static_pool) >= _MIN_SHOTS_FOR_STATS and static_ratio > _STATIC_RATIO_LIMIT:
            violations.append(
                f"{static_ratio:.0%} of picture shots are locked-off; motion is the medium"
            )
            suggestions.append("give at least 40% of picture shots an intentional camera move")
        if lighting_keys <= 1:
            violations.append("every shot shares one lighting plan")
            suggestions.append("vary key, ratio, or motivation between scenes")
        if hero_count == 0:
            violations.append("no hero shot is declared; nothing rolls a second candidate")
            suggestions.append("mark the identity-defining shot as hero")
        for index, item in enumerate(facts):
            if not item.hero or item.spoken:
                continue
            neighbours = [
                facts[j] for j in (index - 1, index + 1) if 0 <= j < count and not facts[j].spoken
            ]
            if neighbours and any(n.shot_size == item.shot_size for n in neighbours):
                violations.append(f"hero shot {item.id} shares its framing with an adjacent shot")
                suggestions.append("frame the hero shot differently from its neighbours so it reads")
                break
    if enough:
        generic = 0
        for item in facts:
            subject = " ".join(item.subject.casefold().split())
            if any(phrase in subject for phrase in GENERIC_SUBJECT_PHRASES):
                generic += 1
        if generic / count >= _GENERIC_RATIO_LIMIT:
            violations.append(
                f"{generic} of {count} subjects lean on generic phrasing that constrains no pixels"
            )
            suggestions.append("name the subject's defining attributes instead of category words")
    score = min(5.0, round(_VIOLATION_WEIGHT * len(violations), 4))
    return RundownReview(
        shot_count=count,
        score=score,
        verdict=verdict_for_score(score),
        violations=violations,
        suggestions=list(dict.fromkeys(suggestions)),
        waived=waived,
        shot_sizes=sizes,
        static_ratio=round(static_ratio, 4),
        lighting_keys=lighting_keys,
        hero_count=hero_count,
    )


def review_shots(shots: Iterable["Shot"]) -> RundownReview:
    return review_rundown([shot_facts(shot) for shot in shots])


def diversify_coverage(recipes: list[dict], pool: list[dict]) -> tuple[list[dict], list[str]]:
    """Break framing runs and static monotony in cycled coverage, deterministically.

    Operates on the recipe dicts the Studio compiles Shots from. A recipe that
    books dialogue (`audio`) is left alone: its framing is a property of speech.
    Returns the adjusted recipes and one line per change for the decision log.
    """
    from diony_contracts.enums import CameraMove

    adjusted = [dict(item) for item in recipes]
    changes: list[str] = []
    if len(adjusted) < _MIN_SHOTS_FOR_STATS or not pool:
        return adjusted, changes

    def size_of(rec: dict) -> str | None:
        if rec.get("audio"):
            return None
        return shot_size_for(float(rec["focal_mm"])).value

    # Framing runs: swap the third shot of a run for the next pool entry whose
    # framing differs from its neighbours.
    for index in range(2, len(adjusted)):
        window = [size_of(adjusted[j]) for j in (index - 2, index - 1, index)]
        if None in window or len(set(window)) != 1:
            continue
        for candidate in pool:
            if candidate.get("audio"):
                continue
            candidate_size = shot_size_for(float(candidate["focal_mm"])).value
            if candidate_size == window[0]:
                continue
            following = size_of(adjusted[index + 1]) if index + 1 < len(adjusted) else None
            if following == candidate_size:
                continue
            for key in ("focal_mm", "t_stop", "lighting_ratio", "camera_move"):
                adjusted[index][key] = candidate[key]
            changes.append(
                f"{index + 1}: reframed to {candidate_size.replace('_', ' ')} "
                f"({candidate['focal_mm']:g}mm) to break a {window[0].replace('_', ' ')} run"
            )
            break
    # Static monotony: alternate a push-in and a pan onto locked-off picture
    # shots until fewer than 60% of them are static.
    picture = [index for index, rec in enumerate(adjusted) if not rec.get("audio")]
    if len(picture) >= _MIN_SHOTS_FOR_STATS:
        moves = (CameraMove.DOLLY_IN, CameraMove.PAN, CameraMove.TRACK)
        turn = 0
        for index in picture:
            static_count = sum(
                1 for j in picture if adjusted[j]["camera_move"] == CameraMove.STATIC
            )
            if static_count / len(picture) <= _STATIC_RATIO_LIMIT:
                break
            if adjusted[index]["camera_move"] != CameraMove.STATIC:
                continue
            adjusted[index]["camera_move"] = moves[turn % len(moves)]
            changes.append(
                f"{index + 1}: {moves[turn % len(moves)].value.replace('_', ' ')} replaces a locked-off camera"
            )
            turn += 1
    return adjusted, changes


# --------------------------------------------------------------------------- decision log


class DecisionCategory(StrEnum):
    COVERAGE = "coverage"
    LANE_SELECTION = "lane_selection"
    SEED_SELECTION = "seed_selection"
    CASTING = "casting"
    RETAKE = "retake"
    SETTLE = "settle"
    HERO_SELECT = "hero_select"
    SOUNDTRACK = "soundtrack"
    LOOK = "look"
    FALLBACK = "fallback"
    FINAL_REVIEW = "final_review"
    SIZING = "sizing"


class DecisionOption(BaseModel):
    id: str
    label: str
    score: float | None = Field(default=None, ge=0, le=1)
    reason: str = ""
    rejected_because: str = ""


class Decision(BaseModel):
    """One production choice with the options it was chosen from.

    Append-only. Re-deciding a subject appends a new entry; the earlier entry
    is history, never rewritten. `confidence` is the recorder's own estimate
    and is never 1.0 for a heuristic choice.
    """

    id: str
    at: datetime = Field(default_factory=_now)
    stage: str
    category: DecisionCategory
    subject: str
    options: list[DecisionOption] = Field(min_length=1)
    selected: str
    reason: str
    confidence: float = Field(default=0.7, ge=0, le=1)
    shot_id: str = ""
    take_id: str = ""
    user_visible: bool = True
    user_approved: bool = False

    @model_validator(mode="after")
    def selected_is_an_option(self) -> Decision:
        if self.selected not in {option.id for option in self.options}:
            raise ValueError("selected must name one of the options considered")
        return self


MAX_DECISIONS_IN_PROJECT = 400


def append_decision(decisions: list[Decision], decision: Decision) -> list[Decision]:
    """Keep the most recent decisions in the Project snapshot; the full log is a file."""
    decisions.append(decision)
    if len(decisions) > MAX_DECISIONS_IN_PROJECT:
        del decisions[: len(decisions) - MAX_DECISIONS_IN_PROJECT]
    return decisions


# --------------------------------------------------------------------------- look profiles


class LookProfile(StrEnum):
    NEUTRAL = "neutral"
    CINEMATIC_WARM = "cinematic_warm"
    CINEMATIC_COOL = "cinematic_cool"
    MOODY_DARK = "moody_dark"
    BRIGHT_CLEAN = "bright_clean"
    VINTAGE_FILM = "vintage_film"
    HIGH_CONTRAST = "high_contrast"


# Program-level looks. Corrective work is the Finish legalizer's; these are
# creative and deliberately restrained: saturation never above 1.10 so skin
# stays on the vector line, shadows lifted rather than crushed, highlights
# rolled rather than clipped. The limiter downstream re-asserts legal range.
_LOOK_CHAINS: dict[LookProfile, str] = {
    LookProfile.NEUTRAL: "",
    LookProfile.CINEMATIC_WARM: (
        "colorbalance=rs=0.05:gs=0.01:bs=-0.04:rh=0.04:bh=-0.03,"
        "curves=all='0/0.02 0.5/0.52 1/0.98',eq=contrast=1.04:saturation=1.06"
    ),
    LookProfile.CINEMATIC_COOL: (
        "colorbalance=rs=-0.02:bs=0.05:rh=0.03:bh=-0.04,"
        "curves=all='0/0.02 0.5/0.49 1/0.98',eq=contrast=1.05:saturation=1.03"
    ),
    LookProfile.MOODY_DARK: (
        "curves=all='0/0.04 0.5/0.46 1/0.96',eq=contrast=1.08:saturation=0.88:brightness=-0.02"
    ),
    LookProfile.BRIGHT_CLEAN: "curves=all='0/0.03 0.5/0.54 1/1',eq=saturation=1.08:brightness=0.015",
    LookProfile.VINTAGE_FILM: (
        "colorbalance=rs=0.04:gs=0.02:bs=-0.02,curves=all='0/0.05 0.5/0.5 1/0.95',"
        "eq=saturation=0.9:contrast=0.97"
    ),
    LookProfile.HIGH_CONTRAST: (
        "curves=all='0/0 0.25/0.2 0.5/0.5 0.75/0.8 1/1',eq=contrast=1.12:saturation=1.05"
    ),
}

LOOK_INTENSITY_MIN = 0.3
LOOK_INTENSITY_MAX = 1.0
# Generated footage is already stylised; a full-strength creative look over it
# reads as processing. 0.6 is the shipped default, never 1.0 by accident.
LOOK_INTENSITY_DEFAULT = 0.6


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def look_filter(profile: LookProfile, intensity: float = LOOK_INTENSITY_DEFAULT, *, lut_path: str | None = None) -> str:
    """One ffmpeg filter segment (no labels) applying the program look at `intensity`.

    Empty when there is nothing to apply, so a neutral Bible costs no re-encode
    path change. A Bible LUT (`lut_asset_id`) is applied after the profile,
    both blended against the ungraded picture at the same intensity.
    """
    chain = _LOOK_CHAINS.get(profile, "")
    if lut_path:
        lut = f"lut3d=file='{_escape_filter_path(str(lut_path))}'"
        chain = f"{chain},{lut}" if chain else lut
    if not chain:
        return ""
    amount = max(LOOK_INTENSITY_MIN, min(LOOK_INTENSITY_MAX, float(intensity)))
    if amount >= LOOK_INTENSITY_MAX - 1e-6:
        return chain
    return (
        f"split=2[lookplate][looksrc];[looksrc]{chain}[lookgraded];"
        f"[lookplate][lookgraded]blend=all_mode=normal:all_opacity={amount:.3f}"
    )


# --------------------------------------------------------------------------- subtitles


SUBTITLE_MAX_CHARS_PER_LINE = 42
SUBTITLE_MAX_LINES = 2
SUBTITLE_MIN_SECONDS = 0.5
SUBTITLE_GAP_SECONDS = 0.08


class SubtitleCue(BaseModel):
    index: int = Field(ge=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    speaker: str = ""
    text: str

    @model_validator(mode="after")
    def ordered(self) -> SubtitleCue:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("a cue must end after it starts")
        return self


def wrap_cue_text(text: str, *, max_chars: int = SUBTITLE_MAX_CHARS_PER_LINE, max_lines: int = SUBTITLE_MAX_LINES) -> str:
    words = " ".join(str(text or "").split()).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        head.append(" ".join(lines[max_lines - 1 :]))
        lines = head
    return "\n".join(lines)


def dialogue_cues(project: "Project") -> list[SubtitleCue]:
    """Authored dialogue placed on the locked timeline.

    Cue timing is derived from each Shot's booked window: turns inside one
    window share it in proportion to their word counts. This is the authored
    line at the authored place, not a transcript alignment; a Shot's window is
    what Wan performed the line inside (Blueprint 23).
    """
    timeline = project.timeline
    if timeline is None:
        return []
    fps = float(timeline.fps or 23.976)
    characters = {character.id: character.name for character in project.bible.characters}
    shots = {shot.id: shot for shot in project.shots}
    cues: list[SubtitleCue] = []
    for event in timeline.events:
        shot = shots.get(event.shot_id)
        if shot is None:
            continue
        turns = [turn for turn in shot.dialogue_turns if " ".join(turn.text.split())]
        if not turns:
            continue
        start = event.start_frame / fps
        window = max(SUBTITLE_MIN_SECONDS, shot.duration_frames / float(shot.fps or fps))
        words = [max(1, len(turn.text.split())) for turn in turns]
        total_words = sum(words)
        cursor = start
        for turn, count in zip(turns, words):
            share = window * (count / total_words)
            end = cursor + share
            cue_end = max(cursor + SUBTITLE_MIN_SECONDS, end - SUBTITLE_GAP_SECONDS)
            cue_end = min(cue_end, start + window)
            if cue_end <= cursor:
                cue_end = cursor + SUBTITLE_MIN_SECONDS
            cues.append(
                SubtitleCue(
                    index=len(cues) + 1,
                    start_seconds=round(cursor, 3),
                    end_seconds=round(cue_end, 3),
                    speaker=characters.get(turn.character_id, ""),
                    text=wrap_cue_text(turn.text),
                )
            )
            cursor = end
    return cues


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def render_srt(cues: Sequence[SubtitleCue]) -> str:
    blocks = [
        f"{cue.index}\n{_srt_timestamp(cue.start_seconds)} --> {_srt_timestamp(cue.end_seconds)}\n{cue.text}\n"
        for cue in cues
    ]
    return "\n".join(blocks)


def render_vtt(cues: Sequence[SubtitleCue]) -> str:
    lines = ["WEBVTT", ""]
    for cue in cues:
        start = _srt_timestamp(cue.start_seconds).replace(",", ".")
        end = _srt_timestamp(cue.end_seconds).replace(",", ".")
        text = f"<v {cue.speaker}>{cue.text}" if cue.speaker else cue.text
        lines.extend([str(cue.index), f"{start} --> {end}", text, ""])
    return "\n".join(lines)


# --------------------------------------------------------------------------- final review


FINAL_REVIEW_VERSION = "diony-final-review-v1"
DURATION_TOLERANCE_RATIO = 0.10
DURATION_TOLERANCE_MIN_SECONDS = 1.0
MOTION_RATIO_MIN = 0.7


class ReviewStatus(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    FAIL = "fail"
    SKIPPED = "skipped"


class RecommendedAction(StrEnum):
    PRESENT = "present_to_user"
    RE_RENDER = "re_render"
    RETAKE = "retake"
    BLOCK = "block"
    SKIPPED = "skipped"


class TechnicalProbe(BaseModel):
    valid_container: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration_seconds: float = 0.0
    frames: int = 0
    has_audio: bool = False
    codec: str = ""
    pix_fmt: str = ""
    issues: list[str] = Field(default_factory=list)


class VisualSpotcheck(BaseModel):
    frames_sampled: int = Field(default=0, ge=0)
    black_frames: int = Field(default=0, ge=0)
    low_sharpness_frames: int = Field(default=0, ge=0)
    exposure_issues: int = Field(default=0, ge=0)
    sharpness_min: float | None = None
    issues: list[str] = Field(default_factory=list)


class AudioSpotcheck(BaseModel):
    dialogue_expected: bool = False
    dialogue_present: bool | None = None
    measured_lufs: float | None = None
    true_peak_dbtp: float | None = None
    target_lufs: float | None = None
    silent: bool | None = None
    clipping: bool | None = None
    issues: list[str] = Field(default_factory=list)


class PromisePreservation(BaseModel):
    target_seconds: float | None = None
    delivered_seconds: float = Field(default=0.0, ge=0)
    duration_ok: bool | None = None
    fps_ok: bool | None = None
    loudness_ok: bool | None = None
    motion_ratio: float | None = Field(default=None, ge=0, le=1)
    motion_ok: bool | None = None
    settled_prints: int = Field(default=0, ge=0)
    failed_prints: int = Field(default=0, ge=0)
    issues: list[str] = Field(default_factory=list)


class SubtitleCheck(BaseModel):
    expected: bool = False
    present: bool = False
    cue_count: int = Field(default=0, ge=0)
    issues: list[str] = Field(default_factory=list)


class FinalReview(BaseModel):
    """What the program master measured after mix, before it is called done."""

    version: str = FINAL_REVIEW_VERSION
    timeline_id: str = ""
    master_path: str = ""
    # The exact master bytes this review measured; a different hash is a
    # different master and earns a fresh measurement.
    master_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    status: ReviewStatus
    recommended_action: RecommendedAction
    technical_probe: TechnicalProbe = Field(default_factory=TechnicalProbe)
    visual_spotcheck: VisualSpotcheck = Field(default_factory=VisualSpotcheck)
    audio_spotcheck: AudioSpotcheck = Field(default_factory=AudioSpotcheck)
    promise_preservation: PromisePreservation = Field(default_factory=PromisePreservation)
    subtitle_check: SubtitleCheck = Field(default_factory=SubtitleCheck)
    issues_found: list[str] = Field(default_factory=list)
    reviewed_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def status_matches_issues(self) -> FinalReview:
        if self.status == ReviewStatus.PASS and self.issues_found:
            raise ValueError("a passing final review cannot carry issues")
        return self


def duration_tolerance_seconds(target_seconds: float) -> float:
    return max(DURATION_TOLERANCE_MIN_SECONDS, DURATION_TOLERANCE_RATIO * float(target_seconds))


def evaluate_final_review(
    *,
    timeline_id: str,
    master_path: str,
    master_sha256: str = "",
    technical: TechnicalProbe,
    visual: VisualSpotcheck,
    audio: AudioSpotcheck,
    promise: PromisePreservation,
    subtitles: SubtitleCheck,
) -> FinalReview:
    """Combine measured sections into one verdict; fail is never softened.

    FAIL: the container is invalid, a black frame reached the master, dialogue
    was promised and the mix is silent, or the mix clips. REVISE: duration
    drifted past tolerance, the motion promise fell below 0.7, exposure or
    sharpness problems were sampled, or subtitles were expected and missing.
    Everything else passes. The recommended action names the owning stage.
    """
    fatal: list[str] = []
    soft: list[str] = []
    if not technical.valid_container:
        fatal.append("program master is not a valid container")
    fatal.extend(technical.issues)
    if visual.black_frames:
        fatal.append(f"{visual.black_frames} sampled frame(s) are black")
    if audio.dialogue_expected and audio.silent:
        fatal.append("dialogue was authored but the program mix is silent")
    if audio.clipping:
        fatal.append("the program mix clips above the true-peak ceiling")
    fatal.extend(audio.issues)
    soft.extend(visual.issues)
    if promise.duration_ok is False:
        soft.append(
            f"delivered {promise.delivered_seconds:.1f}s against a {promise.target_seconds:.1f}s goal"
            if promise.target_seconds is not None
            else "delivered duration missed the goal"
        )
    if promise.fps_ok is False:
        soft.append("master frame rate does not match the Bible")
    if promise.loudness_ok is False:
        soft.append("measured loudness missed the Bible target")
    if promise.motion_ok is False:
        soft.append(
            f"measured motion ratio {promise.motion_ratio:.2f} is below {MOTION_RATIO_MIN:.1f}"
            if promise.motion_ratio is not None
            else "motion promise unmeasured"
        )
    soft.extend(promise.issues)
    if subtitles.expected and not subtitles.present:
        soft.append("dialogue sidecar subtitles were expected and not written")
    soft.extend(subtitles.issues)
    issues = list(dict.fromkeys(fatal + soft))
    if fatal:
        status = ReviewStatus.FAIL
        action = RecommendedAction.BLOCK if not technical.valid_container else RecommendedAction.RE_RENDER
        if visual.black_frames or promise.failed_prints:
            action = RecommendedAction.RETAKE
    elif soft:
        status = ReviewStatus.REVISE
        action = RecommendedAction.RE_RENDER
    else:
        status = ReviewStatus.PASS
        action = RecommendedAction.PRESENT
    return FinalReview(
        timeline_id=timeline_id,
        master_path=master_path,
        master_sha256=master_sha256,
        status=status,
        recommended_action=action,
        technical_probe=technical,
        visual_spotcheck=visual,
        audio_spotcheck=audio,
        promise_preservation=promise,
        subtitle_check=subtitles,
        issues_found=issues,
    )


def skipped_final_review(timeline_id: str, master_path: str, reason: str) -> FinalReview:
    return FinalReview(
        timeline_id=timeline_id,
        master_path=master_path,
        status=ReviewStatus.SKIPPED,
        recommended_action=RecommendedAction.SKIPPED,
        issues_found=[reason] if reason else [],
    )


# --------------------------------------------------------------------------- sizing


RETAKE_ALLOWANCE = 1.3
SIZING_MIN_MEASURED_CLIPS = 3


class ProductionSizing(BaseModel):
    """Honest wall-clock sizing from measured clip latencies, never a guess."""

    basis: str = "unmeasured"
    shot_count: int = Field(ge=0)
    planned_takes: int = Field(ge=0)
    measured_clips: int = Field(default=0, ge=0)
    camera_p50_seconds: float | None = Field(default=None, ge=0)
    finish_p50_seconds: float | None = Field(default=None, ge=0)
    retake_allowance: float = RETAKE_ALLOWANCE
    estimated_seconds_low: float | None = Field(default=None, ge=0)
    estimated_seconds_high: float | None = Field(default=None, ge=0)


def size_production(
    *,
    shot_count: int,
    hero_count: int = 0,
    camera_p50_seconds: float | None,
    finish_p50_seconds: float | None,
    measured_clips: int,
) -> ProductionSizing:
    """Shots × measured p50 (camera + finish), with a 1.3× retake allowance on the high end.

    Below three measured clips the sizing stays `unmeasured` and carries no
    numbers: Blueprint 19 forbids a fabricated ETA on the Production rail.
    """
    planned = int(shot_count) + int(hero_count)
    if measured_clips < SIZING_MIN_MEASURED_CLIPS or not camera_p50_seconds:
        return ProductionSizing(
            shot_count=int(shot_count), planned_takes=planned, measured_clips=int(measured_clips)
        )
    per_take = float(camera_p50_seconds) + float(finish_p50_seconds or 0.0)
    low = planned * per_take
    return ProductionSizing(
        basis="measured",
        shot_count=int(shot_count),
        planned_takes=planned,
        measured_clips=int(measured_clips),
        camera_p50_seconds=round(float(camera_p50_seconds), 3),
        finish_p50_seconds=round(float(finish_p50_seconds or 0.0), 3),
        estimated_seconds_low=round(low, 1),
        estimated_seconds_high=round(low * RETAKE_ALLOWANCE, 1),
    )


def format_sizing(sizing: ProductionSizing) -> str:
    if sizing.basis != "measured" or sizing.estimated_seconds_low is None:
        return ""
    low_h = sizing.estimated_seconds_low / 3600.0
    high_h = (sizing.estimated_seconds_high or sizing.estimated_seconds_low) / 3600.0
    return (
        f"Measured sizing: {sizing.planned_takes} takes × {sizing.camera_p50_seconds:.0f}s camera"
        f"{f' + {sizing.finish_p50_seconds:.0f}s finish' if sizing.finish_p50_seconds else ''}"
        f" p50 ≈ {low_h:.1f}–{high_h:.1f} h (×{sizing.retake_allowance:g} retake allowance; "
        f"basis {sizing.measured_clips} measured clips)"
    )


__all__ = [
    "AudioSpotcheck",
    "Decision",
    "DecisionCategory",
    "DecisionOption",
    "FINAL_REVIEW_VERSION",
    "FinalReview",
    "GENERIC_SUBJECT_PHRASES",
    "LOOK_INTENSITY_DEFAULT",
    "LookProfile",
    "MAX_DECISIONS_IN_PROJECT",
    "MOTION_RATIO_MIN",
    "ProductionSizing",
    "PromisePreservation",
    "RETAKE_ALLOWANCE",
    "RUNDOWN_REVIEW_VERSION",
    "RecommendedAction",
    "ReviewStatus",
    "ReviewVerdict",
    "RundownReview",
    "ShotFacts",
    "ShotSize",
    "SubtitleCheck",
    "SubtitleCue",
    "TechnicalProbe",
    "VisualSpotcheck",
    "append_decision",
    "dialogue_cues",
    "diversify_coverage",
    "duration_tolerance_seconds",
    "evaluate_final_review",
    "format_sizing",
    "look_filter",
    "render_srt",
    "render_vtt",
    "review_rundown",
    "review_shots",
    "shot_facts",
    "shot_size_for",
    "size_production",
    "skipped_final_review",
    "verdict_for_score",
    "wrap_cue_text",
]
