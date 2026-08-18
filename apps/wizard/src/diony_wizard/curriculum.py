"""Learn + do curriculum for the Wizard. IDs match Blueprint 01 / 14."""

from __future__ import annotations

from diony_wizard.lesson import Field, Lesson, field_applies, normalize
from diony_wizard.prep import PACK_LABEL, PRODUCER_URL, SHOW_FLOORS, pack_pull_commands, parse_lease_hosts, vault_is_public

CABLE_MAP = """spark-01 CX7 port2  ----QSFP----  switch fabric port A
spark-02 CX7 port2  ----QSFP----  switch fabric port B
spark-03 CX7 port2  ----QSFP----  switch fabric port C
spark-04 CX7 port2  ----QSFP----  switch fabric port D

spark-01..04 RJ-45  ----Cat6----  mgmt switch
NAS 10GbE           ----Cat6----  mgmt switch
Wizard laptop       ----Cat6----  mgmt switch"""

PATH_LESSON = Lesson(
    "path",
    "Pick where this production will run. One place. Stay there.",
    [
        "Local Quad-Spark Cluster: four GB10 on this floor. Next you unbox them.",
        "Leased Dual-Spark Cluster: two rented GB10 with RoCE between them. Connect, App, then Producer. Brain is TP=2, sequential.",
        "Leased B300: one rented Blackwell B300. Connect, App, then Producer. Pack and Brain are on App.",
    ],
    [
        "A leased site with no name on it",
        "Two Sparks with no fabric between them",
        "Switching sites in the middle of a production",
    ],
    [
        Field(
            "site",
            "Where?",
            "choice",
            options=["local", "dual", "b300"],
            accept=["local", "dual", "b300"],
            hint="Choose one. You cannot move the production later without starting over.",
            option_help={
                "local": "Four GB10 on this floor. You will cable and power them next.",
                "dual": "Two rented Sparks. They must share CX-7 RoCE. Next: Connect, App, then Producer.",
                "b300": "One B300 at a provider. Next: Connect, App, then Producer. Pack and Brain are on App.",
            },
        ),
        Field(
            "provider",
            "Provider",
            "text",
            placeholder="Who owns these GPUs?",
            when="dual,b300",
            hint="The company or floor that rented you this site.",
        ),
        Field(
            "signer",
            "Your name",
            "text",
            placeholder="Name",
            when="dual,b300",
            hint="A leased site needs a named human on the production.",
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
            "Hostnames spark-01 … spark-04. spark-01 is primary (Wizard + Producer).",
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
                "Check: which node runs Wizard and Producer?",
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
            "Paste the fabric IPs here. Wizard records them; it does not write netplan in dry-run.",
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
        "Node NVMe holds weights and scratch. Dailies and masters live on the NAS at /diony/vault. A 1-hour production is multiple TB.",
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
        "The Spark pack is pinned. Pull it, start Producer, then Sign this last line.",
        [
            "Nothing to shop. The pack is DeepSeek-V4-Flash-0731 + Wan2.2 A14B. Pull commands are in How. Brain is spark-4x-v1 TP=4.",
            "Then install and start Producer: pip install -e packages/contracts -e apps/producer, then diony-producer.",
            f"Producer opens at {PRODUCER_URL} — same address on this laptop and on the cluster.",
            "Floors are Idea, Dailies, Program, Lock. Cut is optional prompt-to-edit. Program stitches the cut.",
            "Wan is 720p negative. Deliverables are Diony 1080. Sign locks this setup.",
        ],
        ["Shipping previz as a master", "Notes only in chat", "Cloud prompt extend", "Skipping Program stitch"],
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
            Field(
                "url",
                "Producer",
                "text",
                placeholder=PRODUCER_URL,
                default=PRODUCER_URL,
                hint="Same address on this laptop and on the cluster.",
            ),
            Field(
                "ready",
                "This URL opens the showrunner?",
                "select",
                options=["", "yes"],
                accept=["yes"],
                hint="Sign this line, then open Producer.",
            ),
        ],
        commands=[
            "# 1. Pack — /models, then replicate to spark-02..04",
            *pack_pull_commands("local"),
            "# 2. Producer",
            "pip install -e packages/contracts -e apps/wan -e apps/producer",
            "diony-producer",
            f"# Opens {PRODUCER_URL}",
        ],
        handoff=[
            "Sign this line.",
            "Open Producer. Write the idea, Produce, then screen the Program.",
        ],
    ),
}


def lesson_for(step_id: str, path: str | None = None) -> Lesson:
    if step_id == "path":
        return PATH_LESSON
    if path not in ("local", "lease", "dual", "b300"):
        raise KeyError("compute path not chosen")
    if path == "local":
        return LOCAL_LESSONS[step_id]
    if path == "dual":
        from diony_wizard.lessons_dual import DUAL_LESSONS

        return DUAL_LESSONS[step_id]
    if path == "b300":
        from diony_wizard.lessons_b300 import B300_LESSONS

        return B300_LESSONS[step_id]
    from diony_wizard.lessons_lease import LEASE_LESSONS

    return LEASE_LESSONS[step_id]


def lessons_for(path: str | None) -> dict[str, Lesson]:
    out = {"path": PATH_LESSON}
    if path == "local":
        out.update(LOCAL_LESSONS)
    elif path == "lease":
        from diony_wizard.lessons_lease import LEASE_LESSONS

        out.update(LEASE_LESSONS)
    elif path == "dual":
        from diony_wizard.lessons_dual import DUAL_LESSONS

        out.update(DUAL_LESSONS)
    elif path == "b300":
        from diony_wizard.lessons_b300 import B300_LESSONS

        out.update(B300_LESSONS)
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
        if not raw:
            return f"{field.label} is empty."
        if field.accept:
            if field.kind == "quiz":
                got = normalize(raw)
                if not any(token in got or got == token for token in field.accept):
                    return f"Check missed. Re-read the lesson, then answer {field.label.lower()}."
            elif raw not in field.accept:
                return f"{field.label} is not a passing value."
    if step_id == "path" and site in ("b300", "dual"):
        if len(str(answers.get("signer", "")).strip()) < 2:
            return "This site requires a named human exception."
    if step_id == "connect":
        _, err = parse_lease_hosts(str(answers.get("hosts", "")), path)
        if err:
            return err
        if vault_is_public(str(answers.get("nas_ip", ""))):
            return "Vault must be private. Not a public bucket."
    if step_id == "sync":
        parts = [p.strip() for p in str(answers.get("ips", "")).split(",") if p.strip()]
        if len(parts) < 4:
            return "Need four fabric IPs, comma-separated."
    return None


# Tests and older imports still see a full local book plus Site.
CURRICULUM: dict[str, Lesson] = {"path": PATH_LESSON, **LOCAL_LESSONS}
