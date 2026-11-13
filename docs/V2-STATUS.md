# WLAN Scan v2: status

Branch `v2`. Plan: [PLAN-v2.md](PLAN-v2.md). Updated with every item's commit.

Legend: ✅ done · 🔧 in progress · ⛔ blocked · ⬜ not started

## Phase 0: buildable and testable on Linux

| Item | State | Notes |
|---|---|---|
| P0.1 Scanner backend interface | ✅ | `wlan_scan/backends.py`: `ScannerBackend` protocol, per-platform adapters, `candidate_backends()` + `open_first()`. Worker falls back down the list on failure; status bar shows the active backend. |
| P0.2 Record and replay | ⬜ | |
| P0.3 Tests and CI | ⬜ | |

## Phase 1: remediations

| Item | State | Notes |
|---|---|---|
| R1 IE parser (Wi-Fi 6/7, width) | ⬜ | |
| R2 Security parsing | ⬜ | |
| R3 One IE parser | ⬜ | |
| R4 Max rate | ⬜ | |
| R5 One vendor lookup | ⬜ | |
| R6 Scan timing | ⬜ | |
| R7 Don't block the GUI thread | ⬜ | |
| R8 Start/stop and auto-scan | ⬜ | |
| R9 macOS correctness | ⬜ | |
| R10 CSV export | ⬜ | |
| R11 Versioning and updates | ⬜ | |
| R12 Logging and cleanup | ⬜ | |
| R13 Tests | ⬜ | |

## Phase 2: floor plan survey

| Item | State | Notes |
|---|---|---|
| F1 Survey project file | ⬜ | |
| F2 Floor plan import | ⬜ | |
| F3 Scale calibration | ⬜ | |
| F4 Survey tab | ⬜ | |
| F5 Heatmap | ⬜ | |
| F6 APs on the plan | ⬜ | |
| F7 Export | ⬜ | |
| F8 Tests and demo | ⬜ | |

## Phase 3 (optional)

| Item | State | Notes |
|---|---|---|
| L1 Linux backend | ⬜ | |

## Needs Bryan's hardware

Changes to platform code that could only be checked here against fixtures.
Each needs a run on a real machine before release.

| Item | Platform | What changed | How to check |
|---|---|---|---|

## Plan deviations and findings

Things in the plan that turned out different in the code, and what was done.
