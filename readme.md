# 软件部署管理平台

内网批量软件部署系统，支持 Windows/Linux 混合环境，超过100台机器的批量分发。

## 目录结构（仓库）

```
server.py / client.py          # 核心源码（根目录，便于 python server.py / 开发）
server_config.json
client_config.json
install_cfg_client.py          # 安装向导写回 client_config（Windows/Linux 共用）

windows/                       # Windows 安装与辅助（NSSM、VC++、下载脚本）
  install_server.bat / install_windows.bat / uninstall_*.bat / start_*.bat
  download_nssm.bat / download_nssm.ps1
  add_python_user_path.ps1 / python.cmd / pip.cmd
  （可选）nssm.exe、VC_redist.x64.exe — 放此目录或仓库根目录，打包时会一并检测

linux/                         # Linux x86_64 离线资源 + 随包 shell
  bundle/                      # 打进 zip 内 linux/ 目录：install_server.sh、install_linux.sh 等
  zip_root/                    # 发行 zip「解压根」入口：linux_resolve_bundle.sh、install_*_linux.sh 等
  vendor/、python/、requirements-linux-server.txt …

linux_arm/                     # Linux AArch64 离线资源（结构与 linux/ 类似；随包脚本与 x64 共用 linux/bundle/）

tools/                         # 仅构建用（如 print_dll_dir.py，供 build.bat 找 VC++ DLL）

scripts/                       # 打包与离线下载：download_linux_*、package_zip_bundles、zip_exe_bundle、
                               # package_deploy、package_offline、set_build_time 等

build.bat / package_exe_zip.bat（仓库根，一键打发行 zip）
```

发行包 `.zip` 内布局与此前一致（Windows 扁平；Linux zip 根目录含 `linux_resolve_bundle.sh` 与 `linux/`、`linux_arm/` 子树），仅**源文件在仓库中按上表分类**。

从**源码仓库**调试 Linux 入口（未解压 zip）时，可在仓库根执行：`bash linux/zip_root/install_server_linux.sh`（脚本会识别 `linux/zip_root` 布局并定位到仓库根的 `linux/` / `linux_arm/`）。

```
server.exe              # 由 build.bat 生成于 dist\exe\，再打入 Windows 发行 zip
```

发行包.zip 输出（文件名不含日期；包内根目录含 **VERSION.txt**，记录构建时间与 Build ID）：

| 生成文件（均在 `dist\`） | 用途 |
|--------------------------|------|
| `SoftwareDeploy_Windows.zip` | Windows：exe、bat、配置等 |
| `SoftwareDeploy_Linux_x64.zip` | x86_64：`linux_resolve_bundle.sh`、`install_*_linux.sh`、`linux/` 树及离线依赖 |
| `SoftwareDeploy_Linux_arm64.zip` | AArch64：同上结构，`linux_arm/` 树 |

打包前若需 **Linux 完全离线**：先按需执行 **`scripts\download_linux_offline_all.bat`**（x86_64）、**`scripts\download_linux_arm_offline_all.bat`**（ARM64）填好 **`linux/`**、**`linux_arm/`**；可选执行 **`scripts\download_linux_embedded_python.py`**（及 ARM 环境变量）内置解释器。无人值守或 CI：**`package_exe_zip.bat nopause`** 或 **`set NO_PAUSE=1`**。指定本机 Python 根目录：**`set APP_DEPLOY_PYTHON=...\python3.12.x`** 再运行打包脚本。

## 一、服务端部署

### Windows

1. 将整个目录复制到服务器机器
2. 双击 `install_server.bat`（需管理员权限）
3. 自动注册 Windows 服务 `SoftwareDeployServer`，开机自启
4. 第一次装完后要执行一下start_server.bat
5. 访问 `http://服务器IP:61234`
6. 关机重启后，无需操作，服务会自启动
7. 卸载执行uninstall_server.bat
注意：1.bat执行如果权限问题，请右键以管理员身份运行

### Linux

（推荐）解压发行 zip 后，在**解压根目录**（与 `linux_resolve_bundle.sh` 同级）执行：

```bash
sudo chmod +x *.sh linux_resolve_bundle.sh 2>/dev/null; sudo chmod -R a+rX linux linux_arm 2>/dev/null
sudo bash install_server_linux.sh
```

若已 `cd` 到随包目录 **`linux/`** 或 **`linux_arm/`** 内，也可直接：`sudo ./install_server.sh`。

首次安装后如需排查，可用 **`./start_server.sh`**（与 Windows `start_server.bat` 类似，支持 `reset` 释放 61234 端口；若从发行根目录操作，需先进入上述 bundle 目录再执行脚本）。

### 手动启动（不装服务,不推荐）

