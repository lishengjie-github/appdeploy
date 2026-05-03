#!/usr/bin/env python3
"""
跨平台内网软件部署管理平台 - 客户端
自动适配 Windows / Linux，支持多应用部署
"""

import os
import sys
import json
import time
import socket
import shutil
import hashlib
import zipfile
import tarfile
import logging
import platform
import subprocess
import threading
import traceback
from datetime import datetime

# ── 配置加载 ──────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "client_config.json")

DEFAULT_CONFIG = {
    "server_url": "http://127.0.0.1:61234",
    "client_id": "auto",
    "platform": "auto",
    "install_path": {
        "windows": "C:\\QtProgram",
        "linux": "/opt/qtprogram"
    },
    "apps": [
        {"name": "主程序", "windows": "myqtapp.exe", "linux": "myqtapp"},
        {"name": "辅助工具", "windows": "helper.exe", "linux": "helper"},
    ],
    "cleanup_paths": [],
    "heartbeat_interval": 30,
    "poll_interval": 10,
    "log_file": "./client.log",
    "auto_start": True,
    "max_retries": 3,
    "retry_base_delay": 5,
}

def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            for k, v in user_cfg.items():
                if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"[警告] 配置文件读取失败，使用默认配置: {e}")
    return cfg

config = load_config()

# ── 平台检测 ──────────────────────────────────────────────────────────────────

def detect_platform():
    p = config.get("platform", "auto")
    if p in ("windows", "linux"):
        return p
    return "windows" if platform.system().lower() == "windows" else "linux"

PLATFORM = detect_platform()

def get_config_value(key):
    val = config.get(key, "")
    if isinstance(val, dict):
        return val.get(PLATFORM, "")
    return val

SERVER_URL = config["server_url"].rstrip("/")
HEARTBEAT_INTERVAL = config.get("heartbeat_interval", 30)
POLL_INTERVAL = config.get("poll_interval", 10)
INSTALL_PATH = get_config_value("install_path")
APPS = config.get("apps", [])
AUTO_START = config.get("auto_start", True)
MAX_RETRIES = config.get("max_retries", 3)
RETRY_BASE_DELAY = config.get("retry_base_delay", 5)
CLEANUP_PATHS = config.get("cleanup_paths", [])
_log_raw = get_config_value("log_file") or config.get("log_file", "client.log")
if os.path.isabs(_log_raw):
    LOG_FILE = os.path.normpath(_log_raw)
else:
    # Always next to client.py (Windows services cwd may be wrong)
    LOG_FILE = os.path.normpath(os.path.join(BASE_DIR, _log_raw))

# ── 日志 ──────────────────────────────────────────────────────────────────────

_log_dir = os.path.dirname(LOG_FILE)
if _log_dir:
    os.makedirs(_log_dir, exist_ok=True)

_log_handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
# pythonw.exe has no console; StreamHandler can break startup on some hosts
if not (sys.platform == "win32" and sys.executable.lower().endswith("pythonw.exe")):
    _log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("deploy-client")

# ── HTTP 工具（纯标准库） ─────────────────────────────────────────────────────

import urllib.request
import urllib.error

def http_request(method, path, data=None, json_data=None, timeout=30):
    url = SERVER_URL + path
    headers = {}
    body = None
    if json_data is not None:
        body = json.dumps(json_data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif data is not None:
        body = data if isinstance(data, bytes) else data.encode("utf-8")
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.error("HTTP %d: %s - %s", e.code, path, err_body)
        raise
    except urllib.error.URLError as e:
        logger.error("连接失败: %s - %s", path, e.reason)
        raise

def api_get(path, timeout=30):
    return http_request("GET", path, timeout=timeout)

def api_post(path, json_data=None, timeout=30):
    return http_request("POST", path, json_data=json_data, timeout=timeout)

# ── 进度上报 ──────────────────────────────────────────────────────────────────

def report_progress(task_id, phase, percent, detail=""):
    try:
        api_post(f"/api/progress/{task_id}/{CLIENT_ID}", json_data={
            "phase": phase, "percent": int(percent), "detail": detail
        }, timeout=5)
    except Exception:
        pass

# ── 客户端标识 ────────────────────────────────────────────────────────────────

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_hostname():
    return socket.gethostname()

def get_qt_version():
    version_file = os.path.join(INSTALL_PATH, "version.txt")
    if os.path.exists(version_file):
        try:
            with open(version_file, "r") as f:
                return f.read().strip()
        except Exception:
            pass
    return "unknown"

def get_client_id():
    import uuid
    cid = config.get("client_id", "auto")
    if cid and cid != "auto":
        return cid
    mac = uuid.getnode()
    return hashlib.md5(f"{get_hostname()}-{mac}".encode()).hexdigest()[:12]

try:
    CLIENT_ID = get_client_id()
except Exception:
    CLIENT_ID = get_hostname()

LOCAL_IP = get_local_ip()

# ── 心跳 ──────────────────────────────────────────────────────────────────────

def send_heartbeat():
    global CLIENT_ID
    data = {
        "client_id": CLIENT_ID,
        "ip": LOCAL_IP,
        "hostname": get_hostname(),
        "platform": PLATFORM,
        "qt_version": get_qt_version(),
    }
    try:
        resp = api_post("/api/heartbeat", json_data=data)
        if resp.get("client_id"):
            CLIENT_ID = resp["client_id"]
        logger.debug("心跳成功")
    except Exception as e:
        logger.warning("心跳失败: %s", e)

def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

# ── 进程管理（多应用） ───────────────────────────────────────────────────────

def stop_all_apps():
    """停止所有应用进程"""
    for app in APPS:
        exe = app.get(PLATFORM, "")
        if not exe:
            continue
        logger.info("停止应用 [%s]: %s", app.get("name", ""), exe)
        try:
            if PLATFORM == "windows":
                subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True, timeout=15)
            else:
                subprocess.run(["pkill", "-f", exe], capture_output=True, timeout=15)
            logger.info("已发送停止命令: %s", exe)
        except subprocess.TimeoutExpired:
            logger.warning("停止命令超时: %s", exe)
        except Exception as e:
            logger.warning("停止进程异常（可能未运行）: %s - %s", exe, e)
    time.sleep(2)

