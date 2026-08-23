from diony_wizard.curriculum import CURRICULUM, lesson_for, lessons_for, validate_answers
from diony_wizard.lessons_b200 import B200_LESSONS
from diony_wizard.lessons_b300 import B300_LESSONS
from diony_wizard.lessons_dual import DUAL_LESSONS
from diony_wizard.lessons_lease import LEASE_LESSONS
from diony_wizard.steps import B200_RAIL, B300_RAIL, DUAL_RAIL, LEASE_RAIL, LOCAL_RAIL, initial_steps, rail_ids


def test_first_step_is_site() -> None:
    steps = initial_steps()
    assert steps[0].id == "path"
    assert steps[0].name == "Site"
    assert steps[0].status == "active"
    assert len(steps) == 1


def test_rails_by_site() -> None:
    assert rail_ids(None) == ["path"]
    assert rail_ids("local") == LOCAL_RAIL
    assert rail_ids("lease") == LEASE_RAIL
    assert rail_ids("dual") == DUAL_RAIL
    assert rail_ids("b300") == B300_RAIL
    assert rail_ids("b200") == B200_RAIL
    assert B200_RAIL == B300_RAIL
    assert LEASE_RAIL == [
        "path",
        "connect",
        "producer",
    ]
    assert "cables" not in LEASE_RAIL
    assert "unbox" not in LEASE_RAIL
    assert "inventory" not in LEASE_RAIL
    assert DUAL_RAIL == [
        "path",
        "connect",
        "app",
        "prove",
        "producer",
    ]
    assert B300_RAIL == DUAL_RAIL
    assert B200_RAIL == DUAL_RAIL
    assert "weights" not in B300_RAIL
    assert "brain_smoke" not in B300_RAIL
    assert "brain_smoke" not in LOCAL_RAIL
    assert B300_RAIL != LEASE_RAIL
    assert "unbox" not in B300_RAIL
    assert "unbox" not in DUAL_RAIL


def test_curriculum_covers_every_step() -> None:
    assert set(CURRICULUM) == set(LOCAL_RAIL)
    assert set(lessons_for("local")) == set(LOCAL_RAIL)
    assert set(lessons_for("lease")) == set(LEASE_RAIL)
    assert set(lessons_for("dual")) == set(DUAL_RAIL)
    assert set(lessons_for("b300")) == set(B300_RAIL)
    assert set(lessons_for("b200")) == set(B200_RAIL)
    assert set(LEASE_LESSONS) == set(LEASE_RAIL) - {"path"}
    assert set(B300_LESSONS) == set(B300_RAIL) - {"path"}
    assert set(B200_LESSONS) == set(B200_RAIL) - {"path"}
    assert set(DUAL_LESSONS) == set(DUAL_RAIL) - {"path"}
    for lesson in CURRICULUM.values():
        assert lesson.why
        assert lesson.teach
        assert lesson.fail_if
        assert lesson.fields


def test_local_producer_how_installs_finish() -> None:
    cmds = "\n".join(lesson_for("producer", "local").commands)
    assert "apps/finish" in cmds
    assert "DIONY_VAULT=/diony/vault" in cmds
    assert "diony-producer" in cmds


def test_later_lesson_needs_a_path() -> None:
    try:
        lesson_for("unbox")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    assert lesson_for("unbox", "local").step_id == "unbox"
    try:
        lesson_for("unbox", "lease")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    try:
        lesson_for("inventory", "lease")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    assert lesson_for("connect", "lease").fields[0].id == "hosts"
    try:
        lesson_for("unbox", "b300")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    try:
        lesson_for("app", "local")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    assert lesson_for("connect", "b300").fields[0].id == "hosts"
    assert lesson_for("connect", "b200").fields[0].id == "hosts"
    assert lesson_for("connect", "dual").fields[0].id == "hosts"
    try:
        lesson_for("weights", "b300")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    try:
        lesson_for("brain_smoke", "b300")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    assert lesson_for("app", "b300").step_id == "app"
    assert lesson_for("app", "b200").step_id == "app"
    assert lesson_for("app", "dual").step_id == "app"
    assert lesson_for("prove", "b300").step_id == "prove"
    assert lesson_for("prove", "b200").step_id == "prove"
    assert lesson_for("prove", "dual").step_id == "prove"
    try:
        lesson_for("prove", "local")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    assert "huggingface-cli" in "\n".join(lesson_for("app", "b300").commands)
    assert any("vllm serve" in c for c in lesson_for("app", "b300").commands)
    assert "--max-model-len 16384" in "\n".join(lesson_for("app", "b200").commands)
    assert "1048576" not in "\n".join(lesson_for("app", "b200").commands)
    assert "--tensor-parallel-size 2" in "\n".join(lesson_for("app", "dual").commands)


