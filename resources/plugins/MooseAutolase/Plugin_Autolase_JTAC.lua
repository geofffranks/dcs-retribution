env.info("-----DCSRetribution|MOOSE Autolase plugin - configuration start ------")

-- Defaults (overridden by dcsRetribution.plugins.MooseAutolase when present)
JtacAlphaSmoke           = true
JtacAlphaUHF             = 250
JtacAlphaVHF             = 123
JtacAlphaTargetsMax      = 1
JtacAlphaRadiusNM        = 50
JtacBravoSmoke           = true
JtacBravoUHF             = 249
JtacBravoVHF             = 122
JtacBravoTargetsMax      = 1
JtacBravoRadiusNM        = 50
DangerCloseNM            = 1
AutolaseSmokeDurationSec = 180
MooseAutolaseDebug       = false
UseConvoyChaosSFX        = true -- NEW: set false if ConvoyChaos.ogg not in mission

-- Pull from UI if available
if dcsRetribution and dcsRetribution.plugins and dcsRetribution.plugins.MooseAutolase then
    local p                  = dcsRetribution.plugins.MooseAutolase
    JtacAlphaSmoke           = p.JtacAlphaSmoke
    JtacAlphaUHF             = p.JtacAlphaUHF
    JtacAlphaVHF             = p.JtacAlphaVHF
    JtacAlphaTargetsMax      = p.JtacAlphaTargetsMax
    JtacAlphaRadiusNM        = p.JtacAlphaRadiusNM
    JtacBravoSmoke           = p.JtacBravoSmoke
    JtacBravoUHF             = p.JtacBravoUHF
    JtacBravoVHF             = p.JtacBravoVHF
    JtacBravoTargetsMax      = p.JtacBravoTargetsMax
    JtacBravoRadiusNM        = p.JtacBravoRadiusNM
    DangerCloseNM            = p.DangerCloseNM
    AutolaseSmokeDurationSec = p.AutolaseSmokeDurationSec
    MooseAutolaseDebug       = p.MooseAutolaseDebug
    if p.UseConvoyChaosSFX ~= nil then UseConvoyChaosSFX = p.UseConvoyChaosSFX end
else
    env.info("-----dcsRetribution.plugins.MooseAutolase NOT FOUND")
end

-- Debug output to DCS log
env.info("--------- JtacAlphaSmoke=" .. tostring(JtacAlphaSmoke) ..
    " | JtacAlphaUHF=" .. tostring(JtacAlphaUHF) ..
    " | JtacAlphaVHF=" .. tostring(JtacAlphaVHF) ..
    " | JtacAlphaTargetsMax=" .. tostring(JtacAlphaTargetsMax) ..
    " | JtacAlphaRadiusNM=" .. tostring(JtacAlphaRadiusNM) ..
    " | JtacBravoSmoke=" .. tostring(JtacBravoSmoke) ..
    " | JtacBravoUHF=" .. tostring(JtacBravoUHF) ..
    " | JtacBravoVHF=" .. tostring(JtacBravoVHF) ..
    " | JtacBravoTargetsMax=" .. tostring(JtacBravoTargetsMax) ..
    " | JtacBravoRadiusNM=" .. tostring(JtacBravoRadiusNM) ..
    " | DangerCloseNM=" .. tostring(DangerCloseNM) ..
    " | UseConvoyChaosSFX=" .. tostring(UseConvoyChaosSFX) ..
    " | MooseAutolaseDebug=" .. tostring(MooseAutolaseDebug) ..
    " | AutolaseSmokeDurationSec=" .. tostring(AutolaseSmokeDurationSec)
)

-----------------------------------------------------------------
-- COORDINATE:Smoke duration override (uses AutolaseSmokeDurationSec when duration omitted)
-----------------------------------------------------------------
if not _G.__CoordSmokePatched and COORDINATE and COORDINATE.Smoke then
    _G.__CoordSmokePatched = true
    COORDINATE._Smoke_original = COORDINATE._Smoke_original or COORDINATE.Smoke
    function COORDINATE:Smoke(color, duration, delay, name, offset, direction, distance)
        local useDuration = (duration ~= nil) and duration or AutolaseSmokeDurationSec
        if MooseAutolaseDebug and duration == nil then
            env.info(string.format("[AutolaseSmokePatch] default duration %ds (color=%s)", useDuration, tostring(color)))
        end
        return COORDINATE._Smoke_original(self, color, useDuration, delay, name, offset, direction, distance)
    end
end

-- Safe AM constant
local AM = (radio and radio.modulation and radio.modulation.AM) or 0

-----------------------------------------------------------------
-- JTAC target-prioritization config (Part 1)
-- File-scope so the monkeypatch closure below can capture these.
-----------------------------------------------------------------
local _p = (dcsRetribution and dcsRetribution.plugins and dcsRetribution.plugins.MooseAutolase) or nil
local TierSamThreats     = (_p and _p.TierSamThreats)     or 1
local TierGuidedAaa      = (_p and _p.TierGuidedAaa)      or 2
local TierArtillery      = (_p and _p.TierArtillery)      or 3
local TierUnguidedAaa    = (_p and _p.TierUnguidedAaa)    or 3
local TierArmorLaunchers = (_p and _p.TierArmorLaunchers) or 4
local TierOther          = (_p and _p.TierOther)          or 5

