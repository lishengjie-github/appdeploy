#!/usr/bin/env bash
# 软件部署服务端 - Linux 卸载（移除 systemd 服务，保留数据与日志）
set -euo pipefail

SERVICE_NAME="swdeploy-server"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
WANTS_LINK="/etc/systemd/system/multi-user.target.wants/${SERVICE_NAME}.service"

echo ""
echo "============================================================"
echo "    软件部署管理平台 - 服务端卸载"
echo "============================================================"
echo ""

if [ "$(id -u)" -ne 0 ]; then
    echo "[错误] 请使用 root 或 sudo: sudo bash $0"
    exit 1
fi

echo "[..] 停止服务..."
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

echo "[..] 清除失败状态..."
systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true

echo "[..] 禁用开机自启..."
systemctl disable "$SERVICE_NAME" 2>/dev/null || true

# disable 通常会删掉 wants 下的软链；若仍存在则手动删除
if [ -L "$WANTS_LINK" ] || [ -e "$WANTS_LINK" ]; then
    rm -f "$WANTS_LINK"
    echo "[OK] 已移除: $WANTS_LINK"
fi

if [ -f "$SERVICE_FILE" ]; then
    rm -f "$SERVICE_FILE"
    echo "[OK] 已移除单元文件: $SERVICE_FILE"
else
    echo "[提示] 未找到单元文件: $SERVICE_FILE"
fi

systemctl daemon-reload

echo ""
echo "============================================================"
echo "  完成。服务 ${SERVICE_NAME} 已卸载（不再开机自启）。"
echo "  数据目录与日志文件保留未删除。"
echo "============================================================"
