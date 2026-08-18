"""Leased-B300 lessons. Site → Connect → App → Producer. Pack is on App. Brain is auto."""

from __future__ import annotations

from diony_wizard.lesson import Field, Lesson
from diony_wizard.prep import (
    PACK_LABEL,
    PRODUCER_URL,
    SHOW_FLOORS,
    pack_pull_commands,
    producer_start_commands,
)

B300_LESSONS: dict[str, Lesson] = {
    "connect": Lesson(
        "connect",
        "Name this GPU and the private share that holds dailies and masters.",
        [
            "Host is this rented GPU. Default is b300-01. Change it if the provider used another name. One hostname only.",
            "Vault is private storage for projects, dailies, timelines, and 1080 masters. Weights stay on this GPU’s NVMe at /models. Diony always mounts the vault at /diony/vault.",
            "Where to find it: the provider console for this GPU — Storage, NFS, or the attached volume. The person who rented the box has the server and share. Not a public bucket.",
            "What to type: the NFS address from that page, for example nfs://10.0.8.12/diony-vault.",
        ],
        [
            "Four hostnames. This lease is one B300.",
            "A public cloud bucket (s3:// or open HTTPS) used as the vault.",
        ],
        [
            Field(
                "hosts",
                "Host",
                "text",
                placeholder="b300-01",
                default="b300-01",
                hint="The name of this rented GPU. Producer will run here.",
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
                hint="Wizard SSHes as this user to pull the pack and start Producer. On this GPU it runs locally.",
            ),
        ],
        commands=[
            "# Find the share: provider console → Storage / NFS / Volume on this GPU.",
            "# Type that server here. Example: nfs://10.0.8.12/diony-vault",
            "# Diony mounts it at /diony/vault. Shape: infra/storage/vault.fstab.example",
        ],
        diagram="/models        GPU NVMe     weights\n/scratch       GPU NVMe     wipeable\n/diony/vault   this share   dailies + masters",
        handoff=[
            "Host and vault are recorded. Vault is this share, mounted at /diony/vault.",
            "Next: App. Continue pulls the pack over SSH (or locally on this GPU), then starts Producer.",
        ],
    ),
    "app": Lesson(
        "app",
        "This GPU’s pack is already chosen. Continue pulls it to /models and starts Producer.",
        [
            "Nothing to pick. The pack is pinned for this B300: DeepSeek-V4-Flash-0731 + Wan2.2 A14B (720p) plus diony_sr_v1. Not a Hugging Face latest tag.",
            "Continue SSHes (or runs locally on this GPU), pulls the pack, clones Wan2.2, installs diony-finish, and starts Producer. How is the log of that apply, not a homework sheet. Hugging Face token is HF_TOKEN. Never Dashscope.",
            "SPLIT on this site is sequential: drain brain before Wan, drain Wan before brain. Produce starts Wan. Picture lock starts 1080 finish.",
            f"Producer opens at {PRODUCER_URL} — same address on this laptop and on the GPU.",
            "Continue records the pack and Brain after the pull succeeds. Next you Sign the floors.",
        ],
        [
            "Picking a different DeepSeek or Wan because a blog said so",
            "Leaving DASH_API_KEY set",
            "Skipping Producer start and treating Wizard as the showrunner",
            "Cloud studio / Dashscope as a substitute",
        ],
        [
            Field(
                "pack",
                "Pack",
                "select",
                options=[PACK_LABEL["b300"]],
                accept=[PACK_LABEL["b300"]],
                default=PACK_LABEL["b300"],
                auto=True,
                hint="Pinned for this GPU. Continue pulls it.",
            ),
        ],
        commands=[
            "# 1. Pack — this GPU, into /models",
            *pack_pull_commands("b300"),
            "# 2. Producer — same environment",
            *producer_start_commands(cluster=True),
        ],
        handoff=[
            "Pack and Brain recipe recorded.",
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
                hint="Same address on this laptop and on the GPU.",
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
