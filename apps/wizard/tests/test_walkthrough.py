import re
from pathlib import Path

from fastapi.testclient import TestClient

from diony_wizard.app import app

PASSING_LOCAL = {
    "path": {"site": "local"},
    "unbox": {
        "sparks": "yes",
        "bricks": "yes",
        "switch": "CRS804-4DDQ-HRM",
        "ups": "yes",
        "console": "laptop Sync",
        "quiz": "4",
    },
    "inventory": {
        "s1": "1",
        "s2": "2",
        "s3": "3",
        "s4": "4",
        "os_matched": "yes",
        "quiz": "spark-01",
    },
    "cables": {
        "port2": "yes",
        "loom": "yes",
        "quiz": "10gbe",
    },
    "switch": {
        "sku": "CRS804-4DDQ-HRM",
        "speed": "200G",
        "quiz": "off",
    },
    "sync": {
        "cluster_name": "diony",
        "ips": "10.0.0.1,10.0.0.2,10.0.0.3,10.0.0.4",
        "quiz": "NVIDIA Sync",
    },
    "roce": {
        "iperf_class": "dry-run-recorded",
        "quiz": "one half",
    },
    "vault": {
        "nas_ip": "192.168.10.50",
        "export": "/volume1/diony-vault",
        "quiz": "/diony/vault",
    },
    "producer": {
        "ready": "yes",
    },
}

PASSING_B300 = {
    "path": {
        "site": "b300",
        "provider": "this rack",
        "signer": "Rohn Springfield",
    },
    "connect": {},
    "app": {},
    "prove": {},
    "producer": {
        "floors": "Idea, Production, Gallery",
        "stitch": "yes",
        "url": "http://producer.diony.local",
        "ready": "yes",
    },
}


PASSING_B200 = {
    "path": {
        "site": "b200",
        "provider": "this rack",
        "signer": "Rohn Springfield",
    },
    "connect": {},
    "app": {},
    "prove": {},
    "producer": {
        "floors": "Idea, Production, Gallery",
        "stitch": "yes",
        "url": "http://producer.diony.local",
        "ready": "yes",
    },
}


PASSING_DUAL = {
    "path": {
        "site": "dual",
        "provider": "this rack",
        "signer": "Rohn Springfield",
    },
    "connect": {},
    "app": {},
    "prove": {},
    "producer": {
        "floors": "Idea, Production, Gallery",
        "stitch": "yes",
        "url": "http://producer.diony.local",
        "ready": "yes",
    },
}


def test_cannot_sign_empty_inventory() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    signed = client.post("/api/steps/path/pass", json={"answers": PASSING_LOCAL["path"]})
    assert signed.status_code == 200
    res = client.post("/api/steps/inventory/pass", json={"answers": {}})
    assert res.status_code == 400


def test_site_continue_twice_stays_on_the_next_line() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    first = client.post("/api/steps/path/pass", json={"answers": PASSING_B200["path"]})
    assert first.status_code == 200, first.text
    assert first.json()["active_step"] == "connect"
    assert first.json()["lesson"]["step_id"] == "connect"
    guide = first.json()["lesson"]["guide"]
    assert [row["heading"] for row in guide] == [
        "HTTP services",
        "SSH",
        "SSH over exposed TCP",
        "Web terminal",
        "Direct TCP ports",
        "Pod volume Mount path",
    ]
    assert any(row["use"] == "use" and row["heading"] == "SSH over exposed TCP" for row in guide)
    again = client.post("/api/steps/path/pass", json={"answers": PASSING_B200["path"]})
    assert again.status_code == 200, again.text
    assert again.json()["active_step"] == "connect"
    assert again.json()["compute_path"] == "b200"


def test_site_continue_twice_b300_stays_on_connect() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    first = client.post("/api/steps/path/pass", json={"answers": PASSING_B300["path"]})
    assert first.status_code == 200, first.text
    assert first.json()["active_step"] == "connect"
    again = client.post("/api/steps/path/pass", json={"answers": PASSING_B300["path"]})
    assert again.status_code == 200, again.text
    assert again.json()["active_step"] == "connect"
    assert again.json()["compute_path"] == "b300"


def test_reset_then_pass_b200() -> None:
    client = TestClient(app)
    client.post("/api/steps/path/pass", json={"answers": PASSING_B200["path"]})
    reset = client.post("/api/reset")
    assert reset.status_code == 200
    assert reset.json()["active_step"] == "path"
    passed = client.post("/api/steps/path/pass", json={"answers": PASSING_B200["path"]})
    assert passed.status_code == 200, passed.text
    assert passed.json()["active_step"] == "connect"


def test_state_stays_200_when_lesson_import_fails(monkeypatch) -> None:
    client = TestClient(app)
    client.post("/api/reset")
    signed = client.post("/api/steps/path/pass", json={"answers": PASSING_B200["path"]})
    assert signed.status_code == 200

    import diony_wizard.curriculum as curriculum

    def boom(_path: str) -> dict:
        raise ImportError("cannot import name 'SSH_PORT_DEFAULT' from 'diony_wizard.prep'")

    monkeypatch.setattr(curriculum, "_book", boom)
    res = client.get("/api/state")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "active_step" in body
    assert isinstance(body.get("steps"), list)
    assert body.get("error")
    assert body.get("lesson") is None or body["lesson"].get("step_id") == "path"


def test_gpu_connect_lessons_load() -> None:
    from diony_wizard.lessons_b200 import B200_LESSONS
    from diony_wizard.lessons_b300 import B300_LESSONS

    assert "connect" in B200_LESSONS
    assert "ssh_port" in {f.id for f in B200_LESSONS["connect"].fields}
    assert "connect" in B300_LESSONS
    assert RUNPOD_CONNECT_DIAGRAM_OK()