MooseAutolaseUnitClasses        = (_p and _p.UnitClasses)        or {}
MooseAutolaseFrontlines         = (_p and _p.Frontlines)         or {}
MooseAutolaseDefaultCorridorM   = (_p and _p.DefaultCorridorM)   or 8000
MooseAutolaseArtilleryCorridorM = (_p and _p.ArtilleryCorridorM) or 18000

-- Fixed UnitClass -> category map (categories carry the configurable tier).
local CategoryByClass = {
    TrackRadar = "SamThreats", SearchTrackRadar = "SamThreats",
    TELAR = "SamThreats", SpecializedRadar = "SamThreats",
    OpticalTracker = "SamThreats", Manpad = "SamThreats", SHORAD = "SamThreats",
    Artillery = "Artillery",
    Tank = "ArmorLaunchers", APC = "ArmorLaunchers", IFV = "ArmorLaunchers",
    ATGM = "ArmorLaunchers", Launcher = "ArmorLaunchers",
    -- AAA is split at runtime (see jtacCategoryFor); everything else -> Other.
}

local TierByCategory = {
    SamThreats = TierSamThreats, GuidedAaa = TierGuidedAaa,
    Artillery = TierArtillery, UnguidedAaa = TierUnguidedAaa,
    ArmorLaunchers = TierArmorLaunchers, Other = TierOther,
}

local function jtacCategoryFor(unit)
    local cls = MooseAutolaseUnitClasses[unit:GetTypeName()]
    if cls == "AAA" then
        if unit:HasAttribute("RADAR_BAND1_FOR_ARM")
            or unit:HasAttribute("RADAR_BAND2_FOR_ARM")
            or unit:HasAttribute("Optical Tracker") then
            return "GuidedAaa"
        end
        return "UnguidedAaa"
    end
    return CategoryByClass[cls or ""] or "Other"
end

local function jtacTierFor(category)
    return TierByCategory[category] or TierOther
end

-- Perpendicular distance (m) from point P to segment AB, all DCS vec2 {x,y}.
local function pointSegmentDistance(px, py, ax, ay, bx, by)
    local dx, dy = bx - ax, by - ay
    local len2 = dx * dx + dy * dy
    local t = 0
    if len2 > 0 then
        t = ((px - ax) * dx + (py - ay) * dy) / len2
        if t < 0 then t = 0 elseif t > 1 then t = 1 end
    end
    local cx, cy = ax + t * dx, ay + t * dy
    local ex, ey = px - cx, py - cy
    return math.sqrt(ex * ex + ey * ey)
end

