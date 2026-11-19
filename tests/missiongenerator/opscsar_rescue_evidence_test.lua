-- opscsar_rescue_evidence_test.lua
--
-- Standalone unit tests for the rescue-evidence, boarding-lifetime and
-- survivor-death rules in resources/plugins/opscsar/OpsCSAR.lua, driving the
-- real plugin functions under a harness that models the bundled MOOSE contracts:
--
--   * CSAR:_PickupUnit(_heliUnit, ...) receives the picking aircraft's live
--     UNIT object, records the survivor in inTransitGroups[heliName], destroys
--     the survivor group, and schedules Boarded exactly 5s later
--     (Moose.lua 79064-79095, three call sites: 79164/79179/79220).
--   * CSAR:_RescuePilots(_heliUnit) is the single delivery point (79270):
--     it clears inTransitGroups[heliName] and raises Rescued through
--     FSM:_delayed_transition (19332-47), which abs()'s __Rescued(-1) into a
--     callback 1 second later (negative events deduplicate against a pending
--     one). A helicopter lost inside that second has already handed the
--     pilots over — the delivery must be reported anyway.
--   * onbeforeBoarded rejects nothing, and a same-name respawn keeps
--     inTransitGroups alive (_GetSARHeli answers for the replacement), so
--     boardings are tied to the picking aircraft's DCS runtime unit id —
--     recorded at the real pickup moment by the Ops.CSAR wrapper on
--     _PickupUnit — not to the name and not to elapsed time.
--
-- Scenarios cover: landing evidence without/with boarding; boarding lost with
-- the helicopter (recorded aboard and pending pickup), stale delayed Boarded
-- after loss+respawn refused while a legitimate replacement pickup inside the
-- old window is accepted; delivery → loss → delayed Rescued still reports;
-- same-name respawn / new-passenger overlap with interleaved deliveries;
-- hover-only missions; a survivor killed during the wait (event path and
-- disappearance poll) is never credited, while a silent embark still is.
--
-- Not run by CI (CI has no Lua). Run locally with:
--   lua    tests/missiongenerator/opscsar_rescue_evidence_test.lua
--   luajit tests/missiongenerator/opscsar_rescue_evidence_test.lua

local SCRIPT = os.getenv("OPSCSAR_SCRIPT")
    or "resources/plugins/opscsar/OpsCSAR.lua"

local world_events = {
    S_EVENT_BIRTH = 1,
    S_EVENT_LAND = 2,
    S_EVENT_TAKEOFF = 3,
    S_EVENT_CRASH = 4,
    S_EVENT_UNIT_LOST = 5,
    S_EVENT_DEAD = 6,
}

-- Per-test state ---------------------------------------------------------------

local clock = 0
local event_handler -- the embark_events handler the script registers
local csar_instances -- list of Ops.CSAR instances built by make_csar
local units_by_name -- world registry backing Unit.getByName
local rescued -- the global csar_rescued table dcs_retribution.lua owns
local scheduled_poll -- the periodic pickup check the script schedules
local heli_groups_for_side -- side -> list of helicopter group mocks
local world_groups -- survivor group name -> survivor unit name
local scheduler -- MOOSE CallScheduler stand-in: { at = t, fn = fn }
local next_unit_id

