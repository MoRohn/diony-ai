import importlib.util
import json
from pathlib import Path


def _script_module():
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "benchmark"
        / "measure_wan_attention.py"
    )
    spec = importlib.util.spec_from_file_location("measure_wan_attention", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_b200_operator_measurement_writes_only_a_held_canary_receipt(
    tmp_path, monkeypatch
) -> None:
    module = _script_module()
    evidence = {
        "gpu_name": "NVIDIA B200",
        "compute_capability": "10.0",
        "torch_version": "2.8.0+cu128",
        "cuda_version": "12.8",
        "cudnn_version": 91002,
        "shape": [1, 40, 75600, 128],
        "dtype": "bfloat16",
        "baseline_backend": "flash_sdpa",
        "candidate_backend": "cudnn_sdpa",
        "baseline_ms": [285.85, 285.39, 285.37],
        "candidate_ms": [92.08, 91.19, 91.18],
        "finite": True,
        "max_abs_delta": 0.000244140625,
        "mean_abs_delta": 0.0000111,
    }
    monkeypatch.setattr(module, "measure_operator", lambda **_kwargs: evidence)
    output = tmp_path / "wan-cudnn-sdpa-validation.json"

    receipt = module.write_receipt(
        output,
        project_id="studio-current",
        approved_by="Named reviewer",
        warmups=2,
        repeats=5,
    )

    assert receipt["stage"] == "operator_passed_quality_canary_required"
    assert json.loads(output.read_text()) == receipt
    assert not output.with_suffix(".json.part").exists()

