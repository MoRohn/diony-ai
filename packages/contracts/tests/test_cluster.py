from diony_contracts.cluster import (
    clear_local_locks,
    mode_lock_path,
    refuse_concurrent,
    site_from_lock,
    site_lock_path,
    site_status,
    write_mode_lock,
    write_site_lock,
)
from diony_contracts.enums import ComputePath
from diony_contracts.models import ClusterSite


def test_unsigned_lock_is_local(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    assert read_none() is None
    site = site_from_lock()
    assert site.path == ComputePath.LOCAL
    status = site_status()
    assert status["signed"] is False
    assert status["sequential"] is False
    assert "2–5 days" in status["clock"]


def read_none():
    from diony_contracts.cluster import read_site_lock

    return read_site_lock()


def test_dual_lock_is_sequential(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    write_site_lock(
        {
            "sku": "spark-2x-v1",
            "compute_path": "dual",
            "compute": {
                "path": "dual",
                "profile": "spark-2x-v1",
                "gpu_class": "2×GB10",
                "exception_by": "Rohn Springfield",
            },
            "nodes": ["spark-01", "spark-02"],
            "vault": "nfs://vault",
            "recipe": "spark-2x-v1 TP=2",
            "sequential": True,
            "producer_url": "http://producer.diony.local",
            "exception_by": "Rohn Springfield",
        }
    )
    site = site_from_lock()
    assert site.path == ComputePath.DUAL
    assert site.profile == "spark-2x-v1"
    status = site_status()
    assert status["label"] == "Leased Dual-Spark Cluster"
    assert status["sequential"] is True
    assert "2–5 day" in status["clock"]
    assert "Do not quote" in status["clock"]
    write_mode_lock("BRAIN", "brain")
    held = refuse_concurrent("FARM", "wan")
    assert held is not None
    assert "sequential" in held.lower()
    assert refuse_concurrent("BRAIN", "brain") is None


def test_b200_lock_is_sequential(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "1×B200",
                "exception_by": "Rohn Springfield",
            },
            "nodes": ["b200-01"],
            "vault": "nfs://vault",
            "recipe": "b200-v1 TP=1",
            "sequential": True,
            "producer_url": "http://producer.diony.local",
            "exception_by": "Rohn Springfield",
        }
    )
    site = site_from_lock()
    assert site.path == ComputePath.B200
    assert site.profile == "b200-v1"
    status = site_status()
    assert status["label"] == "Leased B200"
    assert status["sequential"] is True
    assert status["nodes"] == ["b200-01"]
    assert "Do not quote" in status["clock"]
    write_mode_lock("BRAIN", "brain")
    held = refuse_concurrent("FARM", "wan")
    assert held is not None
    assert "sequential" in held.lower()


def test_cluster_site_dual_requires_name() -> None:
    try:
        ClusterSite(path=ComputePath.DUAL, profile="spark-2x-v1")
        raise AssertionError("expected failure")
    except Exception:
        pass
    ClusterSite(
        path=ComputePath.DUAL,
        profile="spark-2x-v1",
        exception_by="Rohn Springfield",
    )


def test_clear_local_locks(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "B200",
                "exception_by": "Rohn Springfield",
            },
        }
    )
    write_mode_lock("SPLIT", "brain", reason="test")
    assert site_lock_path().is_file()
    assert mode_lock_path().is_file()
    clear_local_locks()
    assert not site_lock_path().is_file()
    assert not mode_lock_path().is_file()