local function schedule(delay, fn)
    scheduler[#scheduler + 1] = { at = clock + math.abs(delay), fn = fn }
end

-- Run every callback whose time has come, earliest first (new callbacks
-- scheduled during a run are picked up by the re-scan).
local function run_due()
    while true do
        local best
        for i, job in ipairs(scheduler) do
            if job.at <= clock and (best == nil or job.at < scheduler[best].at) then
                best = i
            end
        end
        if best == nil then
            return
        end
        local job = table.remove(scheduler, best)
        job.fn()
    end
end

local function advance(seconds)
    clock = clock + seconds
    run_due()
end

local function setup(downed_pilots, cfg_overrides)
    clock = 0
    event_handler = nil
    csar_instances = {}
    rescued = {}
    scheduled_poll = nil
    heli_groups_for_side = {}
    world_groups = {}
    scheduler = {}
    next_unit_id = 100

    units_by_name = {}
    for _, dp in ipairs(downed_pilots) do
        world_groups[dp.groupName] = dp.unitName
        units_by_name[dp.unitName] = {
            id = next_unit_id,
            name = dp.unitName,
            isExist = function(self)
                return true
            end,
            getLife = function(self)
                return 1
            end,
            getName = function(self)
                return self.name
            end,
            getPoint = function(self)
                return { x = 0, y = 0, z = 0 }
            end,
        }
        next_unit_id = next_unit_id + 1
    end

    Unit = {
        getByName = function(name)
            return units_by_name[name]
        end,
        Category = { HELICOPTER = "helicopter" },
    }
    Group = { Category = { HELICOPTER = "helicopter" } }
    GROUP = {
        FindByName = function(_, name)
            return { name = name }
        end,
    }
    coalition = {
        side = { BLUE = "blue", RED = "red" },
        -- The script calls coalition.getGroups(side, category) with dot syntax.
        getGroups = function(side, _category)
            return heli_groups_for_side[side] or {}
        end,
    }
    world = {
        event = world_events,
        addEventHandler = function(handler)
            event_handler = handler
        end,
    }
    timer = {
        getTime = function()
            return clock
        end,
        scheduleFunction = function(fn)
            scheduled_poll = fn
        end,
    }
    land = {
        getHeight = function()
            return 0 -- survivors and helicopters sit at sea level in these tests
        end,
    }
    env = { info = function() end, warning = function() end, error = function() end }
    trigger = {
        action = { outText = function() end, smoke = function() end },
        smokeColor = { Red = 1, Blue = 2 },
    }

    -- A minimal but contract-faithful MOOSE CSAR class: the pickup stub does
    -- what Moose.lua 79064-79095 does — the cabin-capacity guard first
    -- (_PilotsOnboard + 1 > AircraftType[type] or max_units: refuse with no
    -- membership, no group destroy, no Boarded), then membership with the
    -- bundled field shape, group destroyed, Boarded +5s; the delivery stub
    -- does what 79283-79292 does (count, clear membership, Rescued through
    -- the delayed-transition abs(-1) = 1s, with the negative-event dedup);
    -- boardings/deliveries run the handlers the script attached to the
    -- instance.
    CSAR = {
        New = function(_, side, template, alias)
            local inst = {
                lid = "",
                inTransitGroups = {},
                max_units = 99, -- generous unless a test tightens it
            }
            function inst:__Start(_delay) end
            function inst:__Boarded(delay, heli_name, wounded_group_name)
                schedule(delay, function()
                    -- onbeforeBoarded runs _ScheduledSARFlight, which clears
                    -- the membership when _GetSARHeli (registry- alive check)
                    -- finds nothing under the name.
                    local heli = units_by_name[heli_name]
                    if heli == nil or not heli:isExist() then
                        self_in_transit_clear(inst, heli_name)
                    end
                    if inst.OnAfterBoarded ~= nil then
                        inst:OnAfterBoarded(nil, nil, nil, heli_name, wounded_group_name)
                    end
                end)
            end
            function inst:__Rescued(delay, heli_unit, heli_name, pilots_saved)
                if delay < 0 and self._rescued_pending then
                    return -- FSM negative-event dedup
                end
                local run_at = math.abs(delay)
                if delay < 0 then
                    self._rescued_pending = true
                end
                schedule(run_at, function()
                    self._rescued_pending = nil
                    if inst.OnAfterRescued ~= nil then
                        inst:OnAfterRescued(nil, nil, nil, heli_unit, heli_name, pilots_saved)
                    end
                end)
            end
            function inst:_PickupUnit(heli_unit, _pilot_name, _wounded_group, wounded_group_name)
                local hn = heli_unit:GetName()
                local units_in_helicopter = 0
                for _ in pairs(self.inTransitGroups[hn] or {}) do
                    units_in_helicopter = units_in_helicopter + 1
                end
                local max_units = self.max_units
                if heli_unit.GetTypeName ~= nil then
                    local by_type = self.AircraftType
                        and self.AircraftType[heli_unit:GetTypeName()]
                    if by_type ~= nil then
                        max_units = by_type
                    end
                end
                if units_in_helicopter + 1 > max_units then
                    -- Cabin full: MOOSE refuses here — no membership, no group
                    -- destroy, no Boarded (Moose.lua 79073-79).
                    return
                end
                self.inTransitGroups[hn] = self.inTransitGroups[hn] or {}
                self.inTransitGroups[hn][wounded_group_name] = {
                    originalUnit = nil,
                    woundedGroup = wounded_group_name,
                    side = "blue",
                    desc = "Downed pilot",
                    player = false,
                }
                local survivor = world_groups[wounded_group_name]
                if survivor ~= nil then
                    units_by_name[survivor] = nil -- the group is destroyed
                end
                self:__Boarded(5, hn, wounded_group_name)
                -- Model _ScheduledSARFlight's ScheduleOnce(5, ...) re-check:
                -- every 5s the membership for a helicopter that no longer
                -- exists under this name is dropped (_GetSARHeli nil).
                schedule(5, function()
                    if units_by_name[hn] == nil then
                        self.inTransitGroups[hn] = nil
                    elseif self.inTransitGroups[hn] ~= nil
                        and next(self.inTransitGroups[hn]) ~= nil then
                        schedule(5, function()
                            if units_by_name[hn] == nil then
                                self.inTransitGroups[hn] = nil
                            end
                        end)
                    end
                end)
            end
            function inst:_RescuePilots(heli_unit)
                local hn = heli_unit:GetName()
                local groups = self.inTransitGroups[hn]
                if groups == nil then
                    return
                end
                local pilots_saved = 0
                for _ in pairs(groups) do
                    pilots_saved = pilots_saved + 1
                end
                self.inTransitGroups[hn] = nil
                self:__Rescued(-1, heli_unit, hn, pilots_saved)
            end
            csar_instances[#csar_instances + 1] = inst
            return inst
        end,
    }

    local cfg = {
        blueEnabled = "true",
        redEnabled = "false",
        blueTemplate = "Template 1",
        redTemplate = "",
        rescueAI = "true",
        requireOpenDoors = "false",
        playerHoverHeight = "20",
        playerHoverDistance = "10",
        hoverExtraction = "false",
        hoverDurationSeconds = "30",
        hoverAltitudeMeters = "30",
        clusterRadius = "0",
        embarkZoneRadius = "300",
        blueCountry = "2",
        redCountry = "6",
        downedPilots = downed_pilots,
    }
    for key, value in pairs(cfg_overrides or {}) do
        cfg[key] = value
    end
    dcsRetribution = { CSAR = cfg }
    csar_rescued = rescued
    dirty_state = false

    assert(loadfile(SCRIPT))()
end

function self_in_transit_clear(inst, heli_name)
    inst.inTransitGroups[heli_name] = nil
end

-- World mutations ---------------------------------------------------------------

-- A survivor or helicopter removed from (or restored to) the world between
-- checks. A respawn takes a fresh DCS runtime id, exactly as a same-name
-- replacement unit does.
local function set_in_world(unit_name, present, id)
    if present then
        if id == nil then
            id = next_unit_id
            next_unit_id = next_unit_id + 1
        end
        units_by_name[unit_name] = {
            id = id,
            name = unit_name,
            isExist = function(self)
                return true
            end,
            getLife = function(self)
                return 1
            end,
            getName = function(self)
                return self.name
            end,
            getID = function(self)
                return self.id
            end,
            getPoint = function(self)
                return { x = 0, y = 0, z = 0 }
            end,
        }
    else
        units_by_name[unit_name] = nil
    end
end

-- An AI rescue helicopter with no player aboard (event-path mock).
local function heli_unit(name, x, z)
    local group = {
        getCategory = function()
            return Group.Category.HELICOPTER
        end,
        getUnits = function()
            return {}
        end,
    }
    return {
        name = name,
        getName = function(self)
            return self.name
        end,
        isExist = function(self)
            return true
        end,
        getLife = function(self)
            return 1
        end,
        getPlayerName = function()
            return nil -- AI, no human crew
        end,
        getPoint = function(self)
            return { x = x, y = 0, z = z }
        end,
        getCoalition = function(self)
            return "blue"
        end,
        getGroup = function()
            return group
        end,
    }
end

-- A group-mock carrying one live helicopter unit, for coalition.getGroups.
local function heli_group(name, x, z)
    local unit = heli_unit(name, x, z)
    return {
        isExist = function(self)
            return true
        end,
        getUnits = function()
            return { unit }
        end,
    }
end

local function heli_event(event_id, heli)
    assert(event_handler, "OpsCSAR registered no embark event handler")
    event_handler:onEvent({ id = event_id, initiator = heli, place = nil })
end

-- Drive the wrapped real _PickupUnit for `heli_name` picking up `group_name`.
local function pickup(instance, heli_name, group_name)
    local heli = assert(units_by_name[heli_name], "heli must be in the world")
    instance:_PickupUnit({ GetName = function()
        return heli_name
    end }, "Pilot", nil, group_name)
end

-- Drive the wrapped real _RescuePilots for `heli_name`.
local function deliver(instance, heli_name)
    instance:_RescuePilots({ GetName = function()
        return heli_name
    end })
end

local function board(instance, heli_name, group_name)
    instance:OnAfterBoarded(nil, nil, nil, heli_name, group_name)
end

local function sorted_rescued()
    local names = {}
    for _, uuid in ipairs(csar_rescued) do
        names[#names + 1] = uuid
    end
    table.sort(names)
    return table.concat(names, ",")
end

local function count_in(list, value)
    local n = 0
    for _, v in ipairs(list) do
        if v == value then
            n = n + 1
        end
    end
    return n
end

local passed = 0
local function check(cond, label)
    if not cond then
        error("FAIL: " .. label, 2)
    end
    passed = passed + 1
end

local function pilot(id, group_name, unit_name, hover)
    return {
        id = id,
        groupName = group_name,
        unitName = unit_name,
        coalition = "blue",
        hoverExtraction = hover and "true" or "false",
        x = "0",
        z = "0",
    }
end

-- C2: the helicopter lands in the embark zone, waits far past the minimum
-- ground time, and lifts off with the survivor still alive and stationary. No
-- rescue may be credited.
local function test_landing_without_boarding_is_not_a_rescue()
    local dp = pilot("uuid-1", "Group 1", "Survivor 1")
    setup({ dp })
    assert(event_handler, "landing pickups must register the embark handler")

    local heli = heli_unit("Rescue 1", 10, 10) -- inside the 300 m zone
    heli_event(world_events.S_EVENT_LAND, heli)
    clock = clock + 300 -- far past EMBARK_MIN_GROUND_TIME (20 s)
    heli_event(world_events.S_EVENT_TAKEOFF, heli)

    check(#csar_rescued == 0,
        "takeoff without the survivor leaving the map is not a rescue")
end

-- C2 positive path: same approach, but the survivor is gone from the map when
-- the helicopter lifts (DCS deletes them as they board). The rescue credits.
local function test_landing_with_boarding_still_credits()
    local dp = pilot("uuid-2", "Group 2", "Survivor 2")
    setup({ dp })

    local heli = heli_unit("Rescue 2", 10, 10)
    heli_event(world_events.S_EVENT_LAND, heli)
    clock = clock + 300
    set_in_world("Survivor 2", false)
    heli_event(world_events.S_EVENT_TAKEOFF, heli)

    check(sorted_rescued() == "uuid-2",
        "disappearance at a sufficiently long pickup credits the rescue")
end

-- C3: pickup -> helicopter lost with the pilot aboard -> same-name respawn ->
-- replacement picks up and delivers a different survivor. Only that survivor
-- may be reported, once. Before the lifetime reconciliation, the lost pilot
-- was reported too.
local function test_board_loss_respawn_board_deliver()
    local dp1 = pilot("uuid-lost", "Group L", "Survivor L")
    local dp2 = pilot("uuid-delivered", "Group D", "Survivor D")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group L") -- Boarded due t5
    advance(5) -- Boarded fires: pilot L is aboard

    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0))
    scheduled_poll() -- post-loss poll: the lost pilot settles unattributed

    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))
    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group D") -- the replacement's own pickup
    scheduled_poll() -- D's accepted pickup in flight when the poll lands
    advance(5) -- Boarded D fires
    deliver(my, "Helo 1")
    advance(1) -- MOOSE raises Rescued 1s after the delivery
    scheduled_poll() -- post-delivery poll: settled, no double report

    check(sorted_rescued() == "uuid-delivered",
        "delivery reports only the replacement aircraft's own passenger")
