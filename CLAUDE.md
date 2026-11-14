# CLAUDE.md — wlan-scan

Project rules for **WLAN Scan**. Bryan's house rules (`~/Repos/app-template/CLAUDE.md`) and the
desktop-app conventions (`~/Repos/app-template/DESKTOP-APPS.md`) govern everything not stated
here. When this file and those disagree, this file wins for this repo; flag the conflict to Bryan.

## What this is

A Wi-Fi analysis and site-survey desktop tool: channel map, RSSI graphs, security parsing,
floor-plan heatmaps (`.wlansurvey` files), ping, neighbor report and speedtest. README.md has
the full feature set and per-OS requirements.

## Run and test

- Stack: Python with PySide6 and numpy (`requirements.txt`, `requirements-dev.txt`).
- `python main.py` runs it (CLI and GUI dispatch).
- `pytest` runs the suite. `tests/fixtures/` has a synthetic survey; the README's heatmap
  image is rendered from it, and it is modelled data, never a real site.
- Linux scanning uses `iw` (root or `CAP_NET_ADMIN` to trigger scans) with a NetworkManager
  fallback. macOS 14+ needs Location Services for SSIDs/BSSIDs. Ask Bryan before running it
  elevated.

## Release

- `.github/workflows/wlan-scan.yml` builds and releases when a `wlan-scan-v*` tag is pushed;
  `tests.yml` runs on every branch push. Artifacts are built only in CI.
- Pushing a tag publishes a release: ask Bryan first. Latest tag is `wlan-scan-v1.0.2`.

## Repo and branches

Private repo `bryjogar/wlan-scan`. Desktop app, so no dev-server or prod deploy. Active work is
on `v2` (survey mode, Linux backends; 28 commits ahead of `main` on 2026-11-14), which merges
to `main` for release. Commits as `Nico <bryjogar@users.noreply.github.com>` (house rule).

An older copy sits in `bryjogar/app-archive` (`wlan-scan/`) as a frozen reference. This repo
is the live one.
