from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from diony_wizard.provisioning import (
    ProvisioningService,
    classify_existing_compute,
    runpod_plan,
)
from diony_wizard.enterprise import enterprise_hf_token
from diony_wizard.provisioning import ProvisioningError

wizard_app = importlib.import_module("diony_wizard.app")


class FakeRunpod:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.polls = 0

    def quote(self, path: str, token: str) -> dict:
        assert token == "rpa_secret"
        return {
            "gpu_id": runpod_plan(path)["gpu_type_id"],
            "stock": "High",
            "hourly": 7.39 if path == "b300" else 5.89,
            "cloud": "COMMUNITY",
        }

    def create(self, path: str, token: str) -> dict:
        assert token == "rpa_secret"
        plan = runpod_plan(path)
        self.created.append(plan)
        return {"id": "pod-diony-1", "desiredStatus": "RUNNING"}

    def get(self, pod_id: str, token: str) -> dict:
        assert pod_id == "pod-diony-1"
        assert token == "rpa_secret"
        self.polls += 1
        if self.polls == 1:
            return {"id": pod_id, "desiredStatus": "RUNNING", "publicIp": None}
        return {
            "id": pod_id,
            "desiredStatus": "RUNNING",
            "publicIp": "198.51.100.42",
            "portMappings": {"22": 30422},
            "volumeMountPath": "/workspace",
        }


class EchoingRunpod(FakeRunpod):
    def quote(self, path: str, token: str) -> dict:
        raise ProvisioningError(f"provider rejected {token} and {enterprise_hf_token()}")


class FakeExistingInspector:
    def __init__(self, *, path: str = "b200", prepared: bool = True) -> None:
        self.path = path
        self.prepared = prepared
        self.calls: list[tuple[str, dict]] = []

    def inspect(self, scope: str, connection: dict) -> dict:
        self.calls.append((scope, dict(connection)))
        hosts = [part for part in connection["hosts"].splitlines() if part]
        return {
            "path": self.path,
            "profile": {
                "local": "spark-4x-v1",
                "dual": "spark-2x-v1",
                "b300": "b300-v1",
                "b200": "b200-v1",
            }[self.path],
            "prepared": self.prepared,
            "producer_running": self.prepared,
            "vault_source": "/workspace" if self.path in {"b200", "b300"} else "nfs://10.0.8.12/diony-vault",
            "hosts": hosts,
            "checks": [
                {"id": "ssh", "label": "SSH", "ok": True, "detail": f"{len(hosts)} host(s) accepted"},
                {"id": "gpu", "label": "GPU", "ok": True, "detail": self.path},
                {"id": "vault", "label": "Vault", "ok": True, "detail": "/diony/vault"},
                {"id": "pack", "label": "Pack", "ok": self.prepared, "detail": "ready" if self.prepared else "missing"},
            ],
            "nodes": [
                {
                    "host": host,
                    "gpu": "NVIDIA B200" if self.path == "b200" else "NVIDIA GB10",
                    "prepared": self.prepared,
                    "producer_running": self.prepared,
                }
                for host in hosts
            ],
        }


def test_runpod_plan_is_the_diony_site_contract() -> None:
    b300 = runpod_plan("b300")
    assert b300["gpu_type_id"] == "NVIDIA B300 SXM6 AC"
    assert b300["gpu_count"] == 1
    assert b300["container_disk_gb"] == 800
    assert b300["volume_gb"] == 2000
    assert b300["volume_mount_path"] == "/workspace"
    assert b300["ports"] == ["22/tcp"]
    assert b300["profile"] == "b300-v1"

    b200 = runpod_plan("b200")
    assert b200["gpu_type_id"] == "NVIDIA B200"
    assert b200["profile"] == "b200-v1"
    assert b200["container_disk_gb"] == 800
    assert b200["volume_gb"] == 2000
    assert b200["interruptible"] is False


