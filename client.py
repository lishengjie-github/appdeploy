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
import re
from datetime import datetime

# 与服务端一致：产品 ID 仅英文字母与数字（作安装子目录名）
PRODUCT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]+$")


def is_valid_product_id(pid):
    return bool(pid and PRODUCT_ID_PATTERN.match(str(pid).strip()))

# ── 配置加载 ──────────────────────────────────────────────────────────────────

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "client_config.json")
# client_id=auto 时写入此文件；避免 Linux 重启后 uuid.getnode() 变化导致服务端出现重复机器条目
CLIENT_ID_FILE = os.path.join(BASE_DIR, ".swdeploy_client_id")

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
    "products": [],
    "heartbeat_interval": 30,
    "poll_interval": 10,
    "log_file": "./client.log",
    "auto_start": True,
    "max_retries": 3,
    "retry_base_delay": 5,
    "report_ip": "auto",
}

def load_config():
    cfg = DEFAULT_CONFIG.copy()
    config_source = CONFIG_FILE

    # PyInstaller onefile: --add-data bundles config into temp _MEI dir.
    # If external config (next to exe) doesn't exist, try the bundled one.
    if not os.path.exists(CONFIG_FILE) and getattr(sys, 'frozen', False):
        internal_cfg = os.path.join(sys._MEIPASS, "client_config.json")
        if os.path.exists(internal_cfg):
            config_source = internal_cfg

    if os.path.exists(config_source):
        try:
            with open(config_source, "r", encoding="utf-8-sig") as f:
                user_cfg = json.load(f)
            for k, v in user_cfg.items():
                if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"[警告] 配置文件读取失败，使用默认配置: {e}")
    else:
        print(f"[警告] 配置文件不存在: {CONFIG_FILE}，使用默认配置")
        print(f"[提示] 请将 client_config.json 放在 {BASE_DIR} 目录下")

    # Write default config next to exe if missing, so user can edit it
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

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


def get_install_root():
    """配置中的安装根目录（不含产品子目录）。"""
    ip = config.get("install_path")
    if isinstance(ip, dict):
        return ip.get(PLATFORM, "") or ""
    return ip or ""


def install_dir_for_product_id(product_id):
    """实际安装目录 = install_path 根目录 + 产品 ID 子目录。"""
    root = get_install_root()
    pid = (product_id or "default").strip() or "default"
    if not is_valid_product_id(pid):
        raise ValueError(f"无效产品 ID: {pid!r}（仅英文字母与数字）")
    return os.path.normpath(os.path.join(root, pid))


def normalize_products(cfg):
    raw = cfg.get("products")
    if isinstance(raw, list) and len(raw) > 0:
        out = []
        for p in raw:
            pid = str(p.get("id") or "").strip()
            if not is_valid_product_id(pid):
                print(f"[警告] 跳过无效产品 ID（仅英文字母与数字）: {p.get('id')!r}")
                continue
            merged_apps = p.get("apps")
            if merged_apps is None:
                merged_apps = cfg.get("apps", [])
            merged_cleanup = p.get("cleanup_paths")
            if merged_cleanup is None:
                merged_cleanup = cfg.get("cleanup_paths", [])
            out.append({
                "id": pid,
                "name": p.get("name") or pid,
                "apps": merged_apps,
                "cleanup_paths": merged_cleanup,
            })
        if out:
            return out
    return [{
        "id": "default",
        "name": "默认产品",
        "apps": cfg.get("apps", []),
        "cleanup_paths": cfg.get("cleanup_paths", []),
    }]

PRODUCT_LIST = normalize_products(config)
PRODUCT_BY_ID = {p["id"]: p for p in PRODUCT_LIST}


def is_legacy_no_products_config():
    """未在配置中填写 products，或 products 为空数组时，使用顶层 install_path/apps。"""
    raw = config.get("products")
    return not isinstance(raw, list) or len(raw) == 0


