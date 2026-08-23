from __future__ import annotations

import json


def test_performance_journal_survives_restart_and_reports_percentiles(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_producer.performance import PerformanceJournal

    journal = PerformanceJournal()
    for seconds in (10.0, 12.0, 14.0, 16.0):
        journal.record(
            stage="wan_camera",
            project_id="project-one",
            take_id=f"take-{seconds}",
            duration_seconds=seconds,
            outcome="succeeded",
            peak_vram_mib=106_558,
        )
    journal.record(
        stage="wan_camera",
        project_id="project-one",
        take_id="take-retry",
        duration_seconds=2.0,
        outcome="failed",
        retry=True,
    )

    restored = PerformanceJournal().snapshot()

    camera = restored["wan_camera"]
    assert camera["completed"] == 4
    assert camera["failed"] == 1
    assert camera["retries"] == 1
    assert camera["latency_seconds_p50"] == 13.0
    assert camera["latency_seconds_p95"] == 16.0
    assert camera["peak_vram_mib"] == 106_558
    rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert all(row["schema_version"] == 1 for row in rows)


def test_performance_prometheus_includes_legacy_camera_and_finish_metrics(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_producer.performance import PerformanceJournal, prometheus_lines

    journal = PerformanceJournal()
    journal.record(
        stage="wan_camera",
        project_id="project-one",
        take_id="take-one",
        duration_seconds=900,
        outcome="succeeded",
    )
    journal.record(
        stage="finish_1080",
        project_id="project-one",
        take_id="take-one",
        duration_seconds=110,
        outcome="succeeded",
    )

    text = "\n".join(prometheus_lines(journal.snapshot()))

    assert "diony_wan_camera_completed 1" in text
    assert "diony_wan_camera_latency_seconds_p50 900" in text
    assert "diony_finish_1080_completed 1" in text


def test_default_journal_follows_the_configured_vault_after_startup(
    tmp_path, monkeypatch
) -> None:
    from diony_producer.performance import PerformanceJournal

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "early-vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "early-vault"))
    journal = PerformanceJournal()

    configured = tmp_path / "configured-vault"
    monkeypatch.setenv("DIONY_VAULT", str(configured))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(configured))
    journal.record(
        stage="wan_camera",
        project_id="project-live",
        take_id="take-live",
        duration_seconds=915,
        outcome="succeeded",
    )

    assert journal.path == configured / ".cluster" / "performance.jsonl"
    assert PerformanceJournal().snapshot()["wan_camera"]["completed"] == 1
