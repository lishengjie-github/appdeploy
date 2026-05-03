#!/usr/bin/env python3
"""Called by install_windows.bat only. Updates client_config.json (ASCII-safe wrapper)."""
import json
import sys


def main():
    if len(sys.argv) < 4:
        print("usage: install_cfg_client.py <client_config.json> <server_url> <install_path>")
        sys.exit(2)
    cfg_file = sys.argv[1]
    server_url = sys.argv[2]
    install_path = sys.argv[3]
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg["server_url"] = server_url
    if install_path.strip():
        if isinstance(cfg.get("install_path"), dict):
            cfg["install_path"]["windows"] = install_path
        else:
            cfg["install_path"] = {"windows": install_path, "linux": "/opt/qtprogram"}
        print("[OK] install_path.windows:", install_path)
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    print("[OK] config updated")
    sys.exit(0)


if __name__ == "__main__":
    main()
