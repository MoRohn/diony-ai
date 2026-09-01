"""The Wan2.2-native spoken lane.

Diony does not synthesize dialogue. A spoken Shot is cast on a prompt clip
(the site's local casting library by roster order, or a reference clip the
maker attached), Producer hands Wan2.2 S2V the authored line and that clip
through Wan's own TTS inputs (``--enable_tts --tts_prompt_audio
--tts_prompt_text --tts_text``), and Wan performs the line, animates the face
from the speech it hears, and muxes its speech into the negative. After the
render Producer measures only what Wan delivered — voiced speech present, the
line finished inside the window, the dialogue stem on the Bible loudness
target — and binds that evidence to the negative for the program mix.

The lane fails closed: without the Wan speech pack on the host (Wan's
CosyVoice checkout and CosyVoice2-0.5B weights beside ``generate.py``, a
casting library or default prompt clip) a spoken Take is a pre-camera hold
with an actionable message, never a silent picture with a line owed later.
"""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from diony_contracts.enums import AssetKind, Engine, LoudnessTarget, VoiceCastingSource, VoiceRenderState
from diony_contracts.models import Asset, Project, Shot, Take
from diony_contracts.studio import speaker_name, spoken_emotion
from diony_contracts.voice import (
    PROMPT_CLIP_MAX_SECONDS,
    PROMPT_CLIP_MIN_SAMPLE_RATE,
    PROMPT_CLIP_MIN_SECONDS,
    SPEECH_BACKEND,
    SpeechClipPackage,
    SpeechQCReceipt,
    expected_speech_ms,
    presence_floor_for,
    VoiceCasting,
    WanSpeechRequest,
)
from diony_contracts.wan_prompt import (
    WAN_NATIVE_TTS_AUDIO_STRATEGY,
    render_negative_prompt,
    render_wan_prompt,
    spoken_wan_prompt_plan,
)

VOICE_LANE_VERSION = "diony-wan-native-speech-v1"
# v2: the voiced-presence floor follows the line (presence_floor_for) and the
# receipt fingerprints the dialogue audio and speech request.
SPEECH_QC_VERSION = "diony-speech-qc-v2"
# Studio allocates authored dialogue at two words per second of Shot time;
# Wan's TTS speaks English at roughly 2.3–2.8. Past three the line cannot
# finish inside the window and the render would only prove it.
MAX_WORDS_PER_SECOND = 3.0

# Wan2.2's `WanS2V.load_tts()` looks for these beside generate.py (its cwd)
# and would try to download them at render time when absent. Studio pins them.
WAN_TTS_CHECKOUT = "CosyVoice"
WAN_TTS_MODEL_DIR = "CosyVoice2-0.5B"
WAN_TTS_MODEL_FILES = ("cosyvoice2.yaml", "llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v2.onnx")
WAN_TTS_CHECKOUT_FILES = ("cosyvoice/cli/cosyvoice.py", "third_party/Matcha-TTS")
HOST_SPEECH_ENV_KEYS = (
    "DIONY_WAN_TTS_PROMPT_LIBRARY",
    "DIONY_WAN_TTS_PROMPT_AUDIO",
    "DIONY_WAN_TTS_PROMPT_TEXT",
)
PROMPT_CLIP_SUFFIXES = (".wav", ".mp3", ".flac")


class VoicePipelineError(RuntimeError):
    """A spoken Take cannot roll (or cannot pass) for a stated, actionable reason."""


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wan_repo() -> Path:
    return Path(os.environ.get("DIONY_WAN_REPO", "").strip() or "/opt/wan22")


def _vault() -> Path:
    from diony_producer.vault import vault_root

    return vault_root()


def _asset_path(project: Project, asset_id: str, *, vault: Path | None = None) -> Path:
    asset = project.asset(asset_id)
    path = Path(asset.path)
    return path if path.is_absolute() else (vault or _vault()) / path


@dataclass(frozen=True)
class PromptClip:
    """The clip Wan2.2 clones for one character, resolved on this host."""

    path: Path
    text: str
    source: str
    sha256: str


# --------------------------------------------------------------------------- casting


