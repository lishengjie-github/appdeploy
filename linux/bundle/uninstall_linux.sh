#!/usr/bin/env bash
# Deploy client - Linux uninstaller
set -e

SERVICE_NAME="swdeploy-client"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo ""
echo "============================================================"
echo "    Deploy client - Linux uninstaller"
echo "============================================================"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] Run as root: sudo bash $0"
    exit 1
fi

echo "[..] Stopping service..."
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

echo "[..] Disabling service..."
systemctl disable "$SERVICE_NAME" 2>/dev/null || true

if [ -f "$SERVICE_FILE" ]; then
    rm -f "$SERVICE_FILE"
    echo "[OK] Removed: $SERVICE_FILE"
else
    echo "[INFO] Service file not found: $SERVICE_FILE"
fi

systemctl daemon-reload 2>/dev/null || true

echo ""
echo "============================================================"
echo "  Done. Service $SERVICE_NAME has been removed."
echo "  Log files and data are preserved."
echo "============================================================"
