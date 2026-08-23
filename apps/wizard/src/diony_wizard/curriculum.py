"""Learn + do curriculum for the Studio. IDs match Blueprint 01 / 14."""

from __future__ import annotations

import os

from diony_wizard.lesson import Field, Lesson, field_applies, normalize
from diony_wizard.prep import (
    PACK_LABEL,
    PRODUCER_URL,
    SHOW_FLOORS,
    hosts_commands,
    pack_pull_commands,
    parse_lease_hosts,
    parse_ssh_port,
    parse_ssh_user,
    producer_start_commands,
    vault_is_public,
)

CABLE_MAP = """spark-01 CX7 port2  ----QSFP----  switch fabric port A
spark-02 CX7 port2  ----QSFP----  switch fabric port B
spark-03 CX7 port2  ----QSFP----  switch fabric port C
spark-04 CX7 port2  ----QSFP----  switch fabric port D

spark-01..04 RJ-45  ----Cat6----  mgmt switch
NAS 10GbE           ----Cat6----  mgmt switch
Studio laptop       ----Cat6----  mgmt switch"""

PATH_LESSON = Lesson(
    "path",
    "Pick where this production will run. One place. Stay there.",
    [
        "Local Quad-Spark Cluster: four GB10 on this floor. Next you unbox them. This is the default. Do not pre-select a lease.",
        "Leased Dual-Spark Cluster: two GB10 with interconnect. Runpod Instant Clusters (2 nodes) first, Enverge 2× DGX Spark GB10 second. Connect, App, Ready, then Producer. Brain is TP=2, sequential. Not a B200 Instant Cluster.",
        "Leased B300: one Blackwell B300 (288 GB class). Runpod Deploy NVIDIA B300 first, Enverge NVIDIA B300 second. Connect, App, Ready, then Producer. Pack and Brain are on App.",
        "Leased B200: one Blackwell B200 (180 GB). Runpod Deploy NVIDIA B200 first, Enverge NVIDIA B200 second. Same rail as B300. Brain is TP=1 with max_model_len 16384. Pack and Brain are on App.",
        "Match Where? to the GPU you deployed. A B200 pod is Leased B200. A B300 pod is Leased B300. Picking the other Blackwell site is a fail.",
    ],
    [
        "A leased site with no name on it",
        "Picking Leased B300 for a B200 pod, or Leased B200 for a B300 pod",
        "Two Sparks with no fabric between them",
        "Recording a B200 Instant Cluster as Dual-Spark",
        "Switching sites in the middle of a production",
    ],
    [
        Field(
            "site",
            "Where?",
            "choice",
            options=["local", "dual", "b300", "b200"],
            accept=["local", "dual", "b300", "b200"],
            hint="One place. Local is the crate. Leases: Runpod first (https://www.runpod.io/), Enverge second (https://enverge.ai/).",
            option_help={
                "local": "Four GB10 on this floor. Next you unbox them.",
                "dual": "Two Sparks with fabric. Not a B200 Instant Cluster.",
                "b300": "One NVIDIA B300. Must match the GPU you deployed.",
                "b200": "One NVIDIA B200 (180 GB). Not B300. Must match the GPU you deployed.",
            },
        ),
        Field(
            "provider",
            "Provider",
            "text",
            placeholder="Runpod",
            when="dual,b300,b200",
            hint="Usually Runpod. Enverge is second.",
        ),
        Field(
            "signer",
            "Your name",
            "text",
            placeholder="Name",
            when="dual,b300,b200",
            hint="Named human on this lease.",
        ),
    ],
)