def _roster_index(project: Project, character_id: str) -> int:
    for index, character in enumerate(project.bible.characters):
        if character.id == character_id:
            return index
    return len(project.bible.characters)


def _character(project: Project, character_id: str):
    for character in project.bible.characters:
        if character.id == character_id:
            return character
    raise VoicePipelineError(f"{character_id} is not a Bible character on this Project")


class VoiceCastingRegistry:
    """Project-scoped casting decisions: which prompt clip each character clones."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def current(self, character_id: str) -> VoiceCasting | None:
        character = next((item for item in self.project.bible.characters if item.id == character_id), None)
        if character is not None and character.voice_casting_id:
            for casting in self.project.voice_castings:
                if casting.id == character.voice_casting_id:
                    return casting
        for casting in reversed(self.project.voice_castings):
            if casting.character_id == character_id:
                return casting
        return None

    def ensure_library(self, character_id: str) -> VoiceCasting:
        """Cast the character on the site's library by roster order (idempotent)."""
        existing = self.current(character_id)
        if existing is not None:
            return existing
        character = _character(self.project, character_id)
        casting = VoiceCasting(
            id=f"cast-{character.id}-v1",
            project_id=self.project.id,
            character_id=character.id,
            source=VoiceCastingSource.LIBRARY,
            cast_index=_roster_index(self.project, character.id),
            descriptors={"name": character.name, "notes": character.notes, "wardrobe": character.wardrobe},
            locked=True,
            locked_at=datetime.now(timezone.utc),
        )
        self.project.voice_castings.append(casting)
        character.voice_casting_id = casting.id
        return casting

    def cast_from_reference(
        self,
        character_id: str,
        asset_id: str,
        *,
        prompt_text: str = "",
        descriptors: dict[str, str] | None = None,
        vault: Path | None = None,
    ) -> VoiceCasting:
        """Cast the character on a local reference clip the maker attached.

        Wan2.2's TTS clones a 5–15 s clip above 16 kHz; the transcript, when
        given, must match the clip exactly (zero-shot cloning), otherwise Wan
        clones cross-lingually from the audio alone. A reference casting is a
        Bible-lock decision: it replaces a library casting and is itself
        immutable — recasting needs a new Project or versioned Bible.
        """
        from diony_producer.speech_audio import SpeechAudioError, media_duration_seconds, prompt_clip_sample_rate

        character = _character(self.project, character_id)
        existing = self.current(character_id)
        if existing is not None and existing.source == VoiceCastingSource.REFERENCE:
            raise VoicePipelineError(
                "This character already has a locked reference voice. Create a new Project or versioned Bible to change identity."
            )
        try:
            asset = self.project.asset(asset_id)
        except KeyError as exc:
            raise VoicePipelineError(f"Voice reference asset {asset_id} is not on this Project") from exc
        if asset.kind != AssetKind.AUDIO:
            raise VoicePipelineError(f"Voice reference asset {asset_id} is not audio")
        path = _asset_path(self.project, asset_id, vault=vault)
        if not path.is_file() or path.stat().st_size <= 44:
            raise VoicePipelineError(f"Voice reference bytes are missing: {asset_id}")
        duration = media_duration_seconds(path)
        if not PROMPT_CLIP_MIN_SECONDS <= duration <= PROMPT_CLIP_MAX_SECONDS:
            raise VoicePipelineError(
                f"Wan2.2 clones a {PROMPT_CLIP_MIN_SECONDS:g}–{PROMPT_CLIP_MAX_SECONDS:g} second prompt clip; "
                f"{asset.filename} runs {duration:.1f} s"
            )
        try:
            rate = prompt_clip_sample_rate(path)
        except SpeechAudioError:
            rate = 0
        if rate and rate < PROMPT_CLIP_MIN_SAMPLE_RATE:
            raise VoicePipelineError(
                f"Wan2.2 clones prompt clips at {PROMPT_CLIP_MIN_SAMPLE_RATE // 1000} kHz or better; {asset.filename} is {rate} Hz"
            )
        version = 1 + sum(1 for item in self.project.voice_castings if item.character_id == character_id)
        casting = VoiceCasting(
            id=f"cast-{character.id}-v{version}",
            project_id=self.project.id,
            character_id=character.id,
            source=VoiceCastingSource.REFERENCE,
            cast_index=_roster_index(self.project, character.id),
            prompt_audio_asset_id=asset.id,
            prompt_audio_sha256=asset.sha256 or _sha256_file(path),
            prompt_duration_seconds=round(duration, 3),
            prompt_text=" ".join(str(prompt_text or asset.metadata.get("transcript") or "").split()),
            descriptors=dict(descriptors or {}),
            locked=True,
            locked_at=datetime.now(timezone.utc),
        )
        self.project.voice_castings.append(casting)
        character.voice_casting_id = casting.id
        return casting


