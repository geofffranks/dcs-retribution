"""Pure carrier shore-standoff checks used before mission generation."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from game.utils import nautical_miles

if TYPE_CHECKING:
    from game import Game

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CarrierStandoffFinding:
    """A carrier or LHA whose current position is inside the configured standoff."""

    display_name: str
    shore_distance_nm: float


def carrier_standoff_preflight(game: "Game") -> list[CarrierStandoffFinding]:
    """Return all carriers/LHAs currently closer to shore than the configured minimum."""
    minimum_nm = game.settings.carrier_min_standoff_distance
    if minimum_nm <= 0 or game.theater.landmap is None:
        return []

    minimum_meters = nautical_miles(minimum_nm).meters
    findings: list[CarrierStandoffFinding] = []
    for control_point in game.theater.controlpoints:
        if not (control_point.is_carrier or control_point.is_lha):
            continue
        shore_distance = game.theater.landmap.distance_to_land(control_point.position)
        if shore_distance is not None and shore_distance < minimum_meters:
            findings.append(
                CarrierStandoffFinding(
                    display_name=control_point.name,
                    shore_distance_nm=shore_distance / nautical_miles(1).meters,
                )
            )
    return findings


def format_standoff_warning(findings: list[CarrierStandoffFinding]) -> str:
    """Render every affected carrier/LHA and its measured shore distance."""
    lines = [
        f"{finding.display_name}: {finding.shore_distance_nm:.1f} nm from shore"
        for finding in findings
    ]
    return (
        "The following carriers/LHAs are closer to shore than the configured "
        "minimum carrier standoff distance:\n" + "\n".join(lines)
    )


def log_carrier_standoff_warning(findings: list[CarrierStandoffFinding]) -> None:
    """Log findings for headless/server generation; do nothing when clear."""
    if findings:
        logger.warning(format_standoff_warning(findings))