def test_runpod_provisioning_is_resumable_and_never_returns_secrets() -> None:
    fake = FakeRunpod()
    service = ProvisioningService(runpod=fake)

    started = service.start("b200")
    assert started["provider"] == "Runpod"
    assert started["checkpoint"] == "provider_account"
    assert started["action_url"] == "https://console.runpod.io/signup"

    quoted = service.advance(
        {
            "action": "provider_ready",
            "signer": "Rohn Springfield",
            "provider_token": "rpa_secret",
            "hf_token": "hf_secret",
        }
    )
    assert quoted["checkpoint"] == "deploy_confirmation"
    assert quoted["quote"]["hourly"] == 5.89
    assert "rpa_secret" not in str(quoted)
    assert "hf_secret" not in str(quoted)

    deploying = service.advance({"action": "deploy"})
    assert deploying["status"] == "working"
    assert deploying["resource_id"] == "pod-diony-1"
    assert fake.created[0]["volume_gb"] == 2000

    waiting = service.refresh()
    assert waiting["status"] == "working"
    ready = service.refresh()
    assert ready["status"] == "ready"
    assert ready["connection"] == {
        "hosts": "198.51.100.42",
        "ssh_user": "root",
        "ssh_port": "30422",
        "nas_ip": "/workspace",
    }
    assert "rpa_secret" not in str(ready)
    assert "hf_secret" not in str(ready)


def test_provider_errors_are_redacted_even_if_the_provider_echoes_a_secret() -> None:
    service = ProvisioningService(runpod=EchoingRunpod())
    service.start("b200")
    try:
        service.advance(
            {
                "action": "provider_ready",
                "signer": "Rohn Springfield",
                "provider_token": "rpa_secret",
            }
        )
    except ProvisioningError as exc:
        assert "rpa_secret" not in str(exc)
        assert enterprise_hf_token() not in str(exc)
    public = service.public()
    assert "rpa_secret" not in str(public)
    assert "hf_secret" not in str(public)


def test_enverge_dual_spark_pauses_only_for_account_and_allocation() -> None:
    service = ProvisioningService(runpod=FakeRunpod())
    started = service.start("dual")
    assert started["provider"] == "Enverge"
    assert started["action_url"] == "https://spark.enverge.ai/"
    assert started["checkpoint"] == "provider_account"

    waiting = service.advance(
        {
            "action": "allocation_requested",
            "signer": "Rohn Springfield",
            "hf_token": "hf_secret",
        }
    )
    assert waiting["checkpoint"] == "provider_allocation"
    assert "hf_secret" not in str(waiting)

    ready = service.advance(
        {
            "action": "connection_ready",
            "hosts": "spark-a.example\nspark-b.example",
            "ssh_user": "diony",
            "ssh_port": "22",
            "nas_ip": "nfs://10.0.8.12/diony-vault",
        }
    )
    assert ready["status"] == "ready"
    assert ready["connection"]["hosts"].splitlines() == [
        "spark-a.example",
        "spark-b.example",
    ]


def test_ready_runpod_site_is_adopted_into_studio_and_starts_app(monkeypatch) -> None:
    fake = FakeRunpod()
    service = ProvisioningService(runpod=fake)
    monkeypatch.setattr(wizard_app, "_provisioner", service)
    client = TestClient(wizard_app.app)
    client.post("/api/reset")

    assert client.post("/api/provision/start", json={"path": "b200"}).status_code == 200
    quoted = client.post(
        "/api/provision/advance",
        json={
            "action": "provider_ready",
            "signer": "Rohn Springfield",
            "provider_token": "rpa_secret",
            "hf_token": "hf_secret",
        },
    )
    assert quoted.status_code == 200
    assert client.post("/api/provision/advance", json={"action": "deploy"}).status_code == 200
    client.get("/api/provision")
    ready = client.get("/api/provision").json()
    assert ready["status"] == "ready"

    adopted = client.post("/api/provision/adopt")
    assert adopted.status_code == 200, adopted.text
    state = adopted.json()
    assert state["compute_path"] == "b200"
    assert state["active_step"] == "prove"
    assert state["workbook"]["connect"]["hosts"] == "198.51.100.42"
    assert state["workbook"]["connect"]["ssh_user"] == "root"
    assert state["workbook"]["connect"]["ssh_port"] == "30422"
    assert state["workbook"]["connect"]["nas_ip"] == "/workspace"
    assert state["workbook"]["weights"]["pack"] == "b200-v1"
    assert "rpa_secret" not in str(state)
    assert "hf_secret" not in str(state)


