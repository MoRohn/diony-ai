"""Clip-accurate edits. New version from a parent take — never overwrite."""

from __future__ import annotations

from dataclasses import dataclass, field

from diony_contracts.enums import (
    ArtifactKind,
    Engine,
    GateName,
    QCVerdict,
    TakeOrigin,
)
from diony_contracts.models import (
    Artifact,
    ClipEdit,
    Note,
    Project,
    QCReport,
    Take,
)

PRESERVE_DEFAULT = ["identity", "wardrobe", "lighting_ratio", "camera_move", "duration"]
WARDROBE_WORDS = (
    "wardrobe",
    "costume",
    "outfit",
    "coat",
    "vest",
    "dress",
    "shirt",
    "clothes",
    "clothing",
)
CAMERA_WORDS = ("dolly", "pan", "tilt", "crane", "handheld", "push in", "pull out", "camera")
REPLACE_WORDS = ("replace the actor", "different person", "new face", "swap the lead")


@dataclass
class EditIntent:
    preserve: list[str]
    engine: Engine
    vace_prompt: str
    op: str
    relax: list[str] = field(default_factory=list)


def interpret_instruction(instruction: str) -> EditIntent:
    text = instruction.strip()
    if len(text) < 4:
        raise ValueError("edit instruction is empty")
    lower = text.lower()
    relax: list[str] = []
    if any(w in lower for w in WARDROBE_WORDS):
        relax.append("wardrobe")
    if any(w in lower for w in CAMERA_WORDS):
        relax.append("camera_move")
    if any(w in lower for w in ("lighting", "darker", "brighter", "practicals")):
        relax.append("lighting_ratio")
    if any(w in lower for w in REPLACE_WORDS):
        relax.append("identity")
    preserve = [p for p in PRESERVE_DEFAULT if p not in relax]
    if "face" in lower and "new face" not in lower:
        if "identity" not in preserve:
            preserve.append("identity")
    op = "vace_edit"
    if any(w in lower for w in CAMERA_WORDS):
        op = "camera"
    elif any(w in lower for w in WARDROBE_WORDS):
        op = "replace"
    keep = ", ".join(preserve) if preserve else "duration only"
    vace_prompt = (
        f"EDIT the existing take. Do not regenerate the shot from scratch. "
        f"KEEP: {keep}. "
        f"INSTRUCTION: {text} "
        f"Do not morph faces. Do not change duration unless asked. "
        f"Do not invent a new identity unless KEEP excludes identity."
    )
    return EditIntent(
        preserve=preserve,
        engine=Engine.VACE_FUN_A14B,
        vace_prompt=vace_prompt,
        op=op,
        relax=relax,
    )


def version_ladder(project: Project, shot_id: str) -> list[Take]:
    takes = project.takes_for(shot_id)
    return sorted(takes, key=lambda t: (t.version, t.created_at, t.id))


def apply_clip_edit(
    project: Project,
    source_take_id: str,
    instruction: str,
    t_in_frames: int | None = None,
    t_out_frames: int | None = None,
    attachment_ids: list[str] | None = None,
) -> Project:
    dist = next((gate for gate in project.gates if gate.name == GateName.DISTRIBUTION), None)
    if dist is not None and not dist.open:
        raise ValueError("distributed; edit is closed")
    parent = project.take(source_take_id)
    shot = project.shot(parent.shot_id)
    lineage_len = 1 + len(parent.lineage)
    if lineage_len >= 8:
        raise ValueError("too many versions on this print; stop for a human")
    intent = interpret_instruction(instruction)
    refs = attachment_ids or []
    for aid in refs:
        project.asset(aid)
    attempt = len(project.takes_for(shot.id)) + 1
    child_id = f"{shot.id}-t{attempt}"
    edit_id = f"ed-{shot.id}-{attempt}"
    scope = "full clip"
    if t_in_frames is not None or t_out_frames is not None:
        scope = f"frames {t_in_frames or 0}-{t_out_frames or shot.duration_frames}"
    vace = f"{intent.vace_prompt} SCOPE: {scope}."
    if refs:
        vace += f" REFERENCE ASSETS: {', '.join(refs)}."
    child = Take(
        id=child_id,
        shot_id=shot.id,
        attempt=attempt,
        engine=intent.engine,
        seed=parent.seed,
        asset_720p_id=f"neg-{child_id}",
        parent_take_id=parent.id,
        version=parent.version + 1,
        lineage=[*parent.lineage, parent.id],
        edit_id=edit_id,
        origin=TakeOrigin.EDIT,
        label="print",
        artifacts=[
            Artifact(
                id=f"art-{child_id}-720",
                take_id=child_id,
                kind=ArtifactKind.NEGATIVE_720,
                path=f"takes/{child_id}/negative.mov",
                duration_frames=shot.duration_frames,
            )
        ],
        qc=QCReport(
            id=f"qc-{child_id}",
            take_id=child_id,
            shot_id=shot.id,
            verdict=QCVerdict.WARN,
            identity_ok="identity" in intent.preserve,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            notes="Edit version. Dry-run until Wan VACE is live. Parent negative preserved.",
        ),
    )
    edit = ClipEdit(
        id=edit_id,
        shot_id=shot.id,
        source_take_id=parent.id,
        result_take_id=child.id,
        instruction=instruction.strip(),
        vace_prompt=vace,
        preserve=intent.preserve,
        t_in_frames=t_in_frames,
        t_out_frames=t_out_frames,
        attachment_ids=refs,
        engine=intent.engine,
    )
    project.takes.append(child)
    project.edits.append(edit)
    shot.selected_take_id = child.id
    project.notes.append(
        Note(
            id=f"n-{edit_id}",
            author="human",
            body=instruction.strip(),
            shot_id=shot.id,
            take_id=child.id,
        )
    )
    return project
