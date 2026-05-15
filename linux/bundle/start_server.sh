#!/bin/bash
# Start / stop / restart / reset / status the deploy server systemd service.
# Windows 侧 start_server.bat 的对应脚本；reset 会尝试释放 61234 端口后再启动服务。

SERVICE_NAME="swdeploy-server"
PORT=61234

if [ "$(id -u)" -ne 0 ]; then
    echo "  [INFO] Use sudo: sudo bash $0 $*"
    exit 1
fi

free_port() {
    if command -v fuser &>/dev/null; then
        fuser -k "${PORT}/tcp" 2>/dev/null || true
        return
    fi
    if command -v ss &>/dev/null; then
        local pids
        pids=$(ss -ltnp "sport = :${PORT}" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)
        for pid in $pids; do
            if [ -n "$pid" ] && [ "$pid" != "-" ]; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        done
    fi
}

case "${1:-start}" in
    start)
        echo "  Starting ${SERVICE_NAME} ..."
        systemctl start "$SERVICE_NAME"
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo "  [OK] Service is running"
        else
            echo "  [WARN] Service may not have started, try: $0 reset"
            systemctl status "$SERVICE_NAME" --no-pager
        fi
        ;;
    stop)
        echo "  Stopping ${SERVICE_NAME} ..."
        systemctl stop "$SERVICE_NAME"
        echo "  [OK] Stopped"
        ;;
    restart)
        echo "  Restarting ${SERVICE_NAME} ..."
        systemctl restart "$SERVICE_NAME"
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo "  [OK] Running"
        else
            echo "  [WARN] Check: systemctl status $SERVICE_NAME"
        fi
        ;;
    status)
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    reset)
        echo "  [..] Force reset ${SERVICE_NAME} ..."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        sleep 2
        echo "  [..] Releasing port ${PORT} if still in use..."
        free_port
        sleep 1
        echo "  [..] Starting service..."
        systemctl start "$SERVICE_NAME"
        sleep 3
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo "  [OK] Service reset and running"
        else
            echo "  [ERROR] Service still not running after reset"
            systemctl status "$SERVICE_NAME" --no-pager
            echo "  See also: journalctl -u ${SERVICE_NAME} -n 80 --no-pager"
        fi
        ;;
    *)
        echo "  Usage: $0 {start|stop|restart|status|reset}"
        exit 1
        ;;
esac
