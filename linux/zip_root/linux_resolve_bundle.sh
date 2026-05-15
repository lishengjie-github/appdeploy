#!/bin/bash
# 与 install_server_linux.sh 等同目录。根据本机 CPU 选择 linux/ 或 linux_arm/，并 stdout 输出该目录绝对路径。
# 环境变量 SWDEPLOY_LINUX_BUNDLE 可强制指定已有目录（调试/特殊布局）。

set -e

if [ -n "${SWDEPLOY_LINUX_BUNDLE:-}" ]; then
    d=$(cd "$SWDEPLOY_LINUX_BUNDLE" && pwd)
    if [ ! -d "$d/python" ]; then
        echo "[错误] SWDEPLOY_LINUX_BUNDLE 下缺少 python/: $d" >&2
        exit 1
    fi
    echo "$d"
    exit 0
fi

# 发行 zip 根目录：本脚本与 linux/、linux_arm/ 同级。仓库源码：本脚本在 linux/zip_root/ 下，二者为兄弟目录。
MYDIR="$(cd "$(dirname "$0")" && pwd)"
# 发行 zip 解压根：本目录下即有 linux/（内含 install_server.sh）与 linux_arm/。
# 源码仓库：入口脚本在 linux/zip_root/，与 linux/bundle/、linux_arm/ 同属仓库根下的子树。
if [ -d "$MYDIR/linux" ] && [ -f "$MYDIR/linux/install_server.sh" ]; then
    ROOT="$MYDIR"
elif [ -f "$MYDIR/../bundle/install_server.sh" ]; then
    ROOT="$(cd "$MYDIR/../.." && pwd)"
else
    ROOT="$MYDIR"
fi
M=$(uname -m 2>/dev/null || echo unknown)

case "$M" in
    aarch64|arm64)
        SUB="$ROOT/linux_arm"
        if [ ! -d "$SUB/python" ]; then
            echo "[错误] 本机为 ARM64，但发行包中未找到: $SUB/python" >&2
            echo "       请在制作 zip 前于 Windows 执行 scripts/download_linux_arm_offline_all.bat 并重新 package_exe_zip.bat" >&2
            exit 1
        fi
        echo "$SUB"
        ;;
    x86_64|amd64)
        SUB="$ROOT/linux"
        if [ ! -d "$SUB/python" ]; then
            echo "[错误] 本机为 x86_64，但发行包中未找到: $SUB/python" >&2
            echo "       请在制作 zip 前于 Windows 执行 scripts/download_linux_offline_all.bat 并重新 package_exe_zip.bat" >&2
            exit 1
        fi
        echo "$SUB"
        ;;
    *)
        echo "[错误] 不支持的 CPU 架构: $M（当前仅支持 x86_64 / amd64 与 aarch64 / arm64）" >&2
        exit 1
        ;;
esac