def _library_clips(library: Path) -> list[Path]:
    if not library.is_dir():
        return []
    return sorted(
        path for path in library.iterdir() if path.is_file() and path.suffix.lower() in PROMPT_CLIP_SUFFIXES
    )


def _sidecar_transcript(clip: Path) -> str:
    for candidate in (clip.with_suffix(".txt"), clip.with_name(clip.name + ".txt")):
        if candidate.is_file():
            try:
                return " ".join(candidate.read_text(encoding="utf-8").split())
            except OSError:
                continue
    return ""


def resolve_prompt_clip(project: Project, casting: VoiceCasting, *, vault: Path | None = None) -> PromptClip:
    """The clip Wan2.2 clones for this casting, on this host.

    Reference castings resolve to the attached Asset. Library castings resolve
    to the site's casting library (``DIONY_WAN_TTS_PROMPT_LIBRARY``, clips in
    name order, slot = cast index modulo the library size, transcript from a
    sibling ``.txt``), then to the single default clip
    (``DIONY_WAN_TTS_PROMPT_AUDIO`` / ``DIONY_WAN_TTS_PROMPT_TEXT``).
    """
    if casting.source == VoiceCastingSource.REFERENCE:
        path = _asset_path(project, casting.prompt_audio_asset_id, vault=vault)
        if not path.is_file() or path.stat().st_size <= 44:
            raise VoicePipelineError(f"Voice reference bytes are missing for {casting.character_id}")
        return PromptClip(path=path, text=casting.prompt_text, source="reference", sha256=casting.prompt_audio_sha256 or _sha256_file(path))
    library_dir = os.environ.get("DIONY_WAN_TTS_PROMPT_LIBRARY", "").strip()
    clips = _library_clips(Path(library_dir)) if library_dir else []
    if clips:
        clip = clips[casting.cast_index % len(clips)]
        return PromptClip(path=clip, text=_sidecar_transcript(clip), source=f"library:{clip.name}", sha256=_sha256_file(clip))
    default = os.environ.get("DIONY_WAN_TTS_PROMPT_AUDIO", "").strip()
    if default and Path(default).is_file():
        text = " ".join(os.environ.get("DIONY_WAN_TTS_PROMPT_TEXT", "").split()) or _sidecar_transcript(Path(default))
        return PromptClip(path=Path(default), text=text, source="default", sha256=_sha256_file(Path(default)))
    raise VoicePipelineError(
        "Voice casting failed: no Wan2.2 prompt clip is available on this host "
        "(DIONY_WAN_TTS_PROMPT_LIBRARY / DIONY_WAN_TTS_PROMPT_AUDIO). Studio Connect seeds the casting library "
        "with the Wan2.2 speech pack; attach a 5–15 s reference clip to cast this character directly."
    )


# --------------------------------------------------------------------------- the line


def shot_speaks(shot: Shot) -> bool:
    return bool(shot.dialogue_turns) or bool(" ".join(str(shot.audio_line or "").split()))


def _primary_character_id(project: Project, shot: Shot) -> str:
    known = {character.id for character in project.bible.characters}
    for character_id in shot.character_ref_ids:
        if character_id in known:
            return character_id
    if project.bible.characters:
        return project.bible.characters[0].id
    raise VoicePipelineError(f"{shot.id} speaks but the Bible has no character to cast")


