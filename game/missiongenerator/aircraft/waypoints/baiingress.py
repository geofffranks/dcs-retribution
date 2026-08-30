import logging
import math

from dcs.point import MovingPoint
from dcs.task import AttackGroup, EngageTargetsInZone, OptFormation, Targets, WeaponType

from game.theater import TheaterGroundObject
from game.theater.theatergroundobject import MotorpoolGroundObject
from game.transfers import MultiGroupTransport
from .pydcswaypointbuilder import PydcsWaypointBuilder


class BaiIngressBuilder(PydcsWaypointBuilder):
    def add_tasks(self, waypoint: MovingPoint) -> None:
        self.register_special_ingress_points()
        if not self.flight.is_helo:
            waypoint.tasks.append(OptFormation.trail_open())
        target = self.package.target
        if isinstance(target, MotorpoolGroundObject):
            unit_positions = [
                unit.position
                for group in target.groups
                for unit in group.units
                if unit.alive
            ]
            if unit_positions:
                radius = (
                    math.ceil(
                        max(
                            target.position.distance_to_point(position)
                            for position in unit_positions
                        )
                    )
                    + 1
                )
                waypoint.add_task(
                    EngageTargetsInZone(
                        position=target.position,
                        radius=radius,
                        targets=[Targets.All.GroundUnits],
                    )
                )
            return

        # TODO: Add common "UnitGroupTarget" base type.
        group_names = []
        if isinstance(target, TheaterGroundObject):
            for group in target.groups:
                group_names.append(group.group_name)
        elif isinstance(target, MultiGroupTransport):
            group_names.append(target.name)
        else:
            logging.error(
                "Unexpected target type for BAI mission: %s",
                target.__class__.__name__,
            )
            return

        for group_name in group_names:
            miz_group = self.mission.find_group(group_name)
            if miz_group is None:
                logging.error("Could not find group for BAI mission %s", group_name)
                continue

            task = AttackGroup(miz_group.id, weapon_type=WeaponType.Auto)
            waypoint.tasks.append(task)