```bash
pip install flask    # 仅首次
python server.py
# 或直接运行 server.exe
```

## 二、客户端部署

### Windows
0. (很关键)客户端压缩包给机器分发前，先修改client_config.json里的server_url为服务端地址，减少每个客户端部署时的操作；install_path安装路径也根据情况修改
1. 将目录复制到客户端机器（或通过网络共享）
2. 双击 `install_windows.bat`
3. 按提示输入 **服务端 IP**（回车跳过则使用默认配置）
4. 可选输入 **安装路径**（默认 `C:\QtProgram`）
5. 自动注册 Windows 服务 `SoftwareDeployClient`，开机自启
6. 第一次装完后要执行一下start_client.bat，之后不用执行
7. 卸载执行uninstall_windows.bat
注意：bat执行如果权限问题，请右键以管理员身份运行
### Linux

（推荐）解压发行 zip 后，在**解压根目录**执行：

```bash
sudo chmod +x *.sh linux_resolve_bundle.sh 2>/dev/null; sudo chmod -R a+rX linux linux_arm 2>/dev/null
sudo bash install_client_linux.sh
```

若已 `cd` 到随包目录 **`linux/`** 或 **`linux_arm/`** 内，也可直接：`sudo ./install_linux.sh`。

无人值守示例（在对应 bundle 内执行 `install_linux.sh` 时同样有效）：`SWDEPLOY_NONINTERACTIVE=1 SWDEPLOY_SERVER_IP=192.168.1.10 SWDEPLOY_INSTALL_LINUX=/opt/qtprogram sudo -E ./install_linux.sh`（`SWDEPLOY_*` 均为可选）。

### 批量部署客户端

将离线包分发到各机器，双击 `install_windows.bat` 即可。客户端启动后会自动注册到服务端。

## 三、使用流程

### 1. 上传版本包

- 打开服务端网页 → 「版本包」标签
- **先在「产品管理」中添加产品**（初始 `server_config.json` 中 `products` 可为空；产品 ID 规则见下文「产品 ID 与安装目录」）
- 选择 **平台**（Windows/Linux）、**产品**、包类型（全量/增量）
- 上传 zip 文件（全量包会先删后装，增量包覆盖安装）
- 注意压缩 zip 前不要压缩最外层文件夹，直接压缩里面的所有文件  
  可按需修改 `server_config.json` 的 `apps`、`cleanup_paths`
- 版本号留空则自动生成时间戳

### 2. 创建部署任务

- 切换到「部署」标签
- 顺序选择：**平台** → **产品** → **版本号**
- 勾选目标机器（离线机器无法勾选）
- 点击「开始部署」

### 3. 监控进度

- 「任务记录」标签查看所有任务
- 点击「详情」查看每台机器的实时下载/解压进度
- 支持强制停止（会通知客户端中止下载）

### 4. 机器管理

- 「机器管理」标签查看所有已注册客户端
- 客户端启动后自动注册，无需手动添加
- 版本列按产品分列显示，可与最新上传包对比「最新」或「落后」

### 5. 产品管理

- 网页「产品管理」标签：维护产品 **ID** 与 **显示名称**，保存后写入 `server_config.json` 的 `products`
- 产品 ID 须与上传/部署、客户端目录规则一致（仅英文字母与数字，见下节）

## 四、配置说明

### 产品 ID 与客户端安装目录

