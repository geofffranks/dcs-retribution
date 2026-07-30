from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock

from game.missiongenerator.kneeboard_recon.atis import build_atis_block
from game.missiongenerator.kneeboard_recon.pages import AirfieldDeparturePage
from game.missiongenerator.kneeboard_recon.tests.test_atis import _stub_weather
from game.radio.radios import RadioFrequency


def test_build_atis_block_passes_through_moose_freq() -> None:
    block = build_atis_block(
        _stub_weather(),
        start_time_local=datetime.datetime(2026, 5, 21, 6, 42),
        start_time_zulu=None,
        sunrise=None,
        sunset=None,
        runway_name="13",
        runway_heading_deg=132,
        atc_freq_str="UHF 251.000",
        tacan_str="",
        moose_atis_freq_str="VHF 131.000",
    )
    assert block.moose_atis_freq == "VHF 131.000"


def test_build_atis_block_moose_freq_defaults_empty() -> None:
    block = build_atis_block(
        _stub_weather(),
        start_time_local=datetime.datetime(2026, 5, 21, 6, 42),
        start_time_zulu=None,
        sunrise=None,
        sunset=None,
        runway_name="13",
        runway_heading_deg=132,
        atc_freq_str="",
        tacan_str="",
    )
    assert block.moose_atis_freq == ""


def test_departure_page_renders_moose_atis_row_when_freq_known(
    tmp_path: Path,
    stub_flight: MagicMock,
    stub_game: MagicMock,
    stub_weather: MagicMock,
) -> None:
    page = AirfieldDeparturePage(
        flight=stub_flight,
        game=stub_game,
        weather=stub_weather,
        atis_by_name={stub_flight.departure.airfield_name: RadioFrequency(131_000_000)},
    )
    page.write(tmp_path / "departure.png")
    assert any(s.startswith("MOOSE ATIS ") for s in page.last_text_log)
    assert any("VHF 131.000" in s for s in page.last_text_log)


def test_departure_page_omits_moose_atis_row_when_no_freq(
    tmp_path: Path,
    stub_flight: MagicMock,
    stub_game: MagicMock,
    stub_weather: MagicMock,
) -> None:
    page = AirfieldDeparturePage(
        flight=stub_flight,
        game=stub_game,
        weather=stub_weather,
    )
    page.write(tmp_path / "departure.png")
    assert not any(s.startswith("MOOSE ATIS ") for s in page.last_text_log)