def spoken_line(project: Project, shot: Shot) -> tuple[str, str]:
    """(character id, text) for the Shot's line: one on-camera speaker per window."""
    turns = [turn for turn in shot.dialogue_turns if " ".join(turn.text.split())]
    if turns:
        speakers = list(dict.fromkeys(turn.character_id or _primary_character_id(project, shot) for turn in turns))
        if len(speakers) > 1:
            raise VoicePipelineError(
                f"Voice casting failed: {shot.id} carries lines for {', '.join(speakers)}; "
                "Wan2.2 S2V performs one speaker per window, split the Shot."
            )
        ordered = sorted(turns, key=lambda turn: turn.start_ms)
        return speakers[0], " ".join(" ".join(turn.text.split()) for turn in ordered)
    text = " ".join(str(shot.audio_line or "").split())
    if not text:
        raise VoicePipelineError(f"{shot.id} has no dialogue")
    return _primary_character_id(project, shot), text


def window_ms(shot: Shot) -> int:
    rate = shot.fps if shot.fps and shot.fps > 1 else 23.976
    return max(1, round(shot.duration_frames / rate * 1000))


def speech_request_for(project: Project, shot: Shot, casting: VoiceCasting, *, prompt: PromptClip | None) -> WanSpeechRequest:
    character_id, text = spoken_line(project, shot)
    if casting.character_id != character_id:
        raise VoicePipelineError(f"{shot.id} is cast for {casting.character_id} but {character_id} speaks the line")
    return WanSpeechRequest(
        character_id=character_id,
        casting_id=casting.id,
        tts_text=text,
        tts_prompt_audio_sha256=prompt.sha256 if prompt is not None else "",
        tts_prompt_text=prompt.text if prompt is not None else casting.prompt_text,
        prompt_source=prompt.source if prompt is not None else "",
        window_ms=window_ms(shot),
        performance_direction=(shot.prompt_plan.motion if shot.prompt_plan is not None else ""),
    )


def _check_word_budget(shot: Shot, request: WanSpeechRequest) -> None:
    seconds = request.window_ms / 1000.0
    if request.word_count > math.ceil(seconds * MAX_WORDS_PER_SECOND):
        raise VoicePipelineError(
            f"Voice timing failed: {request.word_count} words cannot finish inside {shot.id}'s "
            f"{seconds:.1f} s window (Wan2.2 speaks about {MAX_WORDS_PER_SECOND:g} words per second at most); "
            "split the line across more Shots."
        )


def _condition_shot(project: Project, shot: Shot, take: Take) -> None:
    """Route the Shot to S2V with the spoken performance direction; the line rides the TTS inputs."""
    shot.engine = Engine.S2V_14B
    take.engine = Engine.S2V_14B
    # The authored line is the performance: any earlier recorded/locked audio
    # asset on the Shot would make the camera condition on it instead.
    shot.audio_asset_ids = []
    if shot.prompt_plan is not None:
        shot.prompt_plan.generation_method = Engine.S2V_14B.value
        shot.prompt_plan.audio_strategy = WAN_NATIVE_TTS_AUDIO_STRATEGY
        directed = spoken_wan_prompt_plan(
            shot.prompt_plan,
            speaker=speaker_name(project.bible, shot),
            focal_mm=shot.focal_mm,
            emotion=spoken_emotion(shot),
            audio_strategy=WAN_NATIVE_TTS_AUDIO_STRATEGY,
        )
        if directed is not None:
            shot.prompt_plan = directed
            shot.prompt = render_wan_prompt(directed)
            shot.negative_prompt = render_negative_prompt(directed)