end

-- C3 guard: a boarding reported for a helicopter that no longer exists can
-- never be delivered and must not be recorded.
local function test_boarding_for_gone_helicopter_is_ignored()
    local dp = pilot("uuid-3", "Group 3", "Survivor 3")
    setup({ dp })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    board(my, "Ghost Helo", "Group 3")
    deliver(my, "Ghost Helo")
    advance(1)

    check(#csar_rescued == 0, "boarding of a nonexistent helicopter is ignored")
end

-- C3-legitimate-pickup: loss at t0, same-name respawn at t1, and a REAL
-- replacement pickup at t2 (Boarded at t7, delivery at t20). The replacement's
-- boarding is tied to its own recorded pickup identity, so it is accepted and
-- delivered even though it falls inside any loss-based time window. The lost
-- pilot's delayed Boarded (scheduled at pickup, due at t5) must not report.
local function test_legitimate_replacement_pickup_inside_old_window()
    local dp_l = pilot("uuid-lost", "Group L", "Survivor L")
    local dp_d = pilot("uuid-new", "Group N", "Survivor N")
    setup({ dp_l, dp_d })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group L") -- t0: identity recorded, Boarded due t5
    scheduled_poll() -- a poll landing before L's Boarded: accepted pickup
    advance(2) -- t2, via the loss event below
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0))

    set_in_world("Helo 1", true, 202) -- respawn: fresh runtime id
    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))
    pickup(my, "Helo 1", "Group N") -- t2: the replacement's real pickup
    scheduled_poll() -- again before N's Boarded

    advance(3) -- t5: the dead lifetime's delayed Boarded arrives
    advance(4) -- t9: the replacement's Boarded arrives
    deliver(my, "Helo 1") -- t9 delivery
    advance(1) -- t10: Rescued callback
    scheduled_poll() -- post-delivery poll: entry settled, no double report

    check(sorted_rescued() == "uuid-new",
        "a real replacement pickup is accepted and delivered despite the "
        .. "earlier loss; the lost pilot is not")