- **产品 ID**（服务端与客户端约定）：**仅允许英文字母（a–z、A–Z）与数字（0–9）**，**不能含空格、下划线、中文或其它符号**，长度至少 1。网页与接口会对非法 ID 报错。
- **客户端实际安装路径**为：  
  **`install_path` 配置的根目录** + **`/`** + **产品 ID** + **`/`**  
  示例（Windows）：若 `install_path` 为 `C:\QtProgram`，产品 ID 为 `APP1`，则软件解压目录为 **`C:\QtProgram\APP1\`**（其下有 `version.txt`、可执行文件等）。
- **`install_path` 只表示根目录**，不要再在其中写死某一产品的子文件夹名；子文件夹名由部署任务里的 **产品 ID** 决定。
- **`client_config.json` 中 `products` 可为空数组 `[]`**：此时仍使用顶层的 `install_path`、`apps`；服务端下发的任意合法产品 ID 都会解压到 **`install_path/<该产品ID>/`**。若你在客户端配置了多条 `products`（含 `id`、`apps` 等），则部署时会按 **产品 ID** 匹配条目；未配置 `products` 时不校验 ID 是否与列表一致。
- 心跳上报的版本号按 **`install_path/<产品ID>/version.txt`** 读取（与服务端配置的产品列表对齐）。

### server_config.json

| 字段 | 说明 | 默认值 |
|------|------|--------|
| host | 监听地址 | 0.0.0.0 |
| port | 监听端口 | 61234 |
| packages_dir | 版本包存储目录 | ./packages |
| database | 数据库路径 | ./deploy.db |
| heartbeat_timeout | 心跳超时（秒），超时判定离线 | 90 |
| products | 产品列表 `{ "id", "name" }[]`，可在网页「产品管理」维护；可为空，部署前再添加 | [] |
| apps | 管理的应用列表（服务端展示/兼容；客户端仍可用本地 apps） | - |
| cleanup_paths | 部署前清理的路径 | [] |

### client_config.json

| 字段 | 说明 | 默认值 |
|------|------|--------|
| server_url | 服务端地址 | http://127.0.0.1:61234 |
| client_id | 客户端标识，auto 为自动生成 | auto |
| install_path | **安装根目录**（按平台）；真实目录为 **根目录 + `/` + 产品ID + `/`** | Windows: C:\QtProgram, Linux: /opt/qtprogram |
| apps | 管理的应用列表（可执行文件名相对 **上述产品子目录**） | - |
| products | 可选。多产品时为 `{ "id","name","apps",… }[]`；为空则仅用根目录 + 服务端产品 ID 子目录 | [] |
| cleanup_paths | 部署前清理的路径 | [] |
| heartbeat_interval | 心跳间隔（秒） | 30 |
| poll_interval | 任务轮询间隔（秒） | 10 |
| auto_start | 部署完成后自动启动应用 | true |
| max_retries | 下载失败重试次数 | 3 |
| retry_base_delay | 重试基础延迟（秒） | 5 |

### apps 配置示例

```json
"apps": [
    {"name": "主程序", "windows": "myapp.exe", "linux": "myapp"},
    {"name": "辅助工具", "windows": "helper.exe", "linux": "helper"}
]
```

部署时会自动停止所有 app 进程，安装完成后自动启动。

## 五、服务管理命令

### Windows

```bat
# 查看服务状态
sc query SoftwareDeployServer
sc query SoftwareDeployClient

# 启动/停止
nssm start SoftwareDeployServer
nssm stop SoftwareDeployServer

# 卸载
uninstall_server.bat    # 卸载服务端
uninstall_windows.bat   # 卸载客户端
```

### Linux（systemd）

```bash
sudo systemctl start|stop|restart|status swdeploy-server
sudo systemctl start|stop|restart|status swdeploy-client
# 或（脚本封装）
sudo ./start_server.sh   start|stop|restart|status|reset
sudo ./start_client.sh   start|stop|restart|status
```

## 六、注意事项

### 打包规范

1. **zip 第一层必须是根目录**，不要包含外层文件夹。例如安装路径是 `C:\QtProgram`，则 zip 内直接是 `myapp.exe`、`helper.exe` 等文件，不是 `MyApp/myapp.exe`
2. 全量包（full）：部署时先清空 install_path 目录再解压
3. 增量包（incremental）：直接覆盖解压，不清空原有文件

### 网络与防火墙

1. 服务端默认端口 **61234**，需确保防火墙放行
2. 客户端通过 HTTP 轮询服务端，需要能访问服务端 IP:61234
3. 纯内网使用，无认证机制，不要暴露到公网

### Windows 服务相关

1. 安装需**管理员权限**（NSSM 注册服务）
2. 首次安装会自动检测并安装 VC++ 运行时
3. 服务日志：`service_stdout.log`、`service_stderr.log`、`client.log`（或 `server.log`）
4. 重启服务会自动清空日志文件

### 客户端行为

1. 客户端启动后自动注册到服务端，无需手动添加
2. 如果服务端未启动，客户端会等待重试（最多 5 分钟），之后继续后台重试
3. 心跳间隔 30 秒，超过 90 秒无心跳判定为离线
4. 离线客户端不会被选为部署目标

### 部署流程

1. 先停止所有 app 进程 → 下载 → 解压 → 启动所有 app
2. 下载支持断点重试（最多 3 次，指数退避）
3. 支持强制停止：通知客户端中止下载（每 5MB 检查一次停止信号）
4. 服务端重启会自动终止所有未完成的任务

### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 客户端显示离线 | 心跳超时或网络不通 | 检查网络、防火墙、客户端是否运行 |
| 下载速度慢 | 带宽限制 | 7GB 文件在百兆网约需 10 分钟 |
| 解压报拒绝访问 | 文件被占用或权限不足 | 客户端已内置 icacls 权限修复，重试即可 |
| 服务启动失败 | VC++ 运行时缺失 | 运行 `VC_redist.x64.exe` 或重新安装 |
| 配置读取失败 | PowerShell 写入带 BOM | 客户端已兼容 utf-8-sig 编码 |
| 中文文件名下载失败 | URL 编码问题 | 已自动处理 RFC 5987 编码 |

## 七、API 接口

基础地址：`http://服务器IP:61234`

