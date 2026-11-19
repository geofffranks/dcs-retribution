-- opscsar_rescue_evidence_test.lua
--
-- Standalone unit tests for the rescue-evidence, boarding-lifetime and
-- survivor-death rules in resources/plugins/opscsar/OpsCSAR.lua:
--
--   * An AI helicopter landing inside a survivor's embark zone and lifting off
--     after the minimum ground time does NOT credit a rescue unless the survivor
--     actually went from the map (DCS removes them the moment they board).
--     Ground time alone is not boarding evidence.
--   * Passengers recorded aboard a helicopter live only as long as that
--     helicopter: on its loss (or a same-name respawn) the boarding record is
--     dropped, pending pickups listed in MOOSE's own inTransitGroups are marked,
--     and a Boarded reported within the pickup-delay window of the loss is the
--     previous lifetime's callback — so a replacement aircraft's delivery
--     reports only the pilots it actually carried.
--   * The handler runs in every pickup mode: an all-hover mission still watches
--     helicopter lifetimes.
--   * A survivor killed while a helicopter sits in the zone (explicit loss
--     event on their unit) is dead, not embarked: neither the takeoff path nor
--     the disappearance poll may credit them. A silent removal with no death
--     event — the real native embark — still credits through the poll.
--
-- Not run by CI (CI has no Lua). Run locally with:
--   lua    tests/missiongenerator/opscsar_rescue_evidence_test.lua
--   luajit tests/missiongenerator/opscsar_rescue_evidence_test.lua

local SCRIPT = "resources/plugins/opscsar/OpsCSAR.lua"

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

local function setup(downed_pilots)
    clock = 0
    event_handler = nil
    csar_instances = {}
    rescued = {}
    scheduled_poll = nil
    heli_groups_for_side = {}

    units_by_name = {}
    for _, dp in ipairs(downed_pilots) do
        units_by_name[dp.unitName] = {
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
    CSAR = {
        New = function(_, side, template, alias)
            local inst = { lid = "" }
            function inst:__Start(_delay) end
            csar_instances[#csar_instances + 1] = inst
            return inst
        end,
    }

    dcsRetribution = {
        CSAR = {
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
        },
    }
    csar_rescued = rescued
    dirty_state = false

    assert(loadfile(SCRIPT))()
end

-- A survivor removed from (or restored to) the world between checks.
local function set_in_world(unit_name, present)
    if present then
        units_by_name[unit_name] = {
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
            getPoint = function(self)
                return { x = 0, y = 0, z = 0 }
            end,
        }
    else
        units_by_name[unit_name] = nil
    end
end

-- An AI rescue helicopter with no player aboard.
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

local function board(instance, heli_name, group_name)
    instance:OnAfterBoarded(nil, nil, nil, heli_name, group_name)
end

local function delivered(instance, heli_name, pilots_saved)
    instance:OnAfterRescued(nil, nil, nil, nil, heli_name, pilots_saved)
end

local function sorted_rescued()
    local names = {}
    for _, uuid in ipairs(csar_rescued) do
        names[#names + 1] = uuid
    end
    table.sort(names)
    return table.concat(names, ",")
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

-- C3: board -> helicopter lost -> respawn reuses the name -> replacement boards
-- a different survivor and delivers them. Only that survivor may be reported,
-- once. Before the lifetime reconciliation, the lost pilot was reported too
-- (twice, via the stale callback).
local function test_board_loss_respawn_board_deliver()
    local dp1 = pilot("uuid-lost", "Group L", "Survivor L")
    local dp2 = pilot("uuid-delivered", "Group D", "Survivor D")
    setup({ dp1, dp2 })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    -- First lifetime: survivor L boards "Helo 1".
    set_in_world("Helo 1", true)
    board(my, "Helo 1", "Group L")

    -- The helicopter is destroyed with them aboard.
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0))

    -- A delayed Boarded callback from the previous lifetime must not re-board
    -- the pilot who went down with it.
    board(my, "Helo 1", "Group L")

    -- The slot respawns under the same name; the replacement picks up its own
    -- survivor some time later (after the stale-callback window) and delivers
    -- them (PilotsSaved = 1).
    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))
    clock = clock + 30
    set_in_world("Helo 1", true)
    board(my, "Helo 1", "Group D")
    delivered(my, "Helo 1", 1)

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
    delivered(my, "Ghost Helo", 1)

    check(#csar_rescued == 0, "boarding of a nonexistent helicopter is ignored")
end

-- C3-delay: MOOSE's _PickupUnit destroys the survivor's group and only then
-- schedules Boarded 5s out, so at the moment of a helicopter loss the pickup is
-- pending, not yet in our onboard table. A loss + same-name respawn inside the
-- window must refuse the delayed Boarded for that pilot (and any delayed
-- callback must not be laundered by the respawned unit existing), while the
-- replacement's own later boarding and delivery still credit exactly its pilot.
local function test_pending_pickup_lost_then_respawn_refuses_delayed_boarding()
    local dp_l = pilot("uuid-pending", "Group P", "Survivor P")
    local dp_d = pilot("uuid-later", "Group Q", "Survivor Q")
    setup({ dp_l, dp_d })
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")

    set_in_world("Helo 1", true)
    -- MOOSE's _PickupUnit state: survivor P is in transit on Helo 1, its group
    -- destroyed, Boarded scheduled 5s out. Our onboard table has nothing yet.
    my.inTransitGroups = { ["Helo 1"] = { ["Group P"] = { player = false } } }

    -- The helicopter is lost before the delayed Boarded can run.
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0))

    -- It respawns under the same name within the window; the delayed Boarded
    -- for the pending pickup arrives and must be refused despite the (new)
    -- helicopter existing and MOOSE still listing the old pair.
    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))
    clock = clock + 2
    board(my, "Helo 1", "Group P")

    -- Much later the replacement performs a real pickup of survivor Q, which
    -- boards and delivers (PilotsSaved = 1).
    clock = clock + 30
    board(my, "Helo 1", "Group Q")
    delivered(my, "Helo 1", 1)

    check(sorted_rescued() == "uuid-later",
        "pending pickup lost with the helicopter never rescues; "
        .. "the replacement's own pilot does")
end

-- C3-hover: the event handler is registered even when every survivor needs a
-- hoist, because crewed hover rescues board helicopters whose lifetimes need
-- the same reconciling as landing-mode ones.
local function test_all_hover_mission_still_registers_lifetimes()
    local dp = pilot("uuid-hover", "Group H", "Survivor H", true)
    setup({ dp })

    check(event_handler ~= nil,
        "all-hover mission still registers the lifetime handler")

    -- And it reconciles: board on a hover mission, lose the helicopter, and the
    -- pilot cannot board again on the stale callback.
    local my = assert(csar_instances[1], "Ops.CSAR instance not built")
    set_in_world("Helo Hover", true)
    board(my, "Helo Hover", "Group H")
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo Hover", 0, 0))
    board(my, "Helo Hover", "Group H")
    delivered(my, "Helo Hover", 1)

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

local function main()
    test_landing_without_boarding_is_not_a_rescue()
    test_landing_with_boarding_still_credits()
    test_board_loss_respawn_board_deliver()
    test_boarding_for_gone_helicopter_is_ignored()
    test_pending_pickup_lost_then_respawn_refuses_delayed_boarding()
    test_all_hover_mission_still_registers_lifetimes()
    test_death_during_wait_then_takeoff_is_not_a_rescue()
    test_poll_death_is_not_a_rescue_but_embark_still_is()
    print("opscsar_rescue_evidence_test: " .. passed .. " checks passed")
end

main()
