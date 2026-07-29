import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from dcs.mapping import Point
from game.missiongenerator.carrierstandoff import (
    CarrierStandoffFinding,
    carrier_standoff_preflight,
    format_standoff_warning,
    log_carrier_standoff_warning,
)


def _carrier(name: str, x: float) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        position=cast(Any, Point(x, 0, cast(Any, None))),
        is_carrier=True,
        is_lha=False,
    )


def _lha(name: str, x: float) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        position=cast(Any, Point(x, 0, cast(Any, None))),
        is_carrier=False,
        is_lha=True,
    )


def _theater(*controlpoints: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        controlpoints=list(controlpoints),
        landmap=SimpleNamespace(
            distance_to_land=lambda point: abs(point.x),
        ),
    )


def test_preflight_reports_all_unsafe_carriers_with_measured_nm() -> None:
    unsafe = _carrier("Carrier Alpha", 1000)
    safe = _carrier("Carrier Bravo", 200000)
    game = SimpleNamespace(
        settings=SimpleNamespace(carrier_min_standoff_distance=60),
        theater=_theater(unsafe, safe),
    )

    result = carrier_standoff_preflight(cast(Any, game))

    assert result == [
        CarrierStandoffFinding(
            display_name="Carrier Alpha",
            shore_distance_nm=1000 / 1852,
        )
    ]


def test_preflight_skips_disabled_and_unmeasurable_geometry() -> None:
    carrier = _carrier("Carrier Alpha", 0)
    for setting, landmap in ((0, _theater(carrier).landmap), (60, None)):
        game = SimpleNamespace(
            settings=SimpleNamespace(carrier_min_standoff_distance=setting),
            theater=SimpleNamespace(controlpoints=[carrier], landmap=landmap),
        )
        assert carrier_standoff_preflight(cast(Any, game)) == []


def test_preflight_does_not_mutate_campaign_state() -> None:
    carrier = _carrier("Carrier Alpha", 1000)
    target = cast(Any, Point(2000, 0, cast(Any, None)))
    carrier.target_position = target
    game = SimpleNamespace(
        settings=SimpleNamespace(carrier_min_standoff_distance=60),
        theater=_theater(carrier),
    )

    carrier_standoff_preflight(cast(Any, game))

    assert carrier.position.x == 1000
    assert carrier.target_position is target
    assert game.settings.carrier_min_standoff_distance == 60


def test_preflight_ignores_non_carrier_control_points() -> None:
    non_carrier = SimpleNamespace(
        name="Destroyer",
        position=cast(Any, Point(0, 0, cast(Any, None))),
        is_carrier=False,
        is_lha=False,
    )
    game = SimpleNamespace(
        settings=SimpleNamespace(carrier_min_standoff_distance=60),
        theater=_theater(non_carrier),
    )

    assert carrier_standoff_preflight(cast(Any, game)) == []


def test_preflight_reports_unsafe_lha() -> None:
    unsafe_lha = _lha("LHA Alpha", 1000)
    game = SimpleNamespace(
        settings=SimpleNamespace(carrier_min_standoff_distance=60),
        theater=_theater(unsafe_lha),
    )

    result = carrier_standoff_preflight(cast(Any, game))

    assert result == [
        CarrierStandoffFinding(
            display_name="LHA Alpha",
            shore_distance_nm=1000 / 1852,
        )
    ]


def test_format_standoff_warning_lists_every_affected_name_and_distance() -> None:
    findings = [
        CarrierStandoffFinding(display_name="Carrier Alpha", shore_distance_nm=12.34),
        CarrierStandoffFinding(display_name="LHA Bravo", shore_distance_nm=5.0),
    ]

    message = format_standoff_warning(findings)

    assert "Carrier Alpha" in message
    assert "12.3" in message
    assert "LHA Bravo" in message
    assert "5.0" in message


def test_log_carrier_standoff_warning_logs_structured_warning_when_findings_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    findings = [
        CarrierStandoffFinding(display_name="Carrier Alpha", shore_distance_nm=12.34)
    ]

    with caplog.at_level(logging.WARNING):
        log_carrier_standoff_warning(findings)

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "Carrier Alpha" in caplog.records[0].message
    assert "12.3" in caplog.records[0].message


def test_log_carrier_standoff_warning_is_silent_with_no_findings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        log_carrier_standoff_warning([])

    assert caplog.records == []
