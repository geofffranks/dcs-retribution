from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from game.dcs.groundunittype import GroundUnitType
from game.ground_forces.ai_ground_planner import reserve_armor_for
from game.theater.controlpoint import ControlPoint, ControlPointType
from game.theater.theatergroup import TheaterGroup, TheaterUnit
from game.theater.theatergroundobject import MotorpoolGroundObject
from game.point_with_heading import PointWithHeading

if TYPE_CHECKING:
    from game.game import Game
    from game.theater.controlpoint import ControlPoint

# Parked vehicles laid in a grid so DCS does not reject overlapping spawns.
_SPACING_M = 12.0
_COLUMNS = 5
# Keep the Garage_A building at the authored marker; start vehicles clear of it
# behind the building. 150 ft is the authoring-friendly value.
_GRID_OFFSET_M = 45.72


def _select_capped(
    reserve: dict[GroundUnitType, int], cap: int
) -> dict[GroundUnitType, int]:
    """Proportionally reduce ``reserve`` so its counts sum to at most ``cap``,
    using the largest-remainder method (keeps a representative spread of types).
    Ties are broken by ``variant_id`` so allocation is deterministic across
    turns, which the motorpool projection reconciliation relies on. Returns a
    copy of ``reserve`` unchanged when it already fits under the cap."""
    total = sum(reserve.values())
    if total <= cap:
        return {ut: n for ut, n in reserve.items() if n > 0}
    exact = {ut: count * cap / total for ut, count in reserve.items()}
    floors = {ut: int(v) for ut, v in exact.items()}
    remaining = cap - sum(floors.values())
    if remaining > 0:
        # Largest fractional remainders first, then original count, then
        # variant_id for a stable, deterministic tie-break.
        by_frac = sorted(
            reserve,
            key=lambda ut: (-(exact[ut] - floors[ut]), -reserve[ut], ut.variant_id),
        )
        for ut in by_frac[:remaining]:
            floors[ut] += 1
    return {ut: n for ut, n in floors.items() if n > 0}


ProjectionKey = tuple[UUID, str, int]


@dataclass(frozen=True)
class _DesiredUnit:
    key: ProjectionKey
    tgo: MotorpoolGroundObject
    unit_type: GroundUnitType
    grid_index: int


