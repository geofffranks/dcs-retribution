-- dcs_retribution_events_test.lua
--
-- Standalone unit test for the ejection/landing bookkeeping in
-- resources/plugins/base/dcs_retribution.lua. The landing-after-ejection event's
-- initiator is the parachutist and its `place` is the aircraft the pilot ejected
-- from (DCS scripting engine; see also bundled Moose.lua's CSAR handler). The
-- landing position must refine only the recorded ejection whose unit name is
-- that aircraft: an interleaved ejection from another side or aircraft keeps its
-- own (safe, at-ejection) coordinates, and a landing with no identifiable
-- aircraft mutates nothing.
--
-- Not run by CI (CI has no Lua). Run locally with:
--   lua    tests/missiongenerator/dcs_retribution_events_test.lua
--   luajit tests/missiongenerator/dcs_retribution_events_test.lua

local SCRIPT = "resources/plugins/base/dcs_retribution.lua"
local JSON = dofile("resources/plugins/base/json.lua")

local world_events = {
    S_EVENT_CRASH = 1,
    S_EVENT_UNIT_LOST = 2,
    S_EVENT_KILL = 3,
    S_EVENT_DEAD = 4,
    S_EVENT_EJECTION = 5,
    S_EVENT_LANDING_AFTER_EJECTION = 6,
    S_EVENT_MISSION_END = 7,
}

local event_handler

-- Install the mocked DCS/mist globals the script closes over, then load it.
-- The script writes its state file through debriefing_file_location, which
-- discoverDebriefingFilePath() resolves from dcsRetribution.installPath.
local function setup(tmp_dir)
    event_handler = nil
    world = { event = world_events }
    mist = {
        Logger = {
            new = function()
                return { info = function() end, warning = function() end }
            end,
        },
        message = { add = function() end },
        addEventHandler = function(fn)
            event_handler = fn
        end,
        getHeading = function()
            return 0
        end,
        scheduleFunction = function() end,
        DBs = { groupsById = {} },
    }
    timer = {
        getTime = function()
            return 1000
        end,
        scheduleFunction = function() end,
    }
    env = { info = function() end, warning = function() end }
    Group = { getByName = function() return nil end }
    dcsRetribution = { installPath = tmp_dir }
    debriefing_file_location = nil

    assert(loadfile(SCRIPT))()
    assert(event_handler, "script never registered an event handler")
end

-- A DCS aircraft unit at (x, z) whose ejection is recorded under its name.
local function aircraft(name, x, z)
    return {
        name = name,
        getName = function(self)
            return self.name
        end,
        getPosition = function(self)
            return { p = { x = x, y = 0, z = z } }
        end,
        getPoint = function(self)
            return { x = x, y = 0, z = z }
        end,
        getTypeName = function(self)
            return "FA-18C_hornet"
        end,
    }
end

local function eject(aircraft_unit)
    event_handler({ id = world.event.S_EVENT_EJECTION, initiator = aircraft_unit })
end

-- The parachutist touches down at (x, z); `place_unit` is the aircraft DCS
-- attributes the landing to, or nil when the engine provides no identity.
local function parachute_lands(x, z, place_unit)
    event_handler({
        id = world.event.S_EVENT_LANDING_AFTER_EJECTION,
        initiator = {
            getPoint = function()
                return { x = x, y = 0, z = z }
            end,
        },
        place = place_unit,
    })
end

local passed = 0
local function check(cond, label)
    if not cond then
        error("FAIL: " .. label, 2)
    end
    passed = passed + 1
end

local function ejection_for(unit_name)
    for _, ej in ipairs(ejection_events) do
        if ej.unit == unit_name then
            return ej
        end
    end
    return nil
end

-- Blue A and red B eject; A's parachutist touches down first. A's entry is
-- refined with A's landing point; B keeps its own at-ejection coordinates until
-- B's own parachutist lands. Before the correlation fix, the first landing
-- overwrote B (the newest unlanded entry) with A's position.
local function test_interleaved_blue_red_ejections_land_in_order()
    setup(os.tmpname())
    local blue_a = aircraft("Aircraft A (blue)", 100, 200)
    local red_b = aircraft("Aircraft B (red)", 300, 400)

    eject(blue_a)
    eject(red_b)

    parachute_lands(111, 222, blue_a) -- A comes down first

    local a = ejection_for("Aircraft A (blue)")
    local b = ejection_for("Aircraft B (red)")
    check(a ~= nil and a.landed and a.x == 111 and a.z == 222,
        "blue A refined with its own landing position")
    check(b ~= nil and not b.landed and b.x == 300 and b.z == 400,
        "red B keeps its at-ejection position until its own landing")

    parachute_lands(333, 444, red_b)
    b = ejection_for("Aircraft B (red)")
    check(b ~= nil and b.landed and b.x == 333 and b.z == 444,
        "red B refined with its own landing position")
    check(a.x == 111 and a.z == 222, "blue A untouched by B's landing")
end

-- A landing whose event carries no identifiable aircraft (place is nil, e.g.
-- the source aircraft was destroyed) must not rewrite any recorded ejection:
-- the at-ejection coordinates stay.
local function test_landing_without_identity_keeps_original_coords()
    setup(os.tmpname())
    local survivor = aircraft("Aircraft C", 500, 600)
    eject(survivor)

    parachute_lands(999, 999, nil)

    local c = ejection_for("Aircraft C")
    check(c ~= nil and not c.landed, "no landing recorded without identity")
    check(c.x == 500 and c.z == 600, "original ejection coordinates retained")
end

-- A landing attributed to an aircraft we never recorded must not steal the
-- position of an unrelated pending ejection.
local function test_landing_of_unknown_aircraft_mutates_nothing()
    setup(os.tmpname())
    local victim = aircraft("Aircraft D", 10, 20)
    eject(victim)

    parachute_lands(50, 60, { getName = function() return "Aircraft Z" end })

    local d = ejection_for("Aircraft D")
    check(d ~= nil and not d.landed, "unknown aircraft's landing not matched")
    check(d.x == 10 and d.z == 20, "pending ejection keeps its coordinates")
end

local function main()
    test_interleaved_blue_red_ejections_land_in_order()
    test_landing_without_identity_keeps_original_coords()
    test_landing_of_unknown_aircraft_mutates_nothing()
    print("dcs_retribution_events_test: " .. passed .. " checks passed")
end

main()