end

-- C3-delivered-before-loss: the delivery transition itself completes the
-- rescue; MOOSE only raises Rescued 1 second later. A helicopter lost inside
-- that second must not erase the completed delivery, and neither must a
-- same-name respawn reusing the name before the callback runs.
local function test_delivered_before_loss_still_reports()
    local dp = pilot("uuid-delivered", "Group V", "Survivor V")
    setup({ dp })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group V") -- Boarded due t5
    advance(5)
    deliver(my, "Helo 1") -- t5: pilots handed over at the MASH
    advance(0.5)
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0)) -- t5.5
    set_in_world("Helo 1", true, 202) -- respawn before the callback
    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))
    advance(0.5) -- t6: the delayed Rescued fires

    check(sorted_rescued() == "uuid-delivered",
        "a rescue completed at the delivery transition is reported even when "
        .. "the helicopter is lost before the Rescued callback runs")
end

-- Same-name respawn and new-passenger overlap: two deliveries on the same
-- name, with loss/respawn in between, each report exactly their own batch once.
local function test_respawn_overlap_reports_each_batch_once()
    local dp1 = pilot("uuid-b1", "Group B1", "Survivor B1")
    local dp2 = pilot("uuid-b2", "Group B2", "Survivor B2")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group B1")
    advance(5) -- Boarded B1
    deliver(my, "Helo 1") -- delivery 1
    advance(1) -- Rescued 1 reports B1

    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0))
    set_in_world("Helo 1", true, 202)
    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))

    pickup(my, "Helo 1", "Group B2")
    advance(5) -- Boarded B2
    deliver(my, "Helo 1") -- delivery 2
    advance(1) -- Rescued 2 reports B2
    advance(5) -- settle any stray timers

    check(sorted_rescued() == "uuid-b1,uuid-b2",
        "each delivery reports its own passengers exactly once across a "
        .. "same-name respawn")
    check(count_in(csar_rescued, "uuid-b1") == 1
            and count_in(csar_rescued, "uuid-b2") == 1,
        "no duplicated or cross-credited rescues")
