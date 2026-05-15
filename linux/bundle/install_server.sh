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

VENDOR_SITE="${SCRIPT_DIR}/vendor/site-packages"
WHEEL_DIR="${SCRIPT_DIR}/vendor/wheels"

# 在 Windows 上打 zip 时，tar 里的符号链接会变成与目标文件等大的重复文件；在目标机补回符号链接以省空间并兼容 -lpython3.12。
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

# ── 仅使用随包离线 Python（download_linux_embedded_python.py -> linux/python/），不依赖系统 python3 ──
resolve_bundle_python() {
    local base="${SCRIPT_DIR}/python/bin"
    [ -d "$base" ] || return 1
    # Zip from Windows 解压后常为 644，-x 误判「未找到」；尽力补齐执行位
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
fi

# portable CPython 需要的动态库路径（仅随包 Python）
if [ -n "$PYTHON_BIN" ] && [ -d "${SCRIPT_DIR}/python/lib" ]; then
    for _ld in "${SCRIPT_DIR}/python/lib" "${SCRIPT_DIR}/python/lib/x86_64-linux-gnu" "${SCRIPT_DIR}/python/lib/aarch64-linux-gnu"; do
        [ -d "$_ld" ] && export LD_LIBRARY_PATH="${_ld}:${LD_LIBRARY_PATH:-}"
    done
fi

SERVER_EXEC=""
if [ -x "${SCRIPT_DIR}/server" ]; then
    SERVER_EXEC="${SCRIPT_DIR}/server"
elif [ -f "$SERVER_SCRIPT" ]; then
    if [ -z "$PYTHON_BIN" ]; then
        echo "  [错误] 未找到随包 Python（本目录下应有 python/bin/python3）。"
        if [ -d "${SCRIPT_DIR}/python/bin" ]; then
            echo "  [提示] 已存在 ${SCRIPT_DIR}/python/bin/，但无可用解释器；若从 zip 解压，请试: chmod -R a+rx \"${SCRIPT_DIR}/python/bin\""
        fi
        if [ ! -d "${SCRIPT_DIR}/python" ]; then
            echo "  [提示] 未找到 ${SCRIPT_DIR}/python/，请用含 linux/python 的完整发行包，或打包前执行: python download_linux_embedded_python.py"
        fi
        echo "  打包前在联网机执行: python download_linux_embedded_python.py"
        exit 1
    fi
    PY_VER=$($PYTHON_BIN --version 2>&1)
    echo "  [√] Python（离线随包）: $PYTHON_BIN ($PY_VER)"
else
    echo "  [错误] 未找到 server 可执行文件或 server.py: ${SCRIPT_DIR}"
    exit 1
fi

# ── 确保可加载 Flask：仅离线（不访问 PyPI、不要求 pip）──
# 优先顺序：已安装 → lib/flask → vendor/wheels 解压到 vendor/site-packages（.whl 即 zip，用 Python 标准库解压）

extract_wheels_to_vendor() {
    mkdir -p "$VENDOR_SITE"
    echo "  [..] 解压离线 wheel 到 ${VENDOR_SITE} （无需 pip）..."
    $PYTHON_BIN -c "
import glob, os, sys, zipfile
wd, vs = sys.argv[1], sys.argv[2]
os.makedirs(vs, exist_ok=True)
paths = sorted(glob.glob(os.path.join(wd, '*.whl')))
if not paths:
    sys.exit(1)
for p in paths:
    with zipfile.ZipFile(p) as z:
        z.extractall(vs)
sys.exit(0)
" "$WHEEL_DIR" "$VENDOR_SITE"
}

ensure_flask() {
    if $PYTHON_BIN -c "import flask" 2>/dev/null; then
        return 0
    fi
    if [ -d "${SCRIPT_DIR}/lib/flask" ]; then
        export PYTHONPATH="${SCRIPT_DIR}/lib:${PYTHONPATH:-}"
        if $PYTHON_BIN -c "import flask" 2>/dev/null; then
            echo "  [√] 已从离线包 lib/ 加载 Flask"
            return 0
        fi
    fi
    if [ -d "$WHEEL_DIR" ] && compgen -G "${WHEEL_DIR}/*.whl" > /dev/null; then
        rm -rf "${VENDOR_SITE}"
        mkdir -p "$VENDOR_SITE"
        if ! extract_wheels_to_vendor; then
            echo "  [错误] 解压 wheel 失败: ${WHEEL_DIR}"
            return 1
        fi
        export PYTHONPATH="${VENDOR_SITE}:${PYTHONPATH:-}"
        if $PYTHON_BIN -c "import sys; sys.path.insert(0, r'''$VENDOR_SITE'''); import flask" 2>/dev/null; then
            echo "  [√] 离线依赖已就绪（随包自带的 vendor/wheels）"
            return 0
        fi
        echo "  [错误] 解压后仍无法 import flask。请确认 wheel 与当前 Python 版本/架构匹配（x86: cp312 manylinux2014_x86_64；ARM: cp312 manylinux2014_aarch64 + 使用对应 linux/ 或 linux_arm/）。"
        return 1
    fi
    echo "  [错误] 离线安装包缺少 vendor/wheels/*.whl。"
    echo "        请在制作发行包前于联网机执行: python download_linux_offline_deps.py"
    echo "        并重新打包；或使用含 linux/vendor/wheels 的完整 ZIP。"
    return 1
}

if [ -n "$SERVER_EXEC" ]; then
    echo "  [√] 服务端: $SERVER_EXEC"
else
    if ! ensure_flask; then
        exit 1
    fi
fi

# ── 供 systemd 使用的 PYTHONPATH / LD_LIBRARY_PATH ──
SYSTEMD_PYTHONPATH=""
if [ -z "$SERVER_EXEC" ]; then
    if [ -d "$VENDOR_SITE" ] && [ -n "$(ls -A "$VENDOR_SITE" 2>/dev/null)" ]; then
        SYSTEMD_PYTHONPATH="$VENDOR_SITE"
    elif [ -d "${SCRIPT_DIR}/lib/flask" ]; then
        SYSTEMD_PYTHONPATH="${SCRIPT_DIR}/lib"
    fi
fi

SYSTEMD_LD=""
for d in "${SCRIPT_DIR}/python/lib" "${SCRIPT_DIR}/python/lib/x86_64-linux-gnu" "${SCRIPT_DIR}/python/lib/aarch64-linux-gnu"; do
    [ -d "$d" ] || continue
    SYSTEMD_LD="${SYSTEMD_LD:+$SYSTEMD_LD:}$d"
done

# ── 停止已有服务 ──
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
fi

# ── 生成 systemd 服务 ──
# 逐行写入，避免 heredoc+变量拼接时 Environment 与 ExecStart 粘成一行（会触发 bad unit file setting）
echo "  [i] 生成 systemd 服务..."
write_server_systemd_unit() {
    umask 022
    {
        echo "[Unit]"
        echo "Description=Software deployment server (swdeploy)"
        echo "After=network.target"
        echo ""
        echo "[Service]"
        echo "Type=simple"
        echo "Environment=PYTHONUNBUFFERED=1"
        if [ -n "$SYSTEMD_PYTHONPATH" ]; then
            printf "Environment=PYTHONPATH=%s\n" "$SYSTEMD_PYTHONPATH"
        fi
        if [ -n "$SYSTEMD_LD" ]; then
            printf "Environment=LD_LIBRARY_PATH=%s\n" "$SYSTEMD_LD"
        fi
        if [ -n "$SERVER_EXEC" ]; then
            printf "ExecStart=%s\n" "$SERVER_EXEC"
        else
            printf "ExecStart=%s -u %s\n" "$PYTHON_BIN" "$SERVER_SCRIPT"
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

write_server_systemd_unit

if command -v systemd-analyze >/dev/null 2>&1; then
    _v_out=$(systemd-analyze verify "$SERVICE_FILE" 2>&1) || {
        echo "  [WARN] systemd-analyze verify: $_v_out"
        echo "  [提示] 若服务仍无法启动，请检查 $SERVICE_FILE 内 ExecStart/Environment 是否单行一条指令"
    }
fi

systemctl daemon-reload
if systemctl enable "$SERVICE_NAME"; then
    echo "  [√] 已启用开机自启 (systemctl enable ${SERVICE_NAME})"
else
    echo "  [错误] systemctl enable 失败"
    exit 1
fi
systemctl start "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║  安装成功！                                      ║"
    echo "  ╠══════════════════════════════════════════════════╣"
    echo "  ║  服务名称: ${SERVICE_NAME}"
    echo "  ║  开机自启: 已启用 (multi-user.target)"
    echo "  ║  监听端口: 61234"
    echo "  ║  配置文件: ${CONFIG_FILE}"
    echo "  ╠══════════════════════════════════════════════════╣"
    echo "  ║  管理命令:                                       ║"
    echo "  ║    便捷: sudo ./start_server.sh {start|stop|restart|status|reset}"
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