def prepare_speech_for_take(project: Project, take: Take, *, vault: Path | None = None) -> SpeechClipPackage | None:
    """Lock what Wan2.2 will be asked to say for this Take, before the camera rolls.

    Returns None for a Shot that does not speak. Raises :class:`VoicePipelineError`
    (a pre-camera hold; no Take is spent) when the line cannot be cast on this
    host or cannot fit its window.
    """
    shot = project.shot(take.shot_id)
    if not shot_speaks(shot):
        return None
    character_id, _text = spoken_line(project, shot)
    registry = VoiceCastingRegistry(project)
    casting = registry.current(character_id) or registry.ensure_library(character_id)
    prompt: PromptClip | None
    try:
        prompt = resolve_prompt_clip(project, casting, vault=vault)
    except VoicePipelineError:
        if not _enabled("DIONY_DRY_RUN"):
            raise
        prompt = None
    request = speech_request_for(project, shot, casting, prompt=prompt)
    _check_word_budget(shot, request)
    current = take.voice_package
    if current is not None and current.request.content_sha256 == request.content_sha256 and current.qc is not None:
        # The same request already rendered and was measured; keep the evidence.
        _condition_shot(project, shot, take)
        return current
    package = SpeechClipPackage(
        id=f"speech-{take.id}-{request.content_sha256[:12]}",
        project_id=project.id,
        shot_id=shot.id,
        take_id=take.id,
        character_id=character_id,
        casting_id=casting.id,
        request=request,
        backend=SPEECH_BACKEND,
        state=VoiceRenderState.PLANNED,
    )
    take.voice_package = package
    _condition_shot(project, shot, take)
    return package


def wan_speech_inputs(project: Project, shot: Shot, *, vault: Path | None = None) -> dict[str, str | None] | None:
    """What the camera passes to Wan's TTS lane for this Shot, or None for a silent Shot."""
    if not shot_speaks(shot):
        return None
    character_id, text = spoken_line(project, shot)
    casting = VoiceCastingRegistry(project).current(character_id)
    if casting is None:
        raise VoicePipelineError(f"Voice casting failed: {character_id} has no voice casting on this Project")
    prompt = resolve_prompt_clip(project, casting, vault=vault)
    return {
        "tts_text": text,
        "tts_prompt_audio": str(prompt.path),
        "tts_prompt_text": prompt.text or None,
    }


# --------------------------------------------------------------------------- after the render


def _append_asset(project: Project, path: Path, *, role: str, take_id: str, kind: AssetKind, mime: str, vault: Path) -> Asset:
    digest = _sha256_file(path)
    relative = str(path.relative_to(vault)) if path.is_relative_to(vault) else str(path)
    asset = Asset(
        id=f"ast-{role.replace('_', '-')}-{digest[:16]}",
        kind=kind,
        filename=path.name,
        mime=mime,
        bytes=path.stat().st_size,
        path=relative,
        sha256=digest,
        role=role,
        metadata={"voice_lane_version": VOICE_LANE_VERSION, "take_id": take_id},
    )
    existing = next((item for item in project.assets if item.id == asset.id), None)
    if existing is None:
        project.assets.append(asset)
        return asset
    return existing


