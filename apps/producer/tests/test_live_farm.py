import time

from fastapi.testclient import TestClient

from diony_producer.app import app


FAKE_GENERATE = """#!/usr/bin/env python3
import sys
from pathlib import Path
args = sys.argv
assert "--use_prompt_extend" not in args
assert "dashscope" not in " ".join(args).lower()
save = Path(args[args.index("--save_file") + 1])
save.parent.mkdir(parents=True, exist_ok=True)
save.write_bytes(b"\\x00\\x00\\x00\\x18ftypmp42" + b"\\x00" * 200)
"""

SLOW_GENERATE = """#!/usr/bin/env python3
import sys, time
from pathlib import Path
time.sleep(0.8)
save = Path(sys.argv[sys.argv.index("--save_file") + 1])
save.parent.mkdir(parents=True, exist_ok=True)
save.write_bytes(b"\\x00\\x00\\x00\\x18ftypmp42" + b"\\x00" * 200)
"""


def _live_env(tmp_path, monkeypatch, script: str) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.delenv("DIONY_WAN_URL", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    gen = tmp_path / "generate.py"
    gen.write_text(script)
    gen.chmod(0o755)
    ckpt = tmp_path / "t2v"
    ckpt.mkdir()
    (ckpt / "w.json").write_text("{}")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(gen))
    monkeypatch.setenv("WAN_CKPT_T2V", str(ckpt))
    monkeypatch.setenv("DIONY_PYTHON", "python3")
    monkeypatch.setenv("DIONY_WAN_REPO", str(tmp_path))
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))


def test_live_roll_writes_mp4(tmp_path, monkeypatch) -> None:
    _live_env(tmp_path, monkeypatch, FAKE_GENERATE)
    client = TestClient(app)
    client.post("/api/reset")
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 2,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    farm = client.get("/api/farm").json()
    assert farm["live"] is True
    deadline = time.time() + 15
    mp4 = None
    while time.time() < deadline:
        project = client.get("/api/project").json()
        take = project["takes"][0]
        rel = take["artifacts"][0]["path"]
        path = tmp_path / "vault" / rel
        if path.is_file() and path.stat().st_size > 32 and rel.endswith(".mp4"):
            mp4 = path
            break
        time.sleep(0.2)
    assert mp4 is not None, "farm should write a Wan 720p mp4"
    assert mp4.read_bytes()[:8]  # clip bytes exist
    catalog = client.get("/api/catalog").json()
    assert catalog["live"] is True
    assert catalog["dry_run"] is False
    idle = time.time() + 20
    while time.time() < idle:
        if not client.get("/api/farm").json()["busy"]:
            return
        time.sleep(0.2)


def test_farm_busy_before_clip_lands(tmp_path, monkeypatch) -> None:
    _live_env(tmp_path, monkeypatch, SLOW_GENERATE)
    client = TestClient(app)
    client.post("/api/reset")
    client.post(
        "/api/studio/open",
        json={
            "idea": "A tungsten newsroom at 2 a.m., rain on the glass.",
            "customer_kind": "series",
            "target_minutes": 1,
        },
    )
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    farm = client.get("/api/farm").json()
    assert farm["live"] is True
    assert farm["busy"] is True
    deadline = time.time() + 20
    while time.time() < deadline:
        project = client.get("/api/project").json()
        rel = project["takes"][0]["artifacts"][0]["path"]
        path = tmp_path / "vault" / rel
        if path.is_file() and path.stat().st_size > 32:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("slow farm never wrote the clip")
    idle = time.time() + 20
    while time.time() < idle:
        if not client.get("/api/farm").json()["busy"]:
            return
        time.sleep(0.2)
