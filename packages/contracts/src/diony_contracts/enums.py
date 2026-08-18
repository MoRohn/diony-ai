from enum import StrEnum


class ComputePath(StrEnum):
    LOCAL = "local"
    DUAL = "dual"
    LEASE = "lease"
    B300 = "b300"


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


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"


class ArtifactKind(StrEnum):
    NEGATIVE_720 = "negative_720"
    FINISH_1080 = "finish_1080"
    PROXY = "proxy"


class TakeOrigin(StrEnum):
    GENERATION = "generation"
    EDIT = "edit"
