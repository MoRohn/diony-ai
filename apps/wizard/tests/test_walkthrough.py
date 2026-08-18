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
    "producer": {
        "floors": "Idea, Dailies, Program, Lock",
        "prompt_edit": "yes",
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
    "producer": {
        "floors": "Idea, Dailies, Program, Lock",
        "prompt_edit": "yes",
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
    assert site["options"] == ["local", "dual", "b300"]


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
    assert report["workbook"]["producer"]["url"] == "http://producer.diony.local"
    assert len(report["workbook"]) >= 5
    assert [s["id"] for s in report["steps"]] == [
        "path",
        "connect",
        "app",
        "producer",
    ]
    assert [s["name"] for s in report["steps"]] == [
        "Site",
        "Connect",
        "App",
        "Producer",
    ]
    assert "signed exception" in report["mandate"]


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
    assert report["site_lock"]


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
    assert names == ["Site", "Connect", "App", "Producer"]
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
    assert after["active_step"] == "producer"
    assert after["workbook"]["weights"]["pack"] == "b300-v1"
    assert after["workbook"]["brain_smoke"]["recipe"] == "b300-v1 TP=1"


def test_b300_app_includes_the_pack() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    client.post("/api/steps/path/pass", json={"answers": PASSING_B300["path"]})
    client.post("/api/steps/connect/pass", json={"answers": {}})
    assert client.get("/api/lessons/weights").status_code == 400
    assert client.get("/api/lessons/brain_smoke").status_code == 400
    producer = client.get("/api/lessons/producer").json()
    assert any("diony-producer" in line for line in producer["commands"])
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
    assert 'class="sheet"' in html
    assert 'id="site-line"' in html
    assert "IBM+Plex+Sans" in html
    assert "IBM Plex Sans" in css
    assert "Newsreader" not in html
    assert "Newsreader" not in css
    assert 'class="continue"' in html
    assert "Continue" in html
    assert "Local Quad-Spark Cluster" in js
    assert "Leased Dual-Spark Cluster" in js
    assert "Leased B300" in js
    assert "This desk" not in html
    assert "Leased GPU" not in html
    assert 'data-site="lease"' not in html
    assert "function paintLesson" in js
    assert "function paintPass" in js
    assert "replaceChildren" in js
    assert "plan-card" in html
    assert "Local Quad-Spark Cluster" in html
    assert 'data-site="local"' in html
    assert 'data-site="dual"' in html
    assert 'data-site="b300"' in html
    assert "v=polish10" in html
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
    assert "Hold" not in html
    assert "New report" not in html