end

-- Interleaved states: survivor A2 picked up (pending, Boarded in flight) while
-- A1 is already aboard; the helicopter delivers both at the transition —
-- MOOSE counts pending pickups in PilotsSaved all the same — and is lost
-- inside the Rescued callback delay afterwards. Both are credited exactly
-- once at the delivery transition; neither the loss nor A2's late Boarded can
-- revive or duplicate them.
local function test_mixed_aboard_and_pending_on_loss()
    local dp1 = pilot("uuid-aboard", "Group A1", "Survivor A1")
    local dp2 = pilot("uuid-pending", "Group A2", "Survivor A2")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group A1") -- Boarded due t5
    advance(2)
    pickup(my, "Helo 1", "Group A2") -- t2: pending, Boarded due t7

    advance(3) -- t5: Boarded A1 accepted (same aircraft)
    deliver(my, "Helo 1") -- t5: both handed over; Rescued scheduled t6
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0)) -- t5
    advance(1) -- t6: Rescued fires (accounting already settled at delivery)
    advance(1) -- t7: A2's delayed Boarded arrives — must not re-credit
    deliver(my, "Helo 1") -- must not report anything
    advance(1)

    check(sorted_rescued() == "uuid-aboard,uuid-pending",
        "delivery credits boarded and still-pending pickups together")
    check(count_in(csar_rescued, "uuid-aboard") == 1
            and count_in(csar_rescued, "uuid-pending") == 1,
        "the loss inside the Rescued delay and the late Boarded cannot "
        .. "revive or duplicate either pilot")
