#!/bin/bash
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║      软件部署服务端 - Linux 安装程序              ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_SCRIPT="${SCRIPT_DIR}/server.py"
CONFIG_FILE="${SCRIPT_DIR}/server_config.json"
SERVICE_NAME="swdeploy-server"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ── 检查 root ──
if [ "$(id -u)" -ne 0 ]; then
    echo "  [错误] 请使用 root 或 sudo 运行此脚本"
    exit 1
fi

# ── 检查 server.py ──
if [ ! -f "$SERVER_SCRIPT" ]; then
    echo "  [错误] 未找到 server.py: $SERVER_SCRIPT"
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

# ── 检查 Flask ──
$PYTHON_BIN -c "import flask" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "  [警告] 未安装 Flask，正在尝试安装..."
    if [ -d "${SCRIPT_DIR}/lib/flask" ]; then
        export PYTHONPATH="${SCRIPT_DIR}/lib:$PYTHONPATH"
        echo "  [√] 已从离线包加载 Flask"
    else
        echo "  [提示] 正在通过 pip 安装 Flask..."
        $PYTHON_BIN -m pip install flask 2>/dev/null
        if [ $? -ne 0 ]; then
            echo "  [错误] Flask 安装失败，请手动安装: pip install flask"
            exit 1
        fi
    fi
fi

# ── 创建日志目录 ──
mkdir -p /opt/swdeploy

# ── 停止已有服务 ──
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
fi

# ── 生成 systemd 服务 ──
echo "  [i] 生成 systemd 服务..."
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=软件部署管理平台 - 服务端
After=network.target

[Service]
Type=simple
ExecStart=${PYTHON_BIN} ${SERVER_SCRIPT}
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
    echo "  ║  监听端口: 61234"
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