LOCAL_LESSONS: dict[str, Lesson] = {
    "unbox": Lesson(
        "unbox",
        "The SKU is a crate, not a cloud. Power comes after the physical line is true. Mixed nodes and a cheap power strip fail clustering before NVIDIA Sync ever opens.",
        [
            "Spark Cluster (4), or matched GB10 OEM, same DGX OS train, 4 TB NVMe BOM.",
            "Four 240 W bricks seated. PDU/UPS live. Plan 1.5–2.0 kW for desk.",
            "Fabric switch in the crate: CRS804-4DDQ-HRM or CRS812-8DS-2DQ-2DDQ.",
            "One display on spark-01, or NVIDIA Sync from a laptop on 10GbE.",
            "Do not apply power until this line is signed.",
        ],
        ["Missing UPS", "Mixed SKUs in the crate", "Powering on before cables are planned"],
        [
            Field(
                "sparks",
                "Spark Cluster (4) on the desk?",
                "select",
                options=["", "yes", "no"],
                accept=["yes"],
            ),
            Field(
                "bricks",
                "Four 240 W bricks seated?",
                "select",
                options=["", "yes", "no"],
                accept=["yes"],
            ),
            Field(
                "switch",
                "Fabric switch in the crate",
                "select",
                options=["", "CRS804-4DDQ-HRM", "CRS812-8DS-2DQ-2DDQ"],
                accept=["CRS804-4DDQ-HRM", "CRS812-8DS-2DQ-2DDQ"],
            ),
            Field(
                "ups",
                "UPS / PDU in the 1.5–2.0 kW class?",
                "select",
                options=["", "yes", "no"],
                accept=["yes"],
            ),
            Field(
                "console",
                "First-boot console",
                "select",
                options=["", "spark-01 display", "laptop Sync"],
                accept=["spark-01 display", "laptop Sync"],
            ),
            Field(
                "quiz",
                "Check: Spark Cluster (4) has how many nodes?",
                "quiz",
                placeholder="4",
                accept=["4", "four"],
            ),
        ],
        commands=[
            "Do not power Spark Cluster (4) until Unbox is signed.",
            "Label the crate: spark-4x-v1 · Diony AI.",
        ],
        diagram="4× Spark + switch + NAS + 10GbE + UPS  →  then power  →  inventory",
    ),
    "inventory": Lesson(
        "inventory",
        "Matched hardware is a clustering rule, not a preference. NVIDIA Sync assumes four nodes on the same DGX OS train.",
        [
            "Hostnames spark-01 … spark-04. spark-01 is primary (Studio + Producer).",
            "4 TB NVMe SKU is the BOM. 1 TB will hurt weights + scratch.",
            "OEM GB10 (ASUS / MSI / Founders) is OK if clustering-compatible.",
        ],
        ["Mixed firmware", "One node still on first-boot OTA", "Missing UPS"],
        [
            Field("s1", "spark-01 serial", "text", placeholder="GB10 serial"),
            Field("s2", "spark-02 serial", "text"),
            Field("s3", "spark-03 serial", "text"),
            Field("s4", "spark-04 serial", "text"),
            Field(
                "os_matched",
                "DGX OS matched on all four?",
                "select",
                options=["", "yes", "no"],
                accept=["yes"],
            ),
            Field(
                "quiz",
                "Check: which node runs Studio and Producer?",
                "quiz",
                placeholder="spark-01",
                accept=["spark-01", "spark 01", "spark01", "primary"],
            ),
        ],
        commands=[
            "hostnamectl  # expect spark-01 … spark-04",
            "cat /etc/dgx-release  # same train on all four",
        ],
    ),
    "cables": Lesson(
        "cables",
        "Two networks, never one. Fabric is RoCE on ConnectX-7. Management and the vault are 10GbE.",
        [
            "Standardize Spark CX-7 port 2 → fabric switch. Label both ends S1-P2 …",
            "RJ-45 on each Spark → management switch, with the NAS and your laptop.",
            "Breakout DACs: switch port 1 → spark-01+02, port 2 → spark-03+04.",
        ],
        ["QSFP in port 1", "NAS on the RoCE fabric", "Wi-Fi as management"],
        [
            Field(
                "port2",
                "All four fabric cables on Spark port 2?",
                "select",
                options=["", "yes", "no"],
                accept=["yes"],
            ),
            Field(
                "loom",
                "Click the loom until every Spark shows fabric on port 2",
                "check",
                accept=["yes"],
            ),
            Field(
                "quiz",
                "Check: the NAS belongs on which network?",
                "quiz",
                placeholder="10GbE / management",
                accept=["10gbe", "10 gb", "management", "mgmt", "rj45", "10g"],
            ),
        ],
        commands=[
            "Label both ends: S1-P2, S2-P2, S3-P2, S4-P2.",
            "NAS Cat6 → mgmt switch, never the QSFP fabric.",
        ],
        diagram=CABLE_MAP,
    ),
    "switch": Lesson(
        "switch",
        "Autoneg on these MikroTik SKUs often lands at 50G. That is a failed cluster, not a slow one.",
        [
            "SKU A: CRS804-4DDQ-HRM. SKU B: CRS812 (forum-proven Spark 4-node).",
            "Force 200G, autoneg off, MTU 9000, RS-FEC. Save RouterOS config.",
            "Snippets live in infra/switch/. Print interfaces before pasting on CRS812.",
        ],
        ["Autoneg left on", "MTU 1500 on fabric", "Config not saved across reboot"],
        [
            Field(
                "sku",
                "Fabric switch",
                "select",
                options=[
                    "",
                    "CRS804-4DDQ-HRM",
                    "CRS812-8DS-2DQ-2DDQ",
                ],
                accept=["CRS804-4DDQ-HRM", "CRS812-8DS-2DQ-2DDQ"],
            ),
            Field(
                "speed",
                "Forced port speed",
                "select",
                options=["", "50G", "100G", "200G"],
                accept=["200G"],
            ),
            Field(
                "quiz",
                "Check: autoneg should be…",
                "quiz",
                placeholder="off",
                accept=["off", "disabled", "no", "false"],
            ),
        ],
        commands=[
            "/interface ethernet print",
            "/export file=diony-spark-4x-v1",
        ],
        diagram="Autoneg on  →  50G  →  FAIL\nForce 200G, autoneg off, MTU 9000, RS-FEC  →  pass class",
    ),
    "sync": Lesson(
        "sync",
        "NVIDIA Sync Cluster Assistant owns ConnectX-7, netplan, SSH-over-fabric, and ib_write_bw. Diony does not reimplement it.",
        [
            "Enroll Spark Cluster (4) in Sync, run Cluster Assistant to healthy.",
            "Paste the fabric IPs here. Studio records them; it does not write netplan in dry-run.",
            "Official docs: DGX Spark clustering guide + Sync Cluster Assistant.",
        ],
        ["Sync only sees 10GbE", "Password SSH leftover", "One node missing from the cluster name"],
        [
            Field("cluster_name", "Sync cluster name", "text", placeholder="diony-spark-4x"),
            Field(
                "ips",
                "Fabric IPs (comma-separated, four)",
                "text",
                placeholder="192.168.200.11, .12, .13, .14",
            ),
            Field(
                "quiz",
                "Check: who configures CX-7 and SSH trust?",
                "quiz",
                placeholder="NVIDIA Sync",
                accept=["nvidia sync", "sync", "cluster assistant", "nvidia"],
            ),
        ],
        commands=[
            "Open NVIDIA Sync on a machine that already enrolled Spark Cluster (4).",
        ],
        handoff=[
            "Open NVIDIA Sync. Spark Cluster (4) must already be enrolled.",
            "Cluster Assistant: readiness (OTA, sudo) → CX-7 topology probe.",
            "Accept the IP plan. Let Sync write netplan.",
            "Wait for ib_write_bw / ib_write_lat and SSH over CX-7.",
            "Paste cluster name + four fabric IPs into this report.",
            "Continue to Diony RoCE gates. Those are ours, not Sync’s.",
        ],
    ),
    "roce": Lesson(
        "roce",
        "A single iperf flow near 100G is the CX-7 half-port, not a pass. Drive both logical halves.",
        [
            "Pass class: ~196–198 Gb/s aggregate per pair (community CRS812).",
            "NCCL must be IB/RoCE, not Socket. Delete leftover NCCL_IB_DISABLE=1.",
            "MTU 9000 end-to-end or performance dies quietly.",
        ],
        ["50G link", "NCCL on 10GbE", "GID index drift after reboot"],
        [
            Field(
                "iperf_class",
                "Dual-half iperf in the 190 Gb/s class (or dry-run recorded)?",
                "select",
                options=["", "yes-live", "dry-run-recorded", "no"],
                accept=["yes-live", "dry-run-recorded"],
            ),
            Field(
                "quiz",
                "Check: one iperf stream at ~100G means…",
                "quiz",
                placeholder="only one CX-7 half",
                accept=["half", "one half", "not a pass", "100g half", "single half"],
            ),
        ],
        commands=[
            "ibstat || rdma link   # CX-7 ACTIVE, 200G class",
            "python3 infra/health/check_cluster.py   # laptop dry-run",
            "python3 infra/health/check_cluster.py --live   # cluster Week 2+",
        ],
        diagram="one iperf ≈ 100G  →  one CX-7 half  →  not a pass\ntwo halves ≈ 196–198 Gb/s  →  Diony RoCE pass class",
    ),
    "vault": Lesson(
        "vault",
        "Node NVMe holds weights and scratch. Takes and masters live on the NAS at /diony/vault. A 1-hour production is multiple TB.",
        [
            "Where to find the NAS: the 10GbE management LAN, not RoCE. Open the NAS admin UI and copy the IPv4 and the NFS export (example /volume1/diony-vault).",
            "What to type: NAS address is that IPv4. Export path is the share on the NAS. Diony mounts it at /diony/vault on all four nodes.",
            "Need ≥50 TB usable, 10GbE (LACP preferred). Write /diony/vault/.diony-health/$HOSTNAME from every node.",
            "Never put vault traffic on the fabric switch.",
        ],
        ["1GbE NAS", "Vault only mounted on spark-01", "Selects only on scratch"],
        [
            Field(
                "nas_ip",
                "NAS address",
                "text",
                placeholder="192.168.10.50",
                hint="10GbE IPv4 from the NAS admin UI. Not a RoCE fabric address.",
            ),
            Field(
                "export",
                "Export path",
                "text",
                placeholder="/volume1/diony-vault",
                hint="The NFS share on the NAS. Diony mounts it at /diony/vault on all four nodes.",
            ),
            Field(
                "quiz",
                "Check: where do ProRes masters live?",
                "quiz",
                placeholder="/diony/vault",
                accept=["/diony/vault", "vault", "nas"],
            ),
        ],
        commands=[
            "touch /diony/vault/.diony-health/${HOSTNAME}   # all four nodes",
            "# fstab: infra/storage/vault.fstab.example",
        ],
        diagram="/models     local NVMe   weights\n/scratch    local NVMe   wipeable\n/diony/vault   NAS NFS   dailies + masters",
    ),
    "producer": Lesson(
        "producer",
        "The Spark pack is pinned. Pull it, start Producer (with finish), then Sign this last line.",
        [
            "Nothing to shop. The pack is DeepSeek-V4-Flash-0731 + Wan2.2 A14B. Pull commands are in How. Brain is spark-4x-v1 TP=4.",
            "Install contracts, wan, finish, and producer. Picture lock needs diony_sr_v1. Then start diony-producer with DIONY_VAULT=/diony/vault.",
            f"Producer opens at {PRODUCER_URL}. Same address on this laptop and on the cluster.",
            "Floors are Idea, Production, Gallery. Gallery is the finished clip inventory.",
            "Wan is 720p negative. Deliverables are Diony 1080. Sign writes site.json so Producer can read this site.",
        ],
        ["Shipping previz as a master", "Notes only in chat", "Cloud prompt extend", "Skipping Production screening"],
        [
            Field(
                "pack",
                "Pack",
                "select",
                options=[PACK_LABEL["local"]],
                accept=[PACK_LABEL["local"]],
                default=PACK_LABEL["local"],
                auto=True,
                hint="Pinned for Spark Cluster (4). Run How, then Sign.",
            ),
            Field(
                "floors",
                "Floors",
                "select",
                options=[SHOW_FLOORS],
                accept=[SHOW_FLOORS],
                default=SHOW_FLOORS,
                auto=True,
            ),
        ],
        commands=[
            "# 1. Pack — /models, then replicate to spark-02..04",
            *pack_pull_commands("local"),
            "# 2. Producer — install finish too. Picture lock needs diony_sr_v1.",
            *producer_start_commands(cluster=True),
            *hosts_commands("spark-01"),
        ],
        handoff=[
            "Sign this line. Studio writes site.json. On a live spark-01 it also starts Producer.",
            "Open Producer. Write the idea, Produce, then screen Production. Gallery holds the clips.",
        ],
    ),
}


