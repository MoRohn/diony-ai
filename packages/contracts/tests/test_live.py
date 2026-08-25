from diony_contracts.live import finish_ready, inference_live, wan_frame_num, wizard_live


def test_dry_run_without_models(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    monkeypatch.delenv("DIONY_LIVE", raising=False)
    assert inference_live() is False
    assert wizard_live() is False


def test_live_flag(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    assert inference_live() is True


def test_wizard_live_flag(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_WIZARD_LIVE", "1")
    assert wizard_live() is True


def test_finish_fake(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    assert finish_ready() is True


def test_frame_num_is_4n_plus_1() -> None:
    n = wan_frame_num(120, 23.976)
    assert n % 4 == 1
    assert 17 <= n <= 81


def test_frame_num_never_delivers_fewer_frames_than_booked_within_the_lane() -> None:
    """Trim-only conform: the camera must cover every booked frame up to the 81 cap."""
    for booked in range(17, 82):
        delivered = wan_frame_num(booked, 23.976)
        assert delivered % 4 == 1
        assert delivered >= booked
        assert delivered <= 81
    # Over-booked legacy shots still cap at the lane ceiling.
    assert wan_frame_num(120, 23.976) == 81