def test_gpu_lessons_survive_stale_prep(monkeypatch) -> None:
    import importlib

    import diony_wizard.lessons_gpu as gpu
    import diony_wizard.prep as prep

    monkeypatch.delattr(prep, "SSH_PORT_DEFAULT", raising=False)
    monkeypatch.delattr(prep, "RUNPOD_CONNECT_DIAGRAM", raising=False)
    reloaded = importlib.reload(gpu)
    book = reloaded.gpu_lessons("b200")
    assert "connect" in book
    assert "ssh_port" in {f.id for f in book["connect"].fields}
    assert "SSH over exposed TCP" in reloaded.RUNPOD_CONNECT_DIAGRAM


def RUNPOD_CONNECT_DIAGRAM_OK() -> bool:
    from diony_wizard.prep import RUNPOD_CONNECT_DIAGRAM

    return "SSH over exposed TCP" in RUNPOD_CONNECT_DIAGRAM


def test_wizard_uses_the_diony_chorus_design_system() -> None:
    static = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "static"
    html = (static / "index.html").read_text()
    css = (static / "app.css").read_text()
    assert "family=Archivo:wdth,wght@125,850" in html
    assert "family=Inter" in html
    assert 'data-product-surface="studio"' in html
    assert 'class="diony-spirit' in html
    assert "--charcoal: #0d0d0d" in css.lower()
    assert "--light: #f6f6f6" in css.lower()
    assert "--signal: #ff402f" in css.lower()
    assert "--ember: #ffa23a" in css.lower()
    assert "--flare: #ff7a2f" in css.lower()
    assert "--garnet: #e43b4b" in css.lower()
    assert "--brand-gradient" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css


def test_wizard_uses_the_production_folio_layout() -> None:
    static = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "static"
    html = (static / "index.html").read_text()
    css = (static / "app.css").read_text()
    assert 'class="mast stage-header"' in html
    assert 'class="sheet production-folio"' in html
    assert 'class="shot-index cue-navigation"' in html
    assert 'class="well cue-workspace"' in html
    assert 'href="/static/folio.css?v=brand3"' in html
    assert "--mast-height: 7rem" in css
    assert "--rail-width: clamp(15rem, 18vw, 19rem)" in css
    assert "v=brand3" in html


def test_wizard_header_uses_the_diony_bar_mark() -> None:
    static = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "static"
    html = (static / "index.html").read_text()
    css = (static / "app.css").read_text()
    assert 'class="brand diony-brand"' in html
    assert 'class="diony-mark"' in html
    assert '<span class="diony-word">Diony</span>' in html
    assert 'fill="currentColor"' in html
    assert "diony-ai-optical-mark" not in html
    assert not (static / "diony-ai-optical-mark.png").exists()
    assert '<i>/</i><span>Diony</span><strong>AI</strong>' not in html
    assert ".diony-brand::after" in css
    brand_line_start = css.index(".diony-brand::after {")
    brand_line_rule = css[brand_line_start : css.index("@media", brand_line_start)]
    assert "content: none;" in brand_line_rule
    brand_start = css.index(".stage-header .diony-brand {")
    brand_rule = css[brand_start : css.index(".diony-mark", brand_start)]
    assert "background: transparent;" in brand_rule
    assert "overflow: visible;" in brand_rule
    assert "color: var(--light);" in brand_rule
    assert "clip-path" not in brand_rule
    word_start = css.index(".diony-word {")
    word_rule = css[word_start : css.index("}", word_start)]
    assert "var(--display)" in word_rule


def test_wizard_header_has_no_open_status_treatment() -> None:
    static = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "static"
    html = (static / "index.html").read_text()
    css = (static / "app.css").read_text()

    assert 'class="studio-intro-bays"' not in html
    assert ".studio-intro-bays" not in css


def test_wizard_intro_motto_is_centered_on_the_window() -> None:
    static = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "static"
    css = (static / "app.css").read_text()
    header_start = css.index('body[data-entry="intro"] .stage-header {')
    header_rule = css[header_start : css.index("}", header_start)]
    motto_start = css.index('body[data-entry="intro"] .studio-intro-motto {')
    motto_rule = css[motto_start : css.index("}", motto_start)]

    assert "position: relative;" in header_rule
    assert "position: absolute;" in motto_rule
    assert "left: 50%;" in motto_rule
    assert "margin: 0;" in motto_rule
    assert "transform: translate(-50%, -50%);" in motto_rule


def test_cannot_skip_to_cables() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post(
        "/api/steps/cables/pass",
        json={"answers": {"port2": "yes", "loom": "yes", "quiz": "10gbe"}},
    )
    assert res.status_code == 404


def test_walkthrough_signs_all_lines() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    for step_id, answers in PASSING_LOCAL.items():
        res = client.post(f"/api/steps/{step_id}/pass", json={"answers": answers})
        assert res.status_code == 200, res.text
    state = client.get("/api/state").json()
    assert state["complete"] is True
    assert state["active_step"] is None
    assert state["compute_path"] == "local"
    assert "accept" not in str(state["lesson"])
    assert state["report"]["sku"] == "spark-4x-v1"
    assert state["report"]["compute_path"] == "local"
    assert state["report"]["compute"]["path"] == "local"
    assert len(state["report"]["workbook"]) >= 9
    assert state["report"]["workbook"]["weights"]["pack"] == "spark-4x-v1"
    assert "Wan2.2-T2V-A14B" in state["report"]["workbook"]["weights"]["camera"]
    assert "TI2V-5B" not in state["report"]["workbook"]["weights"]["camera"]
    assert state["report"]["workbook"]["brain_smoke"]["mode"] == "BRAIN"
    assert state["report"]["workbook"]["brain_smoke"]["recipe"] == "spark-4x-v1 TP=4"
    assert len(state["report"]["steps"]) == 9
    assert [s["name"] for s in state["report"]["steps"]] == [
        "Site",
        "Unbox",
        "Nodes",
        "Cables",
        "Switch",
        "Sync",
        "Fabric",
        "Vault",
        "Producer",
    ]