def resolve_product_for_deploy(server_product_id):
    """解析部署任务使用的产品配置；未配置 products 时任意服务端 product_id 均映射到本机单一安装目录。"""
    pid = (server_product_id or "default").strip() or "default"
    if pid in PRODUCT_BY_ID:
        return PRODUCT_BY_ID[pid]
    if is_legacy_no_products_config() and PRODUCT_LIST:
        try:
            pdir = install_dir_for_product_id(pid)
        except ValueError:
            pdir = os.path.join(get_install_root(), pid)
        logger.info(
            "客户端未配置 products，将服务端产品「%s」解压/运行目录: %s",
            pid,
            pdir,
        )
        return PRODUCT_LIST[0]
    logger.error("未知产品 ID: %s，已配置: %s", pid, list(PRODUCT_BY_ID.keys()))
    return None


SERVER_URL = config["server_url"].rstrip("/")
HEARTBEAT_INTERVAL = config.get("heartbeat_interval", 30)
POLL_INTERVAL = config.get("poll_interval", 10)
INSTALL_PATH = get_install_root()
APPS = PRODUCT_LIST[0]["apps"] if PRODUCT_LIST else []
AUTO_START = config.get("auto_start", True)
MAX_RETRIES = config.get("max_retries", 3)
RETRY_BASE_DELAY = config.get("retry_base_delay", 5)
CLEANUP_PATHS = PRODUCT_LIST[0]["cleanup_paths"] if PRODUCT_LIST else []
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

_log_handlers = []
try:
    _log_handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
except Exception:
    # 日志文件创建失败时回退到脚本目录
    _fallback_log = os.path.join(BASE_DIR, "client.log")
    try:
        _log_handlers.append(logging.FileHandler(_fallback_log, encoding="utf-8"))
    except Exception:
        pass

