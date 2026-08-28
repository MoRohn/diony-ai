from enum import StrEnum


class ComputePath(StrEnum):
    LOCAL = "local"
    DUAL = "dual"
    LEASE = "lease"
    B300 = "b300"
    B200 = "b200"


class FleetMode(StrEnum):
    BRAIN = "BRAIN"
    SPLIT = "SPLIT"
    FARM = "FARM"
    FINISH = "FINISH"


class Engine(StrEnum):
    T2V_A14B = "t2v-A14B"
    I2V_A14B = "i2v-A14B"
    TI2V_5B = "ti2v-5B"
    S2V_14B = "s2v-14B"
    ANIMATE_14B = "animate-14B"
    VACE_FUN_A14B = "vace-fun-A14B"


class ReferenceLock(StrEnum):
    NONE = "none"
    CHARACTER = "character"
    CHARACTER_AND_BACKGROUND = "character_and_background"


class ContinuityMode(StrEnum):
    INDEPENDENT = "independent"
    REFERENCE = "reference"
    PREVIOUS_FRAME = "previous_frame"


class FinishIntent(StrEnum):
    PREVIZ = "previz"
    PROXY = "proxy"
    DELIVERABLE = "deliverable"


class CameraMove(StrEnum):
    STATIC = "static"
    PAN = "pan"
    TILT = "tilt"
    DOLLY_IN = "dolly_in"
    DOLLY_OUT = "dolly_out"
    TRACK = "track"
    CRANE = "crane"
    HANDHELD = "handheld"
    STEADICAM = "steadicam"
    ZOOM = "zoom"


class Grain(StrEnum):
    NONE = "none"
    FINE = "fine"
    GRAIN_35MM = "35mm"


class LoudnessTarget(StrEnum):
    BROADCAST = "broadcast"  # -24 LUFS
    STREAMING = "streaming"  # -16 LUFS


class SoundtrackPolicy(StrEnum):
    """Whether generated (non-speech) audio may join the licensed soundtrack."""

    LICENSED_ONLY = "licensed_only"
    GENERATED_OK = "generated_ok"


class QCVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    BIBLE_PENDING = "bible_pending"
    BIBLE_LOCKED = "bible_locked"
    SHOOTING = "shooting"
    PICTURE_PENDING = "picture_pending"
    PICTURE_LOCKED = "picture_locked"
    MIX_PENDING = "mix_pending"
    MIX_LOCKED = "mix_locked"
    MASTERED = "mastered"
    DISTRIBUTED = "distributed"
    STOPPED = "stopped"


class GateName(StrEnum):
    BIBLE = "bible"
    PICTURE = "picture"
    MIX = "mix"
    DISTRIBUTION = "distribution"


class PictureLockState(StrEnum):
    OPEN = "open"
    LOCKED = "locked"


class MixLockState(StrEnum):
    OPEN = "open"
    LOCKED = "locked"


class NoteAuthor(StrEnum):
    HUMAN = "human"
    CONDUCTOR = "conductor"
    QC = "qc"


class DeliverableKind(StrEnum):
    PRORES_HQ = "prores_hq"
    DNXHR_HQ = "dnxhr_hq"
    H265_PROXY = "h265_proxy"
    STEMS = "stems"
    QC_PACKET = "qc_packet"


class CustomerKind(StrEnum):
    FEATURE = "feature"
    SERIES = "series"
    COMMERCIAL = "commercial"
    MUSIC_VIDEO = "music_video"
    EDUCATION = "education"
    SOCIAL = "social"


class ProductionKind(StrEnum):
    PROGRAM = "program"
    SAMPLE = "sample"


class ProductionStopDisposition(StrEnum):
    KEEP = "keep"
    DELETE = "delete"


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class ArtifactKind(StrEnum):
    NEGATIVE_720 = "negative_720"
    FINISH_1080 = "finish_1080"
    PROXY = "proxy"


class TakeOrigin(StrEnum):
    GENERATION = "generation"
    EDIT = "edit"


class GalleryTransition(StrEnum):
    CUT = "cut"
    CROSS_DISSOLVE = "cross_dissolve"
    FILM_DISSOLVE = "film_dissolve"
    FADE_THROUGH_BLACK = "fade_through_black"
    DIP_TO_WHITE = "dip_to_white"
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"


class GalleryCompositionStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class VoiceCastingSource(StrEnum):
    """Where a character's Wan2.2 S2V voice prompt clip comes from."""

    LIBRARY = "library"  # a clip from the site's local casting library, keyed by cast order
    REFERENCE = "reference"  # a local audio Asset attached to the Project (5–15 s, ≥ 16 kHz)


class VoiceRenderState(StrEnum):
    PLANNED = "planned"
    AUDIO_LOCKED = "audio_locked"
    VERIFIED = "verified"
    FAILED = "failed"
