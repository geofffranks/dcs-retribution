-- airboss_awacs_deferred_start_test.lua
--
-- Standalone unit test for the per-carrier AWACS wiring in
-- resources/plugins/airboss/airboss.lua. Each carrier's E-2D spawn callback
-- replaces the module-global awacsTED, and the RECOVERYTANKER's OnAfterStart
-- fires one second after its own __Start(1). With two deferred spawns — carrier
-- A's callback, then carrier B's callback, then A's scheduled start — the
-- callback must hand carrier A the tanker object the event fires on (self),
-- never whatever the shared global happens to point at by then.
--
-- Not run by CI (CI has no Lua). Run locally with:
--   lua    tests/missiongenerator/airboss_awacs_deferred_start_test.lua
--   luajit tests/missiongenerator/airboss_awacs_deferred_start_test.lua

local SCRIPT = "resources/plugins/airboss/airboss.lua"

-- Auto-method mock: every missing method call is a no-op returning itself, so
-- MOOSE setter chains (SetCallsign, SetAltitude, ...) run untouched.
local function automock(name)
    local obj = { _mock_name = name }
    setmetatable(obj, {
        __index = function(t, _key)
            return function(_, _)
                return t
            end
        end,
    })
    return obj
end

local spawn_callbacks -- Spawn() payloads captured per AddShipAWACS call
local tankers -- RECOVERYTANKER instances, in spawn-callback order
local airboss_by_carrier -- the instances OnAfterStart resolves through

local function setup(carriers)
    spawn_callbacks = {}
    tankers = {}
    airboss_by_carrier = {}

    AirbossInstances = {}
    for _, carrier_name in ipairs(carriers) do
        local instance = {}
        function instance:SetAWACS(tanker)
            self.awacs = tanker
        end
        AirbossInstances[carrier_name] = instance
        airboss_by_carrier[carrier_name] = instance
    end

    MESSAGE = {
        New = function(_, text, _secs, _tag, _all)
            local m = { text = text }
            function m:ToAll()
                return self
            end
            function m:ToLog()
                return self
            end
            return m
        end,
    }
    BASE = { I = function() end }
    EVENTHANDLER = {
        New = function()
            return { HandleEvent = function() end }
        end,
    }
    EVENTS = { EngineShutdown = 1, Land = 2 }
    -- AutoSetup() scans an empty blue naval set: no carriers are auto-configured,
    -- so the test drives AddShipAWACS directly for both carriers.
    SET_UNIT = {
        New = function()
            local set
            set = {
                FilterAlive = function()
                    return set
                end,
                FilterCoalitions = function()
                    return set
                end,
                FilterCategories = function()
                    return set
                end,
                FilterOnce = function()
                    return set
                end,
                FilterStart = function()
                    return set
                end,
                ForEachUnit = function(_, _fn) end,
                GetFirst = function()
                    return nil
                end,
            }
            return set
        end,
    }
    UNIT = {
        FindByName = function(_, _name)
            return automock("carrier unit")
        end,
    }
    SPAWN = {
        NewFromTemplate = function(_, _template, group_name)
            return {
                InitLateActivated = function(self)
                    return self
                end,
                InitCountry = function(self, _id)
                    return self
                end,
                InitCategory = function(self, _cat)
                    return self
                end,
                InitCoalition = function(self, _side)
                    return self
                end,
                OnSpawnGroup = function(self, fn)
                    spawn_callbacks[#spawn_callbacks + 1] = fn
                end,
                Spawn = function() end,
            }
        end,
    }
    RECOVERYTANKER = {
        New = function(_, _carrier, group_name)
            local tanker = automock("tanker:" .. group_name)
            function tanker:GetName()
                return group_name
            end
            tankers[#tankers + 1] = tanker
            return tanker
        end,
    }
    CALLSIGN = {
        AWACS = { Wizard = "Wizard" },
        Tanker = { Mauler = "Mauler" },
    }
    UTILS = {
        UniqueName = function(prefix)
            return prefix .. "-" .. tostring(#spawn_callbacks + 1)
        end,
    }
    country = { id = { USA = 2 } }
    Group = { Category = { AIRPLANE = "airplane" } }
    coalition = { side = { BLUE = "blue", RED = "red" } }
    AirbossCarriers = {}
    AirbossPendingDespawnUnit = {}
    AirbossShutdownSeenUnit = {}
    timer = {
        getTime = function()
            return 0
        end
        ,
        scheduleFunction = function() end,
    }
    env = { info = function() end, warning = function() end }

    assert(loadfile(SCRIPT))()
end

local passed = 0
local function check(cond, label)
    if not cond then
        error("FAIL: " .. label, 2)
    end
    passed = passed + 1
end

-- Two carriers' AWACS spawn callbacks run before either scheduled __Start(1)
-- fires. Each OnAfterStart must register the tanker object it fires on with its
-- own carrier, so carrier A must never receive carrier B's tanker.
local function test_two_deferred_starts_each_register_their_own_tanker()
    setup({ "Carrier A", "Carrier B" })

    assert(#spawn_callbacks == 0, "no spawn callbacks before AddShipAWACS runs")
    AddShipAWACS("Carrier A")
    AddShipAWACS("Carrier B")
    assert(#spawn_callbacks == 2, "both spawn callbacks captured")

    -- Both late-activated spawns resolve while neither scheduled Start event
    -- has fired: callback A created tanker A, callback B then replaced the
    -- shared global with tanker B. MOOSE hands each callback its spawned group;
    -- its name is concatenated into log lines, so GetName must return a string.
    local spawned_group = {
        GetName = function()
            return "E-2D Group"
        end,
    }
    spawn_callbacks[1](spawned_group)
    spawn_callbacks[2](spawned_group)
    assert(#tankers == 2, "both tankers built")
    local tanker_a, tanker_b = tankers[1], tankers[2]

    -- Carrier A's scheduled Start fires one second after its own callback —
    -- after carrier B's callback has already reassigned the global.
    local on_start_a = rawget(tanker_a, "OnAfterStart")
    assert(on_start_a, "carrier A's tanker never got its OnAfterStart handler")
    on_start_a(tanker_a, nil, nil, nil)

    check(AirbossInstances["Carrier A"].awacs == tanker_a,
        "carrier A registered the tanker its own event fired on")
    check(AirbossInstances["Carrier A"].awacs ~= tanker_b,
        "carrier A did not receive carrier B's tanker")

    -- Carrier B's Start fires afterwards and must keep working too.
    local on_start_b = rawget(tanker_b, "OnAfterStart")
    assert(on_start_b, "carrier B's tanker never got its OnAfterStart handler")
    on_start_b(tanker_b, nil, nil, nil)

    check(AirbossInstances["Carrier B"].awacs == tanker_b,
        "carrier B registered its own tanker")
end

local function main()
    test_two_deferred_starts_each_register_their_own_tanker()
    print("airboss_awacs_deferred_start_test: " .. passed .. " checks passed")
end

main()