def test_lease_site_is_not_offered() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post(
        "/api/steps/path/pass",
        json={
            "answers": {
                "site": "lease",
                "provider": "coreweave",
                "gpu_class": "4×H100 80GB",
                "signer": "Rohn Springfield",
            }
        },
    )
    assert res.status_code == 400
    lesson = client.get("/api/lessons/path").json()
    site = next(f for f in lesson["fields"] if f["id"] == "site")
    assert site["options"] == ["local", "dual", "b300", "b200"]


def test_b300_walkthrough_records_exception() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    for step_id, answers in PASSING_B300.items():
        res = client.post(f"/api/steps/{step_id}/pass", json={"answers": answers})
        assert res.status_code == 200, res.text
    state = client.get("/api/state").json()
    assert state["complete"] is True
    assert state["compute_path"] == "b300"
    report = state["report"]
    assert report["sku"] == "b300-v1"
    assert report["compute"]["path"] == "b300"
    assert report["compute"]["gpu_class"] == "1×B300"
    assert report["compute"]["exception_by"] == "Rohn Springfield"
    assert report["pack"]["recipe_class"] == "b300-tp1"
    assert report["pack"]["nodes"] == ["b300-01"]
    assert report["pack"]["interconnect"] == "On-package"
    assert report["workbook"]["weights"]["pack"] == "b300-v1"
    assert report["workbook"]["weights"]["brain"] == "deepseek-ai/DeepSeek-V4-Flash-0731"
    assert "Wan2.2-T2V-A14B" in report["workbook"]["weights"]["camera"]
    assert report["workbook"]["brain_smoke"]["mode"] == "BRAIN"
    assert report["workbook"]["brain_smoke"]["recipe"] == "b300-v1 TP=1"
    assert report["workbook"]["connect"]["ssh_user"] == "root"
    assert report["workbook"]["connect"]["ssh_port"] == "22"
    assert report["workbook"]["connect"]["nas_ip"] == "/workspace"
    assert report["workbook"]["producer"]["url"] == "http://producer.diony.local"
    assert len(report["workbook"]) >= 5
    assert [s["id"] for s in report["steps"]] == [
        "path",
        "connect",
        "app",
        "prove",
        "producer",
    ]
    assert [s["name"] for s in report["steps"]] == [
        "Site",
        "Connect",
        "App",
        "Ready",
        "Producer",
    ]
    assert "signed exception" in report["mandate"]


def test_b200_walkthrough_records_exception() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    for step_id, answers in PASSING_B200.items():
        res = client.post(f"/api/steps/{step_id}/pass", json={"answers": answers})
        assert res.status_code == 200, res.text
    state = client.get("/api/state").json()
    assert state["complete"] is True
    assert state["compute_path"] == "b200"
    report = state["report"]
    assert report["sku"] == "b200-v1"
    assert report["compute"]["path"] == "b200"
    assert report["compute"]["gpu_class"] == "1×B200"
    assert report["compute"]["exception_by"] == "Rohn Springfield"
    assert report["pack"]["recipe_class"] == "b200-tp1"
    assert report["pack"]["nodes"] == ["b200-01"]
    assert report["pack"]["interconnect"] == "On-package B200"
    assert report["workbook"]["weights"]["pack"] == "b200-v1"
    assert report["workbook"]["brain_smoke"]["recipe"] == "b200-v1 TP=1"
    assert report["workbook"]["connect"]["ssh_user"] == "root"
    assert report["workbook"]["connect"]["ssh_port"] == "22"
    assert report["workbook"]["connect"]["nas_ip"] == "/workspace"
    assert report["sequential"] is True
    pack = client.get("/api/files/pack").json()
    assert "b200-v1" in pack["profile_text"]
    assert "--max-model-len 16384" in pack["serving_text"]
    assert "--gpu-memory-utilization 0.95" in pack["serving_text"]
    assert "--max-model-len 1048576" not in pack["serving_text"]
    serving = client.get("/api/files/serving").json()
    assert serving["path"] == "infra/profiles/b200-v1.serving.md"
    snippet = client.get("/api/snippets/On-package B200")
    assert snippet.status_code == 200
    assert "180" in snippet.json()["text"]