class MotorpoolPopulator:
    """Reconcile the persisted motorpool cache with each CP's current reserve."""

    def __init__(self, game: Game) -> None:
        self.game = game

    def populate(self) -> None:
        self.populate_control_points(self.game.theater.controlpoints)

    def _rehome_motorpools(self) -> None:
        """Attach each motorpool to its nearest eligible land control point.

        Motorpool ownership is persisted as which control point's
        ``connected_objectives`` the TGO lives in, so an existing save can park a
        motorpool under the wrong (possibly enemy) base and nothing re-decides it.
        FARPs/FOBs are themselves land control points with their own separate
        ground inventory, so a motorpool parked at a FARP belongs to that FARP --
        not to a far-away base whose influence zone happens to contain the point.

        Because this runs on every ``populate`` (save load via the migrator, turn
        start, and mission generation), an existing save converts on load, and the
        pool changes hands when the nearest eligible control point is captured.
        Naval and off-map control points never own motorpools.
        """
        control_points = self.game.theater.controlpoints
        # Only control points that expose a concrete position and control-point
        # type can be motorpool owners. Real control points always do; the guard
        # also lets the pass run over lightly-constructed test doubles without
        # crashing.
        eligible: list[ControlPoint] = []
        for cp in control_points:
            if getattr(cp, "cptype", None) in (
                ControlPointType.AIRCRAFT_CARRIER_GROUP,
                ControlPointType.LHA_GROUP,
                ControlPointType.OFF_MAP,
            ):
                continue
            if getattr(cp, "position", None) is None:
                continue
            eligible.append(cp)
        if not eligible:
            return
        for owner in control_points:
            objectives = getattr(owner, "connected_objectives", None)
            if objectives is None:
                continue
            for tgo in list(objectives):
                if not isinstance(tgo, MotorpoolGroundObject):
                    continue
                closest = min(
                    eligible,
                    key=lambda cp: cp.position.distance_to_point(tgo.position),
                )
                if closest is owner:
                    continue
                owner.connected_objectives.remove(tgo)
                closest.connected_objectives.append(tgo)
                tgo.control_point = closest

    def populate_control_points(self, control_points: Iterable[ControlPoint]) -> None:
        cap: int = self.game.settings.motorpool_spawn_cap
        enabled: bool = self.game.settings.motorpool_enabled
        for cp in control_points:
            motorpools = [
                tgo
                for tgo in cp.ground_objects
                if isinstance(tgo, MotorpoolGroundObject)
            ]
            if not motorpools:
                continue
            self._backfill_projection_keys(motorpools)
            desired = self._desired_projection(motorpools, cap) if enabled else []
            self._reconcile(motorpools, desired)

    def _desired_projection(
        self, motorpools: list[MotorpoolGroundObject], cap: int
    ) -> list[_DesiredUnit]:
        if cap <= 0:
            return []
        reserve = reserve_armor_for(motorpools[0].control_point)
        selected = _select_capped(reserve, cap)
        expanded = [
            unit_type
            for unit_type in sorted(selected, key=lambda item: item.variant_id)
            for _ in range(selected[unit_type])
        ]
        ordinals: defaultdict[tuple[UUID, str], int] = defaultdict(int)
        grid_indices: defaultdict[UUID, int] = defaultdict(int)
        desired: list[_DesiredUnit] = []
        for slot, unit_type in enumerate(expanded):
            tgo = motorpools[slot % len(motorpools)]
            slice_key = (tgo.id, unit_type.variant_id)
            ordinal = ordinals[slice_key]
            desired.append(
                _DesiredUnit(
                    (tgo.id, unit_type.variant_id, ordinal),
                    tgo,
                    unit_type,
                    grid_indices[tgo.id],
                )
            )
            ordinals[slice_key] += 1
            grid_indices[tgo.id] += 1
        return desired

    @staticmethod
    def _backfill_projection_keys(
        motorpools: list[MotorpoolGroundObject],
    ) -> None:
        for tgo in motorpools:
            if not hasattr(tgo, "motorpool_projection_keys"):
                tgo.motorpool_projection_keys = {}
            retained: dict[int, ProjectionKey] = {}
            used_ordinals: defaultdict[str, set[int]] = defaultdict(set)
            missing: list[tuple[TheaterUnit, GroundUnitType]] = []
            for group in tgo.groups:
                unit_type = tgo.motorpool_unit_types.get(group.id)
                if unit_type is None:
                    continue
                for unit in group.units:
                    existing_key = tgo.motorpool_projection_keys.get(unit.id)
                    ordinal = existing_key[2] if existing_key is not None else -1
                    if (
                        existing_key is not None
                        and existing_key[:2] == (tgo.id, unit_type.variant_id)
                        and ordinal >= 0
                        and ordinal not in used_ordinals[unit_type.variant_id]
                    ):
                        retained[unit.id] = existing_key
                        used_ordinals[unit_type.variant_id].add(ordinal)
                    else:
                        missing.append((unit, unit_type))

            tgo.motorpool_projection_keys = retained
            for unit, unit_type in missing:
                ordinal = 0
                while ordinal in used_ordinals[unit_type.variant_id]:
                    ordinal += 1
                tgo.motorpool_projection_keys[unit.id] = (
                    tgo.id,
                    unit_type.variant_id,
                    ordinal,
                )
                used_ordinals[unit_type.variant_id].add(ordinal)

    def _reconcile(
        self,
        motorpools: list[MotorpoolGroundObject],
        desired: list[_DesiredUnit],
    ) -> None:
        desired_by_tgo: defaultdict[UUID, list[_DesiredUnit]] = defaultdict(list)
        for entry in desired:
            desired_by_tgo[entry.tgo.id].append(entry)
        for tgo in motorpools:
            self._reconcile_tgo(tgo, desired_by_tgo[tgo.id])

    def _reconcile_tgo(
        self, tgo: MotorpoolGroundObject, desired: list[_DesiredUnit]
    ) -> None:
        current_by_key: dict[ProjectionKey, tuple[TheaterGroup, TheaterUnit]] = {}
        for group in tgo.groups:
            for unit in group.units:
                key = tgo.motorpool_projection_keys.get(unit.id)
                if key is not None:
                    current_by_key[key] = (group, unit)

        desired_keys = {entry.key for entry in desired}
        current_keys = set(current_by_key)
        if desired_keys == current_keys:
            return

        desired_by_type: defaultdict[str, list[_DesiredUnit]] = defaultdict(list)
        for entry in desired:
            desired_by_type[entry.unit_type.variant_id].append(entry)
        groups_by_type = self._groups_by_type(tgo)

        occupied_positions = {
            (current[1].position.x, current[1].position.y)
            for entry in desired
            if (current := current_by_key.get(entry.key)) is not None
        }
        next_groups: list[TheaterGroup] = []
        next_unit_types: dict[int, GroundUnitType] = {}
        next_projection_keys: dict[int, ProjectionKey] = {}
        for variant_id, entries in desired_by_type.items():
            unit_type = entries[0].unit_type
            target_group = groups_by_type.get(variant_id)
            units: list[TheaterUnit] = []
            for entry in entries:
                current = current_by_key.get(entry.key)
                if current is not None:
                    unit = current[1]
                else:
                    grid_index = 0
                    position = self._grid_position(tgo, grid_index)
                    while (position.x, position.y) in occupied_positions:
                        grid_index += 1
                        position = self._grid_position(tgo, grid_index)
                    unit = self._make_unit(tgo, entry.unit_type, position)
                    occupied_positions.add((position.x, position.y))
                units.append(unit)
                next_projection_keys[unit.id] = entry.key
            if target_group is None:
                target_group = TheaterGroup.from_template(
                    self.game.next_group_id(),
                    f"{tgo.name} ({unit_type})",
                    units,
                    tgo,
                )
            else:
                target_group.units = units
            next_groups.append(target_group)
            next_unit_types[target_group.id] = unit_type

        tgo.groups = next_groups
        tgo.motorpool_unit_types = next_unit_types
        tgo.motorpool_projection_keys = next_projection_keys

    @staticmethod
    def _groups_by_type(
        tgo: MotorpoolGroundObject,
    ) -> dict[str, TheaterGroup]:
        groups: dict[str, TheaterGroup] = {}
        for group in tgo.groups:
            unit_type = tgo.motorpool_unit_types.get(group.id)
            if unit_type is not None:
                groups[unit_type.variant_id] = group
        return groups

    @staticmethod
    def _grid_position(tgo: MotorpoolGroundObject, index: int) -> PointWithHeading:
        origin = tgo.position
        # Park behind the garage: the first row is offset opposite its heading and
        # each additional row continues farther in that direction. Within a row,
        # vehicles are spaced along the garage's right-hand side.
        behind = tgo.heading.opposite.degrees
        lateral = tgo.heading.right.degrees
        position = origin.point_from_heading(
            behind, _GRID_OFFSET_M + (index // _COLUMNS) * _SPACING_M
        ).point_from_heading(lateral, (index % _COLUMNS) * _SPACING_M)
        return PointWithHeading.from_point(position, tgo.heading)

    def _make_unit(
        self,
        tgo: MotorpoolGroundObject,
        unit_type: GroundUnitType,
        position: PointWithHeading,
    ) -> TheaterUnit:
        return TheaterUnit(
            self.game.next_unit_id(),
            str(unit_type),
            unit_type.dcs_unit_type,
            position,
            tgo,
        )
