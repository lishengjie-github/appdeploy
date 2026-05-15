#!/bin/bash
# 在发行包解压后的根目录执行，按 CPU 自动选择 linux/ 或 linux_arm/。
# 用法: sudo bash install_client_linux.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$("$HERE/linux_resolve_bundle.sh")"
exec bash "$BUNDLE/install_linux.sh" "$@"