def test_dual_walkthrough_records_exception() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    for step_id, answers in PASSING_DUAL.items():
        res = client.post(f"/api/steps/{step_id}/pass", json={"answers": answers})
        assert res.status_code == 200, res.text
    state = client.get("/api/state").json()
    assert state["complete"] is True
    assert state["compute_path"] == "dual"
    report = state["report"]
    assert report["sku"] == "spark-2x-v1"
    assert report["compute"]["path"] == "dual"
    assert report["compute"]["gpu_class"] == "2×GB10"
    assert report["compute"]["exception_by"] == "Rohn Springfield"
    assert report["pack"]["recipe_class"] == "spark-tp2"
    assert report["pack"]["nodes"] == ["spark-01", "spark-02"]
    assert report["pack"]["interconnect"] == "CX-7 RoCE"
    assert report["workbook"]["weights"]["pack"] == "spark-2x-v1"
    assert report["workbook"]["weights"]["brain"] == "deepseek-ai/DeepSeek-V4-Flash-0731"
    assert report["workbook"]["brain_smoke"]["recipe"] == "spark-2x-v1 TP=2"
    assert report["workbook"]["producer"]["url"] == "http://producer.diony.local"
    assert [s["name"] for s in report["steps"]] == [
        "Site",
        "Connect",
        "App",
        "Ready",
        "Producer",
    ]
    assert "signed exception" in report["mandate"]
    from diony_contracts.cluster import read_site_lock

    lock = read_site_lock()
    assert lock is not None
    assert lock["compute_path"] == "dual"
    assert lock["sku"] == "spark-2x-v1"
    assert lock["sequential"] is True
    assert lock["nodes"] == ["spark-01", "spark-02"]
    assert lock["recipe"] == "spark-2x-v1 TP=2"
    assert lock["vault"] == "/diony/vault"
    assert lock["producer_href"] == "http://producer.diony.local"
    assert lock["connect"]["host"] == "spark-01"
    assert lock["connect"]["ssh_user"]
    assert report["site_lock"]
    assert report["vault"] == "/diony/vault"


def test_reset_clears_compute_path() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post(
        "/api/steps/path/pass",
        json={"answers": PASSING_B300["path"]},
    )
    assert res.status_code == 200
    assert client.get("/api/state").json()["compute_path"] == "b300"
    client.post("/api/reset")
    state = client.get("/api/state").json()
    assert state["compute_path"] is None
    assert state["sku"] == "spark-4x-v1"
    assert state["active_step"] == "path"


def test_lease_site_rejected_even_with_name() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post(
        "/api/steps/path/pass",
        json={
            "answers": {
                "site": "lease",
                "provider": "coreweave",
                "gpu_class": "4×H100 80GB",
                "signer": "Rohn Springfield",
            }
        },
    )
    assert res.status_code == 400


def test_b300_without_signer_fails() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post(
        "/api/steps/path/pass",
        json={
            "answers": {
                "site": "b300",
                "provider": "this rack",
                "signer": "",
                "exception": "yes",
            }
        },
    )
    assert res.status_code == 400


def test_snippets_are_real_switch_policy() -> None:
    client = TestClient(app)
    crs804 = client.get("/api/snippets/CRS804-4DDQ-HRM")
    assert crs804.status_code == 200
    body = crs804.json()
    assert "200Gbps" in body["text"]
    assert "auto-negotiation=no" in body["text"]
    crs812 = client.get("/api/snippets/CRS812-8DS-2DQ-2DDQ")
    assert crs812.status_code == 200
    assert "2x200G" in crs812.json()["text"]
    lease = client.get("/api/snippets/InfiniBand")
    assert lease.status_code == 200
    assert "private" in lease.json()["text"].lower()
    b300 = client.get("/api/snippets/On-package")
    assert b300.status_code == 200
    assert "on-package" in b300.json()["text"].lower()
    dual = client.get("/api/snippets/CX-7 RoCE")
    assert dual.status_code == 200
    assert "two rented" in dual.json()["text"].lower() or "dual" in dual.json()["text"].lower()


def test_b300_pack_is_self_contained() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    for step_id, answers in PASSING_B300.items():
        res = client.post(f"/api/steps/{step_id}/pass", json={"answers": answers})
        assert res.status_code == 200, res.text
    pack = client.get("/api/files/pack").json()
    assert pack["sku"] == "b300-v1"
    assert pack["recipe_class"] == "b300-tp1"
    assert pack["nodes"] == ["b300-01"]
    assert pack["interconnect"] == "On-package"
    assert "b300-v1" in pack["profile_text"]
    assert "/diony/vault" in pack["fstab_text"]
    assert "TP=1" in pack["serving_text"] or "tensor-parallel-size 1" in pack["serving_text"]
    profile = client.get("/api/files/profile").json()
    assert profile["path"] == "infra/profiles/b300-v1.yaml"
    blocked = client.post("/api/reset")
    assert blocked.status_code == 200
    crate = client.post(
        "/api/steps/path/pass",
        json={"answers": PASSING_B300["path"]},
    )
    assert crate.status_code == 200
    assert client.post("/api/steps/unbox/pass", json={"answers": {}}).status_code == 404
    assert client.get("/api/lessons/unbox").status_code == 400


def test_b300_connect_does_not_skip_app() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    site = client.post("/api/steps/path/pass", json={"answers": PASSING_B300["path"]})
    assert site.status_code == 200
    names = [s["name"] for s in site.json()["steps"]]
    assert names == ["Site", "Connect", "App", "Ready", "Producer"]
    connected = client.post("/api/steps/connect/pass", json={"answers": {}})
    assert connected.status_code == 200
    state = connected.json()
    assert state["complete"] is False
    assert state["active_step"] == "app"
    assert "weights" not in state["workbook"]
    assert "brain_smoke" not in state["workbook"]
    skipped = client.post("/api/steps/producer/pass", json={"answers": PASSING_B300["producer"]})
    assert skipped.status_code == 400
    empty = client.post("/api/steps/app/pass", json={"answers": {}})
    assert empty.status_code == 200
    after = empty.json()
    assert after["active_step"] == "prove"
    assert after["workbook"]["weights"]["pack"] == "b300-v1"
    assert after["workbook"]["brain_smoke"]["recipe"] == "b300-v1 TP=1"
    ready = client.post("/api/steps/prove/pass", json={"answers": {}})
    assert ready.status_code == 200
    assert ready.json()["active_step"] == "producer"
    assert ready.json()["prove"]["ok"] is True