def test_compute_ui_has_grouped_choices_confirmation_and_agent_progress() -> None:
    client = TestClient(wizard_app.app)
    html = client.get("/classic").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text

    assert "COMPUTE" in html
    assert "Choose where Diony AI will run. Agent will take it from here." in html
    assert 'class="compute-group compute-group-local"' in html
    assert 'class="compute-group compute-group-lease"' in html
    assert "LOCAL" in html
    assert "LEASE" in html
    assert 'id="compute-confirm"' in html
    assert "Confirm option?" in html
    assert 'id="provision-panel"' in html
    assert 'aria-live="polite"' in html
    assert "function openComputeConfirmation" in js
    assert "function paintProvisioning" in js
    assert "const standaloneError = normalizedError && normalizedError !== normalizedDetail" in js
    assert "function setProvisionError" in js
    assert "provisionError.hidden = !detail" in js
    assert "/api/provision/start" in js
    assert "/api/provision/adopt" in js
    assert ".compute-group" in css
    assert ".provision-timeline" in css
    assert ".mast-reset" in css
    assert "margin-left: auto" in css


def test_existing_compute_progress_updates_the_active_validation_event() -> None:
    service = ProvisioningService(runpod=FakeRunpod(), inspector=FakeExistingInspector())
    service.start_existing("lease")
    service.mark_existing_validating()

    state = service.mark_existing_progress("Downloading Wan2.2 speech model")

    assert state["status"] == "working"
    assert state["detail"] == "Downloading Wan2.2 speech model"
    assert state["events"][-1]["status"] == "active"
    assert state["events"][-1]["detail"] == "Downloading Wan2.2 speech model"


def test_setup_agent_owns_action_area_until_validation_is_complete() -> None:
    client = TestClient(wizard_app.app)
    html = client.get("/classic").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text

    assert 'id="provision-progress"' in html
    assert 'role="progressbar"' in html
    assert "SETUP IN PROGRESS" in html
    assert "const setupInProgress = next.status === \"working\" || next.status === \"ready\";" in js
    assert "provisionProgress.hidden = !setupInProgress;" in js
    assert 'provisionContinue.classList.toggle("setup-complete", setupComplete);' in js
    assert 'document.body.dataset.provision === "active"' in js
    assert 'provision?.status === "complete" && provision.handoff_url' in js
    assert "window.location.assign(provision.handoff_url);" in js
    assert ".provision-progress" in css
    assert ".provision-actions .agent-continue" in css
    complete_rule_start = css.index(".provision-actions .agent-continue.setup-complete {")
    complete_rule = css[complete_rule_start : css.index("}", complete_rule_start)]
    assert "background: var(--void);" in complete_rule
    assert "border-color: var(--void);" in complete_rule
    assert "color: #fff;" in complete_rule
    assert "@keyframes provision-turn" in css


def test_connect_page_reports_waiting_working_ready_and_attention_states() -> None:
    client = TestClient(wizard_app.app)
    html = client.get("/classic").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text

    assert '/static/app.js?v=brand17' in html
    assert 'id="provision-live-status"' in html
    assert 'id="provision-live-label"' in html
    assert 'id="provision-live-detail"' in html
    assert 'id="provision-live-count"' in html
    assert 'id="provision-live-track"' in html
    assert 'id="provision-live-bar"' in html

    assert "function provisionStatusView" in js
    assert "function paintProvisionStatus" in js
    assert "function paintProvisionBusy" in js
    assert "Read-only inspection in progress" in js
    assert "Waiting for each host to return GPU, fabric, vault, and pack checks." in js
    assert 'provisionPanel.setAttribute("aria-busy", "true")' in js
    assert 'document.body.dataset.provisionStatus' in js

    assert ".provision-live-status" in css
    assert '.provision-live-status[data-state="working"]' in css
    assert '.provision-live-status[data-state="complete"]' in css
    assert '.provision-live-status[data-state="failed"]' in css
    assert "@keyframes provision-live-scan" in css