end

-- C3-cross-helicopter-delivery: two helicopters of the same instance deliver
-- half a second apart. MOOSE's negative-event dedup is instance-wide, so the
-- second delivery's Rescued callback is cancelled — accounting must not depend
-- on a callback per delivery or helicopter B's pilots would never report.
local function test_cross_helicopter_delivery_with_coalesced_callback()
    local dp1 = pilot("uuid-heli-a", "Group HA", "Survivor HA")
    local dp2 = pilot("uuid-heli-b", "Group HB", "Survivor HB")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo A", true)
    set_in_world("Helo B", true)
    pickup(my, "Helo A", "Group HA") -- Boarded due t5
    pickup(my, "Helo B", "Group HB") -- Boarded due t5
    advance(5) -- both Boarded accepted
    deliver(my, "Helo A") -- t5: Rescued scheduled for t6 (instance-wide)
    advance(0.5)
    deliver(my, "Helo B") -- t5.5: Rescued CANCELLED by the FSM dedup
    advance(2) -- t7.5: settle whatever callbacks survived

    check(sorted_rescued() == "uuid-heli-a,uuid-heli-b",
        "a delivery whose Rescued callback was coalesced away is still "
        .. "credited, with the other helicopter's")
    check(count_in(csar_rescued, "uuid-heli-a") == 1
            and count_in(csar_rescued, "uuid-heli-b") == 1,
        "no stranding and no duplication across helicopters")
end

-- C3-pending-at-delivery: pickup A boards normally, pickup B's Boarded is
-- still in flight when the helicopter delivers (B was handed over with the
-- rest — MOOSE counts it). Both credit at the transition; B's late Boarded is
-- refused, and a second delivery reports nothing.
local function test_pending_pickup_delivered_before_its_boarding()
    local dp1 = pilot("uuid-boarded", "Group PA", "Survivor PA")
    local dp2 = pilot("uuid-pending", "Group PB", "Survivor PB")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group PA") -- Boarded due t5
    advance(5) -- Boarded PA accepted
    pickup(my, "Helo 1", "Group PB") -- t5: pending, Boarded due t10
    scheduled_poll() -- PB's accepted pickup in flight when the poll lands
    deliver(my, "Helo 1") -- t5: both handed over before PB's Boarded

    check(sorted_rescued() == "uuid-boarded,uuid-pending",
        "a pickup delivered before its Boarded is credited at the transition")

    advance(5) -- t10: PB's delayed Boarded arrives — must be refused
    deliver(my, "Helo 1") -- inTransitGroups already cleared: no-op
    advance(1)

    check(sorted_rescued() == "uuid-boarded,uuid-pending",
        "the late Boarded cannot recreate a delivered pilot; a second "
        .. "delivery reports nothing")
    check(count_in(csar_rescued, "uuid-pending") == 1,
        "the delivered-then-boarded pilot is not double-reported")
end

-- Fast delivery: a single pickup handed over before its Boarded ever fires,
-- with the delayed Boarded arriving afterwards. Credited exactly once.
local function test_single_fast_delivery_credits_once()
    local dp = pilot("uuid-fast", "Group F", "Survivor F")
    setup({ dp })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group F") -- Boarded due t5
    deliver(my, "Helo 1") -- t0: delivered before the Boarded
    scheduled_poll() -- post-delivery poll: entry already settled done

    check(sorted_rescued() == "uuid-fast",
        "a single pickup delivered before its Boarded is credited")

    advance(5) -- the delayed Boarded arrives
    deliver(my, "Helo 1")
    advance(1)

    check(sorted_rescued() == "uuid-fast"
            and count_in(csar_rescued, "uuid-fast") == 1,
        "the late Boarded and second delivery cannot duplicate the credit")
end

-- C3-hover: the event handler is registered even when every survivor needs a
-- hoist, because crewed hover rescues board helicopters whose lifetimes need
-- the same reconciling as landing-mode ones.
local function test_all_hover_mission_still_registers_lifetimes()
    local dp = pilot("uuid-hover", "Group H", "Survivor H", true)
    setup({ dp })

    check(event_handler ~= nil,
        "all-hover mission still registers the lifetime handler")

    local my = assert(csar_instances[1], "Ops.CSAR instance not built")
    set_in_world("Helo Hover", true)
    board(my, "Helo Hover", "Group H")
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo Hover", 0, 0))
    board(my, "Helo Hover", "Group H")
    deliver(my, "Helo Hover")
    advance(1)

    check(#csar_rescued == 0,
        "hover-mode boarding reconciles on helicopter loss like landing mode")
