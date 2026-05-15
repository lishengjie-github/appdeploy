#!/usr/bin/env python3
"""Called by install_windows.bat / install_linux.sh. Updates client_config.json."""
import json
import sys


def main():
    if len(sys.argv) < 4:
        print(
            "usage: install_cfg_client.py <client_config.json> <server_url> <install_path> [windows|linux]",
            file=sys.stderr,
        )
        sys.exit(2)
    cfg_file = sys.argv[1]
    server_url = sys.argv[2]
    install_path = sys.argv[3]
    platform = (sys.argv[4] if len(sys.argv) > 4 else "windows").strip().lower()
    if platform not in ("windows", "linux"):
        platform = "windows"

    try:
        with open(cfg_file, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg["server_url"] = server_url
    if install_path.strip():
        if isinstance(cfg.get("install_path"), dict):
            cfg["install_path"][platform] = install_path
        else:
            cfg["install_path"] = {
                "windows": install_path if platform == "windows" else "C:\\QtProgram",
                "linux": install_path if platform == "linux" else "/opt/qtprogram",
            }
        print(f"[OK] install_path.{platform}:", install_path)
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    print("[OK] config updated")
    sys.exit(0)


if __name__ == "__main__":
    main()
