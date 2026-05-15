#!/bin/bash
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║      软件部署客户端 - Linux 安装程序              ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLIENT_SCRIPT="${SCRIPT_DIR}/client.py"
CONFIG_FILE="${SCRIPT_DIR}/client_config.json"
CFG_HELPER="${SCRIPT_DIR}/install_cfg_client.py"
SERVICE_NAME="swdeploy-client"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

repair_bundle_python_symlinks() {
    local base="${SCRIPT_DIR}/python/bin"
    local lib="${SCRIPT_DIR}/python/lib"
    if [ -f "${lib}/libpython3.12.so.1.0" ] && [ -f "${lib}/libpython3.12.so" ]; then
        if cmp -s "${lib}/libpython3.12.so.1.0" "${lib}/libpython3.12.so" 2>/dev/null; then
            rm -f "${lib}/libpython3.12.so"
            ( cd "$lib" && ln -sf libpython3.12.so.1.0 libpython3.12.so )
        fi
    elif [ -f "${lib}/libpython3.12.so.1.0" ] && [ ! -e "${lib}/libpython3.12.so" ]; then
        ( cd "$lib" && ln -sf libpython3.12.so.1.0 libpython3.12.so ) 2>/dev/null || true
    fi
    [ -f "${base}/python3.12" ] || return 0
    if [ -f "${base}/python3" ] && cmp -s "${base}/python3.12" "${base}/python3" 2>/dev/null; then
        rm -f "${base}/python3"
        ln -sf python3.12 "${base}/python3"
    elif [ ! -e "${base}/python3" ]; then
        ln -sf python3.12 "${base}/python3" 2>/dev/null || true
    fi
    if [ -f "${base}/python" ] && cmp -s "${base}/python3.12" "${base}/python" 2>/dev/null; then
        rm -f "${base}/python"
        ln -sf python3.12 "${base}/python"
    elif [ ! -e "${base}/python" ]; then
        ln -sf python3.12 "${base}/python" 2>/dev/null || true
    fi
}

# ── 检查 root ──
if [ "$(id -u)" -ne 0 ]; then
    echo "  [错误] 请使用 root 或 sudo 运行此脚本"
    exit 1
fi

# ── 检查客户端文件（与 Windows 一致：可执行 client 或 client.py）──
if [ ! -x "${SCRIPT_DIR}/client" ] && [ ! -f "$CLIENT_SCRIPT" ]; then
    echo "  [错误] 未找到 client 可执行文件或 client.py: ${SCRIPT_DIR}"
    exit 1
fi

# ── 仅使用随包离线 Python（python/download_linux_embedded_python.py），不依赖系统 python3 ──
resolve_bundle_python() {
    local base="${SCRIPT_DIR}/python/bin"
    [ -d "$base" ] || return 1
    local f
    for f in "${base}/python3" "${base}"/python3.[0-9]*; do
        [ -e "$f" ] || continue
        [ -x "$f" ] || chmod a+x "$f" 2>/dev/null || true
    done
    if [ -f "${base}/python3" ]; then
        [ -x "${base}/python3" ] || chmod a+x "${base}/python3" 2>/dev/null || true
        [ -x "${base}/python3" ] && echo "${base}/python3" && return 0
    fi
    if [ -f "${base}/python3.12" ]; then
        [ -x "${base}/python3.12" ] || chmod a+x "${base}/python3.12" 2>/dev/null || true
        [ -x "${base}/python3.12" ] && echo "${base}/python3.12" && return 0
    fi
    local p
    for p in "${base}"/python3.[0-9]*; do
        [ -f "$p" ] || continue
        [ -x "$p" ] || chmod a+x "$p" 2>/dev/null || true
        [ -x "$p" ] && echo "$p" && return 0
    done
    return 1
}

repair_bundle_python_symlinks

PYTHON_BIN=""
if _bp="$(resolve_bundle_python)"; then
    PYTHON_BIN="$_bp"
    for _ld in "${SCRIPT_DIR}/python/lib" "${SCRIPT_DIR}/python/lib/x86_64-linux-gnu" "${SCRIPT_DIR}/python/lib/aarch64-linux-gnu"; do
        [ -d "$_ld" ] && export LD_LIBRARY_PATH="${_ld}:${LD_LIBRARY_PATH:-}"
    done
    PY_VER=$($PYTHON_BIN --version 2>&1)
    echo "  [√] Python（离线随包）: $PYTHON_BIN ($PY_VER)"
fi

CLIENT_EXEC=""
if [ -x "${SCRIPT_DIR}/client" ]; then
    CLIENT_EXEC="${SCRIPT_DIR}/client"
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "  [错误] 未找到随包 Python（本目录下应有 python/bin/python3）。"
    echo "  请使用含 python/ 的完整离线包，或打包前执行: python download_linux_embedded_python.py"
    exit 1
fi

if [ -n "$CLIENT_EXEC" ]; then
    echo "  [√] 客户端运行单元: $CLIENT_EXEC"
fi

