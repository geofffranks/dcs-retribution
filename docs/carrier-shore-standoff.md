# Carrier shore standoff

The **Carrier minimum shore distance (NM)** setting is in the **Mission Generator** settings context, under Gameplay. It controls how close aircraft carriers and LHAs may be to shore while steaming into wind.

- The default is **60 NM**.
- The valid range is **0 through 80 NM**.
- Set it to **0** to disable the carrier/LHA shore-standoff movement check and generation warning.

This rule applies to aircraft carriers and LHAs, but not to ordinary ships. A new carrier or LHA move whose destination is below the configured shore-distance threshold is rejected. Existing maximum-distance and land/sea movement checks still apply.

Existing unsafe carrier/LHA positions remain loadable. Before generating a mission, normal and Pretense mission generation report any affected carrier or LHA and its current shore distance. Distances in the warning are shown in nautical miles to one decimal place.

- In the interactive flow, the warning lists the affected names and distances and offers **Continue** or **Cancel**. **Cancel** prevents mission output.
- In a direct or headless flow, the warning is logged and generation continues.