end

-- C2-death (event path): the survivor dies while the helicopter sits in the
-- zone; the eventual takeoff must not read the disappearance as an embark.
local function test_death_during_wait_then_takeoff_is_not_a_rescue()
    local dp = pilot("uuid-dead", "Group X", "Survivor X")
    setup({ dp })

    local heli = heli_unit("Rescue X", 10, 10)
    heli_event(world_events.S_EVENT_LAND, heli)
    clock = clock + 10
    -- Enemy fire kills the survivor where they lie; DCS reports it explicitly
    -- and the dead unit leaves the registry.
    heli_event(world_events.S_EVENT_DEAD, heli_unit("Survivor X", 0, 0))
    set_in_world("Survivor X", false)
    clock = clock + 290
    heli_event(world_events.S_EVENT_TAKEOFF, heli)

    check(#csar_rescued == 0,
        "a survivor who died during the wait is not rescued at takeoff")
end

-- C2-death (poll path): a survivor seen alive with a rescue helicopter nearby,
-- then killed between polls, must not be credited by the disappearance
-- attribution — while a silent removal (the real embark) still is.
local function test_poll_death_is_not_a_rescue_but_embark_still_is()
    local dp_dead = pilot("uuid-dead-poll", "Group DX", "Survivor DX")
    local dp_boarded = pilot("uuid-boarded-poll", "Group DY", "Survivor DY")
    setup({ dp_dead, dp_boarded })
    assert(scheduled_poll, "the script scheduled its pickup check")

    -- One blue rescue helicopter parked between the two survivors: close enough
    -- to attribute a disappearance to either.
    heli_groups_for_side["blue"] = { heli_group("Rescue Poll", 50, 50) }

    scheduled_poll() -- both seen alive; helicopter seen in both zones

    -- Survivor DX is killed where they lie (explicit event, then registry
    -- follows); survivor DY boards silently (no event, registry only).
    heli_event(world_events.S_EVENT_DEAD, heli_unit("Survivor DX", 0, 0))
    set_in_world("Survivor DX", false)
    set_in_world("Survivor DY", false)

    scheduled_poll()

    check(sorted_rescued() == "uuid-boarded-poll",
        "poll credits the silent embark and refuses the known-dead survivor")
end

-- Cross-coalition instances: blue and red Ops.CSAR each reconcile and credit
-- their own pickups/deliveries independently.
local function test_cross_coalition_instances_credit_independently()
    local dp_blue = pilot("uuid-blue", "Group CB", "Survivor CB")
    local dp_red = pilot("uuid-red", "Group CR", "Survivor CR")
    setup(
        { dp_blue, dp_red },
        { redEnabled = "true", redTemplate = "Template 2" }
    )
    local red = assert(csar_instances[2], "red Ops.CSAR instance not built")
    units_by_name["Survivor CR"].getCoalition = function()
        return "red"
    end

    set_in_world("Helo Blue", true)
    set_in_world("Helo Red", true)
    pickup(csar_instances[1], "Helo Blue", "Group CB")
    pickup(red, "Helo Red", "Group CR")
    advance(5) -- both Boarded accepted
    deliver(csar_instances[1], "Helo Blue")
    deliver(red, "Helo Red")
    advance(1)

    check(sorted_rescued() == "uuid-blue,uuid-red",
        "blue and red instances credit their own rescues independently")
end

-- C3-poll-before-boarded: the pickup removes the survivor from the world and
-- MOOSE schedules Boarded 5s out — the same cadence as the disappearance
-- poll. A poll that lands between the accepted pickup and its Boarded sees
-- the survivor gone with no AI attribution (player helicopters are excluded
-- from the attribution check); it must leave the entry for its Boarded and
-- delivery instead of settling it as an unattributed disappearance.
local function test_poll_before_boarded_does_not_settle_accepted_pickup()
    local dp = pilot("uuid-inflight", "Group IF", "Survivor IF")
    setup({ dp })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    scheduled_poll() -- initial poll: survivor alive, position recorded

    pickup(my, "Helo 1", "Group IF") -- accepted: survivor leaves the world

    scheduled_poll() -- lands before the Boarded; must not settle the entry

    advance(5) -- Boarded fires and must be accepted, not blocked
    deliver(my, "Helo 1")
    advance(1) -- the delivery transition credits directly

    check(sorted_rescued() == "uuid-inflight",
        "an accepted pickup polled before its Boarded still completes the "
        .. "rescue")