def test_compute_options_name_hardware_and_vendors_without_repeating_group_labels() -> None:
    client = TestClient(wizard_app.app)
    html = client.get("/classic").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text

    for surface in (html, js):
        assert "DGX Quad-Spark Cluster" in surface
        assert "Enverge DGX Dual-Spark Cluster" in surface
        assert "Runpod NVIDIA B300" in surface
        assert "Runpod NVIDIA B200" in surface
        assert "Local Quad-Spark Cluster" not in surface
        assert "Leased Dual-Spark Cluster" not in surface
        assert "Leased B300" not in surface
        assert "Leased B200" not in surface

    assert 'class="local-cluster-sketches"' in html
    assert "function localClusterSketches" in js
    assert "CX-7 PORT 2" in html
    assert "QSFP 200G" in html
    assert "RJ-45 · CAT6" in html
    assert "MIKROTIK FABRIC" in html
    assert "10GBE MANAGEMENT + VAULT" in html
    assert ".local-cluster-sketches" in css
    assert ".cluster-cable" in css


def test_existing_compute_classifier_detects_supported_diony_profiles() -> None:
    local = classify_existing_compute(
        "local",
        [{"gpu": "NVIDIA GB10 Grace Blackwell", "fabric": "ACTIVE 200G"}] * 4,
    )
    assert local == ("local", "spark-4x-v1")

    dual = classify_existing_compute(
        "lease",
        [{"gpu": "NVIDIA GB10", "fabric": "ACTIVE"}] * 2,
    )
    assert dual == ("dual", "spark-2x-v1")

    assert classify_existing_compute("lease", [{"gpu": "NVIDIA B300 SXM6 AC"}]) == (
        "b300",
        "b300-v1",
    )
    assert classify_existing_compute("lease", [{"gpu": "NVIDIA B200"}]) == (
        "b200",
        "b200-v1",
    )

    try:
        classify_existing_compute("local", [{"gpu": "NVIDIA B200"}])
    except ProvisioningError as exc:
        assert "four" in str(exc).lower()
    else:
        raise AssertionError("A single B200 must not be adopted as Local")


def test_existing_compute_agent_discovers_profile_and_only_requests_token_for_repair() -> None:
    prepared_inspector = FakeExistingInspector(path="b200", prepared=True)
    prepared = ProvisioningService(runpod=FakeRunpod(), inspector=prepared_inspector)
    started = prepared.start_existing("lease")
    assert started["mode"] == "existing"
    assert started["checkpoint"] == "existing_connection"
    assert started["required_inputs"] == [
        "provider",
        "signer",
        "hosts",
        "ssh_user",
        "ssh_port",
    ]
    ready = prepared.advance(
        {
            "action": "inspect_existing",
            "provider": "Runpod",
            "signer": "Rohn Springfield",
            "hosts": "198.51.100.42",
            "ssh_user": "root",
            "ssh_port": "30422",
            "ssh_identity_file": "/Users/operator/.ssh/id_ed25519",
        }
    )
    assert ready["status"] == "ready"
    assert ready["path"] == "b200"
    assert ready["profile"] == "b200-v1"
    assert ready["inspection"]["prepared"] is True
    assert ready["connection"]["nas_ip"] == "/workspace"
    assert "id_ed25519" not in str(ready)
    assert prepared_inspector.calls[0][1]["ssh_identity_file"] == (
        "/Users/operator/.ssh/id_ed25519"
    )
    assert prepared_inspector.calls[0][1]["nas_ip"] == ""

    missing = ProvisioningService(
        runpod=FakeRunpod(), inspector=FakeExistingInspector(path="dual", prepared=False)
    )
    missing.start_existing("lease")
    needs_token = missing.advance(
        {
            "action": "inspect_existing",
            "provider": "Enverge",
            "signer": "Rohn Springfield",
            "hosts": "spark-a.example\nspark-b.example",
            "ssh_user": "diony",
            "ssh_port": "22",
        }
    )
    # The pack is missing, but no token is requested: the backend holds the
    # Diony-Enterprise HF Token and repairs only what is missing.
    assert needs_token["status"] == "ready"
    assert needs_token["required_inputs"] == []
    assert any(e["label"] == "Repair authorization" for e in needs_token["events"])
    assert "hf_" not in str(needs_token)
    assert missing.consume()["hf_token"] == enterprise_hf_token()


