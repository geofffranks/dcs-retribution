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

# Five columns and five rows at 12 m spacing, starting 45.72 m behind the garage:
# hypot(45.72 + 4 * 12, 4 * 12) rounds up to 106 m.
_MOTORPOOL_ENGAGEMENT_RADIUS_M = 106


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
        radius = (
            _MOTORPOOL_ENGAGEMENT_RADIUS_M
            if isinstance(target, MotorpoolGroundObject)
            else int(nautical_miles(configured_range).meters)
        )
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
