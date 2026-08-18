"""Leased Dual-Spark lessons. Site → Connect → App → Producer. Pack is on App. Brain is TP=2 sequential."""

from __future__ import annotations

from diony_wizard.lesson import Field, Lesson
from diony_wizard.prep import (
    PACK_LABEL,
    PRODUCER_URL,
    SHOW_FLOORS,
    pack_pull_commands,
    producer_start_commands,
)

DUAL_LESSONS: dict[str, Lesson] = {
    "connect": Lesson(
        "connect",
        "Name the two Sparks and the private share that holds dailies and masters.",
        [
            "Hosts are this rented pair. Defaults are spark-01 and spark-02. Change them if the provider used other names. Exactly two hostnames.",
            "These two must share ConnectX-7 RoCE. Isolated Sparks on a LAN will not load DeepSeek TP=2.",
            "Vault is private storage for projects, dailies, timelines, and 1080 masters. Weights stay on NVMe at /models. Diony always mounts the vault at /diony/vault.",
            "Where to find the vault: the provider console — Storage, NFS, or the attached volume. What to type: the NFS address, for example nfs://10.0.8.12/diony-vault.",
        ],
        [
            "One hostname. This lease is two Sparks.",
            "Four hostnames. That is not this site.",
            "Two Sparks with no fabric between them.",
            "A public cloud bucket (s3:// or open HTTPS) used as the vault.",
        ],
        [
            Field(
                "hosts",
                "Hosts",
                "textarea",
                placeholder="spark-01  spark-02",
                default="spark-01\nspark-02",
                hint="Two names. spark-01 runs Producer. They must share CX-7 RoCE.",
            ),
            Field(
                "nas_ip",
                "Vault",
                "text",
                placeholder="nfs://10.0.8.12/diony-vault",
                default="nfs://vault",
                hint="Paste the NFS address from the provider console (Storage or Volumes). Example: nfs://10.0.8.12/diony-vault. Diony mounts it at /diony/vault.",
            ),
            Field(
                "ssh_user",
                "SSH user",
                "text",
                placeholder="spark",
                default="spark",
                hint="Wizard SSHes as this user to pull the pack and start Producer. On spark-01 it runs locally.",
            ),
        ],
        commands=[
            "# Find the share: provider console → Storage / NFS / Volume.",
            "# Type that server here. Example: nfs://10.0.8.12/diony-vault",
            "# Diony mounts it at /diony/vault. Shape: infra/storage/vault.fstab.example",
            "# Pair fabric: infra/switch/spark-2x-interconnect.md",
        ],
        diagram="/models        NVMe on both   weights\n/scratch       NVMe           wipeable\n/diony/vault   this share     dailies + masters",
        handoff=[
            "Hosts and vault are recorded. Fabric is CX-7 RoCE between this pair.",
            "Next: App. Continue pulls the pack over SSH (or locally on spark-01), then starts Producer.",
        ],
    ),
    "app": Lesson(
        "app",
        "This pair’s pack is already chosen. Continue pulls it to /models and starts Producer.",
        [
            "Nothing to pick. The pack is pinned for Dual-Spark: DeepSeek-V4-Flash-0731 + Wan2.2 A14B (720p) plus diony_sr_v1. Brain is spark-2x-v1 TP=2.",
            "Continue SSHes (or runs locally on spark-01), pulls the pack, clones Wan2.2, installs diony-finish, and starts Producer. How is the log of that apply, not a homework sheet. Hugging Face token is HF_TOKEN on this Wizard process. Never Dashscope.",
            "SPLIT on this site is sequential: drain brain before Wan, drain Wan before brain. Never both at once. Produce starts Wan. Picture lock starts 1080 finish.",
            f"Producer opens at {PRODUCER_URL} — same address on this laptop and on spark-01.",
            "Continue records the pack and Brain after the pull succeeds. Next you Sign the floors. Do not quote the Quad-Spark 2–5 day SLA.",
        ],
        [
            "Picking a different DeepSeek or Wan because a blog said so",
            "Pasting spark-4x-v1 TP=4 onto this pair",
            "Two Sparks with no RoCE between them",
            "Co-resident DeepSeek and Wan A14B",
            "Leaving DASH_API_KEY set",
            "Cloud studio / Dashscope as a substitute",
        ],
        [
            Field(
                "pack",
                "Pack",
                "select",
                options=[PACK_LABEL["dual"]],
                accept=[PACK_LABEL["dual"]],
                default=PACK_LABEL["dual"],
                auto=True,
                hint="Pinned for this pair. Continue pulls it.",
            ),
        ],
        commands=[
            "# 1. Pack — spark-01, into /models, then copy to spark-02",
            *pack_pull_commands("dual"),
            "# 2. Producer — spark-01, same environment",
            *producer_start_commands(cluster=True),
        ],
        handoff=[
            "Pack and Brain recipe recorded (TP=2, sequential SPLIT).",
            "Next: Sign the floors, then open Producer.",
        ],
    ),
    "producer": Lesson(
        "producer",
        "These are the showrunner floors. Sign locks this setup. Then open Producer and start the idea.",
        [
            "Idea writes the brief. Produce expands on DeepSeek if brain is up, drains brain, then farms Wan2.2 720p clips.",
            "Dailies is coverage plus optional prompt-to-edit (Cut). Parent prints stay on the ladder.",
            "Program stitches the selected prints with 8–16 frame overlaps and frame-to-clip on I2V.",
            "Lock is bible, picture, mix, distribution. Picture lock runs diony_sr_v1 to a 1920×1080 Rec.709 master. Humans still own those gates.",
            "Wan is 720p negative. The deliverable is the 1080 program master after picture lock.",
        ],
        [
            "Shipping a 720p preview as the master",
            "Notes only in chat, not on Shot/Take IDs",
            "Cloud prompt extend",
            "Skipping Program stitch and hoping the dailies are the show",
        ],
        [
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
                "prompt_edit",
                "Prompt-to-edit",
                "select",
                options=["yes"],
                accept=["yes"],
                default="yes",
                auto=True,
            ),
            Field(
                "stitch",
                "Program stitch",
                "select",
                options=["yes"],
                accept=["yes"],
                default="yes",
                auto=True,
            ),
            Field(
                "url",
                "Producer",
                "text",
                placeholder=PRODUCER_URL,
                default=PRODUCER_URL,
                hint="Same address on this laptop and on spark-01.",
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
            *producer_start_commands(cluster=True),
        ],
        handoff=[
            "Sign this line.",
            "Open Producer. Write the idea. Produce. Screen the Program.",
        ],
    ),
}
