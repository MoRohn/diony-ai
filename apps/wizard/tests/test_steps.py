from diony_wizard.curriculum import CURRICULUM, lesson_for, lessons_for, validate_answers
from diony_wizard.lessons_b300 import B300_LESSONS
from diony_wizard.lessons_dual import DUAL_LESSONS
from diony_wizard.lessons_lease import LEASE_LESSONS
from diony_wizard.steps import B300_RAIL, DUAL_RAIL, LEASE_RAIL, LOCAL_RAIL, initial_steps, rail_ids


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
        "producer",
    ]
    assert B300_RAIL == DUAL_RAIL
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
    assert set(LEASE_LESSONS) == set(LEASE_RAIL) - {"path"}
    assert set(B300_LESSONS) == set(B300_RAIL) - {"path"}
    assert set(DUAL_LESSONS) == set(DUAL_RAIL) - {"path"}
    for lesson in CURRICULUM.values():
        assert lesson.why
        assert lesson.teach
        assert lesson.fail_if
        assert lesson.fields


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
    assert lesson_for("app", "dual").step_id == "app"
    assert "huggingface-cli" in "\n".join(lesson_for("app", "b300").commands)
    assert any("vllm serve" in c for c in lesson_for("app", "b300").commands)
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
    assert validate_answers("producer", {"ready": "yes"}, path="b300") is None
    assert validate_answers("producer", {}, path="b300") is not None
    assert validate_answers("producer", {"ready": "yes"}, path="local") is None
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


def test_producer_uses_the_product_address() -> None:
    from diony_wizard.prep import PRODUCER_URL, producer_href, producer_url

    assert producer_url("b300", True) == "http://producer.diony.local"
    assert producer_url("local", False) == PRODUCER_URL
    assert producer_href(True) == "http://127.0.0.1:8788"
    assert producer_href(False) == PRODUCER_URL
    assert PRODUCER_URL in "\n".join(lesson_for("app", "dual").commands)
    assert "127.0.0.1" not in lesson_for("app", "dual").why
    assert "127.0.0.1" not in lesson_for("app", "b300").why
    assert "127.0.0.1" not in "\n".join(lesson_for("app", "b300").teach)


def test_b300_connect_teaches_the_vault() -> None:
    lesson = lesson_for("connect", "b300")
    teach = " ".join(lesson.teach).lower()
    assert "provider" in teach
    assert "/diony/vault" in teach
    assert "nfs" in teach
    vault = next(f for f in lesson.fields if f.id == "nas_ip")
    assert "provider" in vault.hint.lower()
    assert any("fstab" in line or "/diony/vault" in line for line in lesson.commands)
    local = lesson_for("vault", "local")
    assert "nas admin" in " ".join(local.teach).lower()


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
    assert site["options"] == ["local", "dual", "b300"]
    assert "dual" in site["option_help"]