def test_b300_app_includes_the_pack() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    client.post("/api/steps/path/pass", json={"answers": PASSING_B300["path"]})
    client.post("/api/steps/connect/pass", json={"answers": {}})
    assert client.get("/api/lessons/weights").status_code == 400
    assert client.get("/api/lessons/brain_smoke").status_code == 400
    producer = client.get("/api/lessons/producer").json()
    assert any("diony-producer" in line for line in producer["commands"])
    assert any("apps/finish" in line for line in producer["commands"])
    app_lesson = client.get("/api/lessons/app").json()
    assert any("huggingface-cli download" in line for line in app_lesson["commands"])
    assert any("Wan2.2-T2V" in line for line in app_lesson["commands"])
    assert any("vllm serve" in line for line in app_lesson["commands"])
    assert any("pip install" in line for line in app_lesson["commands"])
    assert any("DIONY_VAULT=/diony/vault" in line for line in app_lesson["commands"])
    assert any("DIONY_BIND=0.0.0.0" in line for line in app_lesson["commands"])
    assert any("diony-wan" in line for line in app_lesson["commands"])
    assert "How is Producer installed" not in str(app_lesson)
    labels = " ".join(f.get("label", "") for f in app_lesson["fields"])
    assert "HF_TOKEN" in labels
    assert any(f.get("id") == "hf_token" for f in app_lesson["fields"])
    assert any("huggingface.co/settings/tokens" in (f.get("source") or "") for f in app_lesson["fields"])
    assert "How is Producer" not in labels


def test_dual_app_is_tp2() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    client.post("/api/steps/path/pass", json={"answers": PASSING_DUAL["path"]})
    client.post("/api/steps/connect/pass", json={"answers": {}})
    lesson = client.get("/api/lessons/app").json()
    joined = "\n".join(lesson["commands"])
    assert "huggingface-cli download" in joined
    assert "--tensor-parallel-size 2" in joined
    assert "pip install" in joined
    assert "DIONY_VAULT=/diony/vault" in joined
    assert "DIONY_BIND=0.0.0.0" in joined
    assert "diony-wan" in joined
    assert "diony-finish" in joined
    assert "/opt/wan22" in joined
    assert "unset DASH_API_KEY" in joined
    assert "requirements.txt" in joined
    assert "PYTHONPATH=/opt/wan22" in joined
    assert "DIONY_WAN_URL=http://127.0.0.1:8010" in joined
    empty = client.post("/api/steps/app/pass", json={"answers": {}})
    assert empty.status_code == 200
    after = empty.json()
    assert after["workbook"]["weights"]["pack"] == "spark-2x-v1"
    assert after["workbook"]["brain_smoke"]["recipe"] == "spark-2x-v1 TP=2"
    pack = client.get("/api/files/pack").json()
    assert pack["sku"] == "spark-2x-v1"
    assert pack["nodes"] == ["spark-01", "spark-02"]
    assert "TP=2" in pack["serving_text"] or "tensor-parallel-size 2" in pack["serving_text"]


def test_health_dry_run_passes_on_laptop() -> None:
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["dry_run"] is True


def test_report_refuses_until_complete() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.get("/api/report")
    assert res.status_code == 400


def test_lesson_endpoint_hides_accept() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    blocked = client.get("/api/lessons/sync")
    assert blocked.status_code == 400
    path = client.get("/api/lessons/path")
    assert path.status_code == 200
    assert path.json()["step_id"] == "path"
    assert "accept" not in str(path.json())
    signed = client.post(
        "/api/steps/path/pass",
        json={"answers": PASSING_LOCAL["path"]},
    )
    assert signed.status_code == 200
    res = client.get("/api/lessons/sync")
    assert res.status_code == 200
    payload = res.json()
    assert payload["step_id"] == "sync"
    assert "accept" not in str(payload)
    assert payload["handoff"]