def _book(path: str) -> dict[str, Lesson]:
    """Load a site book. Import errors become an empty book, never a 500."""
    try:
        if path == "local":
            return LOCAL_LESSONS
        if path == "dual":
            from diony_wizard.lessons_dual import DUAL_LESSONS

            return DUAL_LESSONS
        if path == "b300":
            from diony_wizard.lessons_b300 import B300_LESSONS

            return B300_LESSONS
        if path == "b200":
            from diony_wizard.lessons_b200 import B200_LESSONS

            return B200_LESSONS
        if path == "lease":
            from diony_wizard.lessons_lease import LEASE_LESSONS

            return LEASE_LESSONS
    except Exception:
        return {}
    return {}


def lesson_for(step_id: str, path: str | None = None) -> Lesson:
    if step_id == "path":
        return PATH_LESSON
    if path not in ("local", "lease", "dual", "b300", "b200"):
        raise KeyError("compute path not chosen")
    book = _book(path)
    if step_id not in book:
        raise KeyError(f"{path} {step_id} unavailable")
    return book[step_id]


def lessons_for(path: str | None) -> dict[str, Lesson]:
    out = {"path": PATH_LESSON}
    if path in ("local", "lease", "dual", "b300", "b200"):
        out.update(_book(path))
    return out


def with_defaults(step_id: str, answers: dict[str, str], path: str | None) -> dict[str, str]:
    out = dict(answers)
    try:
        lesson = lesson_for(step_id, None if step_id == "path" else path)
    except KeyError:
        return out
    site = str(out.get("site", "")).strip() or (path or "")
    for item in lesson.fields:
        if not field_applies(item, site):
            continue
        if not str(out.get(item.id, "")).strip() and item.default:
            out[item.id] = item.default
    return out


