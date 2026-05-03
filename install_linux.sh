#!/bin/bash
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║      软件部署客户端 - Linux 安装程序              ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLIENT_SCRIPT="${SCRIPT_DIR}/client.py"
CONFIG_FILE="${SCRIPT_DIR}/client_config.json"
SERVICE_NAME="swdeploy-client"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ── 检查 root ──
if [ "$(id -u)" -ne 0 ]; then
    echo "  [错误] 请使用 root 或 sudo 运行此脚本"
    exit 1
fi

# ── 查找 Python ──
PYTHON_BIN=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_BIN="$(command -v "$cmd")"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "  [错误] 未找到 Python，请先安装 Python 3.8+"
    echo "  Ubuntu/Debian: apt install python3"
    echo "  CentOS/RHEL:   yum install python3"
    exit 1
fi
PY_VER=$($PYTHON_BIN --version 2>&1)
echo "  [√] Python: $PYTHON_BIN ($PY_VER)"

# ── 配置服务端地址 ──
echo ""
echo "  请输入服务端的 IP 地址（即运行 server.py 的机器 IP）"
echo "  例如: 192.168.1.100"
echo ""
read -p "  服务端 IP: " SERVER_IP

if [ -z "$SERVER_IP" ]; then
    echo "  [错误] IP 地址不能为空"
    exit 1
fi

SERVER_URL="http://${SERVER_IP}:61234"
echo "  [√] 服务端地址: $SERVER_URL"

# ── 配置安装路径 ──
echo ""
echo "  请输入软件安装路径（直接回车使用默认路径）"
echo "  Linux 默认: /opt/qtprogram"
echo ""
read -p "  安装路径: " INSTALL_PATH_INPUT

# 更新配置
$PYTHON_BIN -c "
import json
cfg_file = '${CONFIG_FILE}'
install_path = '${INSTALL_PATH_INPUT}'
try:
    with open(cfg_file, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
except:
    cfg = {}
cfg['server_url'] = '${SERVER_URL}'
if install_path:
    if isinstance(cfg.get('install_path'), dict):
        cfg['install_path']['linux'] = install_path
    else:
        cfg['install_path'] = {'windows': 'C:\\\\QtProgram', 'linux': install_path}
    print('  [√] 安装路径: ' + install_path)
with open(cfg_file, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=4, ensure_ascii=False)
print('  [√] 配置已更新')
"

# ── 设置权限 ──
chmod +x "$CLIENT_SCRIPT"
mkdir -p /opt/qtprogram

# ── 停止已有服务 ──
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
fi

# ── 生成 systemd 服务 ──
echo "  [i] 生成 systemd 服务..."
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=软件部署客户端代理
After=network.target

[Service]
Type=simple
ExecStart=${PYTHON_BIN} ${CLIENT_SCRIPT}
WorkingDirectory=${SCRIPT_DIR}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

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