# ── 读取默认 server_url ──
DEFAULT_URL="http://127.0.0.1:61234"
if [ -f "$CONFIG_FILE" ] && [ -n "$PYTHON_BIN" ]; then
    DEFAULT_URL=$($PYTHON_BIN -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8-sig')).get('server_url','http://127.0.0.1:61234'))" "$CONFIG_FILE" 2>/dev/null) || DEFAULT_URL="http://127.0.0.1:61234"
fi

# ── 非交互模式：SWDEPLOY_NONINTERACTIVE=1，可选 SWDEPLOY_SERVER_IP、SWDEPLOY_INSTALL_LINUX ──
SWDEPLOY_NONINTERACTIVE="${SWDEPLOY_NONINTERACTIVE:-0}"
if [ "$SWDEPLOY_NONINTERACTIVE" = "1" ]; then
    SERVER_IP="${SWDEPLOY_SERVER_IP:-}"
    INSTALL_PATH_INPUT="${SWDEPLOY_INSTALL_LINUX:-}"
    if [ -z "$SERVER_IP" ]; then
        SERVER_URL="$DEFAULT_URL"
        echo "  [OK] 非交互: 使用当前/默认 server_url: $SERVER_URL"
    else
        SERVER_URL="http://${SERVER_IP}:61234"
        echo "  [OK] 非交互: server_url=$SERVER_URL"
    fi
else
    echo ""
    echo "  请输入服务端 IP（直接回车使用当前配置）"
    echo "  当前: $DEFAULT_URL"
    echo ""
    read -r -p "  服务端 IP (回车跳过): " SERVER_IP

    if [ -z "$SERVER_IP" ]; then
        SERVER_URL="$DEFAULT_URL"
        echo "  [OK] 使用当前配置: $SERVER_URL"
    else
        SERVER_URL="http://${SERVER_IP}:61234"
        echo "  [OK] 服务端地址: $SERVER_URL"
    fi

    echo ""
    echo "  请输入软件安装路径（直接回车使用默认路径）"
    echo "  Linux 默认: /opt/qtprogram"
    echo ""
    read -r -p "  安装路径: " INSTALL_PATH_INPUT
fi

# ── 更新配置（与 install_windows.bat 共用 install_cfg_client.py）──
if [ ! -f "$CFG_HELPER" ]; then
    echo "  [错误] 缺少 install_cfg_client.py: $CFG_HELPER"
    exit 1
fi
if ! "$PYTHON_BIN" "$CFG_HELPER" "$CONFIG_FILE" "$SERVER_URL" "$INSTALL_PATH_INPUT" linux; then
    echo "  [错误] 配置更新失败"
    exit 1
fi

# ── 安装根目录（与配置中的 linux 路径一致）──
INSTALL_ROOT="${INSTALL_PATH_INPUT:-}"
if [ -z "$INSTALL_ROOT" ]; then
    INSTALL_ROOT=$($PYTHON_BIN -c "
import json, sys
with open(sys.argv[1], encoding='utf-8-sig') as f:
    c = json.load(f)
ip = c.get('install_path')
if isinstance(ip, dict):
    print(ip.get('linux', '/opt/qtprogram'))
else:
    print('/opt/qtprogram')
" "$CONFIG_FILE" 2>/dev/null) || INSTALL_ROOT="/opt/qtprogram"
fi
mkdir -p "$INSTALL_ROOT"

# ── 设置权限 ──
if [ -f "$CLIENT_SCRIPT" ]; then
    chmod +x "$CLIENT_SCRIPT"
fi

# ── systemd 中随包 Python 的库路径 ──
SYSTEMD_LD=""
for d in "${SCRIPT_DIR}/python/lib" "${SCRIPT_DIR}/python/lib/x86_64-linux-gnu" "${SCRIPT_DIR}/python/lib/aarch64-linux-gnu"; do
    [ -d "$d" ] || continue
    SYSTEMD_LD="${SYSTEMD_LD:+$SYSTEMD_LD:}$d"
done
# ── 停止已有服务 ──
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
fi

# ── 生成 systemd 服务（逐行写入，避免 Environment 与 ExecStart 粘连）──
echo "  [i] 生成 systemd 服务..."
write_client_systemd_unit() {
    umask 022
    {
        echo "[Unit]"
        echo "Description=Software deploy client (swdeploy)"
        echo "After=network.target"
        echo ""
        echo "[Service]"
        echo "Type=simple"
        echo "Environment=PYTHONUNBUFFERED=1"
        if [ -n "$SYSTEMD_LD" ]; then
            printf "Environment=LD_LIBRARY_PATH=%s\n" "$SYSTEMD_LD"
        fi
        if [ -n "$CLIENT_EXEC" ]; then
            printf "ExecStart=%s\n" "$CLIENT_EXEC"
        else
            printf "ExecStart=%s -u %s\n" "$PYTHON_BIN" "$CLIENT_SCRIPT"
        fi
        printf "WorkingDirectory=%s\n" "$SCRIPT_DIR"
        echo "Restart=always"
        echo "RestartSec=10"
        echo "StandardOutput=journal"
        echo "StandardError=journal"
        echo "SyslogIdentifier=${SERVICE_NAME}"
        echo ""
        echo "[Install]"
        echo "WantedBy=multi-user.target"
    } > "$SERVICE_FILE"
}

write_client_systemd_unit

if command -v systemd-analyze >/dev/null 2>&1; then
    _v_out=$(systemd-analyze verify "$SERVICE_FILE" 2>&1) || {
        echo "  [WARN] systemd-analyze verify: $_v_out"
    }
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║  安装成功！                                      ║"
    echo "  ╠══════════════════════════════════════════════════╣"
    echo "  ║  服务名称: ${SERVICE_NAME}"
    echo "  ║  服务端:   ${SERVER_URL}"
    echo "  ║  配置文件: ${CONFIG_FILE}"
    echo "  ╠══════════════════════════════════════════════════╣"
    echo "  ║  管理命令:                                       ║"
    echo "  ║    便捷: sudo ./start_client.sh {start|stop|restart|status}"
    echo "  ║    启动: systemctl start ${SERVICE_NAME}"
    echo "  ║    停止: systemctl stop ${SERVICE_NAME}"
    echo "  ║    状态: systemctl status ${SERVICE_NAME}"
    echo "  ║    日志: journalctl -u ${SERVICE_NAME} -f"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo ""
else
    echo "  [错误] 服务启动失败，请检查: journalctl -u ${SERVICE_NAME}"
    exit 1
fi