def verify_speech_render(project: Project, take: Take, video: Path, *, vault: Path | None = None) -> SpeechClipPackage:
    """Measure the speech Wan2.2 delivered in this negative and bind it to the Take."""
    from diony_producer.speech_audio import (
        SpeechAudioError,
        audio_streams,
        conform_mix_stem,
        decode_pcm,
        extract_dialogue,
        media_duration_seconds,
        measure_loudness,
        speech_activity,
    )

    package = take.voice_package
    if package is None:
        raise VoicePipelineError(f"{take.id} has no Wan speech request; the line was never locked before camera")
    if not video.is_file() or video.stat().st_size <= 32:
        raise VoicePipelineError(f"{take.id} has no rendered negative to measure")
    vault = vault or _vault()
    shot = project.shot(take.shot_id)
    window = float(window_ms(shot))
    target_lufs = -24.0 if project.bible.loudness == LoudnessTarget.BROADCAST else -16.0
    ceiling = float(project.bible.true_peak_dbtp)
    root = vault / "projects" / project.id / "voice" / "takes" / take.id
    root.mkdir(parents=True, exist_ok=True)
    diagnostics: list[str] = [f"backend={SPEECH_BACKEND}", f"request={package.request.content_sha256[:12]}"]
    words = int(package.request.word_count or len(package.request.tts_text.split()))
    floor = presence_floor_for(words, window)
    receipt_fields: dict[str, object] = {
        "measurement_version": SPEECH_QC_VERSION,
        "window_ms": window,
        "loudness_target_lufs": target_lufs,
        "true_peak_ceiling_dbtp": ceiling,
        "speech_presence_min": floor,
        "line_word_count": words,
        "expected_speech_ms": expected_speech_ms(words),
        "request_sha256": package.request.content_sha256,
    }
    diagnostics.append(f"presence floor {floor:.2f} for a {words}-word line in {window:.0f} ms")
    dialogue_asset: Asset | None = None
    stem_asset: Asset | None = None
    try:
        present = bool(audio_streams(video))
        receipt_fields["audio_present"] = present
        if present:
            dialogue = extract_dialogue(video, root / "dialogue.wav")
            duration_ms = round(media_duration_seconds(dialogue) * 1000.0, 1)
            presence, last_active_ms = speech_activity(decode_pcm(dialogue, sample_rate=16_000), sample_rate=16_000)
            receipt_fields["duration_ms"] = duration_ms
            receipt_fields["speech_presence_score"] = presence
            receipt_fields["dialogue_sha256"] = _sha256_file(dialogue)
            if last_active_ms is not None:
                receipt_fields["speech_tail_ms"] = max(0.0, round(window - last_active_ms, 1))
                diagnostics.append(f"speech ends at {last_active_ms:.0f} ms of {window:.0f}")
            stem = conform_mix_stem(
                dialogue,
                root / "mix-stem.wav",
                duration_seconds=window / 1000.0,
                target_lufs=target_lufs,
                true_peak_dbtp=ceiling,
            )
            integrated, true_peak = measure_loudness(stem, target_lufs=target_lufs, true_peak_dbtp=ceiling)
            receipt_fields["integrated_lufs"] = integrated
            receipt_fields["true_peak_dbtp"] = true_peak
            # Blueprint 24: with a local Whisper model on the host, the line Wan
            # performed is heard back and scored against the authored words.
            from diony_producer.speech_transcript import transcribe_line

            heard = transcribe_line(dialogue, package.request.tts_text)
            if heard is not None:
                from diony_producer.speech_transcript import normalize_words

                receipt_fields["transcript"] = heard.transcript
                receipt_fields["transcript_word_accuracy"] = heard.word_accuracy
                receipt_fields["transcript_last_word_ok"] = heard.last_word_ok
                receipt_fields["transcript_word_count"] = len(normalize_words(package.request.tts_text))
                diagnostics.append(
                    f"transcript ({heard.model}) matches {heard.word_accuracy:.0%} of the line"
                    + ("" if heard.last_word_ok else "; the last authored word was not heard")
                    + (f"; not heard: {' '.join(heard.missed_words)}" if heard.missed_words else "")
                )
            dialogue_asset = _append_asset(project, dialogue, role="voice_dialogue", take_id=take.id, kind=AssetKind.AUDIO, mime="audio/wav", vault=vault)
            stem_asset = _append_asset(project, stem, role="voice_mix_stem", take_id=take.id, kind=AssetKind.AUDIO, mime="audio/wav", vault=vault)
        else:
            diagnostics.append("the negative has no audio stream; Wan's TTS lane did not mux its speech")
    except SpeechAudioError as exc:
        diagnostics.append(f"measurement failed: {exc}")
    receipt = SpeechQCReceipt(**receipt_fields, passed=False, diagnostics=diagnostics)
    failures = receipt.failures()
    passed = not failures
    receipt = receipt.model_copy(update={"passed": passed, "diagnostics": diagnostics + [f"gate: {item}" for item in failures]})
    updated = package.model_copy(
        update={
            "dialogue_asset_id": dialogue_asset.id if dialogue_asset is not None else "",
            "mix_stem_asset_id": stem_asset.id if stem_asset is not None else "",
            "video_sha256": _sha256_file(video),
            "state": VoiceRenderState.VERIFIED if passed else VoiceRenderState.FAILED,
            "qc": receipt,
        }
    )
    updated = SpeechClipPackage.model_validate(updated.model_dump(mode="json"))
    take.voice_package = updated
    return updated


