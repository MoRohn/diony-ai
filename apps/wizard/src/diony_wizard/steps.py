"""Wizard step machine — Blueprint 01 / 11 / 14. Day 2 is dry-run (no fabric mutation)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

LOCAL_RAIL = [
    "path",
    "unbox",
    "inventory",
    "cables",
    "switch",
    "sync",
    "roce",
    "vault",
    "producer",
]

# Provider already racked the GPUs. Site → Connect → Ready.
LEASE_RAIL = [
    "path",
    "connect",
    "producer",
]

# Leased Dual-Spark: Site → Connect → App → Producer. Pack is on App. Brain is TP=2 sequential.
DUAL_RAIL = [
    "path",
    "connect",
    "app",
    "producer",
]

# Leased B300: Site → Connect → App → Producer. Pack is on App. Brain is auto.
B300_RAIL = [
    "path",
    "connect",
    "app",
    "producer",
]

STEP_IDS = LOCAL_RAIL
ALL_STEP_IDS = list(
    dict.fromkeys(LOCAL_RAIL + LEASE_RAIL + DUAL_RAIL + B300_RAIL + ["weights", "brain_smoke"])
)


@dataclass
class Step:
    id: str
    name: str
    pass_criteria: str
    operator: str
    status: str = "pending"  # pending | active | passed | failed | skipped
    log: list[str] = field(default_factory=list)

    def as_public(self) -> dict:
        return asdict(self)


STEPS: list[Step] = [
    Step(
        "path",
        "Site",
        "Local Quad-Spark, Leased Dual-Spark, or Leased B300; leases record a named human",
        "Never cloud-by-default. Same Diony on every site.",
    ),
    Step(
        "connect",
        "Connect",
        "Host and vault recorded for this lease",
        "Lease only. Provider already owns the box. App and Producer come next.",
    ),
    Step(
        "unbox",
        "Unbox",
        "Crate on the desk before power",
        "Local crate only. Lease and leased B300 skip this line.",
    ),
    Step(
        "inventory",
        "Nodes",
        "Hosts the software will use are recorded",
        "Desk: Spark serials. Lease hosts are recorded on Connect.",
    ),
    Step(
        "cables",
        "Cables",
        "Training path separate from vault and Producer",
        "Desk crate only.",
    ),
    Step(
        "switch",
        "Switch",
        "Autoneg off; 200G forced; config saved",
        "Desk crate only. 50G is a fail.",
    ),
    Step(
        "sync",
        "Sync",
        "NVIDIA Sync reports healthy SSH-over-fabric",
        "Desk crate only. Diony wraps Sync; it does not replace it.",
    ),
    Step(
        "roce",
        "Fabric",
        "Full fabric class; NCCL on IB/RoCE, not Socket",
        "Desk crate only.",
    ),
    Step(
        "vault",
        "Vault",
        "/diony/vault is where dailies and masters live",
        "Private mount. Never a public bucket as the bible.",
    ),
    Step(
        "weights",
        "Models",
        "Site pack on /models: pinned DeepSeek 0731 + Wan2.2 A14B; Brain auto",
        "Not a Wizard page. Recorded when App (lease) or Producer (desk) is continued.",
    ),
    Step(
        "brain_smoke",
        "Brain",
        "Auto with the model pack (not a Wizard page)",
        "Desk: spark-4x-v1 TP=4. Dual-Spark: spark-2x-v1 TP=2. B300: b300-v1 TP=1. Recorded with the pack.",
    ),
    Step(
        "app",
        "App",
        "Pinned pack on /models; Producer installed and running",
        "Pack is pinned for this site. Pull it, start Producer, Continue.",
    ),
    Step(
        "producer",
        "Producer",
        "Producer URL is recorded; floors include Program stitch and prompt-to-edit",
        "Producer is http://producer.diony.local. Sign this last line, then open it.",
    ),
]

STEP_BY_ID = {s.id: s for s in STEPS}


def rail_ids(path: str | None) -> list[str]:
    if path == "lease":
        return list(LEASE_RAIL)
    if path == "dual":
        return list(DUAL_RAIL)
    if path == "b300":
        return list(B300_RAIL)
    if path == "local":
        return list(LOCAL_RAIL)
    return ["path"]


def _copy_step(step_id: str, status: str, name: str | None = None) -> Step:
    src = STEP_BY_ID[step_id]
    return Step(src.id, name or src.name, src.pass_criteria, src.operator, status, [])


def initial_steps() -> list[Step]:
    site = _copy_step("path", "active")
    return [site]


def attach_rail(path: str, site_step: Step) -> list[Step]:
    """Site is already passed. Reveal the rest of this site’s rail."""
    names = {
        "dual": {
            "app": "App",
            "producer": "Producer",
        },
        "b300": {
            "app": "App",
            "producer": "Producer",
        },
        "lease": {"producer": "Ready"},
    }
    rest: list[Step] = []
    for step_id in rail_ids(path):
        if step_id == "path":
            continue
        label = names.get(path, {}).get(step_id)
        copied = _copy_step(step_id, "pending", label)
        rest.append(copied)
    if rest:
        rest[0].status = "active"
    return [site_step] + rest
