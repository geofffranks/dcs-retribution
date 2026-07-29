# Task 4 report: Apply tanker orbit speed to generation paths

## Implemented
- Ordinary `REFUELING` racetrack generation now calls the shared `select_tanker_orbit_speed` policy and converts the selected speed to KPH for `OrbitAction`.
- Ordinary Auto receiver candidates are limited to other flights in the tanker flight's existing package; the tanker itself is excluded. The existing patrol speed remains the baseline.
- Recovery `RecoveryTanker` generation now calls the same shared policy and converts the selected speed to m/s. Recovery has no explicit receiver relationship in the current model, so it supplies no receiver candidates and retains the existing 250-KIAS baseline. Manual props still override this baseline.
- No runtime controller, callback, event handler, retasking, route, timing, altitude, fuel predicate, or task behavior was added or changed.

## TDD evidence
### RED
Command:
`PATH=".venv/bin:$PATH" rtk pytest tests/missiongenerator/aircraft/test_tanker_orbit_speed.py`

Result: exit status 1 with no RTK diagnostic output. The tests intentionally referenced the not-yet-added `RaceTrackBuilder.tanker_orbit_speed` and `AircraftBehavior.tanker_orbit_speed` helpers.

### GREEN
Command:
`PATH="/home/dev/workspace/dcs-retribution/.venv/bin:$PATH" pytest -q tests/missiongenerator/aircraft/test_tanker_orbit_speed.py`

Result: `5 passed in 1.53s`.

Coverage includes ordinary slowest package receiver selection, ordinary manual override, ordinary baseline fallback, recovery manual override, and recovery baseline fallback without an explicit recovery relationship.

## Verification
- Focused tests: 5 passed.
- Strict mypy on changed production files and focused tests: passed (`Success: no issues found in 3 source files`).
- Black check on changed files: passed.
- `git diff --check`: passed.
- Full top-level suite attempted with `pytest -q tests`; collection was blocked by the environment missing `PySide6` for pre-existing `tests/qt_ui/test_tanker_orbit_speed.py` (`ModuleNotFoundError: No module named 'PySide6'`).

## Files changed
- `game/missiongenerator/aircraft/waypoints/racetrack.py`
- `game/missiongenerator/aircraft/aircraftbehavior.py`
- `tests/missiongenerator/aircraft/test_tanker_orbit_speed.py`

## Self-review
The implementation uses the shared selector in both paths, keeps receiver discovery scoped to ordinary package relationships, and deliberately uses the recovery baseline when no explicit recovery relationship exists. Unit conversion occurs only at the existing task construction points. No unrelated behavior was altered.

## Concerns
The full suite could not be collected because `PySide6` is unavailable in this environment. Focused generation tests and static checks pass.
