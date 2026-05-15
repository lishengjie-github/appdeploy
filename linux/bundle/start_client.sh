#!/bin/bash
# Start / stop / status the deploy client systemd service.

SERVICE_NAME="swdeploy-client"

if [ "$(id -u)" -ne 0 ]; then
    echo "  [INFO] Use sudo: sudo bash $0 $*"
    exit 1
fi

case "${1:-start}" in
    start)
        echo "  Starting ${SERVICE_NAME} ..."
        systemctl start "$SERVICE_NAME"
        sleep 1
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            echo "  [OK] Service is running"
        else
            echo "  [WARN] Service failed to start"
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
        sleep 1
        systemctl is-active --quiet "$SERVICE_NAME" && echo "  [OK] Running" || echo "  [WARN] Check: systemctl status $SERVICE_NAME"
        ;;
    status)
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    *)
        echo "  Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