def test_inventory_requires_quiz() -> None:
    miss = validate_answers(
        "inventory",
        {
            "s1": "a",
            "s2": "b",
            "s3": "c",
            "s4": "d",
            "os_matched": "yes",
            "quiz": "spark-02",
        },
        path="local",
    )
    assert miss is not None


def test_inventory_passes() -> None:
    assert (
        validate_answers(
            "inventory",
            {
                "s1": "a",
                "s2": "b",
                "s3": "c",
                "s4": "d",
                "os_matched": "yes",
                "quiz": "spark-01",
            },
            path="local",
        )
        is None
    )


def test_unbox_requires_ups() -> None:
    miss = validate_answers(
        "unbox",
        {
            "sparks": "yes",
            "bricks": "yes",
            "switch": "CRS804-4DDQ-HRM",
            "ups": "no",
            "console": "laptop Sync",
            "quiz": "4",
        },
        path="local",
    )
    assert miss is not None


def test_unbox_passes() -> None:
    assert (
        validate_answers(
            "unbox",
            {
                "sparks": "yes",
                "bricks": "yes",
                "switch": "CRS804-4DDQ-HRM",
                "ups": "yes",
                "console": "laptop Sync",
                "quiz": "4",
            },
            path="local",
        )
        is None
    )


def test_lease_connect_defaults() -> None:
    assert validate_answers("connect", {}, path="lease") is None
    assert validate_answers("connect", {}, path="b300") is None
    assert validate_answers("connect", {}, path="b200") is None
    assert validate_answers("connect", {}, path="dual") is None


def test_lease_connect_rejects_three_hosts() -> None:
    miss = validate_answers(
        "connect",
        {"hosts": "a b c", "nas_ip": "nfs://vault"},
        path="lease",
    )
    assert miss is not None


def test_lease_connect_rejects_public_vault() -> None:
    miss = validate_answers(
        "connect",
        {
            "hosts": "a b c d",
            "nas_ip": "s3://public-bucket/diony",
        },
        path="lease",
    )
    assert miss is not None


def test_lease_nodes_pass() -> None:
    assert (
        validate_answers(
            "connect",
            {
                "hosts": "i-01 i-02 i-03 i-04",
                "nas_ip": "nfs://vault.internal",
            },
            path="lease",
        )
        is None
    )


def test_path_local_passes() -> None:
    assert (
        validate_answers(
            "path",
            {"site": "local"},
        )
        is None
    )


def test_path_lease_is_not_a_site() -> None:
    miss = validate_answers(
        "path",
        {
            "site": "lease",
            "provider": "coreweave",
            "gpu_class": "4×H100 80GB",
            "signer": "Rohn Springfield",
        },
    )
    assert miss is not None


def test_path_b300_needs_signer() -> None:
    miss = validate_answers(
        "path",
        {
            "site": "b300",
            "provider": "this rack",
            "signer": "R",
            "exception": "yes",
        },
    )
    assert miss is not None


def test_path_b200_needs_signer() -> None:
    miss = validate_answers(
        "path",
        {
            "site": "b200",
            "provider": "this rack",
            "signer": "R",
        },
    )
    assert miss is not None
    assert (
        validate_answers(
            "path",
            {
                "site": "b200",
                "provider": "this rack",
                "signer": "Rohn Springfield",
            },
        )
        is None
    )


def test_path_dual_needs_signer() -> None:
    miss = validate_answers(
        "path",
        {
            "site": "dual",
            "provider": "this rack",
            "signer": "R",
        },
    )
    assert miss is not None