def test_existing_compute_is_validated_signed_and_handed_to_producer(monkeypatch) -> None:
    service = ProvisioningService(
        runpod=FakeRunpod(), inspector=FakeExistingInspector(path="b200", prepared=True)
    )
    monkeypatch.setattr(wizard_app, "_provisioner", service)
    monkeypatch.setenv("DIONY_APPLY_FAKE", "1")
    client = TestClient(wizard_app.app)
    client.post("/api/reset")

    assert client.post("/api/provision/existing/start", json={"scope": "lease"}).status_code == 200
    inspected = client.post(
        "/api/provision/advance",
        json={
            "action": "inspect_existing",
            "provider": "Runpod",
            "signer": "Rohn Springfield",
            "hosts": "198.51.100.42",
            "ssh_user": "root",
            "ssh_port": "30422",
            "ssh_identity_file": "/Users/operator/.ssh/id_ed25519",
            "nas_ip": "/workspace",
        },
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["status"] == "ready"

    adopted = client.post("/api/provision/adopt")
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["provision"]["checkpoint"] == "validating_existing"

    completed = client.post("/api/provision/existing/finalize")
    assert completed.status_code == 200, completed.text
    state = completed.json()
    assert state["complete"] is True
    assert state["compute_path"] == "b200"
    assert state["gpu_ready"] is True
    assert state["report"]["producer_url"] == "http://producer.diony.local"
    assert state["provision"]["status"] == "complete"
    assert state["provision"]["checkpoint"] is None
    assert state["provision"]["handoff_url"] == "http://producer.diony.local"
    assert "id_ed25519" not in str(state)


def test_failed_existing_validation_starts_a_real_retry(monkeypatch) -> None:
    service = ProvisioningService(
        runpod=FakeRunpod(), inspector=FakeExistingInspector(path="b200", prepared=True)
    )
    monkeypatch.setattr(wizard_app, "_provisioner", service)
    monkeypatch.setenv("DIONY_APPLY_FAKE", "1")
    client = TestClient(wizard_app.app)
    client.post("/api/reset")
    client.post("/api/provision/existing/start", json={"scope": "lease"})
    inspected = client.post(
        "/api/provision/advance",
        json={
            "action": "inspect_existing",
            "provider": "Runpod",
            "signer": "Rohn Springfield",
            "hosts": "198.51.100.42",
            "ssh_user": "root",
            "ssh_port": "30422",
            "nas_ip": "/workspace",
        },
    )
    assert inspected.status_code == 200
    assert client.post("/api/provision/adopt").status_code == 200
    service.mark_existing_attention("Voice model download failed.")
    captured: dict = {}

    def fake_retry(path, hosts, *, nas="", workbook=None):
        captured.update(path=path, hosts=hosts, nas=nas, workbook=workbook)
        return {"running": True, "ok": False, "detail": "Checking the pinned model pack"}

    monkeypatch.setattr(wizard_app, "retry_apply", fake_retry)

    retried = client.post("/api/provision/existing/retry")

    assert retried.status_code == 200, retried.text
    assert retried.json()["provision"]["status"] == "working"
    assert captured["path"] == "b200"
    assert captured["hosts"] == ["198.51.100.42"]
    assert captured["nas"] == "/workspace"


def test_existing_local_quad_spark_skips_crate_steps_only_after_four_host_validation(monkeypatch) -> None:
    service = ProvisioningService(
        runpod=FakeRunpod(), inspector=FakeExistingInspector(path="local", prepared=True)
    )
    monkeypatch.setattr(wizard_app, "_provisioner", service)
    monkeypatch.setenv("DIONY_APPLY_FAKE", "1")
    client = TestClient(wizard_app.app)
    client.post("/api/reset")
    client.post("/api/provision/existing/start", json={"scope": "local"})
    inspected = client.post(
        "/api/provision/advance",
        json={
            "action": "inspect_existing",
            "hosts": "spark-01\nspark-02\nspark-03\nspark-04",
            "ssh_user": "spark",
            "ssh_port": "22",
            "nas_ip": "nfs://10.0.8.12/diony-vault",
        },
    )
    assert inspected.status_code == 200, inspected.text
    adopted = client.post("/api/provision/adopt")
    assert adopted.status_code == 200, adopted.text
    completed = client.post("/api/provision/existing/finalize")
    assert completed.status_code == 200, completed.text
    state = completed.json()
    assert state["complete"] is True
    assert state["compute_path"] == "local"
    assert state["report"]["nodes"] == ["spark-01", "spark-02", "spark-03", "spark-04"]
    assert all(step["status"] == "passed" for step in state["steps"])
    assert state["workbook"]["existing_compute"]["profile"] == "spark-4x-v1"


def test_connect_to_existing_compute_is_available_at_bottom_of_both_groups() -> None:
    client = TestClient(wizard_app.app)
    html = client.get("/classic").text
    css = client.get("/static/app.css").text
    js = client.get("/static/app.js").text

    assert html.count("Connect to Existing Compute") == 2
    assert html.count("AUTONOMOUS SETUP") == 2
    assert html.count("Discover · Validate · Open Producer") == 2
    assert html.count('class="existing-compute-status"') == 2
    assert html.count('class="existing-compute-arrow"') == 2
    assert 'data-existing-scope="local"' in html
    assert 'data-existing-scope="lease"' in html
    assert "function startExistingCompute" in js
    assert "/api/provision/existing/start" in js
    assert "/api/provision/existing/finalize" in js
    assert "/api/provision/existing/retry" in js
    assert "Retry validation" in js
    assert "Vault auto-discovery" in js
    assert "Diony identifies private storage through this SSH connection" in js
    assert "inspect-compute-button" in js
    assert ".existing-compute-action" in css
    assert ".existing-compute-copy" in css
    assert ".existing-compute-status" in css
    assert ".existing-compute-arrow" in css
    assert "box-shadow: 6px 6px 0 var(--signal)" in css
    assert "--existing-action-depth: 8px;" in css
    assert "padding-bottom: calc(var(--existing-action-depth) + 0.65rem);" in css
    assert ".vault-discovery-card" in css
    assert ".inspect-compute-button" in css
    assert "background: var(--void);" in css


def test_existing_compute_ssh_hosts_explains_command_and_host_examples() -> None:
    client = TestClient(wizard_app.app)
    js = client.get("/static/app.js").text

    assert '"SSH host(s)"' in js
    assert '"spark-01 or 198.13.252.68"' in js
    assert "root@[host-name] -p [port]" in js
    assert "enter only [host-name]" in js
    assert "gpu.example or spark-01" not in js


def test_existing_compute_connection_only_requires_ssh_and_records_discovered_vault() -> None:
    inspector = FakeExistingInspector(path="b300", prepared=True)
    service = ProvisioningService(runpod=FakeRunpod(), inspector=inspector)
    service.start_existing("lease")

    ready = service.advance(
        {
            "action": "inspect_existing",
            "provider": "Runpod",
            "signer": "Rohn Springfield",
            "hosts": "198.51.100.99",
            "ssh_user": "root",
            "ssh_port": "22122",
        }
    )

    assert ready["status"] == "ready"
    assert ready["connection"] == {
        "hosts": "198.51.100.99",
        "ssh_user": "root",
        "ssh_port": "22122",
        "nas_ip": "/workspace",
    }


def test_remote_setup_pulls_the_optional_montage_pack_best_effort() -> None:
    from diony_wizard.apply import MONTAGE_PACK, remote_setup_script

    script = remote_setup_script("/opt/diony-ai")
    assert "MONTAGE_PACK_START" in script
    assert "HF_OPTIONAL_FAIL" in script and "MONTAGE_PACK_SKIPPED" in script
    assert "${DIONY_MONTAGE_PACK:-1}" in script
    for repo, dest, revision, _ignore, _files, _bytes in MONTAGE_PACK:
        assert repo in script and dest in script and revision in script
    # The optional pack never joins the pack gate.
    assert "faster-whisper-large-v3" not in script.split("MONTAGE_PACK_START")[0].split("HF_START")[1]