def speech_failure_message(package: SpeechClipPackage | None) -> str:
    """Name the measured speech gate that failed instead of hiding it."""
    if package is None or package.qc is None:
        return "Voice audio failed: the speech Wan2.2 delivered was never measured."
    failures = package.qc.failures()
    detail = "; ".join(failures) if failures else "the delivered speech did not pass"
    return f"Voice audio failed: {detail}."


# --------------------------------------------------------------------------- host pack


def speech_pack_missing(wan_repo: Path | None = None) -> list[str]:
    """What Wan2.2's own TTS lane still lacks beside generate.py on this host."""
    repo = wan_repo or _wan_repo()
    missing: list[str] = []
    checkout = repo / WAN_TTS_CHECKOUT
    if not all((checkout / relative).exists() for relative in WAN_TTS_CHECKOUT_FILES):
        missing.append(f"Wan TTS checkout {checkout}")
    model = repo / WAN_TTS_MODEL_DIR
    absent = [name for name in WAN_TTS_MODEL_FILES if not (model / name).is_file()]
    if absent:
        missing.append(f"CosyVoice2-0.5B weights {model} ({', '.join(absent)})")
    return missing


def host_speech_env(*, vault: Path | None = None, wan_repo: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Late-bind the Studio-prepared Wan speech pack from the GPU host layout.

    The GPU Producer inherits its environment when the Connect line launches
    it, but Studio App can still be pulling the pack at that moment. Rather
    than hold spoken Shots on a stale empty environment, Production re-derives
    the same values from the host layout when the first spoken Take needs
    them. Nothing is downloaded here. Returns ``(env, missing)``.
    """
    vault = vault or _vault()
    repo = wan_repo or _wan_repo()
    missing = speech_pack_missing(repo)
    env: dict[str, str] = {}
    library = vault / "runtime" / "voice" / "prompt-library"
    if _library_clips(library):
        env["DIONY_WAN_TTS_PROMPT_LIBRARY"] = str(library)
    default = repo / "examples" / "zero_shot_prompt.wav"
    if not default.is_file():
        default = repo / WAN_TTS_CHECKOUT / "asset" / "zero_shot_prompt.wav"
    if default.is_file():
        env["DIONY_WAN_TTS_PROMPT_AUDIO"] = str(default)
        env["DIONY_WAN_TTS_PROMPT_TEXT"] = DEFAULT_PROMPT_TEXT
    elif "DIONY_WAN_TTS_PROMPT_LIBRARY" not in env:
        missing.append("casting library or default prompt clip")
    if missing:
        return {}, missing
    return env, []


# Transcript of Wan2.2's bundled examples/zero_shot_prompt.wav (the upstream S2V TTS example).
DEFAULT_PROMPT_TEXT = "希望你以后能够做的比我还好呦。"


def bind_host_speech_env() -> None:
    """Fill the speech-pack environment the launch left empty from the host layout."""
    if all(os.environ.get(key, "").strip() for key in HOST_SPEECH_ENV_KEYS[:2]):
        return
    discovered, missing = host_speech_env()
    if not discovered:
        raise VoicePipelineError(
            "Voice is required, but the Wan2.2 speech pack is not ready on this host "
            f"(missing: {'; '.join(missing) or 'no speech pack'}). Studio Connect prepares it; "
            "raw or post-picture TTS is forbidden."
        )
    for key, value in discovered.items():
        if not os.environ.get(key, "").strip():
            os.environ[key] = value
    from diony_producer.vault import record_activity

    record_activity("voice", "Wan2.2 speech pack bound after Producer launch: casting library found on this host.", level="info")


def prepare_voice_for_take(project: Project, take: Take) -> SpeechClipPackage | None:
    """Farm entry: lock the Wan speech request for a spoken Take (None for a silent one)."""
    shot = project.shot(take.shot_id)
    if not shot_speaks(shot):
        return None
    if not _enabled("DIONY_DRY_RUN"):
        bind_host_speech_env()
    return prepare_speech_for_take(project, take)


def verify_voice_animation(project: Project, take: Take, video: Path) -> SpeechClipPackage | None:
    """Farm entry: measure the delivered speech on a rendered spoken negative."""
    if take.voice_package is None:
        return None
    return verify_speech_render(project, take, video)