def test_path_dual_passes() -> None:
    assert (
        validate_answers(
            "path",
            {
                "site": "dual",
                "provider": "this rack",
                "signer": "Rohn Springfield",
            },
        )
        is None
    )
    assert (
        validate_answers(
            "path",
            {
                "site": "b300",
                "provider": "this rack",
                "signer": "Rohn Springfield",
            },
        )
        is None
    )


def test_b300_connect_pass() -> None:
    assert (
        validate_answers(
            "connect",
            {
                "hosts": "b300-01",
                "nas_ip": "nfs://vault",
            },
            path="b300",
        )
        is None
    )


def test_b300_models_and_app_pass() -> None:
    assert validate_answers("app", {}, path="b300") is None
    assert validate_answers("app", {}, path="dual") is None
    assert validate_answers("prove", {}, path="b300") is None
    assert validate_answers("prove", {}, path="b200") is None
    assert validate_answers("prove", {}, path="dual") is None
    assert validate_answers("producer", {}, path="b300") is None
    assert validate_answers("producer", {}, path="local") is None
    assert (
        validate_answers(
            "connect",
            {"hosts": "spark-01"},
            path="dual",
        )
        is not None
    )
    assert (
        validate_answers(
            "connect",
            {"hosts": "spark-01 spark-02 spark-03", "nas_ip": "nfs://vault"},
            path="dual",
        )
        is not None
    )


def test_nfs_placeholder_does_not_mount() -> None:
    from diony_wizard.prep import parse_nfs_source, vault_mount_lines

    assert parse_nfs_source("nfs://vault") is None
    assert parse_nfs_source("nfs://10.0.8.12/diony-vault") == ("10.0.8.12", "/diony-vault")
    assert "mount -t nfs" not in "\n".join(vault_mount_lines("nfs://vault"))
    assert "10.0.8.12:/diony-vault" in "\n".join(vault_mount_lines("nfs://10.0.8.12/diony-vault"))


def test_volume_disk_bind_mounts_workspace() -> None:
    from diony_wizard.prep import vault_bind_source, vault_mount_lines

    assert vault_bind_source("/workspace") == "/workspace"
    assert vault_bind_source("volume-disk") == "/workspace"
    joined = "\n".join(vault_mount_lines("/workspace"))
    assert "mount --bind" in joined
    assert "_diony_ensure_vault /workspace /diony/vault" in joined
    assert "ln -sfn" in joined
    assert "mount -t nfs" not in joined


def test_producer_uses_the_product_address() -> None:
    from diony_wizard.prep import PRODUCER_URL, producer_href, producer_url

    assert producer_url("b300", True) == "http://producer.diony.local"
    assert producer_url("local", False) == PRODUCER_URL
    assert producer_href(True) == PRODUCER_URL
    assert producer_href(False) == PRODUCER_URL
    assert (
        producer_href(
            False,
            "dual",
            {"inventory": {"s1": "10.0.0.8", "s2": "10.0.0.9"}},
        )
        == PRODUCER_URL
    )
    assert PRODUCER_URL in "\n".join(lesson_for("app", "dual").commands)
    assert "127.0.0.1" not in lesson_for("app", "dual").why
    assert "127.0.0.1" not in lesson_for("app", "b300").why
    assert "127.0.0.1" not in lesson_for("app", "b200").why
    assert "127.0.0.1" not in "\n".join(lesson_for("app", "b300").teach)
    assert "127.0.0.1" not in "\n".join(lesson_for("app", "b200").teach)


def test_producer_line_has_no_redundant_url_confirmation() -> None:
    for path in ("local", "dual", "b300", "b200"):
        lesson = lesson_for("producer", path)
        field_ids = {field.id for field in lesson.fields}
        labels = " ".join(field.label for field in lesson.fields)
        assert "url" not in field_ids
        assert "ready" not in field_ids
        assert "This URL opens the showrunner?" not in labels
        assert validate_answers("producer", {}, path=path) is None


