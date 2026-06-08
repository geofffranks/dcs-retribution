from datetime import datetime

from game.missiongenerator.aircraft.waypoints.seadloiter import _loiter_stop_seconds


def test_loiter_stop_seconds_uses_window_after_tot() -> None:
    now = datetime(2020, 1, 1, 0, 0, 0)
    tot = datetime(2020, 1, 1, 0, 10, 0)  # 600 s after now
    # 600 s to reach the anchor + a 1200 s on-station window.
    assert _loiter_stop_seconds(tot, now, 1200) == 1800


def test_loiter_stop_seconds_floors_zero_window_when_tot_unknown() -> None:
    # With no TOT and a 0 s window the bare value would be 0, which DCS treats as
    # "fire immediately" and kills the orbit at mission start; it must floor to 1.
    assert _loiter_stop_seconds(None, datetime(2020, 1, 1), 0) == 1


def test_loiter_stop_seconds_floors_when_tot_already_past() -> None:
    now = datetime(2020, 1, 1, 0, 10, 0)
    tot = datetime(2020, 1, 1, 0, 0, 0)  # 600 s in the past
    # A past TOT (-600 s) plus a 0 window is negative; it must floor to 1.
    assert _loiter_stop_seconds(tot, now, 0) == 1