# pythonw.exe has no console; StreamHandler can break startup on some hosts
if not (sys.platform == "win32" and sys.executable.lower().endswith("pythonw.exe")):
    _log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers if _log_handlers else [logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("deploy-client")

# ── HTTP 工具（纯标准库） ─────────────────────────────────────────────────────

import urllib.request
import urllib.error
import urllib.parse

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


_SERVER_PIDS_CACHE = None
_SERVER_PIDS_TS = 0.0


def get_server_product_ids():
    """缓存服务端配置的产品 ID，供未配置 products 的客户端心跳上报多列版本。"""
    global _SERVER_PIDS_CACHE, _SERVER_PIDS_TS
    now = time.time()
    if _SERVER_PIDS_CACHE is not None and now - _SERVER_PIDS_TS < 300:
        return _SERVER_PIDS_CACHE
    try:
        c = api_get("/api/config", timeout=5)
        prods = c.get("products") or []
        ids = []
        for p in prods:
            if isinstance(p, dict) and p.get("id"):
                sid = str(p["id"]).strip()
                if is_valid_product_id(sid):
                    ids.append(sid)
        _SERVER_PIDS_CACHE = ids
        _SERVER_PIDS_TS = now
        return ids
    except Exception:
        return _SERVER_PIDS_CACHE if _SERVER_PIDS_CACHE is not None else []


def collect_product_versions():
    """install_path/<产品ID>/version.txt -> { product_id: version }。"""

    def read_ver_at(folder_pid):
        try:
            vf = os.path.join(install_dir_for_product_id(folder_pid), "version.txt")
            if os.path.isfile(vf):
                with open(vf, "r") as f:
                    return f.read().strip()
        except ValueError:
            pass
        except Exception:
            pass
        return ""

    if is_legacy_no_products_config() and PRODUCT_LIST:
        srv_ids = get_server_product_ids()
        ver = ""
        for cand in list(srv_ids) + [PRODUCT_LIST[0]["id"]]:
            if not cand:
                continue
            v = read_ver_at(cand)
            if v:
                ver = v
                break
        ver = ver or "unknown"
        if srv_ids:
            return {sid: ver for sid in srv_ids}
        return {PRODUCT_LIST[0]["id"]: ver}

    out = {}
    for prod in PRODUCT_LIST:
        pid = prod["id"]
        out[pid] = read_ver_at(pid) or "unknown"
    return out


# ── 进度上报 ──────────────────────────────────────────────────────────────────

def report_progress(task_id, phase, percent, detail=""):
    try:
        api_post(f"/api/progress/{task_id}/{CLIENT_ID}", json_data={
            "phase": phase, "percent": int(percent), "detail": detail
        }, timeout=5)
    except Exception:
        pass

# Throttle status polls during download (avoid hammering server; still responsive)
_TASK_STOP_CACHE = {}  # task_id -> (time.monotonic(), bool)

def is_task_stopped(task_id):
    """Return True if admin force-stopped this task (deployments.status == stopped)."""
    now = time.monotonic()
    hit = _TASK_STOP_CACHE.get(task_id)
    if hit and (now - hit[0]) < 0.4:
        return hit[1]
    try:
        result = api_get(f"/api/tasks/{task_id}/status", timeout=5)
        stopped = bool(result and result.get("status") == "stopped")
        _TASK_STOP_CACHE[task_id] = (now, stopped)
        return stopped
    except Exception as e:
        logger.debug("查询任务是否停止失败: %s", e)
        return False

# ── 客户端标识 ────────────────────────────────────────────────────────────────

def _ipv4_to_int(ip_str):
    ip_str = (ip_str or "").strip()
    parts = ip_str.split(".")
    if len(parts) != 4:
        return None
    try:
        nums = [int(p) for p in parts]
        if any(n < 0 or n > 255 for n in nums):
            return None
        return (nums[0] << 24) | (nums[1] << 16) | (nums[2] << 8) | nums[3]
    except ValueError:
        return None


def _common_prefix_bits(a_int, b_int):
    xor = (a_int ^ b_int) & 0xFFFFFFFF
    if xor == 0:
        return 32
    return 32 - xor.bit_length()


def _is_rfc1918_ipv4_str(ip_str):
    n = _ipv4_to_int(ip_str)
    if n is None:
        return False
    o1 = (n >> 24) & 0xFF
    o2 = (n >> 16) & 0xFF
    if o1 == 10:
        return True
    if o1 == 172 and 16 <= o2 <= 31:
        return True
    if o1 == 192 and o2 == 168:
        return True
    return False


def _is_loopback_server_host(host):
    if not host:
        return True
    h = host.strip().lower()
    if h in ("localhost", "::1", "0.0.0.0"):
        return True
    if h == "127.0.0.1":
        return True
    if h.startswith("127."):
        return True
    return False


def _udp_source_ip(host, port):
    try:
        p = int(port)
    except (TypeError, ValueError):
        p = 80
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect((host, p))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None
    except Exception:
        return None


def _server_host_port_from_url(server_url):
    parsed = urllib.parse.urlparse(server_url)
    host = parsed.hostname
    if parsed.port:
        port = parsed.port
    else:
        port = 443 if (parsed.scheme or "http").lower() == "https" else 80
    return host, port


def _resolve_first_ipv4(host):
    if not host:
        return None
    host = host.strip()
    if _ipv4_to_int(host) is not None:
        return host
    try:
        for _fam, _typ, _proto, _canon, sockaddr in socket.getaddrinfo(
            host, None, socket.AF_INET, socket.SOCK_DGRAM
        ):
            addr = sockaddr[0]
            if _ipv4_to_int(addr) is not None:
                return addr
    except OSError:
        pass
    return None


def _hostname_ipv4_candidates():
    out = []
    try:
        _alias, _alias2, ip_list = socket.gethostbyname_ex(socket.gethostname())
        for ip in ip_list:
            if _ipv4_to_int(ip) is not None and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out


def get_local_ip():
    """本机 IPv4：候选列表 + 与部署服务器地址最长公共前缀；失败则 UDP 探测与 127.0.0.1。"""
    rp = config.get("report_ip", "auto")
    if isinstance(rp, str):
        rp_st = rp.strip()
        if rp_st and rp_st.lower() != "auto" and _ipv4_to_int(rp_st) is not None:
            return rp_st

    host, port = _server_host_port_from_url(SERVER_URL)
    candidates = []
    candidates.extend(_hostname_ipv4_candidates())

    udp_to_server = None
    if host and not _is_loopback_server_host(host):
        udp_to_server = _udp_source_ip(host, port)
        if udp_to_server and _ipv4_to_int(udp_to_server) and not udp_to_server.startswith("127."):
            candidates.append(udp_to_server)

    seen = set()
    uniq_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq_candidates.append(c)
    candidates = uniq_candidates

    server_ip = None
    if host and not _is_loopback_server_host(host):
        server_ip = _resolve_first_ipv4(host)

    best = None
    best_key = None
    s_int = _ipv4_to_int(server_ip) if server_ip else None
    if s_int is not None:
        srv_169 = server_ip.startswith("169.254.")
        for cand in candidates:
            if cand.startswith("169.254.") and not srv_169:
                continue
            c_int = _ipv4_to_int(cand)
            if c_int is None:
                continue
            bits = _common_prefix_bits(c_int, s_int)
            bonus = 1 if _is_rfc1918_ipv4_str(cand) else 0
            key = (bits, bonus)
            if best_key is None or key > best_key:
                best_key = key
                best = cand

    if best:
        logger.debug("本机 IP 选用(与服务器最长前缀): %s", best)
        return best

    if udp_to_server and not udp_to_server.startswith("127."):
        logger.debug("本机 IP 选用(UDP 至服务器): %s", udp_to_server)
        return udp_to_server

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            logger.debug("本机 IP 选用(8.8.8.8 路由): %s", ip)
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def get_hostname():
    return socket.gethostname()


def _read_linux_machine_id():
    """systemd/DBus 机器 ID，重启不变。"""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                s = f.read().strip()
                if s:
                    return s
        except OSError:
            continue
    return ""


def _read_windows_machine_guid():
    if PLATFORM != "windows":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
        ) as k:
            v, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(v).strip()
    except Exception:
        return ""


