from __future__ import annotations

import logging
import random
from typing import Any, TYPE_CHECKING
from uuid import UUID

from game.ato.flighttype import FlightType
from game.debriefing import Debriefing
from game.ground_forces.combat_stance import CombatStance
from game.missiongenerator.interceptattrition import (
    fielded_qra_by_squadron,
    reconcile_intercept_losses,
)
from game.profiling import logged_duration
from game.squadrons.csarservice import CsarService
from game.squadrons.downedpilot import DownedPilot
from game.squadrons.squadron import Squadron
from game.theater import ControlPoint
from .front_line_resolution import resolve_front_line
from .gameupdateevents import GameUpdateEvents
from ..ato.airtaaskingorder import AirTaskingOrder

if TYPE_CHECKING:
    from ..game import Game


class MissionResultsProcessor:
    def __init__(self, game: Game) -> None:
        self.game = game

    def commit(self, debriefing: Debriefing, events: GameUpdateEvents) -> None:
        with logged_duration("Committing mission results"):
            # Per-turn scratch for the debriefing report, so last turn's prisoners
            # don't show up again.
            self.game.pilots_captured_this_turn.clear()
            # Resolve rescues before processing losses so a pilot rescued this
            # mission is not also reprocessed as a fresh loss.
            with logged_duration("commit_csar_results"):
                self.commit_csar_results(debriefing)
            with logged_duration("commit_air_losses"):
                self.commit_air_losses(debriefing)
            with logged_duration("commit_intercept_losses"):
                self.commit_intercept_losses(debriefing)
            with logged_duration("commit_pilot_experience"):
                self.commit_pilot_experience()
            with logged_duration("commit_front_line_losses"):
                self.commit_front_line_losses(debriefing)
            with logged_duration("commit_motorpool_losses"):
                self.commit_motorpool_losses(debriefing, events)
            with logged_duration("commit_convoy_losses"):
                self.commit_convoy_losses(debriefing)
            with logged_duration("commit_cargo_ship_losses"):
                self.commit_cargo_ship_losses(debriefing)
            with logged_duration("commit_airlift_losses"):
                self.commit_airlift_losses(debriefing)
            with logged_duration("commit_ground_losses"):
                self.commit_ground_losses(debriefing, events)
            with logged_duration("commit_damaged_runways"):
                self.commit_damaged_runways(debriefing)
            # Score the front line before capturing bases: casualty_count
            # attributes a dead front-line unit to its origin CP regardless of
            # side, so a base's defenders (origin == that base) would be
            # miscounted as the new owner's casualties once a capture flips
            # ownership, turning a win into a defeat.
            with logged_duration("commit_front_line_battle_impact"):
                self.commit_front_line_battle_impact(debriefing, events)
            with logged_duration("commit_captures"):
                self.commit_captures(debriefing, events)
            with logged_duration("record_carcasses"):
                self.record_carcasses(debriefing)

    def commit_csar_results(self, debriefing: Debriefing) -> None:
        """Returns rescued downed pilots to recovery (hybrid resolution).

        Ops.CSAR pickups reported in state.json are authoritative for any flight
        that was actually in the mission. The fallback -- a surviving AI CSAR
        flight counts as having rescued its target -- covers only flights the
        simulation resolved before the .miz was generated, which had no chance to
        report anything.

        Crediting a flown flight the mission did not confirm would also make the
        debrief disagree with the in-progress screen, which is rendered before
        this runs and so can never see a fallback rescue.
        """
        csar = CsarService(self.game)
        rescued: set[DownedPilot] = set()

        # Flights that made it into the generated mission. Anything here had a
        # chance to be reported by Ops.CSAR, so its silence means it did not
        # complete the pickup.
        flown = {entry.flight for entry in debriefing.unit_map.aircraft.values()}

        # Authoritative: pilots confirmed rescued in-mission by Ops.CSAR.
        for uuid_str in debriefing.state_data.rescued_pilot_ids:
            try:
                downed = self.game.db.downed_pilots.get(UUID(uuid_str))
            except (KeyError, ValueError):
                logging.warning(f"Ignoring unknown rescued pilot id {uuid_str}")
                continue
            rescued.add(downed)

        # Fallback: AI-flown CSAR flights that reached the pilot and survived.
        for coalition in self.game.coalitions:
            for package in coalition.ato.packages:
                for flight in package.flights:
                    if flight.flight_type is not FlightType.CSAR:
                        continue
                    target = flight.package.target
                    if not isinstance(target, DownedPilot) or target in rescued:
                        continue
                    if flight.client_count > 0:
                        # Player-flown: only the Ops.CSAR result counts, so an
                        # unflown or botched player CSAR correctly fails.
                        continue
                    if flight in flown:
                        # It flew, so Ops.CSAR had its say. Not reported means not
                        # rescued.
                        continue
                    if debriefing.air_losses.surviving_flight_members(flight) > 0:
                        # Worth saying out loud: this credits a rescue the mission
                        # never confirmed, and it is the only reason the debrief
                        # can show more rescues than the in-progress screen did.
                        logging.info(
                            "Crediting %s to surviving AI CSAR flight %s, which "
                            "the simulation resolved before the mission was "
                            "generated.",
                            target.pilot.name,
                            flight,
                        )
                        rescued.add(target)

        for downed in rescued:
            # Record before rescuing: csar.rescue removes the pilot from the db,
            # and the debriefing windows read this to report the recovery.
            debriefing.record_rescue(downed)
            csar.rescue(downed)

    def commit_air_losses(self, debriefing: Debriefing) -> None:
        csar = CsarService(self.game)
        for loss in debriefing.air_losses.losses:
            self._process_lost_pilot(loss, debriefing, csar)
            squadron = loss.flight.squadron
            aircraft = loss.flight.unit_type
            available = squadron.owned_aircraft
            if available <= 0:
                logging.error(
                    f"Found killed {aircraft} from {squadron} but that airbase has "
                    "none available."
                )
                continue

            logging.info(f"{aircraft} destroyed from {squadron}")
            squadron.owned_aircraft -= 1
            squadron.destroyed_aircraft += 1

    def _process_lost_pilot(
        self, loss: Any, debriefing: Debriefing, csar: CsarService
    ) -> None:
        """Decides the fate of a lost aircraft's pilot: safe, downed, or killed.

        Airframe loss accounting is handled by the caller and is unaffected by the
        outcome here.
        """
        pilot = loss.pilot
        if pilot is None:
            return

        # Invulnerable player pilots survive untouched, exactly as before.
        if pilot.player and self.game.settings.invulnerable_player_pilots:
            return

        flight = loss.flight
        if not csar.csar_enabled_for(flight.squadron.player):
            pilot.kill()
            return

        # A real in-mission ejection always produces a downed pilot at the recorded
        # landing position. Otherwise roll the survival chance and scatter near the
        # flight's target (the only position we have for AI/simulated losses).
        ejection_pos = debriefing.ejected_pilot_positions.get(id(pilot))
        if ejection_pos is not None:
            position = ejection_pos
        elif random.randint(1, 100) <= self.game.settings.csar_ejection_chance:
            position = csar.fallback_position_for(flight)
        else:
            pilot.kill()
            return

        # Settles the pilot's fate itself: a downed pilot on the map, or recovered,
        # captured or killed where a rescue was never on the cards.
        csar.down_pilot(flight, pilot, pilot.player, position)

    def commit_intercept_losses(self, debriefing: Debriefing) -> None:
        all_squadrons: list[Squadron] = list(
            self.game.blue.air_wing.iter_squadrons()
        ) + list(self.game.red.air_wing.iter_squadrons())

        # Shared baseline (see fielded_qra_by_squadron): the count actually fielded
        # on alert, matching what the dispatcher was seeded with and what the Lua
        # bounds survivors by, and identical to the debrief report's computation.
        fielded_by_squadron, squadrons_by_id = fielded_qra_by_squadron(all_squadrons)

        if not fielded_by_squadron:
            return

        losses = reconcile_intercept_losses(
            fielded_by_squadron, debriefing.state_data.intercept_survivors
        )
        for squadron_id, loss in losses.items():
            if loss <= 0:
                continue
            squadron = squadrons_by_id.get(squadron_id)
            if squadron is None:
                continue
            logging.info(f"{loss} QRA aircraft lost from {squadron}")
            squadron.owned_aircraft = max(0, squadron.owned_aircraft - loss)
            squadron.lose_pilots(loss)

    @staticmethod
    def _commit_pilot_experience(ato: AirTaskingOrder) -> None:
        for package in ato.packages:
            for flight in package.flights:
                for idx, pilot in enumerate(flight.roster.iter_pilots()):
                    if pilot is None:
                        logging.error(
                            f"Cannot award experience to pilot #{idx} of {flight} "
                            "because no pilot is assigned"
                        )
                        continue
                    pilot.record.missions_flown += 1

    def commit_pilot_experience(self) -> None:
        self._commit_pilot_experience(self.game.blue.ato)
        self._commit_pilot_experience(self.game.red.ato)

    @staticmethod
    def commit_front_line_losses(debriefing: Debriefing) -> None:
        for loss in debriefing.front_line_losses:
            unit_type = loss.unit_type
            control_point = loss.origin
            available = control_point.base.total_units_of_type(unit_type)
            if available <= 0:
                logging.error(
                    f"Found killed {unit_type} from {control_point} but that "
                    "airbase has none available."
                )
                continue

            logging.info(f"{unit_type} destroyed from {control_point}")
            control_point.base.armor[unit_type] -= 1

    @staticmethod
    def commit_motorpool_losses(
        debriefing: Debriefing, events: GameUpdateEvents
    ) -> None:
        for loss in debriefing.motorpool_losses:
            unit_type = loss.unit_type
            control_point = loss.origin
            available = control_point.base.total_units_of_type(unit_type)
            if available <= 0:
                logging.error(
                    f"Found killed motorpool {unit_type} from {control_point} but "
                    "that base has none available."
                )
                continue
            logging.info(f"Motorpool {unit_type} destroyed from {control_point}")
            control_point.base.armor[unit_type] -= 1
            # Refresh the motorpool projection so the depleted TGO is republished
            # in the operation's single accumulator.
            events.update_motorpools_at(control_point)

    @staticmethod
    def commit_convoy_losses(debriefing: Debriefing) -> None:
        for loss in debriefing.convoy_losses:
            unit_type = loss.unit_type
            convoy = loss.convoy
            available = loss.convoy.units.get(unit_type, 0)
            convoy_name = f"convoy from {convoy.origin} to {convoy.destination}"
            if available <= 0:
                logging.error(
                    f"Found killed {unit_type} in {convoy_name} but that convoy has "
                    "none available."
                )
                continue

            logging.info(f"{unit_type} destroyed in {convoy_name}")
            convoy.kill_unit(unit_type)

    @staticmethod
    def commit_cargo_ship_losses(debriefing: Debriefing) -> None:
        for ship in debriefing.cargo_ship_losses:
            logging.info(
                f"All units destroyed in cargo ship from {ship.origin} to "
                f"{ship.destination}."
            )
            ship.kill_all()

    @staticmethod
    def commit_airlift_losses(debriefing: Debriefing) -> None:
        for loss in debriefing.airlift_losses:
            transfer = loss.transfer
            airlift_name = f"airlift from {transfer.origin} to {transfer.destination}"
            for unit_type in loss.cargo:
                try:
                    transfer.kill_unit(unit_type)
                    logging.info(f"{unit_type} destroyed in {airlift_name}")
                except KeyError:
                    logging.exception(
                        f"Found killed {unit_type} in {airlift_name} but that airlift "
                        "has none available."
                    )

    @staticmethod
    def commit_ground_losses(debriefing: Debriefing, events: GameUpdateEvents) -> None:
        for ground_object_loss in debriefing.ground_object_losses:
            ground_object_loss.theater_unit.kill(events)
        for scenery_object_loss in debriefing.scenery_object_losses:
            scenery_object_loss.ground_unit.kill(events)

    @staticmethod
    def commit_damaged_runways(debriefing: Debriefing) -> None:
        for damaged_runway in debriefing.damaged_runways:
            damaged_runway.damage_runway()

    def commit_captures(self, debriefing: Debriefing, events: GameUpdateEvents) -> None:
        for captured in debriefing.base_captures:
            try:
                if captured.captured_by_player.is_blue:
                    self.game.message(
                        f"{captured.control_point} captured!",
                        f"We took control of {captured.control_point}.",
                    )
                else:
                    self.game.message(
                        f"{captured.control_point} lost!",
                        f"The enemy took control of {captured.control_point}.",
                    )

                captured.control_point.capture(
                    self.game, events, captured.captured_by_player
                )
                # After the capture, so the base already belongs to its new owner
                # when we ask whose prisoners these are.
                CsarService(self.game).liberate_prisoners_at(captured.control_point)
            except Exception:
                logging.exception(f"Could not process base capture {captured}")

        for captured in debriefing.base_captures:
            logging.info(f"Will run redeploy for {captured.control_point}")
            self.redeploy_units(captured.control_point)

    def record_carcasses(self, debriefing: Debriefing) -> None:
        for destroyed_unit in debriefing.state_data.destroyed_statics:
            self.game.add_destroyed_units(destroyed_unit)

    def commit_front_line_battle_impact(
        self, debriefing: Debriefing, events: GameUpdateEvents
    ) -> None:
        for cp in self.game.theater.player_points():
            enemy_cps = [e for e in cp.connected_points if e.captured.is_red]
            for enemy_cp in enemy_cps:
                front_line = cp.front_line_with(enemy_cp)
                front_line.update_position()
                events.update_front_line(front_line)

                outcome = resolve_front_line(
                    cp.base.total_armor,
                    enemy_cp.base.total_armor,
                    debriefing.casualty_count(cp),
                    debriefing.casualty_count(enemy_cp),
                    cp.stances[enemy_cp.id],
                    enemy_cp.stances.get(cp.id, CombatStance.DEFENSIVE),
                )

                if outcome.delta == 0.0:
                    self.game.message(
                        "Frontline Report",
                        f"Our ground forces from {cp.name} reached a stalemate "
                        f"with enemy forces from {enemy_cp.name}. {outcome.summary}.",
                    )
                elif outcome.player_won:
                    cp.base.affect_strength(outcome.delta)
                    enemy_cp.base.affect_strength(-outcome.delta)
                    self.game.message(
                        "Frontline Report",
                        f"Our ground forces from {cp.name} are making progress "
                        f"toward {enemy_cp.name}. {outcome.summary}.",
                    )
                else:
                    enemy_cp.base.affect_strength(outcome.delta)
                    cp.base.affect_strength(-outcome.delta)
                    self.game.message(
                        "Frontline Report",
                        f"Our ground forces from {cp.name} are losing ground "
                        f"against enemy forces from {enemy_cp.name}. {outcome.summary}.",
                    )

    def redeploy_units(self, cp: ControlPoint) -> None:
        """ "
        Auto redeploy units to newly captured base
        """
        enemy_connected_cps = [
            ocp for ocp in cp.connected_points if cp.captured != ocp.captured
        ]

        # If the newly captured cp does not have enemy connected cp,
        # then it is not necessary to redeploy frontline units there.
        if len(enemy_connected_cps) == 0:
            return

        ally_connected_cps = [
            ocp
            for ocp in cp.transitive_connected_friendly_destinations()
            if cp.captured == ocp.captured and ocp.base.total_armor
        ]

        settings = cp.coalition.game.settings
        factor = (
            settings.frontline_reserves_factor
            if cp.captured.is_blue
            else settings.frontline_reserves_factor_red
        )

        # From each ally cp, send reinforcements
        for ally_cp in sorted(
            ally_connected_cps,
            key=lambda x: len(
                [cp for cp in x.connected_points if x.captured != cp.captured]
            ),
        ):
            self.redeploy_between(cp, ally_cp)
            if cp.base.total_armor > factor * cp.deployable_front_line_units:
                break

    def redeploy_between(self, destination: ControlPoint, source: ControlPoint) -> None:
        total_units_redeployed = 0
        moved_units = {}

        settings = source.coalition.game.settings
        reserves = max(
            1,
            (
                settings.reserves_procurement_target
                if source.captured.is_blue
                else settings.reserves_procurement_target_red
            ),
        )
        total_units = source.base.total_armor
        reserves_factor = (reserves - 1) / total_units  # slight underestimation

        source_frontline_count = len(
            [cp for cp in source.connected_points if not source.is_friendly_to(cp)]
        )

        move_factor = max(0.0, 1 / (source_frontline_count + 1) - reserves_factor)

        for frontline_unit, count in source.base.armor.items():
            moved_count = int(count * move_factor)
            moved_units[frontline_unit] = moved_count
            total_units_redeployed += moved_count

        destination.base.commission_units(moved_units)
        source.base.commit_losses(moved_units)

        # Also transfer pending deliveries.
        for unit_type, count in list(source.ground_unit_orders.units.items()):
            move_count = int(count * move_factor)
            source.ground_unit_orders.sell({unit_type: move_count})
            destination.ground_unit_orders.order({unit_type: move_count})
            total_units_redeployed += move_count

        if total_units_redeployed > 0:
            self.game.message(
                "Units redeployed",
                f"{total_units_redeployed}  units have been redeployed from "
                f"{source.name} to {destination.name}",
            )