def validate_answers(
    step_id: str,
    answers: dict[str, str],
    path: str | None = None,
) -> str | None:
    if step_id != "path" and path is None:
        path = "local"
    answers = with_defaults(step_id, answers, None if step_id == "path" else path)
    lesson = lesson_for(step_id, path)
    site = str(answers.get("site", "")).strip() or (path or "")
    for field in lesson.fields:
        if not field_applies(field, site):
            continue
        raw = str(answers.get(field.id, "")).strip()
        if field.id == "hf_token":
            if os.environ.get("PYTEST_CURRENT_TEST"):
                continue
            if raw and raw not in {"set", "••••"}:
                continue
            if os.environ.get("HF_TOKEN", "").strip() or os.environ.get(
                "HUGGING_FACE_HUB_TOKEN", ""
            ).strip():
                continue
            return (
                "HF_TOKEN is empty. Open https://huggingface.co/settings/tokens, "
                "Create new token, paste it here."
            )
        if not raw:
            return f"{field.label} is empty."
        if field.accept:
            if field.kind == "quiz":
                got = normalize(raw)
                if not any(token in got or got == token for token in field.accept):
                    return f"Check missed. Re-read the lesson, then answer {field.label.lower()}."
            elif raw not in field.accept:
                return f"{field.label} is not a passing value."
    if step_id == "path" and site in ("b300", "b200", "dual"):
        if len(str(answers.get("signer", "")).strip()) < 2:
            return "This site requires a named human exception."
    if step_id == "connect":
        _, err = parse_lease_hosts(str(answers.get("hosts", "")), path)
        if err:
            return err
        if vault_is_public(str(answers.get("nas_ip", ""))):
            return "Vault must be private. Not a public bucket."
        if path in ("dual", "b300", "b200"):
            ssh_err = parse_ssh_user(str(answers.get("ssh_user", "")))
            if ssh_err:
                return ssh_err
        if path in ("b300", "b200"):
            _, port_err = parse_ssh_port(str(answers.get("ssh_port", "")))
            if port_err:
                return port_err
    if step_id == "sync":
        parts = [p.strip() for p in str(answers.get("ips", "")).split(",") if p.strip()]
        if len(parts) < 4:
            return "Need four fabric IPs, comma-separated."
    return None


# Tests and older imports still see a full local book plus Site.
CURRICULUM: dict[str, Lesson] = {"path": PATH_LESSON, **LOCAL_LESSONS}

# Warm lease books at import. Failure must not prevent Studio from starting.
try:
    from diony_wizard.lessons_b200 import B200_LESSONS as _B200_LESSONS  # noqa: E402, F401
except Exception:  # pragma: no cover — stale prep on a live process
    _B200_LESSONS = {}
try:
    from diony_wizard.lessons_b300 import B300_LESSONS as _B300_LESSONS  # noqa: E402, F401
except Exception:  # pragma: no cover
    _B300_LESSONS = {}
try:
    from diony_wizard.lessons_dual import DUAL_LESSONS as _DUAL_LESSONS  # noqa: E402, F401
except Exception:  # pragma: no cover
    _DUAL_LESSONS = {}
