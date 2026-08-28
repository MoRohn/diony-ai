"""Edit decision list contract (Blueprint 24 §13).

Derived from the locked Timeline: one decision per cut with its reason,
transition family, speaker, and role, plus a CMX3600 rendering for any
cutting room. The EDL is evidence of the edit the run made; it never drives
the stitch (the Timeline does), so it can be regenerated from any Project.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from diony_contracts.models import Project

EDL_VERSION = "diony-edl-v1"
TRANSITION_FAMILY_LIMIT = 4


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CutTransition(BaseModel):
    kind: str = "cut"  # cut | dissolve
    frames: int = Field(default=0, ge=0)


class EditDecision(BaseModel):
    index: int = Field(ge=1)
    shot_id: str
    take_id: str = ""
    reel: str = ""
    source_in: int = Field(ge=0)
    source_out: int = Field(gt=0)
    record_in: int = Field(ge=0)
    record_out: int = Field(gt=0)
    transition_in: CutTransition = Field(default_factory=CutTransition)
    role: str = ""
    reason: str = ""
    speaker: str = ""
    hero: bool = False
    engine: str = ""
    continuity: str = ""
    slug: str = ""

    @property
    def duration(self) -> int:
        return self.record_out - self.record_in


class EditDecisionList(BaseModel):
    version: str = EDL_VERSION
    project_id: str
    timeline_id: str
    title: str
    fps: float
    total_frames: int = Field(ge=0)
    decisions: list[EditDecision] = Field(default_factory=list)
    transition_families: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


_ROLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("establishing", "establishing"),
    ("wide", "establishing"),
    ("character lock", "identity_lock"),
    ("hero", "identity_lock"),
    ("insert", "insert"),
    ("detail", "insert"),
    ("reaction", "reaction"),
    ("dialogue", "dialogue"),
    ("presenter", "dialogue"),
    ("talent", "dialogue"),
    ("performance", "performance"),
    ("bridge", "bridge"),
    ("entry", "bridge"),
    ("coverage", "coverage"),
    ("demo", "coverage"),
    ("hold", "hold"),
    ("button", "button"),
    ("payoff", "button"),
    ("sample", "sample"),
)


def _role_for(shot, *, spoken: bool) -> str:
    if spoken:
        return "dialogue"
    slug = str(shot.slug or "").casefold()
    for keyword, role in _ROLE_KEYWORDS:
        if keyword in slug:
            return role
    if shot.hero:
        return "identity_lock"
    return "coverage"


def _reason_for(shot, *, role: str, speaker: str, first: bool, last: bool) -> str:
    if role == "dialogue":
        return f"{speaker or 'the speaker'} speaks the line to camera; cut to the speaker, cut back the same way"
    if first:
        return "opens the program: the world and the subject are established before any cut"
    if last:
        return "closes on the last image; the cut lands the program"
    if role == "identity_lock":
        return "hero coverage: the identity-defining frame the rest of the cut answers to"
    if role == "insert":
        return "insert: the object or texture the story turns on, held long enough to read"
    if role == "reaction":
        return "reaction: the cost of the beat on the face that receives it"
    if role == "bridge":
        return "bridge: geography between beats so the next cut has a place to land"
    if role == "hold":
        return "hold: the frame breathes after the line"
    if shot.continuity_mode.value == "previous_frame":
        return "continues from the previous print's last frame; a temporal blend keeps the motion"
    return "coverage: a new framing on the beat so the sequence does not read as one held shot"


def build_edl(project: "Project") -> EditDecisionList:
    """One decision per timeline event, with reasons and transition families."""
    timeline = project.timeline
    fps = float(project.bible.fps or 23.976)
    if timeline is None:
        return EditDecisionList(project_id=project.id, timeline_id="", title=project.bible.title, fps=fps, total_frames=0)
    shots = {shot.id: shot for shot in project.shots}
    characters = {character.id: character.name for character in project.bible.characters}
    decisions: list[EditDecision] = []
    families: list[str] = []
    events = list(timeline.events)
    for position, event in enumerate(events):
        shot = shots.get(event.shot_id)
        if shot is None:
            continue
        spoken = bool(shot.dialogue_turns) or bool(" ".join(str(shot.audio_line or "").split()))
        speaker = characters.get(shot.dialogue_turns[0].character_id, "") if shot.dialogue_turns else ""
        role = _role_for(shot, spoken=spoken)
        overlap = max(0, event.handle_in) if position else 0
        transition = CutTransition(kind="dissolve", frames=overlap) if overlap else CutTransition()
        if transition.kind not in families:
            families.append(transition.kind)
        duration = max(1, shot.duration_frames)
        decisions.append(
            EditDecision(
                index=position + 1,
                shot_id=shot.id,
                take_id=event.take_id or "",
                reel=(event.take_id or shot.id)[:8].upper().replace("-", ""),
                source_in=0,
                source_out=duration,
                record_in=event.start_frame,
                record_out=event.start_frame + duration,
                transition_in=transition,
                role=role,
                reason=_reason_for(shot, role=role, speaker=speaker, first=position == 0, last=position == len(events) - 1),
                speaker=speaker,
                hero=bool(shot.hero),
                engine=shot.engine.value,
                continuity=shot.continuity_mode.value,
                slug=shot.slug,
            )
        )
    notes: list[str] = []
    if decisions and all(item.reason for item in decisions):
        notes.append("every cut carries a reason")
    notes.append(
        f"{len(families)} transition famil{'y' if len(families) == 1 else 'ies'} "
        f"({', '.join(families) or 'none'}); limit {TRANSITION_FAMILY_LIMIT}"
    )
    heroes = [item for item in decisions if item.hero]
    if heroes and decisions:
        longest = max(item.duration for item in decisions)
        if all(item.duration >= longest for item in heroes):
            notes.append("hero shots hold the longest")
        else:
            notes.append("a hero shot is held shorter than the longest cutaway")
    return EditDecisionList(
        project_id=project.id,
        timeline_id=timeline.id,
        title=project.bible.title,
        fps=fps,
        total_frames=timeline.total_frames,
        decisions=decisions,
        transition_families=families,
        notes=notes,
    )


def frames_to_timecode(frames: int, fps: float) -> str:
    """HH:MM:SS:FF at the integer frame base (23.976 counts as 24, non-drop)."""
    base = int(round(fps)) or 24
    frames = max(0, int(frames))
    hours, rest = divmod(frames, base * 3600)
    minutes, rest = divmod(rest, base * 60)
    seconds, frame = divmod(rest, base)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


def _reel_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())[:8]
    return (cleaned or "AX").ljust(8)


def render_cmx3600(edl: EditDecisionList) -> str:
    """A CMX3600 EDL: video only, dissolves as `D`, comments carry the reasons."""
    lines = [f"TITLE: {edl.title[:60]}", "FCM: NON-DROP FRAME", ""]
    fps = edl.fps
    event_number = 0
    previous: EditDecision | None = None
    for decision in edl.decisions:
        event_number += 1
        reel = _reel_name(decision.reel or decision.take_id or decision.shot_id)
        transition = decision.transition_in
        if transition.kind == "dissolve" and transition.frames > 0 and previous is not None:
            # A dissolve is written as the outgoing clip's tail followed by the
            # incoming clip with the transition length.
            lines.append(
                f"{event_number:03d}  {_reel_name(previous.reel or previous.take_id or previous.shot_id)} V     C        "
                f"{frames_to_timecode(previous.source_out, fps)} {frames_to_timecode(previous.source_out, fps)} "
                f"{frames_to_timecode(decision.record_in, fps)} {frames_to_timecode(decision.record_in, fps)}"
            )
            lines.append(
                f"{event_number:03d}  {reel} V     D    {transition.frames:03d} "
                f"{frames_to_timecode(decision.source_in, fps)} {frames_to_timecode(decision.source_out, fps)} "
                f"{frames_to_timecode(decision.record_in, fps)} {frames_to_timecode(decision.record_out, fps)}"
            )
        else:
            lines.append(
                f"{event_number:03d}  {reel} V     C        "
                f"{frames_to_timecode(decision.source_in, fps)} {frames_to_timecode(decision.source_out, fps)} "
                f"{frames_to_timecode(decision.record_in, fps)} {frames_to_timecode(decision.record_out, fps)}"
            )
        lines.append(f"* FROM CLIP NAME: {decision.take_id or decision.shot_id}")
        lines.append(f"* DIONY SHOT: {decision.shot_id} · {decision.slug}"[:120])
        lines.append(f"* DIONY ROLE: {decision.role}" + (f" · {decision.speaker}" if decision.speaker else "") + (" · hero" if decision.hero else ""))
        lines.append(f"* DIONY REASON: {decision.reason}"[:120])
        lines.append("")
        previous = decision
    return "\n".join(lines).rstrip("\n") + "\n"


def render_edl_json(edl: EditDecisionList) -> str:
    return json.dumps(edl.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


__all__ = [
    "CutTransition",
    "EDL_VERSION",
    "EditDecision",
    "EditDecisionList",
    "TRANSITION_FAMILY_LIMIT",
    "build_edl",
    "frames_to_timecode",
    "render_cmx3600",
    "render_edl_json",
]