def start_all_apps():
    """启动所有应用进程"""
    for app in APPS:
        exe = app.get(PLATFORM, "")
        if not exe:
            continue
        exe_path = os.path.join(INSTALL_PATH, exe)
        if not os.path.exists(exe_path):
            logger.warning("可执行文件不存在，跳过: %s", exe_path)
            continue
        logger.info("启动应用 [%s]: %s", app.get("name", ""), exe_path)
        try:
            if PLATFORM == "windows":
                subprocess.Popen(
                    [exe_path], cwd=INSTALL_PATH,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                    close_fds=True,
                )
            else:
                os.chmod(exe_path, 0o755)
                subprocess.Popen(
                    ["nohup", exe_path], cwd=INSTALL_PATH,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            logger.info("已启动: %s", exe)
        except Exception as e:
            logger.error("启动失败: %s - %s", exe, e)

# ── 文件下载 ──────────────────────────────────────────────────────────────────

def download_file(url, dest_path, expected_md5=None, progress_cb=None):
    full_url = SERVER_URL + url
    logger.info("下载: %s -> %s", url, dest_path)
    tmp_path = dest_path + ".tmp"
    try:
        req = urllib.request.Request(full_url)
        with urllib.request.urlopen(req, timeout=3600) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            md5 = hashlib.md5()
            last_report = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(1048576)  # 1MB chunks for large files
                    if not chunk:
                        break
                    f.write(chunk)
                    md5.update(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        if pct - last_report >= 1 or downloaded == total:
                            last_report = pct
                            logger.info("下载进度: %.1f%% (%d/%d MB)", pct, downloaded // 1048576, total // 1048576)
                            if progress_cb:
                                progress_cb(pct)
        if expected_md5:
            actual_md5 = md5.hexdigest()
            if actual_md5 != expected_md5:
                logger.error("MD5 校验失败: 期望 %s, 实际 %s", expected_md5, actual_md5)
                os.remove(tmp_path)
                return False
            logger.info("MD5 校验通过: %s", actual_md5)
        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.rename(tmp_path, dest_path)
        logger.info("下载完成: %s (%d bytes)", dest_path, downloaded)
        return True
    except Exception as e:
        logger.error("下载失败: %s\n%s", e, traceback.format_exc())
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False

# ── 解压 ──────────────────────────────────────────────────────────────────────

def extract_package(archive_path, package_type="full"):
    logger.info("解压: %s -> %s (类型=%s)", archive_path, INSTALL_PATH, package_type)
    try:
        # 全量包：先删除清理路径和安装目录
        if package_type == "full":
            for cp in CLEANUP_PATHS:
                if os.path.isdir(cp):
                    logger.info("清理目录: %s", cp)
                    shutil.rmtree(cp, ignore_errors=True)
                elif os.path.isfile(cp):
                    logger.info("清理文件: %s", cp)
                    try:
                        os.remove(cp)
                    except OSError:
                        pass
            if os.path.isdir(INSTALL_PATH):
                logger.info("全量模式: 删除安装目录 %s", INSTALL_PATH)
                shutil.rmtree(INSTALL_PATH)
        os.makedirs(INSTALL_PATH, exist_ok=True)
        if archive_path.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(INSTALL_PATH)
        elif archive_path.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(INSTALL_PATH)
        elif archive_path.endswith(".tar"):
            with tarfile.open(archive_path, "r") as tf:
                tf.extractall(INSTALL_PATH)
        else:
            logger.error("不支持的压缩格式: %s", archive_path)
            return False
        logger.info("解压完成")
        if PLATFORM == "linux":
            for app in APPS:
                exe = app.get("linux", "")
                if exe:
                    exe_path = os.path.join(INSTALL_PATH, exe)
                    if os.path.exists(exe_path):
                        os.chmod(exe_path, 0o755)
            for root, dirs, files in os.walk(INSTALL_PATH):
                for f in files:
                    if f.endswith((".so", ".sh")):
                        try:
                            os.chmod(os.path.join(root, f), 0o755)
                        except OSError:
                            pass
        return True
    except Exception as e:
        logger.error("解压失败: %s\n%s", e, traceback.format_exc())
        return False

def save_version(version):
    try:
        with open(os.path.join(INSTALL_PATH, "version.txt"), "w") as f:
            f.write(version)
    except Exception:
        pass

# ── 部署任务执行 ──────────────────────────────────────────────────────────────

def execute_task(task):
    task_id = task["task_id"]
    version = task["version"]
    filename = task["filename"]
    expected_md5 = task.get("md5")
    download_url = task["download_url"]
    package_type = task.get("package_type", "full")

    log_lines = []
    def log(msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        log_lines.append(line)
        logger.info(msg)

    def on_download_progress(pct):
        report_progress(task_id, "下载中", pct * 0.7, f"下载进度 {pct:.1f}%")

    log(f"开始部署任务 {task_id}, 版本 {version}, 应用数: {len(APPS)}, 包类型: {package_type}")
    report_progress(task_id, "准备中", 0, "开始部署")

    tmp_dir = os.path.join(BASE_DIR, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    archive_path = os.path.join(tmp_dir, filename)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"[尝试 {attempt}/{MAX_RETRIES}] 下载安装包...")
            report_progress(task_id, "下载中", 0, f"尝试 {attempt}/{MAX_RETRIES}")
            if not download_file(download_url, archive_path, expected_md5, progress_cb=on_download_progress):
                log("下载失败")
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log(f"等待 {delay} 秒后重试...")
                    time.sleep(delay)
                    continue
                log("下载失败，已达最大重试次数")
                report_progress(task_id, "失败", 0, "下载失败")
                report_result(task_id, "failed", "\n".join(log_lines))
                return

            log("停止所有应用...")
            report_progress(task_id, "停止应用", 70, "停止所有应用进程")
            stop_all_apps()

            log(f"解压新版本 (类型={package_type})...")
            report_progress(task_id, "解压中", 75, "正在解压安装包")
            if not extract_package(archive_path, package_type):
                log("解压失败")
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log(f"等待 {delay} 秒后重试...")
                    time.sleep(delay)
                    continue
                log("解压失败，已达最大重试次数")
                report_progress(task_id, "失败", 0, "解压失败")
                report_result(task_id, "failed", "\n".join(log_lines))
                return

            save_version(version)
            report_progress(task_id, "完成", 100, "部署成功")

            if AUTO_START:
                log("启动所有应用...")
                start_all_apps()

            log(f"部署成功！版本: {version}")
            report_result(task_id, "success", "\n".join(log_lines))
            try:
                os.remove(archive_path)
            except OSError:
                pass
            return

        except Exception as e:
            log(f"部署异常: {e}\n{traceback.format_exc()}")
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log(f"等待 {delay} 秒后重试...")
                time.sleep(delay)
            else:
                log("已达最大重试次数")
                report_result(task_id, "failed", "\n".join(log_lines))

def report_result(task_id, status, log_text):
    try:
        api_post(f"/api/report/{task_id}/{CLIENT_ID}", json_data={"status": status, "log": log_text})
        logger.info("结果已上报: %s -> %s", task_id, status)
    except Exception as e:
        logger.error("结果上报失败: %s", e)

# ── 任务轮询 ──────────────────────────────────────────────────────────────────

def poll_loop():
    logger.info("开始轮询任务，间隔 %d 秒", POLL_INTERVAL)
    while True:
        try:
            task = api_get(f"/api/poll/{CLIENT_ID}", timeout=15)
            if task and task.get("task_id"):
                logger.info("收到部署任务: %s", task["task_id"])
                t = threading.Thread(target=execute_task, args=(task,), daemon=True)
                t.start()
        except Exception as e:
            logger.debug("轮询异常: %s", e)
        time.sleep(POLL_INTERVAL)

# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 55)
    logger.info("软件部署客户端启动")
    logger.info("平台: %s", PLATFORM)
    logger.info("客户端ID: %s", CLIENT_ID)
    logger.info("本机IP: %s", LOCAL_IP)
    logger.info("主机名: %s", get_hostname())
    logger.info("服务端: %s", SERVER_URL)
    logger.info("安装路径: %s", INSTALL_PATH)
    logger.info("管理应用: %s", ", ".join(a.get("name","") for a in APPS))
    logger.info("=" * 55)

    os.makedirs(INSTALL_PATH, exist_ok=True)

    # 测试服务端连接
    logger.info("正在测试服务端连接...")
    try:
        resp = api_get("/api/config", timeout=5)
        logger.info("服务端连接成功，版本包应用数: %d", len(resp.get("apps", [])))
    except Exception as e:
        logger.error("无法连接服务端 %s: %s", SERVER_URL, e)
        logger.error("请检查: 1) 服务端是否启动  2) IP和端口是否正确  3) 防火墙是否放行 %s", SERVER_URL)

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    try:
        poll_loop()
    except KeyboardInterrupt:
        logger.info("客户端已停止")
    except Exception as e:
        logger.error("客户端异常退出: %s\n%s", e, traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
