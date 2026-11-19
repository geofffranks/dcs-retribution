-- opscsar_rescue_evidence_test.lua
--
-- Standalone unit tests for the rescue-evidence and boarding-lifetime rules in
-- resources/plugins/opscsar/OpsCSAR.lua:
--
--   * An AI helicopter landing inside a survivor's embark zone and lifting off
--     after the minimum ground time does NOT credit a rescue unless the survivor
--     actually went from the map (DCS removes them the moment they board).
--     Ground time alone is not boarding evidence.
--   * Passengers recorded aboard a helicopter live only as long as that
--     helicopter: on its loss (or a same-name respawn) the boarding record is
--     dropped and its pilots cannot re-board via a delayed callback from the
--     previous aircraft lifetime, so a replacement aircraft's delivery reports
--     only the pilots it actually carried.
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

local function setup(downed_pilots)
    clock = 0
    event_handler = nil
    csar_instances = {}
    rescued = {}

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
        getGroups = function()
            return {}
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
        scheduleFunction = function() end,
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
local function set_survivor_in_world(unit_name, present)
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

local function pilot(id, group_name, unit_name)
    return {
        id = id,
        groupName = group_name,
        unitName = unit_name,
        coalition = "blue",
        hoverExtraction = "false",
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
    set_survivor_in_world("Survivor 2", false)
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
    set_survivor_in_world("Helo 1", true)
    board(my, "Helo 1", "Group L")

    -- The helicopter is destroyed with them aboard.
    heli_event(world_events.S_EVENT_UNIT_LOST, heli_unit("Helo 1", 0, 0))

    -- A delayed Boarded callback from the previous lifetime must not re-board
    -- the pilot who went down with it.
    board(my, "Helo 1", "Group L")

    -- The slot respawns under the same name; a fresh survivor boards it and is
    -- delivered (PilotsSaved = 1).
    heli_event(world_events.S_EVENT_BIRTH, heli_unit("Helo 1", 0, 0))
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

local function main()
    test_landing_without_boarding_is_not_a_rescue()
    test_landing_with_boarding_still_credits()
    test_board_loss_respawn_board_deliver()
    test_boarding_for_gone_helicopter_is_ignored()
    print("opscsar_rescue_evidence_test: " .. passed .. " checks passed")
end

main()
