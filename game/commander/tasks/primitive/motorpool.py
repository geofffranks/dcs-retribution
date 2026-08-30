from __future__ import annotations

from dataclasses import dataclass

from game.ato.flighttype import FlightType
from game.commander.theaterstate import TheaterState
from game.commander.tasks.packageplanningtask import PackagePlanningTask
from game.theater.theatergroundobject import MotorpoolGroundObject


@dataclass
class PlanMotorpoolAttack(PackagePlanningTask[MotorpoolGroundObject]):
    """Plans an armed recon package (with escorts) against an enemy motorpool
    depot, destroying parked reserve armor so the owner must repurchase it.

    Armed recon needs no motorpool-specific flight planning: the builder plans
    any MissionTarget via the generic target-area flyover waypoint, and the
    ingress's EngageTargetsInZone (all ground units around the depot) makes
    the AI attack the parked vehicles. Kill attribution is by unit regardless
    of shooter. Any CAS-capable squadron can fly it; when none are available
    no package forms and the motorpool goes unattacked this turn.

    Motorpool groups are a reconciled persisted cache (see MotorpoolPopulator).
    The non-empty gate uses this location's rendered ``alive_unit_count``.
    """

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
        self.propose_flight(FlightType.ARMED_RECON, self.get_flight_size())
        self.propose_common_escorts()

    def _rendered_unit_count(self) -> int:
        """How many vehicles this motorpool has rendered at its location (0 when
        nothing is parked there, so the planner proposes no attack flight).

        Uses the per-location projection (``tgo.alive_unit_count``) rather than
        the control-point-wide reserve total: a CP with multiple motorpools
        spreads its reserve across them, and a flight targets one motorpool.
        """
        return self.target.alive_unit_count
