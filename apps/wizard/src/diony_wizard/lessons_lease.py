"""Lease-GPU lessons. Site → Connect → Ready. Blueprint 14."""

from __future__ import annotations

from diony_wizard.lesson import Field, Lesson
from diony_wizard.prep import PRODUCER_URL

LEASE_LESSONS: dict[str, Lesson] = {
    "connect": Lesson(
        "connect",
        "Point Diony at the four GPUs and the vault. Everything else is already decided.",
        [
            "Hosts are filled. Change them if the provider used different names.",
            "Vault is the private NFS/share that will mount at /diony/vault. Find the server in the provider console (Storage / NFS / Volume). Public buckets fail.",
            "You only confirm hosts and vault. Interconnect, the brain, and Producer are already filled in on this page.",
        ],
        ["Public S3 as the bible", "One VM pretending to be four GPUs", "Mixed GPU types"],
        [
            Field(
                "hosts",
                "Hosts",
                "textarea",
                placeholder="node-01  node-02  node-03  node-04",
                default="node-01\nnode-02\nnode-03\nnode-04",
                hint="Four names or instance IDs. node-01 runs Producer.",
            ),
            Field(
                "nas_ip",
                "Vault",
                "text",
                placeholder="nfs://10.0.8.12/diony-vault",
                default="nfs://vault",
                hint="Paste the NFS address from the provider console (Storage or Volumes). Example: nfs://10.0.8.12/diony-vault. Diony mounts it at /diony/vault.",
            ),
        ],
        commands=[
            "huggingface-cli download deepseek-ai/DeepSeek-V4-Flash-0731 --local-dir /models/deepseek/DeepSeek-V4-Flash-0731",
            "diony-producer",
            f"# Producer: {PRODUCER_URL}",
        ],
    ),
    "producer": Lesson(
        "producer",
        "This is the Diony setup for this lease. Sign to lock it. Then open Producer.",
        [
            "Wan is 720p negative. Deliverables are Diony 1080.",
            "First mode is BRAIN. Later production is SPLIT (two brain + two Wan).",
            "After weight pull, drop outbound. No Dashscope.",
        ],
        ["Shipping previz as a master", "Notes only in chat", "Hosted prompt extend"],
        [],
        commands=[
            "diony-producer",
            f"# Open {PRODUCER_URL}",
        ],
    ),
}
