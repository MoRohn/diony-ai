#!/usr/bin/env python3
"""Measure exact Wan BF16 attention on a B200 and write a held operator receipt.

This script never activates Production. The resulting receipt still requires the
selected immutable Take -> measured AutoQC -> verified 1080 Finish canary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from diony_wan.attention_qualification import (
    BACKEND,
    WAN_ATTENTION_SHAPE,
    build_operator_receipt,
)


def measure_operator(*, warmups: int = 2, repeats: int = 5) -> dict:
    if warmups < 1 or repeats < 3:
        raise ValueError("Attention qualification requires >=1 warmup and >=3 timings.")
    try:
        import torch
        from torch.nn.attention import SDPBackend, sdpa_kernel
    except ImportError as exc:  # pragma: no cover - exercised on the GPU host
        raise RuntimeError("The B200 Wan Python environment must provide PyTorch SDPA.") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("Attention qualification requires the target B200 CUDA device.")
    device = torch.device("cuda", 0)
    gpu_name = torch.cuda.get_device_name(device)
    capability = torch.cuda.get_device_capability(device)
    if gpu_name != "NVIDIA B200" or capability != (10, 0):
        raise RuntimeError(
            f"Attention qualification requires NVIDIA B200 compute capability 10.0; "
            f"found {gpu_name} {capability[0]}.{capability[1]}."
        )
    torch.manual_seed(24001)
    q = torch.randn(WAN_ATTENTION_SHAPE, device=device, dtype=torch.bfloat16)
    k = torch.randn(WAN_ATTENTION_SHAPE, device=device, dtype=torch.bfloat16)
    v = torch.randn(WAN_ATTENTION_SHAPE, device=device, dtype=torch.bfloat16)

    def call(backend):
        with sdpa_kernel([backend]):
            return torch.nn.functional.scaled_dot_product_attention(
                q,
                k,
                v,
                attn_mask=None,
                is_causal=False,
                dropout_p=0.0,
            )

    def timings(backend) -> tuple[list[float], object]:
        output = None
        for _ in range(warmups):
            output = call(backend)
        torch.cuda.synchronize(device)
        measured: list[float] = []
        for _ in range(repeats):
            start = torch.cuda.Event(enable_timing=True)
            end = torch.cuda.Event(enable_timing=True)
            start.record()
            output = call(backend)
            end.record()
            end.synchronize()
            measured.append(float(start.elapsed_time(end)))
        return measured, output

    baseline_ms, baseline = timings(SDPBackend.FLASH_ATTENTION)
    candidate_ms, candidate = timings(SDPBackend.CUDNN_ATTENTION)
    if baseline is None or candidate is None:
        raise RuntimeError("Attention qualification produced no numeric output.")
    delta = (baseline.float() - candidate.float()).abs()
    evidence = {
        "gpu_name": gpu_name,
        "compute_capability": f"{capability[0]}.{capability[1]}",
        "torch_version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda or ""),
        "cudnn_version": torch.backends.cudnn.version(),
        "shape": list(WAN_ATTENTION_SHAPE),
        "dtype": "bfloat16",
        "baseline_backend": "flash_sdpa",
        "candidate_backend": BACKEND,
        "baseline_ms": baseline_ms,
        "candidate_ms": candidate_ms,
        "finite": bool(torch.isfinite(candidate).all().item()),
        "max_abs_delta": float(delta.max().item()),
        "mean_abs_delta": float(delta.mean().item()),
    }
    return evidence


def write_receipt(
    output: Path,
    *,
    project_id: str,
    approved_by: str,
    warmups: int,
    repeats: int,
) -> dict:
    evidence = measure_operator(warmups=warmups, repeats=repeats)
    receipt = build_operator_receipt(
        evidence,
        scope_project_id=project_id,
        approved_by=approved_by,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_suffix(output.suffix + ".part")
    pending.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(output)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/diony/vault/qualification/wan-cudnn-sdpa-validation.json"),
    )
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    receipt = write_receipt(
        args.output,
        project_id=args.project_id,
        approved_by=args.approved_by,
        warmups=args.warmups,
        repeats=args.repeats,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
