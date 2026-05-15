# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cross-platform batch deployment system for distributing software to ~60 machines on an intranet (air-gapped). Mixed Windows 10/11 and Linux targets.

## File Structure

```
server.py              # Flask server: API, web dashboard, SQLite DB
client.py              # Cross-platform client agent (auto-detects OS)
server_config.json     # Server configuration
client_config.json     # Client configuration
install_cfg_client.py  # Shared install helper (called from Windows/Linux installers)

windows/               # Windows NSSM installers, nssm downloaders, VC++ helpers
linux/                 # x86_64 offline vendor/python + bundle/ + zip_root/ shell scripts
linux_arm/             # AArch64 offline payloads (reuses linux/bundle/*.sh in release zips)
tools/                 # Build-only helpers (e.g. print_dll_dir.py for PyInstaller VC++ DLLs)
scripts/               # Packaging: zip bundles, Linux offline downloaders, package_deploy, package_offline
```

## Tech Stack

- Python 3.8+, Flask (only external dependency for server), SQLite via stdlib
- Client uses only stdlib (urllib, zipfile, subprocess, etc.)
- HTTP + JSON REST API, no authentication
- Default port: **61234** (non-standard to avoid conflicts)

## Key Design

- **Multi-app support**: Both server and client manage multiple applications via `apps` array in config. Each app has a name and platform-specific executable name. Deploy stops all apps, extracts, starts all apps.
- **No backup**: Backup functionality has been removed.
- **No auth**: API token removed for simplicity on intranet.
- **Interactive install**: `windows/install_windows.bat` and `linux/bundle/install_linux.sh` prompt for server IP during installation and auto-update `client_config.json`.
- **Dark theme UI**: Server dashboard uses a dark theme with animations.

## Running

```bash
pip install flask       # Offline: pre-downloaded wheels
python server.py        # Starts on 0.0.0.0:61234
python client.py        # Reads client_config.json, starts polling
```
