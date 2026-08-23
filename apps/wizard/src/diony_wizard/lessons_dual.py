"""Leased Dual-Spark lessons. Site → Connect → App → Ready → Producer. Pack is on App. Brain is TP=2 sequential."""

from __future__ import annotations

from diony_wizard.lesson import Field, Lesson
from diony_wizard.lessons_gpu import hf_token_field, prove_lesson
from diony_wizard.prep import (
    PACK_LABEL,
    PRODUCER_URL,
    SHOW_FLOORS,
    SSH_USER_HINT,
    SSH_USER_PLACEHOLDER,
    SSH_USER_TEACH,
    hosts_commands,
    pack_pull_commands,
    producer_start_commands,
)

DUAL_LESSONS: dict[str, Lesson] = {
    "connect": Lesson(
        "connect",
        "Name the two Sparks, the login, and the vault.",
        [
            "Hosts are this rented pair. Defaults are spark-01 and spark-02. On Runpod Instant Clusters, spark-01 is pod-0 and spark-02 is pod-1. On Enverge, spark-01 is node0 and spark-02 is node1. Exactly two hostnames.",
            "These two must share fabric (Runpod Instant Cluster interconnect, or Enverge CX-7 RoCE). Two isolated Pods or two isolated Sparks will not load DeepSeek TP=2.",
            SSH_USER_TEACH,
            "Vault is private storage for projects, dailies, timelines, and 1080 masters. Weights stay on NVMe at /models. Diony always mounts the vault at /diony/vault.",
            "Where to find the vault: Runpod Network Volume first, or Enverge Storage / NFS / Volume. What to type: the NFS or volume address, for example nfs://10.0.8.12/diony-vault.",
        ],
        [
            "One hostname. This lease is two Sparks.",
            "Four hostnames. That is not this site.",
            "Two Sparks with no fabric between them.",
            "Two Runpod Pods that are not an Instant Cluster.",
            "Pasting the whole ssh LOGIN@host line into SSH user.",
            "Your email as the SSH user.",
            "A public cloud bucket (s3:// or open HTTPS) used as the vault.",
        ],
        [
            Field(
                "hosts",
                "Hosts",
                "textarea",
                placeholder="spark-01  spark-02",
                default="spark-01\nspark-02",
                hint="Two names that share fabric. spark-01 is the head.",
            ),
            Field(
                "ssh_user",
                "SSH user (login name)",
                "text",
                placeholder=SSH_USER_PLACEHOLDER,
                default="spark",
                hint=SSH_USER_HINT,
            ),
            Field(
                "nas_ip",
                "Vault",
                "text",
                placeholder="nfs://10.0.8.12/diony-vault",
                default="nfs://vault",
                hint="Runpod Network Volume, or Enverge Storage. Example nfs://10.0.8.12/diony-vault.",
            ),
        ],
        commands=[
            "# SSH user: Runpod Connect, or Enverge SSH line. Type only the word before @.",
            "# Runpod is often root. Enverge example: ssh my-instance@relay.enverge.ai → my-instance",
            "# Find the share: Runpod Network Volume, or Enverge Storage / NFS / Volume.",
            "# Type that server here. Example: nfs://10.0.8.12/diony-vault",
            "# App Continue mounts it at /diony/vault. Shape: infra/storage/vault.fstab.example",
            "# Pair fabric: infra/switch/spark-2x-interconnect.md",
        ],
        diagram="/models        NVMe on both   weights\n/scratch       NVMe           wipeable\n/diony/vault   this share     dailies + masters",
        handoff=[
            "Hosts, login, and vault are recorded. Fabric is the cluster interconnect between this pair.",
            "Next: App. Continue starts the pack pull over SSH (or locally on spark-01) and opens Ready.",
        ],
    ),
    "app": Lesson(
        "app",
        "This pair’s pack is pinned. Continue starts the pull and opens Ready.",
        [
            "Nothing to pick. The pack is pinned for Dual-Spark: DeepSeek-V4-Flash-0731 + Wan2.2 A14B (720p) plus diony_sr_v1. Brain is spark-2x-v1 TP=2.",
            "Continue SSHes (or runs locally on spark-01), mounts the vault at /diony/vault, pulls the pack, clones Wan2.2, installs diony-finish, and starts Producer. How is the log of that apply. Hugging Face token is HF_TOKEN: open https://huggingface.co/settings/tokens, Create new token +, paste it in HF_TOKEN on this page. Never Dashscope.",
            "Brain recipe is recorded (spark-2x-v1 TP=2). App does not start vLLM. Produce expands if brain is up; Produce starts Wan; Production runs 1080 finish and mix. Sequential: never brain and Wan together.",
            f"Producer opens at {PRODUCER_URL}. After Sign, Open Producer uses {PRODUCER_URL}.",
            "Continue starts the pull and opens Ready. The pack can take minutes to hours. Watch it on Ready, then Test GPU. Sign writes site.json onto /diony/vault. Do not quote the Quad-Spark 2–5 day SLA.",
        ],
        [
            "Picking a different DeepSeek or Wan because a blog said so",
            "Pasting spark-4x-v1 TP=4 onto this pair",
            "Two Sparks with no RoCE between them",
            "Co-resident DeepSeek and Wan A14B",
            "Leaving DASH_API_KEY set",
            "Skipping HF_TOKEN (gated DeepSeek and Wan will fail)",
            "Cloud studio / Dashscope as a substitute",
        ],
        [
            hf_token_field(),
            Field(
                "pack",
                "Pack",
                "select",
                options=[PACK_LABEL["dual"]],
                accept=[PACK_LABEL["dual"]],
                default=PACK_LABEL["dual"],
                auto=True,
                hint="Pinned for this pair. Continue starts the pull.",
            ),
        ],
        commands=[
            "# 1. Pack — spark-01, into /models, then copy to spark-02",
            *pack_pull_commands("dual"),
            "# 2. Producer — spark-01, same environment. App starts this. Not vLLM.",
            *producer_start_commands(cluster=True),
        ],
        handoff=[
            "Pack and Brain recipe recorded (TP=2, sequential SPLIT). Producer is running.",
            "Next: Ready. Test GPU, then Sign Producer.",
        ],
    ),
    "prove": prove_lesson("dual"),
    "producer": Lesson(
        "producer",
        "Sign writes the site lock. Then open Producer.",
        [
            "App already started Producer on spark-01. Sign writes /diony/vault/.cluster/site.json so this Producer knows the site.",
            "Idea writes the brief. Produce expands on DeepSeek if brain is up, drains brain, then farms Wan2.2 720p clips.",
            "Production is the run: farm, 1080 finish, mix. Not a shot list.",
            "Gallery is the inventory of produced clips. Production writes the 1080 finish.",
            "Human gates stay: bible and distribution. Production runs diony_sr_v1 to a 1920×1080 Rec.709 master, then mix.",
            "Wan is 720p negative. The deliverable is the 1080 program master from Production.",
        ],
        [
            "Shipping a 720p preview as the master",
            "Notes only in chat, not on Shot/Take IDs",
            "Cloud prompt extend",
            "Skipping Production and treating Gallery as the run",
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
                "stitch",
                "Overlap stitch",
                "select",
                options=["yes"],
                accept=["yes"],
                default="yes",
                auto=True,
            ),
        ],
        commands=[
            *producer_start_commands(cluster=True),
            *hosts_commands("spark-01"),
        ],
        handoff=[
            "Sign this line. site.json lands on /diony/vault.",
            "Open Producer. Write the idea. Produce. Screen the Program.",
        ],
    ),
}