def test_b300_connect_teaches_the_vault() -> None:
    lesson = lesson_for("connect", "b300")
    teach = " ".join(lesson.teach).lower()
    assert "provider" in teach or "runpod" in teach
    assert "/diony/vault" in teach
    assert "/workspace" in teach
    vault = next(f for f in lesson.fields if f.id == "nas_ip")
    hint = vault.hint.lower()
    assert "runpod" in hint or "enverge" in hint or "provider" in hint
    assert vault.default == "/workspace"
    assert any("fstab" in line or "/diony/vault" in line or "/workspace" in line for line in lesson.commands)
    local = lesson_for("vault", "local")
    assert "nas admin" in " ".join(local.teach).lower()


def test_gpu_connect_teaches_runpod_map() -> None:
    for site in ("b300", "b200"):
        lesson = lesson_for("connect", site)
        teach = " ".join(lesson.teach).lower()
        joined = "\n".join(lesson.commands).lower()
        assert "runpod" in teach
        assert "word before @" in teach or "before @" in teach
        assert "/workspace" in teach
        assert "terminate" in teach
        assert lesson.diagram
        assert "volume disk" in lesson.diagram.lower() or "/workspace" in lesson.diagram.lower()
        assert "ssh user" in lesson.diagram.lower() or "root" in lesson.diagram.lower()
        headings = [row["heading"] for row in lesson.guide]
        assert headings == [
            "HTTP services",
            "SSH",
            "SSH over exposed TCP",
            "Web terminal",
            "Direct TCP ports",
            "Pod volume Mount path",
        ]
        used = [row["heading"] for row in lesson.guide if row["use"] == "use"]
        assert used == ["SSH over exposed TCP", "Pod volume Mount path"]
        skipped = [row["heading"] for row in lesson.guide if row["use"] == "skip"]
        assert "HTTP services" in skipped
        assert "Web terminal" in skipped
        tcp = next(row for row in lesson.guide if row["heading"] == "SSH over exposed TCP")
        assert "198.13.252.68" in tcp["example"]
        assert "28231" in tcp["take"]
        host = next(f for f in lesson.fields if f.id == "hosts")
        assert host.placeholder == "198.13.252.68"
        assert host.prefill is False
        port = next(f for f in lesson.fields if f.id == "ssh_port")
        assert port.placeholder == "28231"
        assert port.prefill is False
        assert "blueprint 14" in joined
        assert "ssh_port" in {f.id for f in lesson.fields}
        assert port.default == "22"
        assert "jupyter" in teach
        assert "8888" in teach
        assert "exposed tcp" in teach
        assert "28231" in teach or "28231" in joined
        assert "ssh.runpod.io" in teach
        assert "rsync" in teach or "scp" in teach
        host = next(f for f in lesson.fields if f.id == "hosts")
        ssh = next(f for f in lesson.fields if f.id == "ssh_user")
        vault = next(f for f in lesson.fields if f.id == "nas_ip")
        assert host.default == ("b200-01" if site == "b200" else "b300-01")
        assert ssh.default == "root"
        assert vault.default == "/workspace"
        app = lesson_for("app", site)
        app_teach = " ".join(app.teach)
        assert "DIONY_SSH_USER" in app_teach
        assert "HF_TOKEN" in app_teach
        assert "huggingface.co/settings/tokens" in app_teach
        token = next(f for f in app.fields if f.id == "hf_token")
        assert token.kind == "password"
        ready = lesson_for("prove", site)
        assert any(f.id == "hf_token" for f in ready.fields)
        assert "huggingface.co/settings/tokens" in " ".join(ready.teach)
        assert "does not start vLLM" in app_teach or "App does not start vLLM" in app_teach
        if site == "b200":
            assert "16384" in app_teach
            assert "1048576" in app_teach
            cmds = "\n".join(app.commands)
            assert "--max-model-len 16384" in cmds
            assert "--gpu-memory-utilization 0.95" in cmds
            assert "1048576" not in cmds
        else:
            cmds = "\n".join(app.commands)
            assert "--max-model-len 1048576" in cmds