def test_ui_is_crate_to_system() -> None:
    client = TestClient(app)
    html = client.get("/").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text
    assert "Crate to system" in html
    assert 'id="rail"' in html
    assert 'class="sheet production-folio"' in html
    assert 'id="site-line"' in html
    assert "family=Inter" in html
    assert "Inter" in css
    assert "Newsreader" not in html
    assert "Newsreader" not in css
    assert 'class="continue"' in html
    assert "Continue" in html
    assert "DGX Quad-Spark Cluster" in js
    assert "Enverge DGX Dual-Spark Cluster" in js
    assert "Runpod NVIDIA B300" in js
    assert "Runpod NVIDIA B200" in js
    assert "This desk" not in html
    assert "Leased GPU" not in html
    assert 'data-site="lease"' not in html
    assert "function paintLesson" in js
    assert "function paintPass" in js
    assert "replaceChildren" in js
    assert "plan-card" in html
    assert "DGX Quad-Spark Cluster" in html
    assert "https://www.runpod.io/" in html
    assert "https://enverge.ai/" in html
    assert "Runpod first" in html
    assert 'data-site="local"' in html
    assert 'data-site="dual"' in html
    assert 'data-site="b300"' in html
    assert 'data-site="b200"' in html
    assert "v=brand3" in html
    assert '<h2>DIONY STUDIO</h2>' not in html
    assert '<h2>A DIONY STUDIO</h2>' not in html
    assert 'poster="/static/studio-maker-motion-first.jpg?v=entrymotion8"' in html
    assert 'poster="/static/studio-team-motion-first.jpg?v=entrymotion8"' in html
    assert ".entry-portrait" in css
    static = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "static"
    for name in ("studio-maker-sketch.png", "studio-team-sketch.png"):
        asset = static / name
        assert asset.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert asset.read_bytes()[25] in {4, 6}
    assert 'id="start-over"' in html
    assert 'id="site-refresh"' in html
    assert 'id="pack-progress"' in html
    assert "function paintPackProgress" in js
    assert "Pack pull in progress" in html
    assert "function refreshWizard" in js
    assert "function paintSiteRefresh" in js
    assert "function stopApplyPoll" in js
    assert "function wipeSheet" in js
    assert "retry-state" in html
    assert "Could not load Studio state" in js
    assert "Diony AI Studio" in html
    assert '<span class="on">Studio</span>' not in html
    assert 'aria-label="Surface"' not in html
    assert "function triggerStudioTransition" in js
    assert "studio-scene-transition" in js
    assert "studio-scene-transition" in css
    assert "studio-scene-in" in css
    assert "function showStateFail" in js
    assert "retry-apply" in html
    assert "done-open-note" in html
    assert "Pull pack" in html
    assert "id=\"prove-card\"" in html
    assert "id=\"test-gpu\"" in html
    assert "id=\"ready-actions\"" in html
    assert "function paintProve" in js
    assert 'step === "app" || step === "prove"' in js
    assert "function lineInteractive" in js
    assert "function sshLineDead" in js
    assert "huggingface.co/settings/tokens" in js
    assert 'field.kind === "password"' in js
    assert "function packRowMissing" in js
    assert "/api/prove" in js
    assert "Local DGX Cluster" not in html
    assert "Local DGX Cluster" not in js
    assert "asChoiceField" in js
    assert "done-sheet" in html
    assert "Compare options" not in js
    assert "advance-nock" in html
    assert "#pass.continue" in css
    assert 'getElementById("site-line")' in js
    assert "Setup is locked. Open Producer." in html
    assert "sprockets" not in html
    assert "Barlow" not in html
    assert "--baize" in css
    assert "--paper" in css
    assert ".choice" in css
    assert "overflow: hidden" in css
    assert "Details" in html
    assert "disclose" in css
    assert "id=\"about\"" in html
    assert "id=\"notes\"" in html
    assert "id=\"connect-guide\"" in html
    assert "id=\"confirm-line\"" in html
    assert "function paintGuide" in js
    assert "function paintConfirm" in js
    assert "Use this" in js
    assert "guide-skip-line" in js
    assert "Hold" not in html
    assert "New report" not in html


def test_connect_confirmation_tracks_live_form_values() -> None:
    js = TestClient(app).get("/static/app.js").text
    assert "function connectConfirmFromInputs" in js
    assert 'formEl?.addEventListener("input"' in js
    assert '["hosts", "ssh_user", "ssh_port", "nas_ip"]' in js
    assert "connectConfirmFromInputs() || state?.confirm" in js


def test_signed_producer_handoff_cannot_reveal_or_repaint_wizard_entry() -> None:
    client = TestClient(app)
    html = client.get("/").text
    js = client.get("/static/app.js").text
    css = client.get("/static/app.css").text
    assert 'id="studio-entry" class="studio-entry"' in html
    assert 'id="studio-intro" class="studio-intro"' in html
    assert 'class="studio-entry" aria-labelledby="studio-entry-title" hidden' in html
    assert ".studio-entry[hidden]" in css
    assert "function syncStudioEntry" in js
    assert 'document.body.dataset.entry = "signed"' in js
    assert 'intro?.setAttribute("hidden", "")' in js
    assert "stopApplyPoll();\n      showComplete();" in js
    assert "function handoffToProducer" in js
    assert "window.location.replace(href)" in js
    assert 'window.addEventListener("pagehide", stopApplyPoll)' in js


def test_studio_intro_enters_start_join_in_place_on_the_named_studio_url() -> None:
    client = TestClient(app)
    html = client.get("/").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text

    assert 'data-product-surface="studio"' in html
    assert 'id="studio-intro" class="studio-intro"' in html
    assert 'id="enter-studio"' in html
    assert "<span>From idea</span><span>to moving</span><span>image.</span>" in html
    assert "Create with control." in html
    assert "Enter to Start New or Join Studio" in html
    assert 'class="studio-intro-mark"' in html
    assert ">ENTER<" in html
    assert 'body[data-entry="intro"] #prep' in css
    assert ".studio-intro[hidden]" in css
    assert "function enterStudioFlow()" in js
    assert 'sessionStorage.setItem("diony_studio_intro", "entered")' in js
    enter_flow = js[js.index("function enterStudioFlow()") : js.index("function startNewStudioEntry()")]
    assert "showStudioEntryChoice()" in enter_flow
    assert "window.open" not in enter_flow
    assert "window.location" not in enter_flow
    assert 'document.getElementById("enter-studio")?.addEventListener("click", enterStudioFlow)' in js


