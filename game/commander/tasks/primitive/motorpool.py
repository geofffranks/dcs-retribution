from __future__ import annotations

from dataclasses import dataclass

from game.ato.flighttype import FlightType
from game.commander.missionproposals import EscortType
from game.commander.tasks.packageplanningtask import PackagePlanningTask
from game.commander.theaterstate import TheaterState
from game.theater.theatergroundobject import MotorpoolGroundObject


@dataclass
class PlanMotorpoolAttack(PackagePlanningTask[MotorpoolGroundObject]):
    """Plans a strike or BAI package (with escorts) against an enemy motorpool
    depot, destroying parked reserve armor so the owner must repurchase it.

    Motorpool groups are a reconciled persisted cache (see MotorpoolPopulator).
    Flight sizing uses this location's rendered ``alive_unit_count`` so a
    multi-motorpool control point produces one appropriately sized attack per target.
    """

    #: BAI is the doctrinal primary (parked ground forces, not in contact); STRIKE
    #: is the fallback so the package can still form when no BAI-capable aircraft
    #: are available. Both match what the manual planner offers for a motorpool.
    task: FlightType

    def preconditions_met(self, state: TheaterState) -> bool:
        if self.target not in state.motorpool_targets:
            return False
        if not self._rendered_unit_count():
            return False
        if not self.target_area_preconditions_met(state):
            return False
        return super().preconditions_met(state)

    def apply_effects(self, state: TheaterState) -> None:
        state.motorpool_targets.remove(self.target)
        super().apply_effects(state)

    def propose_flights(self) -> None:
        target_count = self._rendered_unit_count()
        if self.task is FlightType.BAI:
            self.propose_flight(FlightType.BAI, min(4, (target_count // 4) + 1))
        else:
            self.propose_flight(
                FlightType.STRIKE,
                min(4, (target_count // 2) + target_count % 2),
            )
            if (
                self.target.control_point.coalition.game.settings.autoplan_tankers_for_strike
            ):
                self.propose_flight(FlightType.REFUELING, 1, EscortType.Refuel)
        self.propose_common_escorts()

    def _rendered_unit_count(self) -> int:
        """How many vehicles this motorpool has rendered at its location (0 when
        nothing is parked there, so the planner proposes no attack flight).

        Uses the per-location projection (``tgo.alive_unit_count``) rather than
        the control-point-wide reserve total: a CP with multiple motorpools
        spreads its reserve across them, and a flight targets one motorpool.
        """
        return self.target.alive_unit_count