def test_connect_ssh_user_is_the_login_name() -> None:
    for site in ("dual", "b300", "b200"):
        lesson = lesson_for("connect", site)
        teach = " ".join(lesson.teach).lower()
        assert "word before @" in teach or "before @" in teach
        assert "email" in teach
        field = next(f for f in lesson.fields if f.id == "ssh_user")
        assert "login" in field.label.lower()
        assert "before @" in field.hint.lower()
        assert "email" in field.hint.lower()
        assert "runpod" in teach
        assert "runpod" in field.hint.lower()
        if site == "dual":
            assert field.default == "spark"
        else:
            assert field.default == "root"
    ok = validate_answers(
        "connect",
        {"hosts": "spark-01 spark-02", "nas_ip": "nfs://vault", "ssh_user": "my-instance"},
        path="dual",
    )
    assert ok is None
    pasted = validate_answers(
        "connect",
        {
            "hosts": "spark-01 spark-02",
            "nas_ip": "nfs://vault",
            "ssh_user": "ssh my-instance@relay.enverge.ai",
        },
        path="dual",
    )
    assert pasted is not None
    assert "before @" in pasted.lower()
    email = validate_answers(
        "connect",
        {"hosts": "b300-01", "nas_ip": "nfs://vault", "ssh_user": "me@enverge.ai"},
        path="b300",
    )
    assert email is not None


def test_b300_models_use_the_site_pack() -> None:
    from diony_wizard.prep import expand_site_models, model_pack

    assert validate_answers("app", {}, path="b300") is None
    pack = model_pack("b300")
    assert pack is not None
    assert pack["id"] == "b300-v1"
    assert "Wan-AI/Wan2.2-T2V-A14B" in pack["camera_ids"]
    assert "TI2V-5B" not in ",".join(pack["camera_ids"])
    filled = expand_site_models("b300", {})
    assert filled["weights"]["pack"] == "b300-v1"
    assert filled["brain_smoke"]["recipe"] == "b300-v1 TP=1"
    local = expand_site_models("local", {})
    assert local["weights"]["pack"] == "spark-4x-v1"
    assert local["brain_smoke"]["recipe"] == "spark-4x-v1 TP=4"
    dual = expand_site_models("dual", {})
    assert dual["weights"]["pack"] == "spark-2x-v1"
    assert dual["brain_smoke"]["recipe"] == "spark-2x-v1 TP=2"


def test_b200_models_use_the_site_pack() -> None:
    from diony_wizard.prep import expand_lease_connect, expand_site_models, model_pack, pack_pull_commands

    assert validate_answers("app", {}, path="b200") is None
    pack = model_pack("b200")
    assert pack is not None
    assert pack["id"] == "b200-v1"
    assert pack["recipe"] == "b200-v1 TP=1"
    cmds = "\n".join(pack_pull_commands("b200"))
    assert "--max-model-len 16384" in cmds
    assert "1048576" not in cmds
    filled = expand_site_models("b200", {})
    assert filled["weights"]["pack"] == "b200-v1"
    assert filled["brain_smoke"]["recipe"] == "b200-v1 TP=1"
    connect = expand_lease_connect(
        "b200",
        "1×B200",
        {"hosts": "b200-01", "nas_ip": "/workspace"},
    )
    assert connect["inventory"]["s1"] == "b200-01"
    assert "weights" not in connect
    assert validate_answers(
        "connect",
        {"hosts": "b200-01", "nas_ip": "/workspace", "ssh_user": "root"},
        path="b200",
    ) is None
    two_hosts = validate_answers(
        "connect",
        {"hosts": "b200-01 extra", "nas_ip": "/workspace", "ssh_user": "root"},
        path="b200",
    )
    assert two_hosts is not None


