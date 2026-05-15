#!/bin/bash
# 发行 zip 解压根目录执行；源码仓库中位于 linux/zip_root/。按 CPU 自动选择 linux/ 或 linux_arm/。
# 用法: sudo bash install_server_linux.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$("$HERE/linux_resolve_bundle.sh")"
exec bash "$BUNDLE/install_server.sh" "$@"