def test_wizard_first_page_has_equal_start_and_join_paths() -> None:
    client = TestClient(app)
    html = client.get("/").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text
    assert 'id="studio-entry"' in html
    assert 'id="start-studio"' in html
    assert 'id="join-existing-studio"' in html
    assert "Unsigned" not in html
    assert "Unsigned" not in js
    assert "START YOUR OWN" in html
    assert "JOIN A TEAM" in html
    assert "START ONE." not in html
    assert "JOIN ONE." not in html
    assert "<span>OWN YOUR STUDIO</span>" in html
    assert "<strong>PRODUCE AT COST</strong>" in html
    assert "Start a Studio" in html
    assert "Join a Studio" in html
    assert html.count('class="entry-action-label"') == 2
    assert html.count('class="entry-action-arrow"') == 2
    assert "Start the Studio" not in html
    assert "Join the Studio" not in html
    assert "http://producer.diony.local/#join" in js
    assert ".studio-entry" in css
    assert ".entry-motto" in css
    assert "grid-template-columns: minmax(0, 1fr) 3rem;" in css
    assert "padding: 1.1rem 1.25rem 1.2rem;" in css
    assert "border: 0;\n  background: transparent;\n  color: var(--ember);" in css
    assert "margin: 0.48rem auto 0.22rem;" in css
    assert "width: clamp(16rem, 27vw, 31rem);" in css
    assert "width: clamp(17rem, 29vw, 33rem);" in css
    assert "height: clamp(10rem, 28vh, 25rem);" in css
    assert ".site-line:empty" in css
    assert ".studio-seam" in css
    assert "@media (max-width: 760px)" in css


def test_wizard_first_page_plays_cropped_entry_motion_once_then_restores_the_first_frame() -> None:
    client = TestClient(app)
    html = client.get("/").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text

    videos = re.findall(r"<video\b.*?</video>", html, flags=re.DOTALL)
    assert len(videos) == 2
    for video in videos:
        assert "muted" in video
        assert "playsinline" in video
        assert 'preload="auto"' in video
        assert "autoplay" not in video
        assert "loop" not in video
        assert "controls" not in video

    assert 'class="entry-motion-still"' not in html
    assert html.count('class="entry-motion-video"') == 2
    assert 'src="/static/studio-maker-motion.mp4?v=entrymotion8"' in html
    assert 'src="/static/studio-team-motion.mp4?v=entrymotion8"' in html
    assert 'poster="/static/studio-maker-motion-first.jpg?v=entrymotion8"' in html
    assert 'poster="/static/studio-team-motion-first.jpg?v=entrymotion8"' in html
    assert "function playStudioEntryMotionOnce()" in js
    assert "function resetStudioEntryMotion" in js
    assert 'motion.dataset.motionPlayed = "true"' in js
    assert 'motion.dataset.motionState = "still"' in js
    assert 'video.addEventListener("ended"' in js
    assert 'video.currentTime = 0' in js
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js
    assert "requestAnimationFrame(playStudioEntryMotionOnce)" in js
    assert '.entry-motion[data-motion-state="playing"] .entry-motion-video' not in css
    assert '.entry-motion[data-motion-state="playing"] .entry-motion-still' not in css
    assert "background: transparent;\n  border: 0;\n  box-shadow: none;" in css
    video_start = css.rindex(".entry-portrait .entry-motion-video {")
    video_rule = css[video_start : css.index("}", video_start)]
    assert "opacity: 0.90;" in video_rule
    assert "filter: none;" in video_rule
    assert "mix-blend-mode: normal;" in video_rule
    assert "visibility: visible;" in video_rule
    assert "clip-path: inset(2px);" in css
    assert "entry-motion-control" not in html

    static = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "static"
    for name in ("studio-maker-motion.mp4", "studio-team-motion.mp4"):
        asset = static / name
        assert asset.read_bytes()[4:8] == b"ftyp"
    for name in ("studio-maker-motion-first.jpg", "studio-team-motion-first.jpg"):
        asset = static / name
        assert asset.read_bytes().startswith(b"\xff\xd8\xff")


