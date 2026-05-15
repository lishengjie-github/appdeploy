Linux 完全离线说明（不依赖系统 python3 / pip / 外网）
=====================================================

为什么不是「随便拷个包」就行
--------------------------
在 Windows 上准备 Linux 离线环境，要分别对准两类东西：

  • 解释器：必须是 **Linux + 目标 CPU 架构** 的 CPython（本仓库用 glibc x86_64
    的独立构建，由 download_linux_embedded_python.py 拉取并解压到 linux/python/）。
    不能把 Windows 版 Python 或错架构的 Linux 包塞给目标机。

  • 依赖库：即便在 Windows 上执行下载，也要用 **pip download** 并指定**目标**
    平台与 Python 版本（例如 manylinux2014_x86_64、cp312），得到的是给 Linux
    用的 wheel，而不是 Windows 用的。这与「直接 pip download 不指定 platform」
    有本质区别。

本仓库用 linux/requirements-linux-server.txt 列出服务端需要的 Flask 栈（可按需
增删，不必整项目 pip freeze）。脚本 download_linux_offline_deps.py 等价于在
Windows 上为一台「虚拟的」Linux x86_64 + Python 3.12 执行跨平台下载；ARM64 见文末。

发行包「linux」目录下应包含：

1) python/          便携 CPython（python/bin/python3）
   在联网开发机执行（仅需一次）：
     python download_linux_embedded_python.py
   下载脚本默认「先 GitHub 再镜像」，避免在失效代理上长时间卡住。仅直连可设：
     CPYTHON_SKIP_MIRRORS=1          只下官方直链（国内能直连 GitHub 时最快）
   其他环境变量：
     CPYTHON_LINUX_TARBALL_URL=...   自定义 tar.gz 直链
     CPYTHON_MIRRORS_FIRST=1         改回先镜像后 GitHub（老顺序）
     GITHUB_RELEASE_MIRROR=...       额外镜像前缀（逗号分隔，替换 github.com 部分）
     LINUX_PYTHON_STRIPPED=1         更小的 stripped 包
     CPYTHON_DOWNLOAD_TIMEOUT=300    超时秒数

2) vendor/wheels/   Flask 等 .whl（与上一步 Python 3.12 匹配）
     python download_linux_offline_deps.py

3) 再打 Windows 发行 zip（会把 linux/python、linux/vendor 一并打入）

目标机解压后（推荐在 zip 根目录，自动按 CPU 选架构）：
  sudo bash install_server_linux.sh
若仅在 x86 包内安装，也可：
  cd linux && chmod +x install_server.sh start_server.sh && sudo ./install_server.sh

无需 apt/yum 安装 python3；脚本只使用随包 python/。

ARM64 服务器：请自行准备 aarch64 的 CPython 解压到 python/，并用 LINUX_WHEEL_PLATFORM=manylinux2014_aarch64 重新下载 wheel。