### 连通性

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/ping` | GET | 连通性测试，返回 `{"status":"ok","time":"..."}` |
| `/api/config` | GET | 获取配置：`apps`、`products`（产品 ID 与名称列表）、`port` |
| `/api/debug` | GET | 诊断信息（机器状态、心跳详情） |

```bash
http://192.168.1.100:61234/api/ping
http://192.168.1.100:61234/api/config
http://192.168.1.100:61234/api/debug
```

### 产品列表（与「产品管理」页一致）

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/products` | GET | 返回当前产品数组：`[{"id":"APP1","name":"业务A"}, ...]`。与 `server_config.json` 中 `products` 一致；未配置时可为 `[]`。 |
| `/api/products` | PUT | **整体替换** 产品列表。请求体为 JSON：`{"products":[{"id":"APP1","name":"业务A"},{"id":"APP2","name":"工具B"}]}`。允许 `{"products":[]}` 表示尚未配置任何产品。每个 `id` 须为 **仅英文字母与数字**（与「产品 ID 与客户端安装目录」规则相同）。若删除的某个 `id` 在数据库中仍有版本包记录，接口会返回错误，无法删除。成功后会写入 `server_config.json`。 |

**GET 示例：**

```bash
curl -s http://192.168.1.100:61234/api/products
```

**PUT 示例：**

```bash
curl -s -X PUT http://192.168.1.100:61234/api/products ^
  -H "Content-Type: application/json" ^
  -d "{\"products\":[{\"id\":\"APP1\",\"name\":\"业务系统\"}]}"
```

（Linux / macOS 下将 `^` 换行改为 `\`，或写成一行。）

## 八、发行打包与开发说明

### 一键打包（Windows 开发机）

1. 安装 Python 3.x，并 `pip install pyinstaller`（及项目依赖，用于 `build.bat`）。
2. （可选）设置固定 Python 路径：`set APP_DEPLOY_PYTHON=C:\Path\to\python3.12`（含 `python.exe` 的目录）。
3. Linux 完全离线 / 减小安装时下载：在仓库根执行 **`scripts\download_linux_offline_all.bat`**、**`scripts\download_linux_arm_offline_all.bat`**；需要随包内置解释器时再运行 **`python scripts\download_linux_embedded_python.py`**（x86_64 默认写入 `linux/python`；ARM64 请设置环境变量 `LINUX_PACK_ROOT=linux_arm`、`CPYTHON_LINUX_ARCH=aarch64` 后执行）。可选 **`--dedupe-existing`** 仅对已有 `linux/python`、`linux_arm/python` 去重（见下）。
4. 执行 **`package_exe_zip.bat`**，加 **`nopause`** 或设置 **`NO_PAUSE=1`** 可跳过结束暂停。  
   主脚本会调用 **`build.bat`** 生成 `dist\exe\server.exe` 与 `client.exe`，再调用 **`scripts\package_zip_bundles.bat`** 生成 **`dist\SoftwareDeploy_Windows.zip`**、**`SoftwareDeploy_Linux_x64.zip`**、**`SoftwareDeploy_Linux_arm64.zip`**。一般**不需要**单独运行 **`scripts\set_build_time.bat`** 或 **`scripts\package_zip_bundles.bat`**。

### 发行包内 VERSION.txt

每个 zip 根目录的 **VERSION.txt** 含构建时间、Build ID 与包类型说明，**压缩包文件名本身不含日期**。

### Linux 随包 Python 与体积说明

在 Windows 上准备仓库时，嵌入式 CPython 的 tar 内**符号链接**可能被展开成多份大文件（如 `libpython3.12.so` 与 `.so.1.0`）。`download_linux_embedded_python.py` 在下载后会去重；**`install_server.sh` / `install_linux.sh`** 在目标 Linux 上安装时也会把重复库/解释器改回软链接。若你自行替换 `linux/python` 目录，可执行：  
`python scripts\download_linux_embedded_python.py --dedupe-existing`。

### 源码修改与 PyInstaller

若修改了 **`client.py` / `server.py`**，需在开发环境用 **PyInstaller** 重新执行 **`build.bat`** 或 **`package_exe_zip.bat`** 生成新的 `server.exe`、`client.exe`；**现场运维包通常不含 Python 开发环境**，一般不能在现场改代码再编 exe。

若改为 **`python server.py` / `python client.py`** 直接运行，可配合调试改代码，但 **Windows 下通过 NSSM 管理的是 exe 服务**；仅脚本运行需自行用任务计划或 systemd 等方式做开机自启，与「一键安装bat + exe 服务」流程不同。