def test_studio_entry_uses_right_aligned_team_type_blended_art_and_capsule_actions() -> None:
    css = TestClient(app).get("/static/app.css").text

    heading_rules = re.findall(r"\.studio-entry > h1 span:last-child\s*\{([^}]*)\}", css)
    assert any("margin-left: auto;" in rule for rule in heading_rules)
    assert any("text-align: right;" in rule for rule in heading_rules)

    assert ".entry-portrait .entry-motion-still" not in css
    video_start = css.rindex(".entry-portrait .entry-motion-video {")
    video_rule = css[video_start : css.index("}", video_start)]
    assert "filter: none;" in video_rule
    assert "mix-blend-mode: normal;" in video_rule
    portrait_rules = re.findall(r"\.entry-portrait\s*\{([^}]*)\}", css)
    assert any("background-color: var(--folio);" in rule for rule in portrait_rules)

    assert "--action-pill-radius: 999px;" in css
    for selector in (
        ".studio-intro-enter",
        ".entry-action",
        ".word",
        ".production-folio .choice button",
        ".existing-compute-action",
        ".agent-continue",
        ".inspect-compute-button",
        ".production-folio #pass.continue",
    ):
        rules = re.findall(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
        assert any("border-radius: var(--action-pill-radius);" in rule for rule in rules)

    path_rules = re.findall(r"\.entry-paths\s*\{([^}]*)\}", css)
    assert any("border-bottom: 0;" in rule for rule in path_rules)
    entry_action_rules = re.findall(r"\.entry-action\s*\{([^}]*)\}", css)
    assert any("min-height: 3.5rem;" in rule for rule in entry_action_rules)
    assert any("margin-bottom: 0.7rem;" in rule for rule in entry_action_rules)
    arrow_start = css.rindex(".entry-action-arrow {")
    arrow_rule = css[arrow_start : css.index("}", arrow_start)]
    assert "background: transparent;" in arrow_rule
    for selector in (".existing-compute-arrow", ".inspect-compute-button > b"):
        arrow_rules = re.findall(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
        assert any("background: transparent;" in rule for rule in arrow_rules)


def test_compute_page_uses_balanced_readable_responsive_layout() -> None:
    client = TestClient(app)
    html = client.get("/").text
    js = client.get("/static/app.js").text
    css = client.get("/static/app.css").text

    assert '/static/app.css?v=brand11' in html
    assert 'class="compute-group compute-group-local"' in html
    assert 'class="compute-group compute-group-lease"' in html
    assert 'group.className = `compute-group compute-group-${label.toLowerCase()}`;' in js

    assert "--compute-card-radius: 1.5rem;" in css
    assert 'body[data-step="path"] .production-folio .cue-workspace' in css
    assert "max-width: 78rem;" in css
    assert "grid-template-columns: minmax(0, 1.45fr) minmax(21rem, 0.85fr);" in css
    assert "border-radius: var(--compute-card-radius);" in css
    assert "text-wrap: balance;" in css
    assert "overflow-wrap: normal;" in css
    assert "@media (max-width: 1180px)" in css
    assert "@media (max-height: 950px)" in css
    assert 'body[data-step="path"]:not([data-provision="active"]) .existing-compute-action' in css
    assert 'body[data-provision="active"] .production-folio .cue-workspace' in css

    final_group_rule = css[css.rindex(".compute-group {") :]
    final_group_rule = final_group_rule[: final_group_rule.index("}")]
    assert "background: rgba(255, 255, 255, 0.035);" in final_group_rule

    final_provision_rule = css[css.rindex(".provision-panel {") :]
    final_provision_rule = final_provision_rule[: final_provision_rule.index("}")]
    assert "border-radius: var(--compute-card-radius);" in final_provision_rule
    assert "background: rgba(255, 255, 255, 0.035);" in final_provision_rule

    final_runpod_rule = css[css.rindex(".runpod-command-entry {") :]
    final_runpod_rule = final_runpod_rule[: final_runpod_rule.index("}")]
    assert "rgba(255, 255, 255, 0.025)" in final_runpod_rule

    final_provision_heading_rule = css[css.rindex(".provision-panel h3 {") :]
    final_provision_heading_rule = final_provision_heading_rule[: final_provision_heading_rule.index("}")]
    assert "color: var(--light);" in final_provision_heading_rule
    assert "font-size: clamp(1.8rem, 2.25vw, 2.7rem);" in final_provision_heading_rule

    assert 'body[data-provision="active"] .provision-form:has(.runpod-command-entry) label.wide textarea' in css
    assert "min-height: 3rem;" in css
    assert 'body[data-provision="active"] .provision-actions' in css

    final_refresh_rule = css[css.rindex('body[data-step="path"] .site-refresh {') :]
    final_refresh_rule = final_refresh_rule[: final_refresh_rule.index("}")]
    assert "margin-top: 1.25rem;" in final_refresh_rule


def test_studio_motion_grade_matches_the_static_art_transition() -> None:
    client = TestClient(app)
    html = client.get("/").text
    css = client.get("/static/app.css").text

    assert html.count("?v=entrymotion8") == 4
    assert '.entry-motion[data-motion-state="playing"] .entry-motion-video' not in css

    video_start = css.rindex(".entry-portrait .entry-motion-video {")
    video_rule = css[video_start : css.index("}", video_start)]
    assert "filter: none;" in video_rule
    assert "mix-blend-mode: normal;" in video_rule

    portrait_rules = re.findall(r"\.entry-portrait\s*\{([^}]*)\}", css)
    assert any("background-color: var(--folio);" in rule for rule in portrait_rules)


def test_studio_motion_and_final_frame_share_one_video_color_pipeline() -> None:
    client = TestClient(app)
    html = client.get("/").text
    css = client.get("/static/app.css").text

    assert 'class="entry-motion-still"' not in html
    assert html.count('class="entry-motion-video"') == 2
    assert html.count("?v=entrymotion8") == 4

    video_start = css.rindex(".entry-portrait .entry-motion-video {")
    video_rule = css[video_start : css.index("}", video_start)]
    assert "filter: none;" in video_rule
    assert "mix-blend-mode: normal;" in video_rule
    assert "visibility: visible;" in video_rule
    assert "opacity: 0.90;" in video_rule

    assert '.entry-motion[data-motion-state="playing"] .entry-motion-still' not in css
    assert '.entry-motion[data-motion-state="playing"] .entry-motion-video' not in css


def test_production_folio_never_replays_a_full_surface_opacity_animation() -> None:
    css = TestClient(app).get("/static/app.css").text
    assert """.sheet.production-folio {
  animation: none;
  opacity: 1;
  transform: none;
}""" in css


def test_wizard_keeps_only_the_action_and_fields_visible_by_default() -> None:
    html = TestClient(app).get("/").text
    js = TestClient(app).get("/static/app.js").text

    details_at = html.index('<details id="about"')
    assert details_at < html.index('id="plan-card"')
    assert details_at < html.index('id="connect-guide"')
    assert '<details id="activity"' in html
    assert html.index('<details id="activity"') < html.index('id="apply-log"')
    assert "SHORT_INSTRUCTIONS" in js
    assert "setText(learnWhy, stepInstruction(item))" in js
    assert 'slate.textContent = "Check Site"' in js


def test_minimal_wizard_has_a_last_loaded_narrow_layout() -> None:
    css = TestClient(app).get("/static/folio.css").text
    assert "Ready 36 responsive authority" in css
    assert ".production-folio .screening" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "position: fixed" in css


def test_ready_only_offers_pull_when_the_pack_is_missing_or_failed() -> None:
    js = TestClient(app).get("/static/app.js").text
    assert 'step === "prove" ? packGone || Boolean(apply.error)' in js