def test_gpu_connect_ssh_port_optional(monkeypatch) -> None:
    from diony_wizard.prep import expand_lease_connect

    monkeypatch.delenv("DIONY_SSH_PORT", raising=False)
    monkeypatch.delenv("DIONY_SSH_USER", raising=False)

    for site, host, gpu in (("b200", "b200-01", "1×B200"), ("b300", "b300-01", "1×B300")):
        assert (
            validate_answers(
                "connect",
                {"hosts": host, "nas_ip": "/workspace", "ssh_user": "root"},
                path=site,
            )
            is None
        )
        filled = expand_lease_connect(
            site,
            gpu,
            {"hosts": host, "nas_ip": "/workspace", "ssh_user": "root"},
        )
        assert filled["connect"]["ssh_port"] == "22"
        assert filled["connect"]["ssh_user"] == "root"
        tcp = validate_answers(
            "connect",
            {
                "hosts": "198.13.252.68",
                "nas_ip": "/workspace",
                "ssh_user": "root",
                "ssh_port": "28231",
            },
            path=site,
        )
        assert tcp is None
        tcp_fill = expand_lease_connect(
            site,
            gpu,
            {
                "hosts": "198.13.252.68",
                "nas_ip": "/workspace",
                "ssh_user": "root",
                "ssh_port": "28231",
            },
        )
        assert tcp_fill["connect"]["ssh_port"] == "28231"
        assert tcp_fill["inventory"]["s1"] == "198.13.252.68"
        pasted = validate_answers(
            "connect",
            {
                "hosts": host,
                "nas_ip": "/workspace",
                "ssh_user": "ssh root@198.13.252.68 -p 28231",
            },
            path=site,
        )
        assert pasted is not None
        assert "before @" in pasted.lower()
        proxy_host = validate_answers(
            "connect",
            {
                "hosts": "ssh.runpod.io",
                "nas_ip": "/workspace",
                "ssh_user": "root",
                "ssh_port": "22",
            },
            path=site,
        )
        assert proxy_host is not None
        assert "exposed tcp" in proxy_host.lower() or "proxy" in proxy_host.lower()
        jupyter = validate_answers(
            "connect",
            {
                "hosts": host,
                "nas_ip": "/workspace",
                "ssh_user": "root",
                "ssh_port": "8888",
            },
            path=site,
        )
        assert jupyter is not None
        assert "jupyter" in jupyter.lower()
    dual_port = {f.id for f in lesson_for("connect", "dual").fields}
    assert "ssh_port" not in dual_port


def test_b300_connect_does_not_prefill_models() -> None:
    from diony_wizard.prep import expand_lease_connect

    filled = expand_lease_connect(
        "b300",
        "1×B300",
        {"hosts": "b300-01", "nas_ip": "nfs://vault"},
    )
    assert filled["inventory"]["s1"] == "b300-01"
    assert "weights" not in filled
    assert "brain_smoke" not in filled
    assert "producer" not in filled
    dual = expand_lease_connect(
        "dual",
        "2×GB10",
        {"hosts": "spark-01 spark-02", "nas_ip": "nfs://vault"},
    )
    assert dual["inventory"]["s1"] == "spark-01"
    assert dual["inventory"]["s2"] == "spark-02"
    assert "weights" not in dual
    lease = expand_lease_connect(
        "lease",
        "4×H100 80GB",
        {"hosts": "a b c d", "nas_ip": "nfs://vault"},
    )
    assert "weights" in lease
    assert "producer" in lease


def test_path_needs_a_site() -> None:
    miss = validate_answers("path", {})
    assert miss is not None


def test_public_lesson_hides_accept() -> None:
    public = CURRICULUM["switch"].as_public()
    assert "accept" not in str(public)
    assert public["commands"]
    assert public["diagram"] or public["handoff"] or public["commands"]
    lease = lesson_for("connect", "lease").as_public()
    assert "accept" not in str(lease)
    assert lease["fields"][0]["id"] == "hosts"
    assert any("b300" in (f.get("when") or "") for f in lesson_for("path").as_public()["fields"])
    site = next(f for f in lesson_for("path").as_public()["fields"] if f["id"] == "site")
    assert "local" in site["option_help"]
    assert "b300" in site["option_help"]
    assert "lease" not in site["option_help"]
    assert site["options"] == ["local", "dual", "b300", "b200"]
    assert "dual" in site["option_help"]
    assert "b200" in site["option_help"]
    assert "not b300" in site["option_help"]["b200"].lower() or "180" in site["option_help"]["b200"]


def test_site_nodes_prefers_connect_hosts() -> None:
    from diony_wizard.prep import site_nodes

    nodes = site_nodes(
        "b200",
        {"connect": {"hosts": "10.9.8.7"}, "inventory": {"s1": "198.13.252.68"}},
    )
    assert nodes == ["10.9.8.7"]
    pair = site_nodes(
        "dual",
        {
            "connect": {"hosts": "10.0.0.1\n10.0.0.2"},
            "inventory": {"s1": "old-a", "s2": "old-b"},
        },
    )
    assert pair == ["10.0.0.1", "10.0.0.2"]
