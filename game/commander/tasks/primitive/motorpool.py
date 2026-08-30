from __future__ import annotations

from dataclasses import dataclass

from game.ato.flighttype import FlightType
from game.commander.tasks.packageplanningtask import PackagePlanningTask
from game.commander.theaterstate import TheaterState
from game.theater.theatergroundobject import MotorpoolGroundObject


@dataclass
class PlanMotorpoolAttack(PackagePlanningTask[MotorpoolGroundObject]):
    """Plans an armed recon package against an enemy motorpool depot."""

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
        """How many vehicles this motorpool will render this turn (0 when nothing
        will spawn, so the planner proposes no attack flight)."""
        from game.missiongenerator.motorpoolpopulator import projected_motorpool_counts

        settings = self.target.control_point.coalition.game.settings
        if settings.motorpool_spawn_cap <= 0 or not settings.motorpool_enabled:
            return 0
        motorpools = [
            ground_object
            for ground_object in self.target.control_point.ground_objects
            if isinstance(ground_object, MotorpoolGroundObject)
        ]
        return projected_motorpool_counts(motorpools, settings.motorpool_spawn_cap).get(
            self.target.id, 0
        )
