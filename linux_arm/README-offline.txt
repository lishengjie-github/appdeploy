Linux ARM64（AArch64）完全离线说明 — 与上层 linux/ 并列，目录名为 linux_arm
================================================================

适用：麒麟 / 树莓派等 **AArch64 + glibc** 的 Linux 服务器（本包为 manylinux2014_aarch64）。

与 linux/（x86_64）的区别
--------------------------
  • 仅目录名不同：在发行 zip 里为 **linux_arm/**，与 **linux/** 平级，互斥使用。
  • 目标机为 x86_64 请用 **linux/**；为 ARM64 请把 **linux_arm/** 整份拷到目标机再安装。

准备离线内容（在联网 Windows 开发机，各执行一次即可）
------------------------------------------
  1) 便携 CPython（AArch64）-> linux_arm/python/
     可用项目根目录批处理： **download_linux_arm_offline_all.bat**
     或手动：
       set LINUX_PACK_ROOT=linux_arm
       set CPYTHON_LINUX_ARCH=aarch64
       set CPYTHON_SKIP_MIRRORS=1
       python download_linux_embedded_python.py

  2) 依赖 wheel（manylinux aarch64）-> linux_arm/vendor/wheels/
       set LINUX_PACK_ROOT=linux_arm
       python download_linux_offline_deps.py
     （当 LINUX_PACK_ROOT=linux_arm 时，默认 --platform 为 manylinux2014_aarch64）

  3) 再打 Windows 发行 zip（package_exe_zip.bat 会同时打入 linux 与 linux_arm 中已存在的目录）

目标机（ARM64）
--------------
推荐在发行 zip 根目录（与 server.exe 同级）：
  sudo bash install_server_linux.sh
仅使用本目录时：
  cd .../linux_arm && chmod +x install_server.sh start_server.sh && sudo ./install_server.sh

勿将 x86 的 linux/python 与 ARM 的 linux_arm/python 混用；架构不一致会导致无法运行。