-----------------------------------------------------------------
-- Part 2: monkeypatch AUTOLASE:onafterMonitor with tier + frontline-box
-- selection. Guarded exactly like the COORDINATE:Smoke patch above.
-----------------------------------------------------------------
if not _G.__AutolaseTierPatched and AUTOLASE then
    _G.__AutolaseTierPatched = true
    AUTOLASE._onafterMonitor_tierOriginal = AUTOLASE.onafterMonitor

    function AUTOLASE:onafterMonitor(From, Event, To)
        self:T({From, Event, To})
        -- Fall back to stock behaviour when this instance has no frontline
        -- segment or the class table is missing (e.g. >2 JTACs, no front).
        if not self._frontlineSegment or not next(MooseAutolaseUnitClasses) then
            return AUTOLASE._onafterMonitor_tierOriginal(self, From, Event, To)
        end

        self:CleanCurrentLasing()
        self:SetPilotMenu()

        local seg = self._frontlineSegment
        local boxed, unboxed = {}, {}

        -- Stock onafterMonitor maintains GroupsByThreat/UnitsByThreat/RecceNames/
        -- RecceUnitNames, but those are single-cycle scratch read only within stock
        -- onafterMonitor (which this patch fully replaces). The pilot Status menu and
        -- CheckIsLased/CleanCurrentLasing run off self.CurrentLasing, which we still
        -- populate in the lasing loop below, so we don't rebuild the unused tables.
        for _, contact in pairs(self.Contacts or {}) do
            local grp = contact.group
            if grp and grp:IsGround() then
                local units = grp:GetUnits() or {}
                for _, unit in pairs(units) do
                    if unit and unit:IsAlive() then
                        local category = jtacCategoryFor(unit)
                        local tier = jtacTierFor(category)
                        if tier > 0 then  -- 0 = Don't lase
                            local vec = unit:GetVec2()
                            local corridor = (category == "Artillery")
                                and MooseAutolaseArtilleryCorridorM
                                or MooseAutolaseDefaultCorridorM
                            local dist = pointSegmentDistance(
                                vec.x, vec.y, seg.a.x, seg.a.y, seg.b.x, seg.b.y)
                            local entry = {
                                unit = unit, tier = tier,
                                threat = unit:GetThreatLevel() or 0,
                            }
                            table.insert(unboxed, entry)
                            if dist <= corridor then
                                table.insert(boxed, entry)
                            end
                        end
                    end
                end
            end
        end

        -- Box-empty fallback: open up to everything in lase range.
        local chosen = (#boxed > 0) and boxed or unboxed

        -- Highest priority first: lowest tier number, then highest threat.
        table.sort(chosen, function(a, b)
            if a.tier ~= b.tier then return a.tier < b.tier end
            return a.threat > b.threat
        end)

        for _, detectingunit in pairs(self.RecceUnits) do
            local reccename = detectingunit.name
            local recce = detectingunit.unit
            local reccecount = self.targetsperrecce[reccename] or 0
            local targets = 0
            for _, entry in pairs(chosen) do
                local unit = entry.unit
                local unitname = unit:GetName()
                if targets + reccecount < self.maxlasing
                    and not self:CheckIsLased(unitname)
                    and unit:IsAlive()
                    and self:CanLase(recce, unit) then
                    targets = targets + 1
                    local code = self:GetLaserCode(reccename)
                    local spot = SPOT:New(recce)
                    spot:LaseOn(unit, code, self.LaseDuration)
                    local locationstring = unit:GetCoordinate():ToStringLLDDM()
                    if _SETTINGS:IsA2G_MGRS() then
                        local precision = _SETTINGS:GetMGRS_Accuracy()
                        local settings = {}
                        settings.MGRS_Accuracy = precision
                        locationstring = unit:GetCoordinate():ToStringMGRS(settings)
                    elseif _SETTINGS:IsA2G_LL_DMS() then
                        locationstring = unit:GetCoordinate():ToStringLLDMS(_SETTINGS)
                    elseif _SETTINGS:IsA2G_BR() then
                        locationstring = unit:GetCoordinate():ToStringBULLS(self.coalition, _SETTINGS)
                    end
                    local laserspot = {
                        laserspot = spot, lasedunit = unit, lasingunit = recce,
                        lasercode = code, location = locationstring,
                        timestamp = timer.getAbsTime(),
                        unitname = unitname, reccename = reccename,
                        unittype = unit:GetTypeName(),
                        coordinate = unit:GetCoordinate(),
                    }
                    if self.smoketargets then
                        local coord = unit:GetCoordinate()
                        if self.smokeoffset then
                            coord:Translate(self.smokeoffset.Distance, self.smokeoffset.Angle, true, true)
                        end
                        local color = self:GetSmokeColor(reccename)
                        coord:Smoke(color)
                    end
                    self.lasingindex = self.lasingindex + 1
                    self.CurrentLasing[self.lasingindex] = laserspot
                    self:__Lasing(2, laserspot)
                end
            end
        end

        local nextloop = -self.MonitorFrequency or -30
        self:__Monitor(nextloop)
        return self
    end
end

-- Utility: format integer values with thousands separators (e.g., 12,345)
local function FormatWithCommas(n)
    local s = tostring(math.floor(n or 0))
    local left, num, right = s:match('^([^%d]*%d)(%d*)(.-)$')
    return left .. (num:reverse():gsub('(%d%d%d)', '%1,'):reverse()) .. right
end

-- Per-file durations (seconds). Adjust as needed.
local SFX_DURATION_DEFAULT = 5.0
local SFX_DURATIONS = {
    ["ConvoyChaos.ogg"] = 6.0,
    ["LaserOff.ogg"]    = 1.0,
    ["LaserOn.ogg"]     = 1.0,
    ["TargetLost.ogg"]  = 1.0,
    ["TargetSmoke.ogg"] = 5.0, -- assumed from your note
}
local function GetSfxDuration(sfx)
    return SFX_DURATIONS[sfx] or SFX_DURATION_DEFAULT
end

-- Unified transmitter (uses the JTAC’s own unit(1))
local function TransmitRadio(forGroup, freqMHz, sfx, text, dbgTag)
    local unit = forGroup and forGroup:GetUnit(1)
    if not unit then
        if MooseAutolaseDebug then env.info(string.format("[%s] No unit to transmit on", dbgTag or "TX")) end
        return
    end
    local Radio = unit:GetRadio()
    if not Radio then
        if MooseAutolaseDebug then env.info(string.format("[%s] No Radio on %s", dbgTag or "TX", unit:GetName())) end
        return
    end
    Radio:SetFrequency(freqMHz)
    Radio:SetModulation(AM)
    Radio:SetPower(100)
    Radio:SetFileName(sfx)
    Radio:SetSubtitle(text, 60)
    Radio:Broadcast()
    if MooseAutolaseDebug then
        env.info(string.format("[%s] tx @ %.3f MHz AM via %s (sfx=%s)", dbgTag or "TX", freqMHz, unit:GetName(), sfx))
    end
end

-----------------------------------------------------------------
------ ALPHA
-----------------------------------------------------------------
if GROUP:FindByName("JTAC Alpha") then
    if MooseAutolaseDebug then env.info("------JTAC Alpha Located-------") end

    local JtacAlpha            = GROUP:FindByName("JTAC Alpha")
    local JtacAlphaCoord       = JtacAlpha:GetCoordinate()
    local AlphaDroneUnit       = JtacAlpha:GetUnit(1)
    local JtacAlphaFlightgroup = FLIGHTGROUP:New(JtacAlpha)
    local JtacAlphaName        = JtacAlpha:GetName()

    if MooseAutolaseDebug then
        MESSAGE:New("SETTING UP JTAC ALPHA FOR AUTOLASE (MOOSE)", 5, "RETRIBUTION", false):ToAll():ToLog()
    end

    local AlphaRacetrack = AUFTRAG:NewORBIT_CIRCLE(JtacAlphaCoord, 20000, 120)
    JtacAlphaFlightgroup:SetDefaultInvisible(true)
    JtacAlphaFlightgroup:SetDefaultImmortal(true)
    JtacAlphaFlightgroup:AddMission(AlphaRacetrack)

    local function _DumpMissionQueue(tag)
        local lines = {}
        local queue = JtacAlphaFlightgroup and JtacAlphaFlightgroup.missionqueue or nil

        local function safe(call, default)
            local ok, val = pcall(call); return ok and val or default
        end
        local function _describeAuftrag(idx, auf)
            local name   = safe(function() return auf:GetName() end,
                safe(function() return auf.Name end, safe(function() return auf.name end, "AUFTRAG")))
            local prio   = safe(function() return auf:GetPriority() end,
                safe(function() return auf.Priority end, safe(function() return auf.prio end, "?")))
            local urgent = safe(function() return tostring(auf:GetUrgent()) end,
                safe(function() return tostring(auf.Urgent) end, safe(function() return tostring(auf.urgent) end, "?")))
            return string.format("%02d) %s  [prio=%s, urgent=%s]", idx, tostring(name), tostring(prio), tostring(urgent))
        end

        if type(queue) == "table" then
            local idx = 0
            for i, auf in ipairs(queue) do
                idx = i; table.insert(lines, _describeAuftrag(i, auf))
            end
            for k, auf in pairs(queue) do
                if type(k) ~= "number" or k < 1 or k > idx then
                    table.insert(lines,
                        _describeAuftrag(#lines + 1, auf))
                end
            end
        else
            table.insert(lines, "(missionqueue not available on this FLIGHTGROUP)")
        end

        local text = string.format("JTAC %s MissionQueue — %s\n%s", JtacAlphaName, tag or "update",
            table.concat(lines, "\n"))
        if MooseAutolaseDebug then MESSAGE:New(text, 15, "Alpha Queue"):ToAll() end
    end

    local Alpha_AutolaseSet = SET_GROUP:New():FilterPrefixes("Alpha"):FilterCoalitions("blue"):FilterOnce()
    Alpha_AutolaseSet:AddGroup(JtacAlpha)

    local Alpha_DroneZone                          = ZONE_GROUP:New("Alpha_DroneZone", JtacAlpha,
        1852 * JtacAlphaRadiusNM)
    local Alpha_PilotSet                           = SET_CLIENT:New():FilterCoalitions("blue"):FilterZones({
        Alpha_DroneZone }):FilterActive():FilterStart()

    local Alpha_RetributionAutolase                = AUTOLASE:New(Alpha_AutolaseSet, coalition.side.BLUE,
            JtacAlphaName .. " Autolase", Alpha_PilotSet)
        :SetMaxLasingTargets(JtacAlphaTargetsMax)
        :SetLasingParameters(15000, AutolaseSmokeDurationSec)
        :SetNotifyPilots(false)
        -- Tier selection replaces stock min-threat filtering; hide the now-inert
        -- F10 "Set min lasing threat" menu (our onafterMonitor ignores minthreatlevel).
        :DisableThreatLevelMenu()
        :SetSmokeTargets(JtacAlphaSmoke, SMOKECOLOR.Red)
        :EnableSmokeMenu({ Angle = 30, Distance = 40 })

    Alpha_RetributionAutolase._currentOrbitAuftrag = AlphaRacetrack
    Alpha_RetributionAutolase._homeCoord           = JtacAlphaCoord
    Alpha_RetributionAutolase._altHomeFt           = 20000
    Alpha_RetributionAutolase._altTgtFt            = 18000
    Alpha_RetributionAutolase._orbitSpeedKts       = 120
    Alpha_RetributionAutolase._lastLaserInfo       = {}
    Alpha_RetributionAutolase._frontlineSegment    = MooseAutolaseFrontlines[JtacAlphaName]

    function Alpha_RetributionAutolase:_ReplaceOrbitAt(coord, altFt, reasonTag)
        if not coord then return end
        if self._currentOrbitAuftrag then
            pcall(function() self._currentOrbitAuftrag:Cancel() end); self._currentOrbitAuftrag = nil
        end
        local newOrbit = AUFTRAG:NewORBIT_CIRCLE(coord, altFt or self._altTgtFt, self._orbitSpeedKts)
        newOrbit:SetPriority(1, true, 1)
        JtacAlphaFlightgroup:AddMission(newOrbit)
        self._currentOrbitAuftrag = newOrbit
        if MooseAutolaseDebug then
            env.info(string.format("JTAC %s orbit RECENTERED at %s (alt %d ft) [%s]",
                JtacAlphaName, coord:ToStringMGRS(), altFt or self._altTgtFt, tostring(reasonTag or "update")))
        end
        _DumpMissionQueue(reasonTag or "orbit retask")
    end

    function Alpha_RetributionAutolase:OnAfterLasing(From, Event, To, LaserSpot)
        if MooseAutolaseDebug then env.info(JtacAlphaName .. " ------ Laser On!") end

        self._lastLaserInfo = {
            code      = LaserSpot and LaserSpot.lasercode or 0,
            mgrs      = (LaserSpot and LaserSpot.coordinate) and LaserSpot.coordinate:ToStringMGRS() or "N/A",
            unittype  = LaserSpot and LaserSpot.unittype or "Unknown",
            reccename = LaserSpot and LaserSpot.reccename or "Unknown"
        }

        if LaserSpot and LaserSpot.coordinate then
            self:_ReplaceOrbitAt(LaserSpot.coordinate, self._altTgtFt, "new lase")
        else
            self:_ReplaceOrbitAt(self._homeCoord, self._altHomeFt, "no coord")
        end

        -- pilot notifications (+ danger-close friendlies + BR fix + sound selection)
        Alpha_PilotSet:ForEachClient(function(client)
            if client and client:IsAlive() then
                local laserspot = LaserSpot
                if laserspot and laserspot.coordinate then
                    local clientCoord             = client:GetCoordinate()
                    local BRInfo                  = laserspot.coordinate:ToStringBR(clientCoord)
                    local BRInfoClean             = (BRInfo or "N/A"):gsub("^%s*:%s*", "")

                    -- DANGER CLOSE detection
                    local dangerNote, dangerClose = "", false
                    do
                        local tgtCoord          = laserspot.coordinate
                        local DC_RADIUS_M       = DangerCloseNM * 1852
                        local jtacSide          = JtacAlpha:GetCoalition()
                        local sideStr           = (jtacSide == coalition.side.RED and "red")
                            or (jtacSide == coalition.side.BLUE and "blue")
                            or "neutral"
                        local nearestCoord, nearestDist

                        local FriendlyGroundSet = SET_GROUP:New()
                            :FilterCoalitions(sideStr)
                            :FilterActive()
                            :FilterStart()

                        FriendlyGroundSet:ForEachGroup(function(g)
                            if g:IsGround() then
                                local c = g:GetCoordinate()
                                local d = c and tgtCoord and c:Get2DDistance(tgtCoord) or nil
                                if d and d <= DC_RADIUS_M and (not nearestDist or d < nearestDist) then
                                    nearestDist, nearestCoord = d, c
                                end
                            end
                        end)

                        if nearestCoord and nearestDist then
                            dangerClose = true
                            nearestCoord:Smoke(SMOKECOLOR.Green, AutolaseSmokeDurationSec)

                            local brtxt    = tgtCoord:ToStringBR(nearestCoord) or ""
                            local bearing  = tonumber(brtxt:match("(%d+)")) or 0
                            local dirs     = { "north", "northeast", "east", "southeast", "south", "southwest", "west",
                                "northwest" }
                            local idx      = (math.floor((bearing + 22.5) / 45) % 8) + 1
                            local cardinal = dirs[idx]

                            local feet     = math.floor(nearestDist * 3.28084 + 0.5)
                            local feetStr  = (feet >= 1000) and FormatWithCommas(feet) or tostring(feet)

                            dangerNote     = string.format(
                                "\nFriendlies are DANGER CLOSE, %s feet %s of target, marking with green smoke.",
                                feetStr, cardinal)
                        end
                    end

                    local text = string.format(
                        "%s is lasing %s code %d\nat %s\n%s%s",
                        laserspot.reccename or "Unknown",
                        laserspot.unittype or "Unknown",
                        laserspot.lasercode or 0,
                        laserspot.coordinate:ToStringMGRS(),
                        BRInfoClean,
                        dangerNote
                    )

                    -- choose SFX (unchanged from your working logic)
                    local function pickSFX(smokeOn)
                        if dangerClose and UseConvoyChaosSFX then return "ConvoyChaos.ogg" end
                        return (smokeOn and "TargetSmoke.ogg") or "LaserOn.ogg"
                    end

                    -- choose SFX exactly as you already do
                    local function pickSFX(smokeOn)
                        if dangerClose and UseConvoyChaosSFX then return "ConvoyChaos.ogg" end
                        return (smokeOn and "TargetSmoke.ogg") or "LaserOn.ogg"
                    end
                    local sfx = pickSFX(Alpha_RetributionAutolase.smoketargets)
                    local firstDur = GetSfxDuration(sfx)

                    -- UHF now
                    TransmitRadio(JtacAlpha, JtacAlphaUHF, sfx, text, "ALPHA UHF")

                    -- VHF after UHF completes (+pad)
                    TIMER:New(function()
                        TransmitRadio(JtacAlpha, JtacAlphaVHF, sfx, text, "ALPHA VHF")
                    end):Start(firstDur + 0.15)

                    MESSAGE:New(text, 60, "Alpha"):ToLog()
                end
            end
        end)
    end

    function Alpha_RetributionAutolase:OnAfterTargetDestroyed(From, Event, To, UnitName, RecceName)
        env.info("----JTAC Alpha's target destroyed, returning to home orbit-----")
        self:_ReplaceOrbitAt(self._homeCoord, self._altHomeFt, "target destroyed")
    end

    function Alpha_RetributionAutolase:OnAfterTargetLost(From, Event, To, UnitName, RecceName)
        if MooseAutolaseDebug then env.info(JtacAlphaName .. " ------ Target Lost!") end

        Alpha_PilotSet:ForEachClient(function(client)
            if client and client:IsAlive() then
                local info = self._lastLaserInfo or {}
                local text = string.format("%s LOST TARGET\n%s (code %d) at %s", info.reccename or RecceName,
                    info.unittype or "Unknown", info.code or 0, info.mgrs or "Unknown")

                -- match your previous logic: TargetLost if smoke on, else LaserOff
                local sfx = (Alpha_RetributionAutolase.smoketargets and "TargetLost.ogg") or "LaserOff.ogg"
                local firstDur = GetSfxDuration(sfx)

                TransmitRadio(JtacAlpha, JtacAlphaUHF, sfx, text, "ALPHA UHF LOST")

                TIMER:New(function()
                    TransmitRadio(JtacAlpha, JtacAlphaVHF, sfx, text, "ALPHA VHF LOST")
                end):Start(firstDur + 0.15)

                MESSAGE:New(text, 60, "Alpha"):ToLog()
            end
        end)

        self:_ReplaceOrbitAt(self._homeCoord, self._altHomeFt, "target lost")
    end

    if AlphaDroneUnit then
        local unitName  = AlphaDroneUnit:GetName()
        local laserCode = 1688
        Alpha_RetributionAutolase:SetRecceLaserCode(unitName, laserCode)
        Alpha_RetributionAutolase:SetRecceSmokeColor(unitName, SMOKECOLOR.Red)
        if MooseAutolaseDebug then env.info(string.format("%s assigned laser code %d", unitName, laserCode)) end
    end

    _DumpMissionQueue("initial")
else
    if MooseAutolaseDebug then env.info("------JTAC Alpha NOT Located-------") end
end

-----------------------------------------------------------------
------ BRAVO
-----------------------------------------------------------------
if GROUP:FindByName("JTAC Bravo") then
    if MooseAutolaseDebug then env.info("------JTAC Bravo Located-------") end

    local JtacBravo            = GROUP:FindByName("JTAC Bravo")
    local JtacBravoCoord       = JtacBravo:GetCoordinate()
    local BravoDroneUnit       = JtacBravo:GetUnit(1)
    local JtacBravoName        = JtacBravo:GetName()
    local JtacBravoFlightgroup = FLIGHTGROUP:New(JtacBravo)

    if MooseAutolaseDebug then
        MESSAGE:New("SETTING UP JTAC BRAVO FOR AUTOLASE (MOOSE)", 5, "RETRIBUTION", false):ToAll():ToLog()
    end

    local BravoRacetrack = AUFTRAG:NewORBIT_CIRCLE(JtacBravoCoord, 15000, 120)
    JtacBravoFlightgroup:SetDefaultInvisible(true)
    JtacBravoFlightgroup:SetDefaultImmortal(true)
    JtacBravoFlightgroup:AddMission(BravoRacetrack)

    local function _DumpMissionQueue(tag)
        local lines = {}
        local queue = JtacBravoFlightgroup and JtacBravoFlightgroup.missionqueue or nil
        local function safe(call, default)
            local ok, val = pcall(call); return ok and val or default
        end
        local function _describeAuftrag(idx, auf)
            local name   = safe(function() return auf:GetName() end,
                safe(function() return auf.Name end, safe(function() return auf.name end, "AUFTRAG")))
            local prio   = safe(function() return auf:GetPriority() end,
                safe(function() return auf.Priority end, safe(function() return auf.prio end, "?")))
            local urgent = safe(function() return tostring(auf:GetUrgent()) end,
                safe(function() return tostring(auf.Urgent) end, safe(function() return tostring(auf.urgent) end, "?")))
            return string.format("%02d) %s  [prio=%s, urgent=%s]", idx, tostring(name), tostring(prio), tostring(urgent))
        end
        if type(queue) == "table" then
            local idx = 0
            for i, auf in ipairs(queue) do
                idx = i; table.insert(lines, _describeAuftrag(i, auf))
            end
            for k, auf in pairs(queue) do
                if type(k) ~= "number" or k < 1 or k > idx then
                    table.insert(lines,
                        _describeAuftrag(#lines + 1, auf))
                end
            end
        else
            table.insert(lines, "(missionqueue not available on this FLIGHTGROUP)")
        end
        local text = string.format("JTAC %s MissionQueue — %s\n%s", JtacBravoName, tag or "update",
            table.concat(lines, "\n"))
        if MooseAutolaseDebug then MESSAGE:New(text, 15, "Bravo Queue"):ToAll() end
    end

    local Bravo_AutolaseSet = SET_GROUP:New():FilterPrefixes("Bravo"):FilterCoalitions("blue"):FilterOnce()
    Bravo_AutolaseSet:AddGroup(JtacBravo)

    local Bravo_DroneZone                          = ZONE_GROUP:New("Bravo_DroneZone", JtacBravo,
        1852 * JtacBravoRadiusNM)
    local Bravo_PilotSet                           = SET_CLIENT:New():FilterCoalitions("blue"):FilterZones({
        Bravo_DroneZone }):FilterActive():FilterStart()

    local Bravo_RetributionAutolase                = AUTOLASE:New(Bravo_AutolaseSet, coalition.side.BLUE,
            JtacBravoName .. " Autolase", Bravo_PilotSet)
        :SetMaxLasingTargets(JtacBravoTargetsMax)
        :SetLasingParameters(10000, AutolaseSmokeDurationSec)
        :SetNotifyPilots(false)
        -- Tier selection replaces stock min-threat filtering; hide the now-inert
        -- F10 "Set min lasing threat" menu (our onafterMonitor ignores minthreatlevel).
        :DisableThreatLevelMenu()
        :SetSmokeTargets(JtacBravoSmoke, SMOKECOLOR.Red)
        :EnableSmokeMenu({ Angle = 30, Distance = 40 })

    Bravo_RetributionAutolase._currentOrbitAuftrag = BravoRacetrack
    Bravo_RetributionAutolase._homeCoord           = JtacBravoCoord
    Bravo_RetributionAutolase._altHomeFt           = 15000
    Bravo_RetributionAutolase._altTgtFt            = 16000
    Bravo_RetributionAutolase._orbitSpeedKts       = 120
    Bravo_RetributionAutolase._lastLaserInfo       = {}
    Bravo_RetributionAutolase._frontlineSegment    = MooseAutolaseFrontlines[JtacBravoName]

    function Bravo_RetributionAutolase:_ReplaceOrbitAt(coord, altFt, reasonTag)
        if not coord then return end
        if self._currentOrbitAuftrag then
            pcall(function() self._currentOrbitAuftrag:Cancel() end); self._currentOrbitAuftrag = nil
        end
        local newOrbit = AUFTRAG:NewORBIT_CIRCLE(coord, altFt or self._altTgtFt, self._orbitSpeedKts)
        newOrbit:SetPriority(1, true, 1)
        JtacBravoFlightgroup:AddMission(newOrbit)
        self._currentOrbitAuftrag = newOrbit
        if MooseAutolaseDebug then
            env.info(string.format("JTAC %s orbit RECENTERED at %s (alt %d ft) [%s]",
                JtacBravoName, coord:ToStringMGRS(), altFt or self._altTgtFt, tostring(reasonTag or "update")))
        end
        _DumpMissionQueue(reasonTag or "orbit retask")
    end

    function Bravo_RetributionAutolase:OnAfterLasing(From, Event, To, LaserSpot)
        if MooseAutolaseDebug then env.info(JtacBravoName .. " ------ Laser On!") end

        self._lastLaserInfo = {
            code      = LaserSpot and LaserSpot.lasercode or 0,
            mgrs      = (LaserSpot and LaserSpot.coordinate) and LaserSpot.coordinate:ToStringMGRS() or "N/A",
            unittype  = LaserSpot and LaserSpot.unittype or "Unknown",
            reccename = LaserSpot and LaserSpot.reccename or "Unknown"
        }

        if LaserSpot and LaserSpot.coordinate then
            self:_ReplaceOrbitAt(LaserSpot.coordinate, self._altTgtFt, "new lase")
        else
            self:_ReplaceOrbitAt(self._homeCoord, self._altHomeFt, "no coord")
        end

        -- pilot notifications (+ danger-close friendlies + BR fix + sound selection)
        Bravo_PilotSet:ForEachClient(function(client)
            if client and client:IsAlive() then
                local laserspot = LaserSpot
                if laserspot and laserspot.coordinate then
                    local clientCoord             = client:GetCoordinate()
                    local BRInfo                  = laserspot.coordinate:ToStringBR(clientCoord)
                    local BRInfoClean             = (BRInfo or "N/A"):gsub("^%s*:%s*", "")

                    -- DANGER CLOSE detection
                    local dangerNote, dangerClose = "", false
                    do
                        local tgtCoord          = laserspot.coordinate
                        local DC_RADIUS_M       = DangerCloseNM * 1852
                        local jtacSide          = JtacBravo:GetCoalition()
                        local sideStr           = (jtacSide == coalition.side.RED and "red")
                            or (jtacSide == coalition.side.BLUE and "blue")
                            or "neutral"
                        local nearestCoord, nearestDist

                        local FriendlyGroundSet = SET_GROUP:New()
                            :FilterCoalitions(sideStr)
                            :FilterActive()
                            :FilterStart()

                        FriendlyGroundSet:ForEachGroup(function(g)
                            if g:IsGround() then
                                local c = g:GetCoordinate()
                                local d = c and tgtCoord and c:Get2DDistance(tgtCoord) or nil
                                if d and d <= DC_RADIUS_M and (not nearestDist or d < nearestDist) then
                                    nearestDist, nearestCoord = d, c
                                end
                            end
                        end)

                        if nearestCoord and nearestDist then
                            dangerClose = true
                            nearestCoord:Smoke(SMOKECOLOR.Green, AutolaseSmokeDurationSec)

                            local brtxt    = tgtCoord:ToStringBR(nearestCoord) or ""
                            local bearing  = tonumber(brtxt:match("(%d+)")) or 0
                            local dirs     = { "north", "northeast", "east", "southeast", "south", "southwest", "west",
                                "northwest" }
                            local idx      = (math.floor((bearing + 22.5) / 45) % 8) + 1
                            local cardinal = dirs[idx]

                            local feet     = math.floor(nearestDist * 3.28084 + 0.5)
                            local feetStr  = (feet >= 1000) and FormatWithCommas(feet) or tostring(feet)

                            dangerNote     = string.format(
                                "\nFriendlies are DANGER CLOSE, %s feet %s of target, marking with green smoke.",
                                feetStr, cardinal)
                        end
                    end

                    local text = string.format(
                        "%s is lasing %s code %d\nat %s\n%s%s",
                        laserspot.reccename or "Unknown",
                        laserspot.unittype or "Unknown",
                        laserspot.lasercode or 0,
                        laserspot.coordinate:ToStringMGRS(),
                        BRInfoClean,
                        dangerNote
                    )

                    local function pickSFX(smokeOn)
                        if dangerClose and UseConvoyChaosSFX then return "ConvoyChaos.ogg" end
                        return (smokeOn and "TargetSmoke.ogg") or "LaserOn.ogg"
                    end

                    local function pickSFX(smokeOn)
                        if dangerClose and UseConvoyChaosSFX then return "ConvoyChaos.ogg" end
                        return (smokeOn and "TargetSmoke.ogg") or "LaserOn.ogg"
                    end
                    local sfx = pickSFX(Bravo_RetributionAutolase.smoketargets)
                    local firstDur = GetSfxDuration(sfx)

                    TransmitRadio(JtacBravo, JtacBravoUHF, sfx, text, "BRAVO UHF")

                    TIMER:New(function()
                        TransmitRadio(JtacBravo, JtacBravoVHF, sfx, text, "BRAVO VHF")
                    end):Start(firstDur + 0.15)

                    MESSAGE:New(text, 60, "Bravo"):ToLog()
                end
            end
        end)
    end

    function Bravo_RetributionAutolase:OnAfterTargetDestroyed(From, Event, To, UnitName, RecceName)
        env.info("----JTAC Bravo's target destroyed, returning to home orbit-----")
        self:_ReplaceOrbitAt(self._homeCoord, self._altHomeFt, "target destroyed")
    end

    function Bravo_RetributionAutolase:OnAfterTargetLost(From, Event, To, UnitName, RecceName)
        if MooseAutolaseDebug then env.info(JtacBravoName .. " ------ Target Lost!") end

        Bravo_PilotSet:ForEachClient(function(client)
            if client and client:IsAlive() then
                local info = self._lastLaserInfo or {}
                local text = string.format("%s LOST TARGET\n%s (code %d) at %s", info.reccename or RecceName,
                    info.unittype or "Unknown", info.code or 0, info.mgrs or "Unknown")

                local sfx = (Bravo_RetributionAutolase.smoketargets and "TargetLost.ogg") or "LaserOff.ogg"
                local firstDur = GetSfxDuration(sfx)

                TransmitRadio(JtacBravo, JtacBravoUHF, sfx, text, "BRAVO UHF LOST")

                TIMER:New(function()
                    TransmitRadio(JtacBravo, JtacBravoVHF, sfx, text, "BRAVO VHF LOST")
                end):Start(firstDur + 0.15)

                MESSAGE:New(text, 60, "Bravo"):ToLog()
            end
        end)

        self:_ReplaceOrbitAt(self._homeCoord, self._altHomeFt, "target lost")
    end

    if BravoDroneUnit then
        local unitName  = BravoDroneUnit:GetName()
        local laserCode = 1688
        Bravo_RetributionAutolase:SetRecceLaserCode(unitName, laserCode)
        Bravo_RetributionAutolase:SetRecceSmokeColor(unitName, SMOKECOLOR.Red)
        if MooseAutolaseDebug then env.info(string.format("%s assigned laser code %d", unitName, laserCode)) end
    end

    _DumpMissionQueue("initial")
else
    if MooseAutolaseDebug then env.info("------JTAC Bravo NOT Located-------") end
end

env.info("-----DCSRetribution|MOOSE Autolase plugin - configuration end ------")