def _stable_machine_fingerprint():
    if PLATFORM == "linux":
        return _read_linux_machine_id()
    if PLATFORM == "windows":
        return _read_windows_machine_guid()
    return ""


def _persist_auto_client_id(cid):
    if config.get("client_id", "auto") != "auto":
        return
    try:
        with open(CLIENT_ID_FILE, "w", encoding="utf-8") as f:
            f.write(cid)
    except OSError:
        pass


def get_client_id():
    """auto：优先读本地持久化文件，再用主机名+机器指纹+网卡派生 ID（避免仅依赖 uuid.getnode 在 Linux 上重启变化）。"""
    import uuid

    cid = config.get("client_id", "auto")
    if cid and cid != "auto":
        return cid

    if os.path.isfile(CLIENT_ID_FILE):
        try:
            with open(CLIENT_ID_FILE, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved and 8 <= len(saved) <= 64 and re.match(r"^[a-fA-F0-9]+$", saved):
                return saved
        except OSError:
            pass

    fp = _stable_machine_fingerprint()
    mac = uuid.getnode()
    basis = f"{get_hostname()}\0{fp}\0{mac}"
    new_id = hashlib.md5(basis.encode("utf-8")).hexdigest()[:12]
    _persist_auto_client_id(new_id)
    return new_id


try:
    CLIENT_ID = get_client_id()
except Exception:
    CLIENT_ID = get_hostname()

LOCAL_IP = get_local_ip()

# ── 心跳 ──────────────────────────────────────────────────────────────────────

def send_heartbeat():
    global CLIENT_ID
    pv = collect_product_versions()
    summary = "; ".join(f"{k}:{v}" for k, v in sorted(pv.items()))
    data = {
        "client_id": CLIENT_ID,
        "ip": LOCAL_IP,
        "hostname": get_hostname(),
        "platform": PLATFORM,
        "qt_version": summary or "unknown",
        "product_versions": pv,
    }
    try:
        resp = api_post("/api/heartbeat", json_data=data)
        if resp.get("client_id"):
            CLIENT_ID = resp["client_id"]
        _persist_auto_client_id(CLIENT_ID)
        logger.debug("心跳成功")
    except Exception as e:
        logger.warning("心跳失败: %s", e)

def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

# ── 进程管理（多应用） ───────────────────────────────────────────────────────

def stop_apps_for_product(prod, folder_product_id):
    """停止某一产品目录下的应用进程（目录 = install_path/<folder_product_id>/）。"""
    try:
        inst = install_dir_for_product_id(folder_product_id)
    except ValueError as e:
        logger.warning("%s", e)
        return
    apps = prod.get("apps") or []
    label = prod.get("name") or prod.get("id", "")
    for app in apps:
        exe = app.get(PLATFORM, "")
        if not exe:
            continue
        logger.info("停止 [%s] 应用 [%s]: %s", label, app.get("name", ""), exe)
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

def stop_all_apps():
    """停止所有已配置产品下的应用进程"""
    for prod in PRODUCT_LIST:
        stop_apps_for_product(prod, prod["id"])

def start_main_app_for_product(prod, folder_product_id):
    """仅启动该产品目录下「主程序」（或 apps 第一项）。"""
    try:
        inst = install_dir_for_product_id(folder_product_id)
    except ValueError as e:
        logger.warning("%s", e)
        return
    apps = prod.get("apps") or []
    if not apps:
        logger.warning("产品 %s apps 为空，跳过启动", prod.get("id"))
        return
    target = None
    for app in apps:
        if (app.get("name") or "").strip() == "主程序":
            target = app
            break
    if target is None:
        target = apps[0]
        logger.info(
            "产品 %s 未找到 name=主程序，按兼容策略仅启动第一项: %s",
            prod.get("id"), target.get("name", ""),
        )
    exe = target.get(PLATFORM, "")
    if not exe:
        logger.warning("产品 %s 主程序未配置 %s 可执行文件名", prod.get("id"), PLATFORM)
        return
    exe_path = os.path.join(inst, exe)
    if not os.path.exists(exe_path):
        logger.warning("可执行文件不存在，跳过: %s", exe_path)
        return
    logger.info("启动 [%s] 主程序 [%s]: %s", prod.get("id"), target.get("name", ""), exe_path)
    try:
        if PLATFORM == "windows":
            subprocess.Popen(
                [exe_path], cwd=inst,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
        else:
            os.chmod(exe_path, 0o755)
            subprocess.Popen(
                ["nohup", exe_path], cwd=inst,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        logger.info("已启动: %s", exe)
    except Exception as e:
        logger.error("启动失败: %s - %s", exe, e)

def start_all_apps():
    """为每个产品启动主程序"""
    for prod in PRODUCT_LIST:
        start_main_app_for_product(prod, prod["id"])

# ── 文件下载 ──────────────────────────────────────────────────────────────────

def _encode_url(url):
    """Encode non-ASCII characters in URL path (e.g. Chinese filenames)."""
    parsed = urllib.parse.urlparse(url)
    encoded_path = urllib.parse.quote(parsed.path, safe="/:@!$&'()*+,;=-._~")
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, encoded_path,
                                    parsed.params, parsed.query, parsed.fragment))

def download_file(url, dest_path, expected_md5=None, progress_cb=None, stop_check=None):
    full_url = _encode_url(SERVER_URL + url)
    logger.info("下载: %s -> %s", url, dest_path)
    tmp_path = dest_path + ".tmp"
    # Small reads so we re-check admin "force stop" frequently (large read() blocks until full chunk arrives).
    read_unit = 262144  # 256 KiB
    try:
        req = urllib.request.Request(full_url)
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            if total > 0:
                logger.info("文件大小: %d MB", total // 1048576)
            downloaded = 0
            md5 = hashlib.md5()
            last_report_pct = -100  # ensure first report fires
            last_report_time = 0
            if stop_check and stop_check():
                logger.warning("任务在开始下载前已被强制停止")
                raise InterruptedError("任务已被强制停止")
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(read_unit)
                    if not chunk:
                        break
                    f.write(chunk)
                    md5.update(chunk)
                    downloaded += len(chunk)
                    if stop_check and stop_check():
                        logger.warning("任务已被强制停止，中止下载")
                        raise InterruptedError("任务已被强制停止")
                    if total > 0:
                        pct = downloaded / total * 100
                        now = time.time()
                        # Report every 5% or every 10 seconds, whichever comes first
                        if pct - last_report_pct >= 5 or now - last_report_time >= 10 or downloaded == total:
                            last_report_pct = pct
                            last_report_time = now
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
    except InterruptedError:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    except Exception as e:
        logger.error("下载失败: %s\n%s", e, traceback.format_exc())
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False

# ── 解压 ──────────────────────────────────────────────────────────────────────

def _decode_zip_name(name):
    """解码 zip 内文件名，兼容 Windows 中文编码 (GBK) 和 UTF-8"""
    try:
        # 先尝试 UTF-8（现代 zip 工具默认）
        return name.encode('cp437').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    try:
        # 回退到 GBK（Windows 中文系统默认）
        return name.encode('cp437').decode('gbk')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return name

def _fix_permissions(path):
    """Try to reset permissions on Windows so deletion/extraction won't fail."""
    if PLATFORM == "windows" and os.path.isdir(path):
        try:
            subprocess.run(
                ["icacls", path, "/grant", "Everyone:F", "/t", "/c", "/q"],
                capture_output=True, timeout=60
            )
        except Exception:
            pass

def _rmtree_retry(path, retries=3):
    """Delete directory with retry and permission fix."""
    for i in range(retries):
        try:
            shutil.rmtree(path)
            return True
        except PermissionError:
            logger.warning("删除目录被拒绝，尝试修复权限: %s (尝试 %d/%d)", path, i+1, retries)
            _fix_permissions(path)
            time.sleep(2)
        except OSError as e:
            logger.warning("删除目录失败: %s - %s (尝试 %d/%d)", e, path, i+1, retries)
            _fix_permissions(path)
            time.sleep(2)
    # Last try
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.exists(path)

def extract_package(archive_path, package_type, prod, folder_product_id):
    """将包解压到 install_path/<folder_product_id>/。"""
    try:
        install_base = install_dir_for_product_id(folder_product_id)
    except ValueError as e:
        logger.error("%s", e)
        return False
    cleanup_paths = prod.get("cleanup_paths") or []
    apps = prod.get("apps") or []
    logger.info(
        "解压: %s -> %s (目录产品ID=%s, 类型=%s)",
        archive_path, install_base, folder_product_id, package_type,
    )
    try:
        os.makedirs(install_base, exist_ok=True)
        _test_file = os.path.join(install_base, ".write_test")
        try:
            with open(_test_file, "w") as f:
                f.write("test")
            os.remove(_test_file)
        except (PermissionError, OSError) as e:
            logger.error("安装目录无写入权限: %s - %s", install_base, e)
            logger.error("请检查: 1) 以管理员身份运行  2) 目录权限  3) 文件是否被占用")
            _fix_permissions(install_base)
            try:
                with open(_test_file, "w") as f:
                    f.write("test")
                os.remove(_test_file)
            except Exception:
                logger.error("修复权限后仍无法写入，解压终止")
                return False

        if package_type == "full":
            for cp in cleanup_paths:
                if os.path.isdir(cp):
                    logger.info("清理目录: %s", cp)
                    _rmtree_retry(cp)
                elif os.path.isfile(cp):
                    logger.info("清理文件: %s", cp)
                    try:
                        os.remove(cp)
                    except OSError:
                        pass
            if os.path.isdir(install_base):
                logger.info("全量模式: 删除安装目录 %s", install_base)
                _rmtree_retry(install_base)
        os.makedirs(install_base, exist_ok=True)
        if archive_path.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for info in zf.infolist():
                    decoded_name = _decode_zip_name(info.filename)
                    target_path = os.path.join(install_base, decoded_name)
                    if info.is_dir():
                        os.makedirs(target_path, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with zf.open(info) as src, open(target_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
        elif archive_path.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz", errors="surrogateescape") as tf:
                tf.extractall(install_base)
        elif archive_path.endswith(".tar"):
            with tarfile.open(archive_path, "r", errors="surrogateescape") as tf:
                tf.extractall(install_base)
        else:
            logger.error("不支持的压缩格式: %s", archive_path)
            return False
        logger.info("解压完成")
        if PLATFORM == "linux":
            for app in apps:
                exe = app.get("linux", "")
                if exe:
                    exe_path = os.path.join(install_base, exe)
                    if os.path.exists(exe_path):
                        os.chmod(exe_path, 0o755)
            for root, dirs, files in os.walk(install_base):
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

def save_version(version, folder_product_id):
    try:
        base = install_dir_for_product_id(folder_product_id)
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "version.txt"), "w") as f:
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
    product_id = (task.get("product_id") or "default").strip() or "default"
    if not is_valid_product_id(product_id):
        report_result(
            task_id,
            "failed",
            f"产品 ID 无效（仅英文字母与数字）: {product_id}",
        )
        return
    prod = resolve_product_for_deploy(product_id)
    if not prod:
        logger.error("未知产品 ID: %s，已配置: %s", product_id, list(PRODUCT_BY_ID.keys()))
        report_result(
            task_id,
            "failed",
            f"客户端无法解析产品 {product_id}：请在 client_config.json 的 products 中配置对应 id，或清空 products 使用单一 install_path",
        )
        return

    tmp_dir = os.path.join(BASE_DIR, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    archive_path = os.path.join(tmp_dir, filename)

    log_lines = []
    def log(msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        log_lines.append(line)
        logger.info(msg)

    def on_download_progress(pct):
        report_progress(task_id, "下载中", pct * 0.7, f"下载进度 {pct:.1f}%")

    def stop_check():
        return is_task_stopped(task_id)

    try:
        target_install = install_dir_for_product_id(product_id)
    except ValueError as e:
        report_result(task_id, "failed", str(e))
        return
    p_apps = prod.get("apps") or []
    apps_desc = ", ".join(
        f"{a.get('name', '?')} -> {a.get(PLATFORM, '')}" for a in p_apps if a.get(PLATFORM)
    )
    log("========== 部署环境（客户端本机）==========")
    log(f"主机名: {get_hostname()}")
    log(f"本机 IP: {LOCAL_IP}")
    log(f"客户端 ID: {CLIENT_ID}")
    log(f"平台: {PLATFORM}")
    if is_legacy_no_products_config() and prod.get("id") != product_id:
        log(f"产品: 服务端 ID={product_id}（未配置 products，使用 install_path/{product_id}/）")
    else:
        log(f"产品: {product_id} ({prod.get('name', '')})")
    log(f"软件安装目录 (install_path 根: {get_install_root()}，本产品子目录: {target_install})")
    log(f"客户端程序目录 (BASE_DIR): {BASE_DIR}")
    log(f"服务端 URL: {SERVER_URL}")
    log(f"任务版本: {version} | 包类型: {package_type} | 安装包文件名: {filename}")
    log(f"下载路径: {download_url}")
    log(f"本地临时包路径: {archive_path}")
    log(f"自动启动应用: {AUTO_START} | 管理应用: {apps_desc or '(未配置)'}")
    log("==========================================")
    log(f"开始部署任务 {task_id}, 产品 {product_id}, 版本 {version}, 应用数: {len(p_apps)}, 包类型: {package_type}")
    report_progress(task_id, "准备中", 0, "开始部署")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"[尝试 {attempt}/{MAX_RETRIES}] 下载安装包...")
            if attempt > 1:
                report_progress(task_id, "重试下载", 0, f"第 {attempt} 次尝试")
            else:
                report_progress(task_id, "下载中", 0, "开始下载")
            try:
                ok_dl = download_file(
                    download_url, archive_path, expected_md5,
                    progress_cb=on_download_progress, stop_check=stop_check,
                )
            except InterruptedError:
                log("任务已被强制停止，下载已中止")
                report_progress(task_id, "失败", 0, "管理员强制停止")
                report_result(task_id, "failed", "\n".join(log_lines))
                return
            if not ok_dl:
                if stop_check():
                    log("任务已被强制停止")
                    report_result(task_id, "failed", "\n".join(log_lines))
                    return
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

            if stop_check():
                log("任务已被强制停止")
                report_result(task_id, "failed", "\n".join(log_lines))
                return

            try:
                sz = os.path.getsize(archive_path)
                log(f"安装包已下载: {archive_path} ({sz} bytes, {sz / 1048576:.2f} MB)")
            except OSError as e:
                log(f"安装包已下载: {archive_path} (无法读取大小: {e})")

            log(f"停止产品 {product_id} 的应用...")
            report_progress(task_id, "停止应用", 70, "停止该产品应用进程")
            stop_apps_for_product(prod, product_id)

            log(f"解压新版本 (类型={package_type})...")
            report_progress(task_id, "解压中", 75, "正在解压安装包")
            if not extract_package(archive_path, package_type, prod, product_id):
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

            try:
                names = sorted(os.listdir(target_install))
                preview = names[:40]
                extra = len(names) - len(preview)
                log(f"安装目录 {target_install} 顶层条目 ({len(names)} 个，显示前 {len(preview)} 个): {', '.join(preview)}")
                if extra > 0:
                    log(f"... 另有 {extra} 个文件/目录未列出")
            except OSError as e:
                log(f"列出安装目录失败: {e}")

            save_version(version, product_id)
            report_progress(task_id, "完成", 100, "部署成功")

            if stop_check():
                log("任务已被强制停止")
                report_result(task_id, "failed", "\n".join(log_lines))
                return

            if AUTO_START:
                log(f"启动产品 {product_id} 的主程序...")
                start_main_app_for_product(prod, product_id)

            log(f"部署成功！版本: {version}，产品: {product_id}，安装路径: {target_install}")
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

_running_tasks = set()  # task_ids currently being executed
_running_tasks_lock = threading.Lock()

def poll_loop():
    logger.info("开始轮询任务，间隔 %d 秒", POLL_INTERVAL)
    while True:
        try:
            task = api_get(f"/api/poll/{CLIENT_ID}", timeout=15)
            if task and task.get("task_id"):
                task_id = task["task_id"]
                with _running_tasks_lock:
                    if task_id in _running_tasks:
                        logger.debug("任务 %s 已在执行中，跳过", task_id)
                    else:
                        _running_tasks.add(task_id)
                        logger.info("收到部署任务: %s", task_id)
                        def _run(t):
                            try:
                                execute_task(t)
                            finally:
                                with _running_tasks_lock:
                                    _running_tasks.discard(t["task_id"])
                        threading.Thread(target=_run, args=(task,), daemon=True).start()
        except Exception as e:
            logger.debug("轮询异常: %s", e)
        time.sleep(POLL_INTERVAL)

# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def truncate_log(path):
    """Clear log file on startup."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Log cleared on startup\n")
    except Exception:
        pass

def main():
    truncate_log(LOG_FILE)
    logger.info("=" * 55)
    logger.info("软件部署客户端启动")
    logger.info("平台: %s", PLATFORM)
    logger.info("客户端ID: %s", CLIENT_ID)
    logger.info("本机IP: %s", LOCAL_IP)
    logger.info("主机名: %s", get_hostname())
    logger.info("服务端: %s", SERVER_URL)
    for p in PRODUCT_LIST:
        try:
            pdir = install_dir_for_product_id(p["id"])
        except ValueError:
            pdir = "(产品 ID 无效，请检查配置)"
        logger.info("产品 [%s] %s -> %s", p["id"], p.get("name", ""), pdir)
    logger.info("=" * 55)

    os.makedirs(get_install_root(), exist_ok=True)
    for p in PRODUCT_LIST:
        try:
            os.makedirs(install_dir_for_product_id(p["id"]), exist_ok=True)
        except ValueError:
            pass

    # 测试服务端连接（重试最多60次，每次间隔5秒，共5分钟）
    server_ready = False
    for i in range(60):
        try:
            resp = api_get("/api/config", timeout=5)
            logger.info("服务端连接成功，版本包应用数: %d", len(resp.get("apps", [])))
            print(f"[OK] 客户端启动成功 连接服务端 {SERVER_URL}")
            server_ready = True
            break
        except Exception:
            if i == 0:
                logger.warning("服务端未就绪，等待重试...")
                print(f"[..] 服务端未就绪，等待重试 ({SERVER_URL})")
            time.sleep(5)

    if not server_ready:
        logger.error("无法连接服务端 %s，将继续重试", SERVER_URL)
        print(f"[WARN] 无法连接服务端，客户端将继续重试")

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
    import signal

    _crash_log = os.path.join(BASE_DIR, "client_crash.log")

    def _log_crash(msg):
        try:
            with open(_crash_log, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now()}] {msg}\n")
        except Exception:
            pass

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        _log_crash(f"SIGNAL {sig_name} ({signum}) - process exiting")
        logger.info("收到信号 %s，客户端退出", sig_name)
        sys.exit(0)

    # Catch nssm/service control signals
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, _signal_handler)

    try:
        main()
    except Exception as e:
        _log_crash(f"FATAL: {e}\n{traceback.format_exc()}")
        sys.exit(1)
