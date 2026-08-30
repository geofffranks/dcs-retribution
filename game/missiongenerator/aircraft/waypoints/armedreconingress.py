import math

from dcs.point import MovingPoint
from dcs.task import (
    OptECMUsing,
    ControlledTask,
    Targets,
    EngageTargetsInZone,
)

from game.theater.theatergroundobject import MotorpoolGroundObject
from game.utils import nautical_miles
from .pydcswaypointbuilder import PydcsWaypointBuilder


class ArmedReconIngressBuilder(PydcsWaypointBuilder):
    def add_tasks(self, waypoint: MovingPoint) -> None:
        self.register_special_ingress_points()
        # Preemptively use ECM to better avoid getting swatted.
        ecm_option = OptECMUsing(value=OptECMUsing.Values.UseIfDetectedLockByRadar)
        waypoint.tasks.append(ecm_option)

        target = self.flight.package.target
        configured_range = (
            self.flight.coalition.game.settings.armed_recon_engagement_range_distance
        )
        if isinstance(target, MotorpoolGroundObject):
            unit_positions = [
                unit.position
                for group in target.groups
                for unit in group.units
                if unit.alive
            ]
            radius = (
                math.ceil(
                    max(
                        target.position.distance_to_point(position)
                        for position in unit_positions
                    )
                )
                + 1
                if unit_positions
                else 0
            )
        else:
            radius = int(nautical_miles(configured_range).meters)
        waypoint.add_task(
            ControlledTask(
                EngageTargetsInZone(
                    position=self.flight.flight_plan.tot_waypoint.position,
                    radius=radius,
                    targets=[
                        Targets.All.GroundUnits,
                        Targets.All.Air.Helicopters,
                    ],
                )
            )
        )
