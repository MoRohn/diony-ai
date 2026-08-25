"""Library-backed and generated soundtrack planning: content with receipts, never invented noise.

The operator provisions a licensed sound library (`DIONY_SOUND_LIBRARY`, default
`{vault}/runtime/audio/library`) with `ambience/`, `foley/`, and `score/`
subdirectories. At roll — while the brain is still resident, before the FARM
drain — DeepSeek edit-lists cues against the rundown and the plan is persisted
as a digest-protected receipt.

Two cue sources exist, in fixed priority: licensed library bytes (verified by
sha256 at mix), and — only when the site enables `DIONY_AUDIOGEN` AND the Bible
policy is `generated_ok` — cues rendered by the local open-weights generators
(ThinkSound for picture-synced Foley/ambience, ACE-Step for score, Stable Audio
Open for textures) through the `DIONY_AUDIOGEN_COMMAND` adapter, each with a
receipt and a no-speech QC gate. No library, no generators, or no brain means
the dialogue-only mix stands — visibly, never silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from diony_contracts.enums import ArtifactKind, SoundtrackPolicy
from diony_contracts.models import Project
from diony_producer.brain import _brain_reachable, _chat, _json_object, brain_up
from diony_producer.vault import record_activity, vault_root

SOUNDTRACK_PLAN_VERSION = "diony-soundtrack-plan-v2"
_ACCEPTED_PLAN_VERSIONS = {"diony-soundtrack-plan-v1", SOUNDTRACK_PLAN_VERSION}
STEM_ROLES = ("ambience", "foley", "score")
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac"}
_MAX_CATALOG = 200
_MAX_CUES = 64
_MAX_GENERATED_CUES = 20
_MAX_PROMPT_CHARS = 300
_GAIN_BOUNDS_DB = (-36.0, 6.0)
PLAN_MAX_TOKENS = 4096

# Open-weights generators (verified licenses: ThinkSound Apache-2.0,
# ACE-Step Apache-2.0, Stable Audio Open under the Stability Community
# License). Roles are constrained per instrument; ThinkSound conditions on the
# shot's accepted negative so its cues are picture-synced by construction.
GENERATORS = {
    "thinksound": {"roles": ("foley", "ambience"), "max_seconds": 30.0, "video": True},
    "ace-step": {"roles": ("score",), "max_seconds": 240.0, "video": False},
    "stable-audio-open": {"roles": ("ambience", "foley"), "max_seconds": 47.0, "video": False},
}

SOUNDTRACK_SYSTEM = (
    "You are the sound designer for Diony AI. You receive a production's story "
    "context, its shot rundown (id, slug, seconds, dialogue yes/no, start frame), "
    "a catalog of licensed local audio cues (id, role, tags, seconds), and a list "
    "of available generators. Design the soundtrack as an edit list. Return one "
    'JSON object only: {"cues": [...], "rationale": one sentence}. A cue row is '
    'either {"source": "library", "cue_id", "role", "shot_id" or null, '
    '"offset_frames", "duration_frames", "gain_db"} or, when generators are '
    'available, {"source": "generate", "generator", "prompt", "role", "shot_id" '
    'or null, "offset_frames", "duration_frames", "gain_db"}. Prefer a fitting '
    "library cue over generating; use thinksound for Foley and shot ambience "
    "(it hears the shot's own picture — anchor it to a shot_id), ace-step for "
    "score, stable-audio-open for textures. Roles must match the instrument. "
    "Ambience beds sit low (about -24 to -16 dB); Foley hits land on physical "
    "actions inside their shot; score enters and exits with dramatic intent and "
    "never fights dialogue. Generated prompts describe sound only — never "
    "spoken words, singing, or dialogue. Prefer restraint over wall-to-wall "
    "coverage. Do not invent cue ids. Do not mention cloud APIs."
)


@dataclass(frozen=True)
class LibraryCue:
    id: str
    path: str
    role: str
    tags: tuple[str, ...]
    seconds: float
    sha256: str


@dataclass(frozen=True)
class PlannedCue:
    cue_id: str
    path: str
    role: str
    start_seconds: float
    duration_seconds: float
    gain_db: float
    source: str = "library"


def sound_library_root() -> Path:
    configured = os.environ.get("DIONY_SOUND_LIBRARY", "").strip()
    if configured:
        return Path(configured)
    return vault_root() / "runtime" / "audio" / "library"


def generation_enabled(project: Project) -> bool:
    """Generated cues need the site opt-in, the Bible policy, and the adapter."""
    if os.environ.get("DIONY_AUDIOGEN", "").strip().lower() != "enabled":
        return False
    if project.bible.soundtrack_policy != SoundtrackPolicy.GENERATED_OK:
        return False
    return bool(os.environ.get("DIONY_AUDIOGEN_COMMAND", "").strip())


def _cue_seconds(path: Path) -> float:
    ffprobe = os.environ.get("DIONY_FFPROBE", "").strip() or shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return max(0.0, float(probe.stdout.strip()))
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def sound_library_present() -> bool:
    """Cheap existence probe — the full scan (ffprobe + sha256 per clip) waits
    until planning is actually going to happen."""
    root = sound_library_root()
    for role in STEM_ROLES:
        folder = root / role
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.is_file() and path.suffix.lower() in _AUDIO_SUFFIXES:
                return True
    return False


def scan_sound_library() -> list[LibraryCue]:
    root = sound_library_root()
    cues: list[LibraryCue] = []
    for role in STEM_ROLES:
        folder = root / role
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _AUDIO_SUFFIXES:
                continue
            seconds = _cue_seconds(path)
            if seconds <= 0:
                continue
            tags = tuple(
                token
                for token in path.stem.replace("-", " ").replace("_", " ").casefold().split()
                if token
            )
            cues.append(
                LibraryCue(
                    id=f"{role}/{path.name}",
                    path=str(path),
                    role=role,
                    tags=tags,
                    seconds=round(seconds, 3),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
            if len(cues) >= _MAX_CATALOG:
                return cues
    return cues


def plan_path(project: Project) -> Path:
    return vault_root() / "projects" / project.id / "audio" / "soundtrack-plan.json"


def soundtrack_plan_exists(project: Project) -> bool:
    return plan_path(project).is_file()


def _generated_root(project: Project) -> Path:
    return vault_root() / "projects" / project.id / "audio" / "generated"


def _payload_digest(payload: dict) -> str:
    scrubbed = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(
        json.dumps(scrubbed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _spec_sha(row: dict) -> str:
    canonical = json.dumps(
        {key: row.get(key) for key in ("generator", "prompt", "role", "shot_id", "duration_frames", "seed")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_rows(
    candidate: object,
    catalog: dict[str, LibraryCue],
    project: Project,
    *,
    allow_generation: bool,
) -> list[dict]:
    if not isinstance(candidate, list):
        return []
    shot_ids = {shot.id for shot in project.shots}
    fps = project.bible.fps or 23.976
    rows: list[dict] = []
    generated = 0
    for row in candidate[:_MAX_CUES]:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or ("generate" if row.get("generator") else "library"))
        shot_id = row.get("shot_id")
        if shot_id is not None and str(shot_id) not in shot_ids:
            continue
        try:
            offset = max(0, int(row.get("offset_frames") or 0))
            duration = int(row.get("duration_frames") or 0)
            gain = float(row.get("gain_db") or 0.0)
        except (TypeError, ValueError):
            continue
        gain = min(_GAIN_BOUNDS_DB[1], max(_GAIN_BOUNDS_DB[0], gain))
        if source == "library":
            cue = catalog.get(str(row.get("cue_id") or ""))
            if cue is None:
                continue
            if str(row.get("role") or "") != cue.role:
                continue
            max_frames = max(1, int(cue.seconds * fps))
            rows.append(
                {
                    "source": "library",
                    "cue_id": cue.id,
                    "role": cue.role,
                    "shot_id": str(shot_id) if shot_id is not None else None,
                    "offset_frames": offset,
                    "duration_frames": min(max(1, duration), max_frames),
                    "gain_db": round(gain, 2),
                }
            )
            continue
        if source != "generate" or not allow_generation or generated >= _MAX_GENERATED_CUES:
            continue
        generator = str(row.get("generator") or "")
        spec = GENERATORS.get(generator)
        role = str(row.get("role") or "")
        prompt = " ".join(str(row.get("prompt") or "").split())[:_MAX_PROMPT_CHARS]
        if spec is None or role not in spec["roles"] or not prompt:
            continue
        if spec["video"] and shot_id is None:
            continue
        max_frames = max(1, int(float(spec["max_seconds"]) * fps))
        if spec["video"]:
            shot = next(item for item in project.shots if item.id == str(shot_id))
            max_frames = min(max_frames, shot.duration_frames)
        validated = {
            "source": "generate",
            "generator": generator,
            "prompt": prompt,
            "role": role,
            "shot_id": str(shot_id) if shot_id is not None else None,
            "offset_frames": offset,
            "duration_frames": min(max(1, duration), max_frames),
            "gain_db": round(gain, 2),
        }
        validated["seed"] = int(_spec_sha(validated)[:8], 16) % 2_000_000_000
        rows.append(validated)
        generated += 1
    return rows


def plan_soundtrack(project: Project) -> dict | None:
    """Author and persist the soundtrack edit list. Never raises; always visible."""
    allow_generation = generation_enabled(project)
    if not sound_library_present() and not allow_generation:
        return None
    if os.environ.get("DIONY_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}:
        # The dry-run desktop has no brain by design; skip without a warning.
        return None
    if not _brain_reachable() or not brain_up():
        record_activity(
            "brain",
            "Soundtrack planning skipped: brain is offline; the dialogue-only mix stands.",
            level="warn",
        )
        return None
    catalog = {cue.id: cue for cue in scan_sound_library()}
    if not catalog and not allow_generation:
        return None
    shots = [
        {
            "id": shot.id,
            "slug": shot.slug,
            "seconds": round(shot.duration_frames / (shot.fps or 23.976), 2),
            "dialogue": bool(shot.audio_line or shot.dialogue_turns),
            "start_frame": next(
                (event.start_frame for event in (project.timeline.events if project.timeline else []) if event.shot_id == shot.id),
                0,
            ),
        }
        for shot in project.shots
    ]
    context = {
        "logline": project.bible.logline[:240],
        "world": project.bible.world[:400],
        "loudness": project.bible.loudness.value,
        "shots": shots,
        "catalog": [
            {"id": cue.id, "role": cue.role, "tags": list(cue.tags), "seconds": cue.seconds}
            for cue in catalog.values()
        ],
        "generators": (
            [
                {"id": name, "roles": list(spec["roles"]), "max_seconds": spec["max_seconds"]}
                for name, spec in GENERATORS.items()
            ]
            if allow_generation
            else []
        ),
    }
    try:
        payload = _json_object(
            _chat(SOUNDTRACK_SYSTEM, "Production: " + json.dumps(context), max_tokens=PLAN_MAX_TOKENS)
        )
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        record_activity(
            "brain",
            f"Soundtrack planning fell back to the dialogue-only mix: {str(exc)[:160]}",
            level="warn",
        )
        return None
    rows = _validated_rows(payload.get("cues"), catalog, project, allow_generation=allow_generation)
    if not rows:
        record_activity(
            "brain",
            "Soundtrack planning returned no usable cues; the dialogue-only mix stands.",
            level="warn",
        )
        return None
    referenced = {row["cue_id"] for row in rows if row["source"] == "library"}
    receipt = {
        "version": SOUNDTRACK_PLAN_VERSION,
        "project_id": project.id,
        "rationale": str(payload.get("rationale") or "")[:300],
        "cues": rows,
        "sources": {
            cue_id: {"path": catalog[cue_id].path, "sha256": catalog[cue_id].sha256}
            for cue_id in sorted(referenced)
        },
    }
    receipt["digest"] = _payload_digest(receipt)
    destination = plan_path(project)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    counts = {role: sum(1 for row in rows if row["role"] == role) for role in STEM_ROLES}
    generated = sum(1 for row in rows if row["source"] == "generate")
    record_activity(
        "brain",
        (
            f"DeepSeek planned {len(rows)} soundtrack cues "
            f"(ambience {counts['ambience']} · foley {counts['foley']} · score {counts['score']}"
            + (f" · {generated} generated" if generated else "")
            + ")."
        ),
    )
    return receipt


def _load_receipt(project: Project) -> dict | None:
    path = plan_path(project)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") not in _ACCEPTED_PLAN_VERSIONS:
        return None
    if payload.get("project_id") != project.id:
        return None
    if payload.get("digest") != _payload_digest(payload):
        return None
    if not isinstance(payload.get("sources"), dict) or not isinstance(payload.get("cues"), list):
        return None
    return payload


def _shot_video(project: Project, shot_id: str) -> Path | None:
    try:
        shot = project.shot(shot_id)
    except KeyError:
        return None
    take = None
    if shot.selected_take_id:
        try:
            take = project.take(shot.selected_take_id)
        except KeyError:
            take = None
    if take is None:
        takes = project.takes_for(shot_id)
        take = takes[-1] if takes else None
    if take is None:
        return None
    for artifact in take.artifacts:
        if artifact.kind == ArtifactKind.NEGATIVE_720:
            path = vault_root() / artifact.path
            if path.is_file():
                return path
    return None


def _invoke_audiogen(action: str, request: dict) -> dict | None:
    command = shlex.split(os.environ.get("DIONY_AUDIOGEN_COMMAND", "").strip())
    if not command:
        return None
    output = Path(str(request["output"]))
    request_path = output.with_suffix(output.suffix + f".{action}.request.json")
    response_path = output.with_suffix(output.suffix + f".{action}.response.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [*command, action, "--request", str(request_path), "--response", str(response_path)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode or not response_path.is_file():
        return None
    try:
        return json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ensure_generated_cues(project: Project) -> None:
    """Render any planned generated cues that are not on disk yet.

    Runs in the FINISH window (mix start): the farm is done, the accepted
    negatives exist for ThinkSound conditioning, and the generators are small
    enough to co-reside with finish. A cue that fails to render or fails its
    QC gate is dropped with a visible warning; decorative audio never blocks
    delivery.
    """
    payload = _load_receipt(project)
    if payload is None:
        return
    if not generation_enabled(project):
        return
    fps = project.bible.fps or 23.976
    for row in payload["cues"]:
        if not isinstance(row, dict) or row.get("source") != "generate":
            continue
        spec_sha = _spec_sha(row)
        output = _generated_root(project) / f"{spec_sha[:12]}.wav"
        if output.is_file() and output.stat().st_size > 44:
            continue
        seconds = max(0.5, int(row.get("duration_frames") or 1) / fps)
        request = {
            "generator": row.get("generator"),
            "prompt": row.get("prompt"),
            "seconds": round(seconds, 3),
            "seed": int(row.get("seed") or 0),
            "output": str(output),
        }
        action = "generate-cue"
        generator_spec = GENERATORS.get(str(row.get("generator") or ""), {})
        if generator_spec.get("video"):
            video = _shot_video(project, str(row.get("shot_id") or ""))
            if video is None:
                record_activity(
                    "mix",
                    f"Generated cue dropped: no accepted picture for {row.get('shot_id')}.",
                    level="warn",
                )
                continue
            request["video"] = str(video)
            action = "generate-foley"
        response = _invoke_audiogen(action, request)
        if response is None or not output.is_file() or output.stat().st_size <= 44:
            record_activity(
                "mix",
                (
                    f"Generated {row.get('role')} cue dropped "
                    f"({row.get('generator')}): render or QC failed."
                ),
                level="warn",
            )
            continue
        record_activity(
            "mix",
            f"Generated {row.get('role')} cue rendered by {row.get('generator')} ({output.name}).",
        )


def load_soundtrack_plan(project: Project) -> list[PlannedCue] | None:
    """The persisted plan, only if intact and every cue's bytes still verify.

    Library cues must match their planned sha256 exactly. Generated cues must
    exist with their adapter receipt; unrendered generated cues are dropped
    (never blocking), tampered library bytes invalidate the whole plan.
    """
    payload = _load_receipt(project)
    if payload is None:
        return None
    verified: dict[str, Path] = {}
    for cue_id, source in payload["sources"].items():
        if not isinstance(source, dict):
            return None
        cue_path = Path(str(source.get("path") or ""))
        if not cue_path.is_file():
            return None
        if hashlib.sha256(cue_path.read_bytes()).hexdigest() != source.get("sha256"):
            return None
        verified[str(cue_id)] = cue_path
    fps = project.bible.fps or 23.976
    starts = {
        event.shot_id: event.start_frame
        for event in (project.timeline.events if project.timeline else [])
    }
    resolved: list[PlannedCue] = []
    for row in payload["cues"]:
        if not isinstance(row, dict):
            return None
        source = str(row.get("source") or "library")
        if source == "generate":
            cue_path = _generated_root(project) / f"{_spec_sha(row)[:12]}.wav"
            receipt = cue_path.with_suffix(cue_path.suffix + ".receipt.json")
            if not cue_path.is_file() or cue_path.stat().st_size <= 44 or not receipt.is_file():
                continue
            cue_id = f"generated/{row.get('generator')}/{cue_path.stem}"
        else:
            library_path = verified.get(str(row.get("cue_id") or ""))
            if library_path is None:
                return None
            cue_path = library_path
            cue_id = str(row["cue_id"])
        anchor = starts.get(str(row.get("shot_id"))) if row.get("shot_id") else 0
        start_frame = int(anchor or 0) + max(0, int(row.get("offset_frames") or 0))
        resolved.append(
            PlannedCue(
                cue_id=cue_id,
                path=str(cue_path),
                role=str(row.get("role") or ""),
                start_seconds=round(start_frame / fps, 6),
                duration_seconds=round(max(1, int(row.get("duration_frames") or 1)) / fps, 6),
                gain_db=float(row.get("gain_db") or 0.0),
                source=source,
            )
        )
    return resolved
