前提：
1.在局域网（内网环境）部署Qt程序
2.有60台机器，机器很多一个一个装很费劲，我想通过开发一个部署系统的软件来部署



---

## ✍️ 二、AI提示词模板（可直接复制）

```text
请帮我用 Python 实现一个内网批量部署系统，用于将 Qt 程序部署到 60 台混合 Windows 和 Linux 的机器上。

**核心要求：**
- 采用客户端-服务器架构，客户端代理安装在所有目标机器上。
- 服务端负责上传版本包、下发任务、监控状态。
- 客户端跨平台（Windows/Linux），接收指令后停止旧进程、下载对应平台的包、解压覆盖、启动新进程。
- Qt程序分为两个独立的压缩包：Windows版和Linux版，服务端需根据机器的实际操作系统分发正确的包。

**详细需求已整理如下，请严格按此清单实现：**

# 项目名称：跨平台内网Qt程序批量部署系统（客户端-服务器模式）

## 环境约束
- 纯内网环境，无互联网连接（所有依赖必须离线可用或提前内置）
- 目标机器：共60台，其中一部分为 Windows 10/11，另一部分为 Linux（Ubuntu/CentOS 等）
- Qt程序分为两个独立包：Windows版（.exe + 依赖）、Linux版（可执行文件 + .so等）
- 每台机器需要安装一个常驻客户端代理（Python脚本，可作为Windows服务或Linux systemd服务）

## 角色架构
- **服务端**：部署在一台独立的Windows或Linux机器上，提供Web界面和API
- **客户端**：运行在60台目标机器上，接收服务端指令并执行本地操作

## 服务端功能需求

### 1. 机器管理
- 支持添加/删除机器（IP地址、主机名、标签、平台类型手动指定或自动探测）
- 支持批量导入机器列表（CSV/JSON）
- 显示每台机器的状态（在线/离线、当前部署版本、最后部署时间）

### 2. 版本包管理
- 上传Windows版Qt程序包（ZIP格式）
- 上传Linux版Qt程序包（ZIP或.tar.gz格式）
- 服务端自动存储版本号（基于上传时间或手动指定）
- 支持回滚到历史版本（保留最近5个版本）

### 3. 部署任务
- 一键选择一批机器（可按平台筛选），并选择要部署的版本
- 支持立即部署或定时部署
- 部署流程：
  - 服务端通知指定客户端开始部署
  - 客户端从服务端下载对应平台的程序包（HTTP下载）
  - 客户端停止正在运行的Qt程序进程
  - 备份当前版本到本地指定目录（例如 `/opt/backup/` 或 `C:\backup\`）
  - 解压新包到目标安装目录（可配置路径）
  - 根据平台设置可执行权限（Linux `chmod +x`）
  - 启动新版本的Qt程序（可根据配置决定是否自动启动）
  - 返回部署结果（成功/失败 + 日志）
- 支持部署失败自动重试（最多3次，指数退避）
- 支持并发部署（用户可设置同时部署的最大机器数，如10台）

### 4. 状态监控与日志
- Web界面实时显示部署进度（百分比、阶段描述）
- 每台机器的详细日志可查看（服务端存储）
- 部署结束后生成汇总报告（成功/失败列表、耗时）

### 5. 客户端保活与升级
- 服务端可向客户端发送“心跳”检测在线状态
- 支持推送客户端自身更新（客户端代理有新版本时，服务端可下发更新脚本）

## 客户端功能需求（跨平台Windows/Linux）

### 运行方式
- Windows：作为后台服务运行（可使用 `pythonw.exe` + `nssm` 或直接 `pyinstaller` 打包为exe）
- Linux：作为 systemd 服务运行

### 功能点
- 注册自身到服务端（启动时向服务端上报本机IP、主机名、操作系统类型、版本、Qt程序当前版本）
- 定期向服务端发送心跳（例如每30秒）
- 接收服务端的部署指令（通过HTTP长轮询或WebSocket，简化为轮询即可）
- 下载文件（支持断点续传、校验MD5）
- 执行本地命令（停止进程、启动进程、备份、解压、设置权限）
- 返回执行结果和实时日志
- 支持本地配置（配置文件指定服务端地址、安装路径、Qt程序可执行文件名等）

### 具体跨平台处理
- **进程停止**：
  - Windows：`taskkill /f /im myqtapp.exe`
  - Linux：`pkill -f myqtapp` 或 `systemctl stop myqtapp`
- **进程启动**：
  - Windows：`start /b myqtapp.exe`
  - Linux：`nohup ./myqtapp &`
- **备份路径**：
  - Windows：`C:\QtDeploy\backup`
  - Linux：`/opt/qtdeploy/backup`
- **安装路径**：
  - Windows：`C:\QtProgram`
  - Linux：`/opt/qtprogram`

## 通信协议
- HTTP + JSON API（RESTful风格）
- 所有接口需携带API Token（简单静态token，内网足够）
- 文件下载使用 `/api/download/<platform>/<version>/<filename>` 端点

## 非功能性需求
- 安全性：最低要求（内网），但需防止恶意调用（token校验）
- 可靠性：服务端需持久化存储机器列表、部署任务状态（使用SQLite）
- 容错：单个客户端部署失败不影响其他客户端
- 日志：服务端日志分级（INFO/WARNING/ERROR），客户端日志保存在本地文件

## 配置文件示例

### 服务端配置 (server_config.json)
```json
{
    "host": "0.0.0.0",
    "port": 5000,
    "api_token": "your-secret-token",
    "packages_dir": "./packages",
    "database": "./deploy.db",
    "max_concurrent": 10,
    "heartbeat_timeout": 90
}

{
    "server_url": "http://192.168.1.100:5000",
    "api_token": "your-secret-token",
    "client_id": "auto_or_manual",
    "platform": "auto",   // auto, windows, linux
    "install_path": {
        "windows": "C:\\QtProgram",
        "linux": "/opt/qtprogram"
    },
    "executable_name": "myqtapp.exe",   // windows
    "executable_name_linux": "myqtapp",
    "backup_path": {
        "windows": "C:\\QtDeploy\\backup",
        "linux": "/opt/qtdeploy/backup"
    },
    "heartbeat_interval": 30,
    "log_file": "./client.log"
}

请生成：
1. 服务端代码（一个 Python 文件，包含 Flask 应用、SQLite 数据库操作、HTML 模板）
2. 客户端代码（一个 Python 文件，自动适配 Windows/Linux）
3. Windows 端安装客户端的脚本（使用 nssm 注册为服务）
4. Linux 端安装客户端的 systemd service 文件
5. 配置文件样例（server_config.json, client_config.json）
6. 详细的使用说明文档

注意：所有代码应在纯内网环境下运行，请避免依赖需要实时在线下载的资源。如果有第三方库，请说明如何提前离线安装。尽量使用标准库，减少外部依赖。