end

-- C3-refused-pickup-credit: a full cabin refuses the second pickup. The
-- refused survivor stays alive on the ground, is never credited for the other
-- pilot's delivery, and remains available for a later genuine pickup once the
-- cabin empties.
local function test_full_cabin_refusal_is_not_a_rescue_and_keeps_survivor_available()
    local dp1 = pilot("uuid-aboard", "Group FA", "Survivor FA")
    local dp2 = pilot("uuid-refused", "Group FB", "Survivor FB")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")
    my.max_units = 1 -- single-seat cabin (Moose.lua 79073-79 guard)

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group FA") -- accepted
    advance(5) -- Boarded FA accepted
    pickup(my, "Helo 1", "Group FB") -- capacity 1: refused, survivor stays
    scheduled_poll() -- FB is still alive: the poll just refreshes its position

    check(units_by_name["Survivor FB"] ~= nil,
        "a refused pickup leaves the survivor alive in the world")

    deliver(my, "Helo 1")
    advance(1)
    check(sorted_rescued() == "uuid-aboard",
        "delivery credits the accepted pilot only, not the refused one")

    -- The cabin is empty again: a later genuine pickup of the refused
    -- survivor is accepted and delivered.
    pickup(my, "Helo 1", "Group FB")
    advance(5)
    deliver(my, "Helo 1")
    advance(1)
    check(sorted_rescued() == "uuid-aboard,uuid-refused",
        "the refused survivor is delivered by the later genuine pickup")
end

-- A refused attempt alongside an unrelated pending pickup: the refusal settles
-- nothing about anyone. When the helicopter is then lost, only the pilot
-- actually picked up is reconciled as lost; the refused survivor stays
-- available and a later genuine pickup delivers them.
local function test_rejected_attempt_with_pending_pickup_then_loss()
    local dp1 = pilot("uuid-pending-a", "Group RA", "Survivor RA")
    local dp2 = pilot("uuid-refused-b", "Group RB", "Survivor RB")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")
    my.max_units = 1

    set_in_world("Helo 1", true)
    pickup(my, "Helo 1", "Group RA") -- accepted, pending (Boarded due t5)
    advance(5) -- Boarded RA accepted; the returning loop re-arms
    pickup(my, "Helo 1", "Group RB") -- capacity 1: refused, B untouched

    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0))
    set_in_world("Helo 1", true, 202) -- same-name respawn
    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))
    advance(3) -- the returning loop clears the dead lifetime's membership

    -- Another rescue flight (empty cabin) picks the refused survivor up and
    -- delivers them.
    set_in_world("Helo 2", true)
    pickup(my, "Helo 2", "Group RB")
    advance(5)
    deliver(my, "Helo 2")
    advance(1)

    check(sorted_rescued() == "uuid-refused-b",
        "only the refused survivor's later genuine delivery reports; the "
        .. "pilot picked up by the lost helicopter does not")
    check(units_by_name["Survivor RB"] == nil,
        "the refused survivor is gone from the world at their real pickup")
end

local function main()
    test_landing_without_boarding_is_not_a_rescue()
    test_landing_with_boarding_still_credits()
    test_board_loss_respawn_board_deliver()
    test_boarding_for_gone_helicopter_is_ignored()
    test_legitimate_replacement_pickup_inside_old_window()
    test_delivered_before_loss_still_reports()
    test_respawn_overlap_reports_each_batch_once()
    test_mixed_aboard_and_pending_on_loss()
    test_cross_helicopter_delivery_with_coalesced_callback()
    test_pending_pickup_delivered_before_its_boarding()
    test_single_fast_delivery_credits_once()
    test_cross_coalition_instances_credit_independently()
    test_full_cabin_refusal_is_not_a_rescue_and_keeps_survivor_available()
    test_rejected_attempt_with_pending_pickup_then_loss()
    test_all_hover_mission_still_registers_lifetimes()
    test_death_during_wait_then_takeoff_is_not_a_rescue()
    test_poll_death_is_not_a_rescue_but_embark_still_is()
    test_poll_before_boarded_does_not_settle_accepted_pickup()
    print("opscsar_rescue_evidence_test: " .. passed .. " checks passed")
end

main()
