#!/usr/bin/env python3
"""
跨平台内网软件部署管理平台 - 服务端
Flask + SQLite + 嵌入式Web仪表盘
"""

import os
import sys
import json
import time
import uuid
import shutil
import socket
import sqlite3
import urllib.parse
import hashlib
import logging
import zipfile
import tarfile
import threading
import traceback
import re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_file, Response

# 产品 ID：仅英文字母与数字（与客户端安装子目录名一致）
PRODUCT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]+$")


def is_valid_product_id(pid):
    return bool(pid and PRODUCT_ID_PATTERN.match(str(pid).strip()))

# ── 路径检测（frozen 必须在最前面） ──────────────────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 日志配置 ──────────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(BASE_DIR, "server.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("deploy-server")

# 抑制 Flask/werkzeug 的请求日志（/api/poll 和 /api/heartbeat 太频繁）
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ── 加载配置 ──────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(BASE_DIR, "server_config.json")

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 61234,
    "packages_dir": "./packages",
    "database": "./deploy.db",
    "max_concurrent": 10,
    "heartbeat_timeout": 90,
    "products": [],
    "apps": [
        {"name": "主程序", "windows": "myqtapp.exe", "linux": "myqtapp"},
        {"name": "辅助工具", "windows": "helper.exe", "linux": "helper"},
    ]
}

def load_config():
    read_path = CONFIG_FILE
    materialized_from_bundle = False

    # PyInstaller onefile: --add-data puts a template under sys._MEI* (temp).
    # Always materialize to server_config.json next to the exe so edits/persist live with the app.
    if not os.path.exists(CONFIG_FILE) and getattr(sys, "frozen", False):
        internal_cfg = os.path.join(sys._MEIPASS, "server_config.json")
        if os.path.exists(internal_cfg):
            read_path = internal_cfg
            materialized_from_bundle = True

    if os.path.exists(read_path):
        try:
            with open(read_path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            if materialized_from_bundle:
                try:
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=4, ensure_ascii=False)
                    logger.info(
                        "已加载配置（内置模板已复制到可执行文件目录）: %s",
                        os.path.abspath(CONFIG_FILE),
                    )
                except Exception as e:
                    logger.warning(
                        "无法将配置写入 %s（%s），本次仍从临时目录读取",
                        os.path.abspath(CONFIG_FILE),
                        e,
                    )
                    logger.info("已加载配置文件: %s", os.path.abspath(read_path))
            else:
                logger.info("已加载配置文件: %s", os.path.abspath(read_path))
            return cfg
        except Exception as e:
            logger.error("配置文件读取失败，使用默认配置: %s", e)

    # Write default config next to exe so user can edit it
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        logger.info("已生成默认配置文件: %s", CONFIG_FILE)
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()

config = load_config()

def _resolve_path(p):
    """Resolve path relative to BASE_DIR, not CWD (CWD is System32 for Windows services)."""
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(BASE_DIR, p))

PACKAGES_DIR = _resolve_path(config["packages_dir"])
DATABASE = _resolve_path(config["database"])
MAX_CONCURRENT = config.get("max_concurrent", 10)
HEARTBEAT_TIMEOUT = config.get("heartbeat_timeout", 90)
APPS = config.get("apps", [])
_pcfg = config.get("products")
PRODUCTS = list(_pcfg) if isinstance(_pcfg, list) else []

config_lock = threading.Lock()


def persist_config():
    """将内存中的 config 原子写入 server_config.json。"""
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


def normalize_products_payload(raw):
    """校验并规范化页面/API 提交的产品列表（允许空列表，表示尚未配置产品）。"""
    if not isinstance(raw, list):
        raise ValueError("products 须为数组")
    if len(raw) == 0:
        return []
    seen = set()
    out = []
    for p in raw:
        if not isinstance(p, dict):
            raise ValueError("产品项格式无效")
        pid = str(p.get("id", "")).strip()
        name = str(p.get("name", "")).strip() or pid
        if not pid:
            raise ValueError("产品 ID 不能为空")
        if not is_valid_product_id(pid):
            raise ValueError(f"产品 ID 只能为英文字母与数字，不能含空格或符号: {pid}")
        if pid in seen:
            raise ValueError(f"重复的产品 ID: {pid}")
        seen.add(pid)
        out.append({"id": pid, "name": name})
    return out


def products_blocked_by_packages(removed_ids):
    """若移除的产品仍被版本包引用，返回 (product_id, count)，否则 None。"""
    if not removed_ids:
        return None
    conn = get_db()
    try:
        for pid in removed_ids:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM packages WHERE product_id=?", (pid,)
            ).fetchone()
            if row and row["c"] > 0:
                return pid, row["c"]
        return None
    finally:
        conn.close()


os.makedirs(PACKAGES_DIR, exist_ok=True)

# ── Flask 应用 ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024 * 1024  # 10GB

# ── 部署线程池 ────────────────────────────────────────────────────────────────
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT)
active_tasks = {}
task_lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════════════════
# 数据库
# ══════════════════════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    try:
        conn = get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS machines (
                id          TEXT PRIMARY KEY,
                ip          TEXT NOT NULL,
                hostname    TEXT DEFAULT '',
                platform    TEXT DEFAULT 'unknown',
                tag         TEXT DEFAULT '',
                status      TEXT DEFAULT 'offline',
                current_version TEXT DEFAULT '',
                last_heartbeat  TEXT DEFAULT '',
                last_deploy     TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS packages (
                id          TEXT PRIMARY KEY,
                version     TEXT NOT NULL,
                platform    TEXT NOT NULL,
                filename    TEXT NOT NULL,
                filepath    TEXT NOT NULL,
                md5         TEXT NOT NULL,
                size        INTEGER DEFAULT 0,
                uploaded_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS deployments (
                id          TEXT PRIMARY KEY,
                version     TEXT NOT NULL,
                platform    TEXT NOT NULL,
                target_ids  TEXT NOT NULL,
                status      TEXT DEFAULT 'pending',
                total       INTEGER DEFAULT 0,
                success     INTEGER DEFAULT 0,
                failed      INTEGER DEFAULT 0,
                started_at  TEXT,
                finished_at TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS deploy_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     TEXT NOT NULL,
                machine_id  TEXT NOT NULL,
                status      TEXT DEFAULT 'running',
                log         TEXT DEFAULT '',
                started_at  TEXT DEFAULT (datetime('now','localtime')),
                finished_at TEXT,
                FOREIGN KEY (task_id) REFERENCES deployments(id)
            );
        """)
        conn.commit()
        conn.close()
        logger.info("数据库初始化完成: %s", DATABASE)
    except Exception as e:
        logger.error("数据库初始化失败: %s\n%s", e, traceback.format_exc())
        sys.exit(1)

def migrate_db():
    """数据库迁移：添加新列 / 多产品版本表"""
    try:
        conn = get_db()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(packages)").fetchall()}
        if "package_type" not in cols:
            conn.execute("ALTER TABLE packages ADD COLUMN package_type TEXT DEFAULT 'full'")
            conn.commit()
            logger.info("数据库迁移: packages 表添加 package_type 列")
        if "product_id" not in cols:
            conn.execute("ALTER TABLE packages ADD COLUMN product_id TEXT DEFAULT 'default'")
            conn.execute("UPDATE packages SET product_id='default' WHERE product_id IS NULL OR TRIM(product_id)=''")
            conn.commit()
            logger.info("数据库迁移: packages 添加 product_id")

        dcols = {row[1] for row in conn.execute("PRAGMA table_info(deployments)").fetchall()}
        if "product_id" not in dcols:
            conn.execute("ALTER TABLE deployments ADD COLUMN product_id TEXT DEFAULT 'default'")
            conn.execute("UPDATE deployments SET product_id='default' WHERE product_id IS NULL OR TRIM(product_id)=''")
            conn.commit()
            logger.info("数据库迁移: deployments 添加 product_id")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS machine_product_versions (
                machine_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                version TEXT DEFAULT '',
                updated_at TEXT,
                PRIMARY KEY (machine_id, product_id)
            )
        """)
        conn.commit()

        # 将旧版 current_version 回填到 default 产品（若尚无记录）
        try:
            rows = conn.execute(
                "SELECT id, current_version FROM machines WHERE current_version IS NOT NULL AND TRIM(current_version) != ''"
            ).fetchall()
            for r in rows:
                exists = conn.execute(
                    "SELECT 1 FROM machine_product_versions WHERE machine_id=? AND product_id='default'",
                    (r["id"],),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO machine_product_versions (machine_id, product_id, version, updated_at) VALUES (?,?,?,?)",
                        (r["id"], "default", r["current_version"], now_str()),
                    )
            conn.commit()
        except Exception as ex:
            logger.warning("回填 machine_product_versions: %s", ex)

        conn.close()
    except Exception as e:
        logger.error("数据库迁移失败: %s", e)

# ── 部署进度追踪（内存） ──────────────────────────────────────────────────────
deploy_progress = {}  # task_id -> {client_id: {phase, percent, detail}}
progress_lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def gen_id():
    return uuid.uuid4().hex[:12]

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def md5_file(filepath):
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def prune_versions(platform_type, product_id, keep=5):
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, filepath FROM packages WHERE platform=? AND product_id=? ORDER BY uploaded_at DESC",
            (platform_type, product_id),
        ).fetchall()
        for row in rows[keep:]:
            try:
                if os.path.exists(row["filepath"]):
                    os.remove(row["filepath"])
            except OSError:
                pass
            conn.execute("DELETE FROM packages WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("清理旧版本失败: %s", e)


def _filepath_under_packages(filepath):
    try:
        fp = os.path.normpath(os.path.abspath(filepath))
        root = os.path.normpath(os.path.abspath(PACKAGES_DIR))
        return fp.startswith(root + os.sep) or fp == root
    except Exception:
        return False

def recover_packages():
    try:
        conn = get_db()
        existing = {row["filepath"] for row in conn.execute("SELECT filepath FROM packages").fetchall()}
        recovered = 0

        def insert_pkg(platform_type, product_id, version, filename, filepath):
            nonlocal recovered
            if not os.path.isfile(filepath) or filepath in existing:
                return
            pkg_id = gen_id()
            file_md5 = md5_file(filepath)
            file_size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT OR IGNORE INTO packages (id, version, platform, product_id, filename, filepath, md5, size, uploaded_at, package_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pkg_id, version, platform_type, product_id, filename, filepath, file_md5, file_size, mtime, "full"),
            )
            recovered += 1
            logger.info("恢复包记录: %s/%s/%s/%s", platform_type, product_id, version, filename)

        for platform_type in ("windows", "linux"):
            platform_dir = os.path.join(PACKAGES_DIR, platform_type)
            if not os.path.isdir(platform_dir):
                continue
            for entry in os.listdir(platform_dir):
                path1 = os.path.join(platform_dir, entry)
                if not os.path.isdir(path1):
                    continue
                subs = [x for x in os.listdir(path1) if os.path.isdir(os.path.join(path1, x))]
                files_here = [x for x in os.listdir(path1) if os.path.isfile(os.path.join(path1, x))]
                # Legacy: platform/<version>/archive.zip (no product folder)
                if files_here and any(
                    f.endswith((".zip", ".tar.gz", ".gz")) for f in files_here
                ):
                    version = entry
                    for filename in files_here:
                        filepath = os.path.join(path1, filename)
                        insert_pkg(platform_type, "default", version, filename, filepath)
                    continue
                # New: platform/<product_id>/<version>/file
                product_id = entry
                for version in subs:
                    version_dir = os.path.join(path1, version)
                    if not os.path.isdir(version_dir):
                        continue
                    for filename in os.listdir(version_dir):
                        filepath = os.path.join(version_dir, filename)
                        if os.path.isfile(filepath):
                            insert_pkg(platform_type, product_id, version, filename, filepath)

        conn.commit()
        conn.close()
        if recovered:
            logger.info("共恢复 %d 个包记录", recovered)
        else:
            logger.info("包记录检查完成，无需恢复")
    except Exception as e:
        logger.error("包恢复检查失败: %s\n%s", e, traceback.format_exc())

# ══════════════════════════════════════════════════════════════════════════════
# 部署执行器
# ══════════════════════════════════════════════════════════════════════════════

def execute_deployment(task_id, machine_id, version, platform_type):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO deploy_logs (task_id, machine_id, status, log) VALUES (?,?,?,?)",
            (task_id, machine_id, "running", f"[{now_str()}] 等待客户端拉取部署任务...\n")
        )
        conn.commit()
        logger.info("为机器 %s 创建部署日志，任务 %s", machine_id, task_id)
    except Exception as e:
        logger.error("创建部署日志失败: machine=%s, task=%s, error=%s", machine_id, task_id, e)
    finally:
        conn.close()

def start_deployment(task_id):
    try:
        conn = get_db()
        task = conn.execute("SELECT * FROM deployments WHERE id=?", (task_id,)).fetchone()
        if not task:
            conn.close()
            logger.warning("部署任务不存在: %s", task_id)
            return

        target_ids = json.loads(task["target_ids"])
        version = task["version"]
        platform_type = task["platform"]

        conn.execute("UPDATE deployments SET status='running', started_at=? WHERE id=?",
                     (now_str(), task_id))
        conn.commit()
        conn.close()

        for mid in target_ids:
            execute_deployment(task_id, mid, version, platform_type)

        conn = get_db()
        conn.execute("UPDATE deployments SET total=? WHERE id=?", (len(target_ids), task_id))
        conn.commit()
        conn.close()
        tdict = dict(task)
        pid = (tdict.get("product_id") or "default")
        logger.info("部署任务 %s 已启动，产品=%s, 版本=%s 平台=%s 共%d台", task_id, pid, version, platform_type, len(target_ids))
    except Exception as e:
        logger.error("启动部署任务失败: %s\n%s", e, traceback.format_exc())

# ══════════════════════════════════════════════════════════════════════════════
# API 路由 - 机器管理
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/machines", methods=["GET"])
def list_machines():
    try:
        conn = get_db()
        platform_filter = request.args.get("platform")
        status_filter = request.args.get("status")
        query = "SELECT * FROM machines"
        conditions, params = [], []
        if platform_filter:
            conditions.append("platform=?")
            params.append(platform_filter)
        if status_filter:
            conditions.append("status=?")
            params.append(status_filter)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        mids = [r["id"] for r in rows]
        pv_map = {}
        if mids:
            placeholders = ",".join("?" * len(mids))
            for pr in conn.execute(
                f"SELECT machine_id, product_id, version FROM machine_product_versions WHERE machine_id IN ({placeholders})",
                mids,
            ).fetchall():
                pv_map.setdefault(pr["machine_id"], {})[pr["product_id"]] = pr["version"] or ""
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["product_versions"] = pv_map.get(r["id"], {})
            out.append(d)
        logger.debug("查询机器列表: 共 %d 条", len(out))
        return jsonify(out)
    except Exception as e:
        logger.error("查询机器列表失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/machines/<machine_id>", methods=["DELETE"])
def delete_machine(machine_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM machine_product_versions WHERE machine_id=?", (machine_id,))
        conn.execute("DELETE FROM machines WHERE id=?", (machine_id,))
        conn.commit()
        conn.close()
        logger.info("删除机器: %s", machine_id)
        return jsonify({"message": "已删除"})
    except Exception as e:
        logger.error("删除机器失败: %s", e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# API 路由 - 版本包管理
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/packages", methods=["GET"])
def list_packages():
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM packages ORDER BY uploaded_at DESC").fetchall()
        conn.close()
        logger.debug("查询版本包列表: %d 条", len(rows))
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.error("查询版本包失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/packages/<pkg_id>", methods=["DELETE"])
def delete_package(pkg_id):
    try:
        conn = get_db()
        row = conn.execute("SELECT * FROM packages WHERE id=?", (pkg_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "包不存在"}), 404
        try:
            if os.path.exists(row["filepath"]):
                os.remove(row["filepath"])
                parent = os.path.dirname(row["filepath"])
                if os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
        except OSError as e:
            logger.warning("删除文件失败: %s", e)
        conn.execute("DELETE FROM packages WHERE id=?", (pkg_id,))
        conn.commit()
        conn.close()
        logger.info("已删除包: %s/%s (%s)", row["platform"], row["version"], row["filename"])
        return jsonify({"message": "已删除"})
    except Exception as e:
        logger.error("删除包失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/api/packages/upload", methods=["POST"])
def upload_package():
    try:
        if "file" not in request.files:
            return jsonify({"error": "未找到上传文件"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "文件名为空"}), 400

        platform_type = request.form.get("platform", "")
        version = request.form.get("version", "")
        package_type = request.form.get("package_type", "full")
        if platform_type not in ("windows", "linux"):
            return jsonify({"error": "platform 必须为 windows 或 linux"}), 400
        if package_type not in ("full", "incremental"):
            package_type = "full"
        if not version:
            version = datetime.now().strftime("v%Y%m%d_%H%M%S")

        product_id = (request.form.get("product_id") or "default").strip() or "default"
        if not is_valid_product_id(product_id):
            return jsonify({"error": "product_id 只能为英文字母与数字"}), 400

        dest_dir = os.path.join(PACKAGES_DIR, platform_type, product_id, version)
        os.makedirs(dest_dir, exist_ok=True)
        filepath = os.path.join(dest_dir, f.filename)
        f.save(filepath)

        file_md5 = md5_file(filepath)
        file_size = os.path.getsize(filepath)

        pid = gen_id()
        conn = get_db()
        conn.execute(
            "INSERT INTO packages (id, version, platform, product_id, filename, filepath, md5, size, package_type) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, version, platform_type, product_id, f.filename, filepath, file_md5, file_size, package_type)
        )
        conn.commit()
        conn.close()

        prune_versions(platform_type, product_id, keep=5)
        logger.info(
            "包已上传: %s/%s/%s (%s, 类型=%s, %.2fMB)",
            platform_type, product_id, version, f.filename, package_type, file_size / 1024 / 1024,
        )
        return jsonify({
            "id": pid, "version": version, "platform": platform_type, "product_id": product_id,
            "filename": f.filename, "md5": file_md5, "size": file_size, "package_type": package_type
        }), 201
    except Exception as e:
        logger.error("上传处理异常: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": f"服务器内部错误: {e}"}), 500

@app.route("/api/package-file/<pkg_id>")
def download_package_by_id(pkg_id):
    """按包 ID 下载（推荐，路径与 product_id 无关）。"""
    try:
        conn = get_db()
        row = conn.execute("SELECT filepath FROM packages WHERE id=?", (pkg_id,)).fetchone()
        conn.close()
        if not row or not row["filepath"]:
            return jsonify({"error": "包不存在"}), 404
        filepath = row["filepath"]
        if not _filepath_under_packages(filepath) or not os.path.isfile(filepath):
            return jsonify({"error": "文件不存在"}), 404
        file_size = os.path.getsize(filepath)
        fn = os.path.basename(filepath)
        logger.info("文件下载(by id): %s (%d MB)", pkg_id, file_size // 1048576)

        def stream_file():
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(1048576)
                    if not chunk:
                        break
                    yield chunk

        resp = Response(stream_file(), content_type="application/octet-stream")
        resp.headers["Content-Length"] = str(file_size)
        resp.headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{urllib.parse.quote(fn)}"
        )
        return resp
    except Exception as e:
        logger.error("文件下载失败: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/<platform_type>/<version>/<filename>")
def download_package(platform_type, version, filename):
    """旧版 URL：仅兼容 default 产品目录 layout。"""
    try:
        filepath = os.path.join(PACKAGES_DIR, platform_type, "default", version, filename)
        if not os.path.exists(filepath):
            filepath = os.path.join(PACKAGES_DIR, platform_type, version, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "文件不存在"}), 404
        file_size = os.path.getsize(filepath)
        logger.info("文件下载: %s/%s/%s (%d MB)", platform_type, version, filename, file_size // 1048576)

        def stream_file():
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(1048576)  # 1MB chunks
                    if not chunk:
                        break
                    yield chunk

        resp = Response(stream_file(), content_type="application/octet-stream")
        resp.headers["Content-Length"] = str(file_size)
        resp.headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
        )
        return resp
    except Exception as e:
        logger.error("文件下载失败: %s", e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# API 路由 - 部署任务
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/deploy", methods=["POST"])
def create_deployment():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "缺少请求体"}), 400

        version = data.get("version")
        platform_type = data.get("platform")
        product_id = (data.get("product_id") or "default").strip() or "default"
        machine_ids = data.get("machine_ids", [])

        if not version or not platform_type:
            return jsonify({"error": "缺少 version 或 platform"}), 400
        if not is_valid_product_id(product_id):
            return jsonify({"error": "产品 ID 只能为英文字母与数字"}), 400

        conn = get_db()
        pkg = conn.execute(
            "SELECT * FROM packages WHERE version=? AND platform=? AND product_id=?",
            (version, platform_type, product_id),
        ).fetchone()
        if not pkg:
            conn.close()
            return jsonify({"error": f"未找到 {platform_type}/{product_id}/{version} 的包"}), 404

        if not machine_ids:
            rows = conn.execute(
                "SELECT id FROM machines WHERE platform=? AND status='online'", (platform_type,)
            ).fetchall()
            machine_ids = [r["id"] for r in rows]
        else:
            # Filter out offline machines from user-selected list
            placeholders = ",".join("?" * len(machine_ids))
            rows = conn.execute(
                f"SELECT id, status FROM machines WHERE id IN ({placeholders})", machine_ids
            ).fetchall()
            online_ids = [r["id"] for r in rows if r["status"] == "online"]
            skipped = len(machine_ids) - len(online_ids)
            if skipped > 0:
                logger.info("跳过 %d 台离线机器", skipped)
            machine_ids = online_ids

        if not machine_ids:
            conn.close()
            return jsonify({"error": "没有可部署的目标机器（全部离线）"}), 400

        task_id = gen_id()
        conn.execute(
            "INSERT INTO deployments (id, version, platform, product_id, target_ids, status, total) VALUES (?,?,?,?,?,?,?)",
            (task_id, version, platform_type, product_id, json.dumps(machine_ids), "pending", len(machine_ids))
        )
        conn.commit()
        conn.close()

        with task_lock:
            future = executor.submit(start_deployment, task_id)
            active_tasks[task_id] = future

        logger.info(
            "创建部署任务: %s, 产品=%s, 版本=%s, 平台=%s, 目标=%d台",
            task_id, product_id, version, platform_type, len(machine_ids),
        )
        return jsonify({"task_id": task_id, "total": len(machine_ids)}), 201
    except Exception as e:
        logger.error("创建部署任务失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM deployments ORDER BY created_at DESC LIMIT 50").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.error("查询任务列表失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    try:
        conn = get_db()
        task = conn.execute("SELECT * FROM deployments WHERE id=?", (task_id,)).fetchone()
        if not task:
            conn.close()
            return jsonify({"error": "任务不存在"}), 404
        logs = conn.execute(
            "SELECT dl.*, m.ip AS machine_ip, m.hostname AS machine_hostname "
            "FROM deploy_logs dl LEFT JOIN machines m ON dl.machine_id = m.id "
            "WHERE dl.task_id=? ORDER BY dl.started_at",
            (task_id,),
        ).fetchall()
        conn.close()
        result = {k: task[k] for k in task.keys()}
        out_logs = []
        for row in logs:
            d = {k: row[k] for k in row.keys()}
            d["ip"] = d.pop("machine_ip", "") or ""
            d["hostname"] = d.pop("machine_hostname", "") or ""
            out_logs.append(d)
        result["logs"] = out_logs
        with progress_lock:
            result["live_progress"] = deploy_progress.get(task_id, {})
        return jsonify(result)
    except Exception as e:
        logger.error("查询任务详情失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<task_id>/status", methods=["GET"])
def get_task_status(task_id):
    try:
        conn = get_db()
        task = conn.execute("SELECT status FROM deployments WHERE id=?", (task_id,)).fetchone()
        conn.close()
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify({"status": task["status"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tasks/<task_id>/stop", methods=["POST"])
def force_stop_task(task_id):
    try:
        conn = get_db()
        task = conn.execute("SELECT * FROM deployments WHERE id=?", (task_id,)).fetchone()
        if not task:
            conn.close()
            return jsonify({"error": "任务不存在"}), 404
        if task["status"] == "finished":
            conn.close()
            return jsonify({"error": "任务已结束"}), 400
        # 标记所有执行中的日志为失败
        conn.execute(
            "UPDATE deploy_logs SET status='failed', log=COALESCE(log,'') || '\n[强制停止] 任务被管理员强制终止', finished_at=? "
            "WHERE task_id=? AND status NOT IN ('success','failed')",
            (now_str(), task_id)
        )
        # 统计结果
        success_count = conn.execute(
            "SELECT COUNT(*) as c FROM deploy_logs WHERE task_id=? AND status='success'", (task_id,)
        ).fetchone()["c"]
        failed_count = conn.execute(
            "SELECT COUNT(*) as c FROM deploy_logs WHERE task_id=? AND status='failed'", (task_id,)
        ).fetchone()["c"]
        conn.execute(
            "UPDATE deployments SET status='stopped', success=?, failed=?, finished_at=? WHERE id=?",
            (success_count, failed_count, now_str(), task_id)
        )
        conn.commit()
        conn.close()
        # 清理实时进度
        with progress_lock:
            deploy_progress.pop(task_id, None)
        logger.info("任务已强制停止: %s, 成功=%d, 失败=%d", task_id, success_count, failed_count)
        return jsonify({"message": "任务已停止", "success": success_count, "failed": failed_count})
    except Exception as e:
        logger.error("强制停止任务失败: %s", e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# API 路由 - 心跳 & 客户端轮询
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "缺少请求体"}), 400

        client_id = data.get("client_id", "")
        ip = data.get("ip", request.remote_addr)
        hostname = data.get("hostname", "")
        platform_type = data.get("platform", "unknown")
        qt_version = data.get("qt_version", "")
        product_versions = data.get("product_versions")

        conn = get_db()
        existing = conn.execute("SELECT * FROM machines WHERE id=?", (client_id,)).fetchone()

        display_ver = qt_version
        if isinstance(product_versions, dict) and product_versions:
            parts = [f"{k}:{v}" for k, v in sorted(product_versions.items()) if v]
            if parts:
                display_ver = "; ".join(parts)

        if existing:
            conn.execute(
                "UPDATE machines SET ip=?, hostname=?, platform=?, status='online', "
                "current_version=?, last_heartbeat=? WHERE id=?",
                (ip, hostname, platform_type, display_ver, now_str(), client_id),
            )
        else:
            if not client_id:
                client_id = gen_id()
            conn.execute(
                "INSERT OR REPLACE INTO machines (id, ip, hostname, platform, status, current_version, last_heartbeat) "
                "VALUES (?,?,?,?,?,?,?)",
                (client_id, ip, hostname, platform_type, "online", display_ver, now_str()),
            )
            logger.info("新客户端注册: id=%s ip=%s hostname=%s platform=%s", client_id, ip, hostname, platform_type)

        if isinstance(product_versions, dict) and client_id:
            ts = now_str()
            for pid, ver in product_versions.items():
                if ver is None or str(ver).strip() == "":
                    continue
                pkey = str(pid).strip() or "default"
                conn.execute(
                    """INSERT INTO machine_product_versions (machine_id, product_id, version, updated_at)
                       VALUES (?,?,?,?)
                       ON CONFLICT(machine_id, product_id) DO UPDATE SET
                         version=excluded.version,
                         updated_at=excluded.updated_at""",
                    (client_id, pkey, str(ver).strip(), ts),
                )
        conn.commit()
        conn.close()
        return jsonify({"client_id": client_id, "status": "ok"})
    except Exception as e:
        logger.error("心跳处理异常: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/api/ping", methods=["GET"])
def ping():
    """Simple connectivity check for clients."""
    return jsonify({"status": "ok", "time": now_str()})

@app.route("/api/poll/<client_id>", methods=["GET"])
def poll_task(client_id):
    try:
        conn = get_db()
        tasks = conn.execute(
            "SELECT * FROM deployments WHERE status IN ('pending','running') ORDER BY created_at DESC"
        ).fetchall()

        for task in tasks:
            target_ids = json.loads(task["target_ids"])
            if client_id not in target_ids:
                continue
            existing = conn.execute(
                "SELECT * FROM deploy_logs WHERE task_id=? AND machine_id=?",
                (task["id"], client_id)
            ).fetchone()
            if existing and existing["status"] in ("success", "failed"):
                continue

            tdict = dict(task)
            pid = (tdict.get("product_id") or "default").strip() or "default"
            pkg = conn.execute(
                "SELECT * FROM packages WHERE version=? AND platform=? AND product_id=?",
                (task["version"], task["platform"], pid),
            ).fetchone()
            if not pkg:
                continue

            conn.close()
            pkg_dict = {k: pkg[k] for k in pkg.keys()}
            return jsonify({
                "task_id": task["id"],
                "product_id": pid,
                "version": task["version"],
                "platform": task["platform"],
                "filename": pkg_dict["filename"],
                "md5": pkg_dict["md5"],
                "package_type": pkg_dict.get("package_type") or "full",
                "download_url": f"/api/package-file/{pkg_dict['id']}",
            })

        conn.close()
        return jsonify({})
    except Exception as e:
        logger.error("轮询任务异常: client=%s, error=%s", client_id, e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/report/<task_id>/<client_id>", methods=["POST"])
def report_result(task_id, client_id):
    try:
        data = request.json
        status = data.get("status", "failed")
        log_text = data.get("log", "")

        conn = get_db()
        existing = conn.execute(
            "SELECT * FROM deploy_logs WHERE task_id=? AND machine_id=?",
            (task_id, client_id)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE deploy_logs SET status=?, log=?, finished_at=? WHERE id=?",
                (status, log_text, now_str(), existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO deploy_logs (task_id, machine_id, status, log, finished_at) VALUES (?,?,?,?,?)",
                (task_id, client_id, status, log_text, now_str())
            )

        if status == "success":
            task = conn.execute(
                "SELECT version, product_id FROM deployments WHERE id=?", (task_id,)
            ).fetchone()
            if task:
                ver = task["version"]
                pid = (dict(task).get("product_id") or "default").strip() or "default"
                conn.execute(
                    "UPDATE machines SET current_version=?, last_deploy=? WHERE id=?",
                    (ver, now_str(), client_id),
                )
                conn.execute(
                    """INSERT INTO machine_product_versions (machine_id, product_id, version, updated_at)
                       VALUES (?,?,?,?)
                       ON CONFLICT(machine_id, product_id) DO UPDATE SET
                         version=excluded.version,
                         updated_at=excluded.updated_at""",
                    (client_id, pid, ver, now_str()),
                )

        success_count = conn.execute(
            "SELECT COUNT(*) as c FROM deploy_logs WHERE task_id=? AND status='success'", (task_id,)
        ).fetchone()["c"]
        failed_count = conn.execute(
            "SELECT COUNT(*) as c FROM deploy_logs WHERE task_id=? AND status='failed'", (task_id,)
        ).fetchone()["c"]
        total_finished = conn.execute(
            "SELECT COUNT(*) as c FROM deploy_logs WHERE task_id=? AND status IN ('success','failed')", (task_id,)
        ).fetchone()["c"]
        total = conn.execute("SELECT total FROM deployments WHERE id=?", (task_id,)).fetchone()["total"]

        conn.execute(
            "UPDATE deployments SET success=?, failed=? WHERE id=?", (success_count, failed_count, task_id)
        )

        if total_finished >= total:
            conn.execute(
                "UPDATE deployments SET status='finished', finished_at=? WHERE id=?", (now_str(), task_id)
            )
            logger.info("部署任务已完成: %s, 成功=%d, 失败=%d", task_id, success_count, failed_count)

        conn.commit()
        conn.close()

        # Clean up live progress for this client
        with progress_lock:
            if task_id in deploy_progress:
                deploy_progress[task_id].pop(client_id, None)

        return jsonify({"message": "结果已记录"})
    except Exception as e:
        logger.error("上报结果异常: task=%s client=%s error=%s", task_id, client_id, e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# API 路由 - 部署进度
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/progress/<task_id>/<client_id>", methods=["POST"])
def report_progress(task_id, client_id):
    try:
        data = request.json
        new_pct = data.get("percent", 0)
        with progress_lock:
            if task_id not in deploy_progress:
                deploy_progress[task_id] = {}
            current = deploy_progress[task_id].get(client_id, {})
            current_pct = current.get("percent", 0)
            # Progress only increases, never decreases (prevents jumps on retry)
            if new_pct >= current_pct:
                deploy_progress[task_id][client_id] = {
                    "phase": data.get("phase", ""),
                    "percent": new_pct,
                    "detail": data.get("detail", ""),
                }
            else:
                # Still update phase/detail, but keep higher percent
                deploy_progress[task_id][client_id] = {
                    "phase": data.get("phase", current.get("phase", "")),
                    "percent": current_pct,
                    "detail": data.get("detail", current.get("detail", "")),
                }
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("进度上报异常: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/progress/<task_id>", methods=["GET"])
def get_progress(task_id):
    try:
        with progress_lock:
            return jsonify(deploy_progress.get(task_id, {}))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# API 路由 - 配置 & 客户端更新
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def get_config():
    with config_lock:
        _p = config.get("products")
        prods = list(_p) if isinstance(_p, list) else []
    return jsonify(
        {
            "apps": APPS,
            "products": prods,
            "port": config.get("port", 61234),
        }
    )


@app.route("/api/products", methods=["GET"])
def api_get_products():
    try:
        with config_lock:
            _p = config.get("products")
            plist = list(_p) if isinstance(_p, list) else []
        return jsonify(plist)
    except Exception as e:
        logger.error("读取产品列表失败: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/products", methods=["PUT"])
def api_put_products():
    global PRODUCTS
    try:
        data = request.json
        if not data or "products" not in data:
            return jsonify({"error": "缺少 products 字段"}), 400
        new_list = normalize_products_payload(data["products"])
        with config_lock:
            old_ids = [p["id"] for p in (config.get("products") or [])]
        new_ids = {p["id"] for p in new_list}
        removed = set(old_ids) - new_ids
        blocked = products_blocked_by_packages(removed)
        if blocked:
            pid, n = blocked
            return jsonify({"error": f"无法移除产品「{pid}」：仍有 {n} 个版本包引用该 ID"}), 400
        with config_lock:
            config["products"] = new_list
            PRODUCTS = new_list
        try:
            persist_config()
        except Exception as e:
            logger.error("保存配置文件失败: %s", e)
            return jsonify({"error": "写入 server_config.json 失败"}), 500
        logger.info("产品列表已更新，共 %d 项", len(new_list))
        return jsonify({"message": "已保存", "products": new_list})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("更新产品列表失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/api/client-update", methods=["POST"])
def upload_client_update():
    try:
        if "file" not in request.files:
            return jsonify({"error": "未找到上传文件"}), 400
        f = request.files["file"]
        dest = os.path.join(PACKAGES_DIR, "client_update")
        os.makedirs(dest, exist_ok=True)
        filepath = os.path.join(dest, f.filename)
        f.save(filepath)
        logger.info("客户端更新已上传: %s", f.filename)
        return jsonify({"message": "客户端更新已上传", "filename": f.filename})
    except Exception as e:
        logger.error("上传客户端更新失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/client-update/check", methods=["GET"])
def check_client_update():
    try:
        dest = os.path.join(PACKAGES_DIR, "client_update")
        if not os.path.exists(dest):
            return jsonify({})
        files = os.listdir(dest)
        if not files:
            return jsonify({})
        latest = max(files, key=lambda f: os.path.getmtime(os.path.join(dest, f)))
        return jsonify({
            "filename": latest,
            "download_url": f"/api/client-update/download/{latest}",
            "md5": md5_file(os.path.join(dest, latest))
        })
    except Exception as e:
        logger.error("检查客户端更新失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/client-update/download/<filename>")
def download_client_update(filename):
    try:
        filepath = os.path.join(PACKAGES_DIR, "client_update", filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "文件不存在"}), 404
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        logger.error("下载客户端更新失败: %s", e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# 心跳超时检测
# ══════════════════════════════════════════════════════════════════════════════

def heartbeat_checker():
    while True:
        try:
            conn = get_db()
            threshold = (datetime.now() - timedelta(seconds=HEARTBEAT_TIMEOUT)).strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "UPDATE machines SET status='offline' WHERE status='online' AND last_heartbeat < ?",
                (threshold,)
            )
            if cur.rowcount > 0:
                logger.info("心跳超时，已将 %d 台机器标记为离线", cur.rowcount)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("心跳检测异常: %s", e)
        time.sleep(30)

@app.route("/api/debug", methods=["GET"])
def debug_info():
    """Diagnostic endpoint to check server and client status"""
    try:
        conn = get_db()
        machines = conn.execute("SELECT id, ip, hostname, platform, status, last_heartbeat FROM machines").fetchall()
        threshold = (datetime.now() - timedelta(seconds=HEARTBEAT_TIMEOUT)).strftime("%Y-%m-%d %H:%M:%S")
        now = now_str()
        conn.close()
        return jsonify({
            "server_time": now,
            "heartbeat_timeout": HEARTBEAT_TIMEOUT,
            "threshold": threshold,
            "total_machines": len(machines),
            "machines": [
                {
                    "id": m["id"],
                    "ip": m["ip"],
                    "hostname": m["hostname"],
                    "platform": m["platform"],
                    "status": m["status"],
                    "last_heartbeat": m["last_heartbeat"],
                }
                for m in machines
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# Web 仪表盘
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><defs><linearGradient id='g' x1='0%25' y1='0%25' x2='100%25' y2='100%25'><stop offset='0%25' stop-color='%235cc9b0'/><stop offset='100%25' stop-color='%235cb8f0'/></linearGradient></defs><rect width='100' height='100' rx='20' fill='%23111827'/><path d='M50 15 L75 45 L60 45 L60 75 L40 75 L40 45 L25 45 Z' fill='url(%23g)' transform='rotate(-45 50 50)'/><circle cx='50' cy='68' r='8' fill='none' stroke='url(%23g)' stroke-width='3'/><line x1='50' y1='76' x2='50' y2='88' stroke='url(%23g)' stroke-width='3' stroke-linecap='round'/></svg>">
<title>软件部署管理平台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#111827;--card:rgba(30,41,59,.92);--card2:rgba(42,55,75,.92);--accent:#5cc9b0;--accent2:#4aaa94;--red:#e86060;--blue:#5cb8f0;--yellow:#e8b840;--text:#e8edf4;--text2:#94a3b8;--border:rgba(92,201,176,.25)}
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden}
canvas#bg{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none}
.header{position:relative;z-index:1;background:linear-gradient(135deg,rgba(92,201,176,.08),rgba(92,184,240,.08));border-bottom:1px solid var(--border);padding:20px 36px;display:flex;justify-content:space-between;align-items:center;backdrop-filter:blur(20px)}
.header h1{font-size:28px;font-weight:800;background:linear-gradient(135deg,#5cc9b0,#5cb8f0,#9b8ef0);background-size:200% 200%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:gradShift 4s ease infinite}
@keyframes gradShift{0%,100%{background-position:0% 50%}50%{background-position:100% 50%}}
.header .subtitle{font-size:12px;color:var(--text2);margin-top:4px;letter-spacing:1px}
.tabs{position:relative;z-index:1;display:flex;background:rgba(16,24,40,.6);border-bottom:1px solid var(--border);padding:0 36px;gap:2px;backdrop-filter:blur(12px)}
.tabs button{padding:16px 28px;border:none;background:none;cursor:pointer;font-size:14px;color:var(--text2);border-bottom:2px solid transparent;transition:all .3s;position:relative;font-weight:500}
.tabs button:hover{color:var(--text);background:rgba(92,201,176,.06)}
.tabs button.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:700;text-shadow:0 0 20px rgba(92,201,176,.3)}
.tabs button.active::after{content:'';position:absolute;bottom:-1px;left:15%;right:15%;height:2px;background:var(--accent);border-radius:1px;box-shadow:0 0 12px var(--accent),0 0 30px rgba(92,201,176,.3)}
.container{position:relative;z-index:1;max-width:1400px;margin:28px auto;padding:0 36px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;margin-bottom:24px;transition:all .4s;backdrop-filter:blur(16px);position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(92,201,176,.3),transparent)}
.card:hover{border-color:rgba(92,201,176,.25);box-shadow:0 8px 40px rgba(0,0,0,.4),0 0 30px rgba(92,201,176,.06);transform:translateY(-1px)}
.card h2{font-size:18px;margin-bottom:18px;color:var(--accent);display:flex;align-items:center;gap:10px;font-weight:700}
.card h2::before{content:'';display:inline-block;width:3px;height:18px;background:linear-gradient(180deg,var(--accent),var(--blue));border-radius:2px;box-shadow:0 0 8px rgba(92,201,176,.4)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:13px 16px;text-align:left;border-bottom:1px solid rgba(92,184,240,.08)}
th{background:rgba(51,65,85,.5);color:#cbd5e1;font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:1px}
tr{transition:all .2s}
tr:hover{background:rgba(92,201,176,.04)}
.badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:.5px}
.badge-online{background:rgba(92,201,176,.12);color:var(--accent);box-shadow:0 0 10px rgba(92,201,176,.15)}
.badge-offline{background:rgba(232,96,96,.12);color:var(--red);box-shadow:0 0 10px rgba(232,96,96,.15)}
.badge-latest{background:rgba(92,201,176,.18);color:var(--accent);font-size:11px;padding:2px 6px;border-radius:4px;margin-left:6px;vertical-align:middle}
.badge-outdated{background:rgba(232,184,64,.15);color:var(--yellow);font-size:11px;padding:2px 6px;border-radius:4px;margin-left:6px;vertical-align:middle}
.badge-running{background:rgba(92,184,240,.12);color:var(--blue);box-shadow:0 0 10px rgba(92,184,240,.15);animation:pulse 2s ease infinite}
.badge-finished{background:rgba(92,201,176,.12);color:var(--accent);box-shadow:0 0 10px rgba(92,201,176,.15)}
.badge-failed{background:rgba(232,96,96,.12);color:var(--red);box-shadow:0 0 10px rgba(232,96,96,.15)}
.badge-pending{background:rgba(232,184,64,.12);color:var(--yellow);box-shadow:0 0 10px rgba(232,184,64,.15)}
.badge-stopped{background:rgba(148,163,184,.12);color:var(--text2);box-shadow:0 0 10px rgba(148,163,184,.15)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.btn{padding:9px 20px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:400;transition:all .25s;position:relative;overflow:hidden}
.btn::after{content:'';position:absolute;top:50%;left:50%;width:0;height:0;background:rgba(255,255,255,.15);border-radius:50%;transform:translate(-50%,-50%);transition:width .4s,height .4s}
.btn:active::after{width:200px;height:200px}
.btn:active{transform:scale(.95)}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#1a332b;box-shadow:0 4px 15px rgba(92,201,176,.3)}
.btn-primary:hover{box-shadow:0 6px 25px rgba(92,201,176,.5);transform:translateY(-1px)}
.btn-danger{background:linear-gradient(135deg,var(--red),#e04848);color:#fff;box-shadow:0 4px 15px rgba(232,96,96,.25)}
.btn-danger:hover{box-shadow:0 6px 25px rgba(232,96,96,.45);transform:translateY(-1px)}
.form-row{display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;align-items:flex-end}
.form-row label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text2);font-weight:600;letter-spacing:.3px}
.form-row input,.form-row select,.form-row textarea{padding:11px 14px;border:1px solid rgba(92,184,240,.2);border-radius:8px;font-size:14px;background:rgba(30,41,59,.7);color:var(--text);transition:all .3s;backdrop-filter:blur(8px)}
.form-row input:focus,.form-row select:focus,.form-row textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(92,201,176,.12),0 0 20px rgba(92,201,176,.08)}
.form-row select option{background:var(--card2);color:var(--text)}
.progress-bar{height:8px;background:rgba(22,34,56,.8);border-radius:4px;overflow:hidden;box-shadow:inset 0 1px 3px rgba(0,0,0,.3)}
.progress-bar .fill{height:100%;background:linear-gradient(90deg,#5cc9b0,#5cb8f0,#9b8ef0);background-size:200% 100%;border-radius:4px;transition:width .6s ease;animation:barShine 2s linear infinite}
@keyframes barShine{0%{background-position:200% 0}100%{background-position:-200% 0}}
.hidden{display:none!important}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px;margin-bottom:28px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;transition:all .4s;position:relative;overflow:hidden;backdrop-filter:blur(16px)}
.stat-card:hover{transform:translateY(-4px) scale(1.02);box-shadow:0 12px 40px rgba(0,0,0,.4),0 0 30px rgba(92,201,176,.08)}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#5cc9b0,#5cb8f0,#9b8ef0);background-size:200% 100%;animation:gradShift 3s ease infinite}
.stat-card::after{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(92,201,176,.04) 0%,transparent 70%);animation:rotate 20s linear infinite}
@keyframes rotate{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.stat-card .num{font-size:36px;font-weight:900;background:linear-gradient(135deg,var(--accent),var(--blue));-webkit-background-clip:text;-webkit-text-fill-color:transparent;position:relative;z-index:1}
.stat-card .label{font-size:13px;color:var(--text2);margin-top:8px;font-weight:600;letter-spacing:.5px;position:relative;z-index:1}
.checkbox-label{display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;color:var(--text2)}
.checkbox-label input[type="checkbox"]{accent-color:var(--accent);width:16px;height:16px}
.modal-overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.75);z-index:1000;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(8px)}
.modal-overlay.hidden{display:none!important}
.modal-box{background:rgba(16,24,40,.95);border:1px solid var(--border);border-radius:16px;padding:28px;width:90%;max-width:1100px;max-height:85vh;overflow-y:auto;font-family:Consolas,"Microsoft YaHei UI","Courier New",monospace;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;position:relative;color:var(--text);backdrop-filter:blur(20px);box-shadow:0 20px 60px rgba(0,0,0,.5),0 0 40px rgba(92,201,176,.05)}
.modal-box .close-btn{position:sticky;top:0;float:right;background:linear-gradient(135deg,var(--red),#e04848);color:#fff;border:none;border-radius:8px;padding:8px 20px;cursor:pointer;font-size:13px;font-weight:700;z-index:1;box-shadow:0 4px 15px rgba(232,96,96,.3)}
.modal-box .close-btn:hover{box-shadow:0 6px 25px rgba(232,96,96,.5);transform:scale(1.05)}
@keyframes fadeIn{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}
.card,.stat-card{animation:fadeIn .4s ease}
.toast{position:fixed;top:24px;right:24px;padding:14px 28px;border-radius:10px;font-size:13px;font-weight:600;z-index:2000;animation:slideIn .3s ease;box-shadow:0 8px 30px rgba(0,0,0,.4);backdrop-filter:blur(12px)}
.toast-success{background:rgba(92,201,176,.9);color:#080c14}
.toast-error{background:rgba(232,96,96,.9);color:#fff}
@keyframes slideIn{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:translateX(0)}}
.glow{position:fixed;width:300px;height:300px;border-radius:50%;filter:blur(80px);opacity:.07;pointer-events:none;z-index:0}
.glow-1{top:10%;left:5%;background:#5cc9b0;animation:float1 8s ease-in-out infinite}
.glow-2{bottom:15%;right:10%;background:#5cb8f0;animation:float2 10s ease-in-out infinite}
.glow-3{top:50%;left:50%;background:#9b8ef0;animation:float3 12s ease-in-out infinite;width:200px;height:200px}
@keyframes float1{0%,100%{transform:translate(0,0)}50%{transform:translate(40px,30px)}}
@keyframes float2{0%,100%{transform:translate(0,0)}50%{transform:translate(-30px,-40px)}}
@keyframes float3{0%,100%{transform:translate(-50%,-50%) scale(1)}50%{transform:translate(-50%,-50%) scale(1.3)}}
.matrix-scroll{width:100%;overflow-x:auto;margin-top:4px;-webkit-overflow-scrolling:touch;border-radius:12px}
.matrix-scroll table{min-width:720px}
th.col-pv{text-align:center;min-width:92px;vertical-align:middle}
.th-pv-name{display:block;font-size:12px;color:#cbd5e1;font-weight:700;line-height:1.2}
.th-pv-id{display:block;font-size:10px;color:var(--text2);font-weight:500;margin-top:4px;opacity:.85}
.pv-cell{text-align:center;font-size:13px;vertical-align:middle}
.pv-ver{color:var(--text)}
.pv-empty{color:var(--text2);opacity:.55}
.deploy-flow-hint{font-size:12px;color:var(--text2);margin:-8px 0 14px 0;line-height:1.5}
.filter-bar{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-bottom:14px}
</style>
</head>
<body>
<canvas id="bg"></canvas>
<div class="glow glow-1"></div>
<div class="glow glow-2"></div>
<div class="glow glow-3"></div>
<script>
(function(){
const c=document.getElementById('bg'),x=c.getContext('2d');
let w,h,pts=[];
function resize(){w=c.width=innerWidth;h=c.height=innerHeight;pts=[];for(let i=0;i<80;i++)pts.push({x:Math.random()*w,y:Math.random()*h,vx:(Math.random()-.5)*.4,vy:(Math.random()-.5)*.4,r:Math.random()*1.5+.5})}
resize();window.addEventListener('resize',resize);
function draw(){
x.clearRect(0,0,w,h);
for(let i=0;i<pts.length;i++){
let p=pts[i];
p.x+=p.vx;p.y+=p.vy;
if(p.x<0)p.x=w;if(p.x>w)p.x=0;if(p.y<0)p.y=h;if(p.y>h)p.y=0;
x.beginPath();x.arc(p.x,p.y,p.r,0,Math.PI*2);x.fillStyle='rgba(92,201,176,.4)';x.fill();
for(let j=i+1;j<pts.length;j++){
let q=pts[j],dx=p.x-q.x,dy=p.y-q.y,d=Math.sqrt(dx*dx+dy*dy);
if(d<120){x.beginPath();x.moveTo(p.x,p.y);x.lineTo(q.x,q.y);x.strokeStyle='rgba(92,201,176,'+(1-d/120)*.15+')';x.lineWidth=.5;x.stroke()}
}
}
requestAnimationFrame(draw)}
draw();
})();
</script>
<div class="header">
    <div>
        <h1>软件部署管理平台</h1>
        <div class="subtitle">跨平台内网部署 · 多产品版本分列对照</div>
    </div>
</div>
<div class="tabs">
    <button class="active" onclick="switchTab('dashboard',this)">总览</button>
    <button onclick="switchTab('machines',this)">机器管理</button>
    <button onclick="switchTab('products',this)">产品管理</button>
    <button onclick="switchTab('packages',this)">版本包</button>
    <button onclick="switchTab('deploy',this)">部署</button>
    <button onclick="switchTab('tasks',this)">任务记录</button>
</div>

<div class="container">
    <!-- 总览 -->
    <div id="tab-dashboard">
        <div class="stats">
            <div class="stat-card"><div class="num" id="stat-total">-</div><div class="label">机器总数</div></div>
            <div class="stat-card"><div class="num" id="stat-online">-</div><div class="label">在线</div></div>
            <div class="stat-card"><div class="num" id="stat-offline">-</div><div class="label">离线</div></div>
            <div class="stat-card"><div class="num" id="stat-products">-</div><div class="label">配置产品数</div></div>
            <div class="stat-card"><div class="num" id="stat-packages">-</div><div class="label">版本包</div></div>
        </div>
        <div class="card">
            <h2>机器列表 <span style="font-size:12px;color:var(--text2);font-weight:500">（每列对应一个产品，单机多版本并排）</span></h2>
            <div class="matrix-scroll">
            <table>
                <thead><tr id="dashboard-head-row"><th>主机名</th><th>IP</th><th>平台</th><th>状态</th><th class="col-pv">…</th><th>最后心跳</th></tr></thead>
                <tbody id="dashboard-table"></tbody>
            </table>
            </div>
        </div>
    </div>

    <!-- 机器管理 -->
    <div id="tab-machines" class="hidden">
        <div class="card">
            <h2>机器列表 <span style="font-size:12px;color:var(--text2);font-weight:normal">（客户端启动后自动注册）</span></h2>
            <div class="matrix-scroll">
            <table>
                <thead><tr id="machines-head-row"><th>主机名</th><th>IP</th><th>平台</th><th>标签</th><th>状态</th><th class="col-pv">…</th><th>操作</th></tr></thead>
                <tbody id="machines-table"></tbody>
            </table>
            </div>
        </div>
    </div>

    <!-- 产品管理 -->
    <div id="tab-products" class="hidden">
        <div class="card">
            <h2>产品管理 <span style="font-size:12px;color:var(--text2);font-weight:500">（与版本包/部署中的「产品」一致，保存后写入 server_config.json）</span></h2>
            <p class="deploy-flow-hint" style="margin-bottom:16px">部署后在下列表中<strong>新增产品</strong>（初始配置为空）。产品 ID 只能为<strong>英文字母与数字</strong>、无空格；客户端实际安装目录为 <code style="color:var(--accent)">install_path/&lt;产品ID&gt;/</code>。若某产品下仍有版本包则不可从列表中移除该项。</p>
            <div class="form-row" style="margin-bottom:12px">
                <button type="button" class="btn btn-primary" onclick="addProductAdminRow()">添加一行</button>
                <button type="button" class="btn btn-primary" onclick="saveProductsAdmin()">保存</button>
                <button type="button" class="btn" onclick="loadProductsAdmin()" style="background:rgba(92,184,240,.15);color:var(--blue);border:1px solid rgba(92,184,240,.3)">重新加载</button>
            </div>
            <div class="matrix-scroll">
            <table>
                <thead><tr><th style="min-width:140px">产品 ID</th><th style="min-width:160px">显示名称</th><th style="width:100px">操作</th></tr></thead>
                <tbody id="products-admin-body"></tbody>
            </table>
            </div>
        </div>
    </div>

    <!-- 版本包 -->
    <div id="tab-packages" class="hidden">
        <div class="card">
            <h2>上传版本包</h2>
            <p class="deploy-flow-hint" style="margin-bottom:12px">按<strong>平台</strong>与<strong>产品</strong>归档存储；列表区可按产品筛选。</p>
            <div class="form-row">
                <label>平台<select id="upload-platform"><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
                <label>产品<select id="upload-product"></select></label>
                <label>类型<select id="upload-package-type"><option value="full">全量包（先删后装）</option><option value="incremental">增量包（覆盖）</option></select></label>
                <label>版本号<input type="text" id="upload-version" placeholder="留空则自动生成"></label>
                <label>文件<input type="file" id="upload-file" accept=".zip,.tar.gz,.gz"></label>
                <button class="btn btn-primary" onclick="uploadPackage()">上传</button>
            </div>
            <div id="upload-progress" class="hidden" style="margin-top:14px">
                <div class="progress-bar"><div class="fill" id="upload-bar" style="width:0"></div></div>
                <div style="font-size:12px;color:var(--text2);margin-top:6px" id="upload-status"></div>
            </div>
        </div>
        <div class="card">
            <h2>已上传的版本包</h2>
            <div class="filter-bar">
                <label style="display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--text2);font-weight:600">按产品筛选<select id="packages-filter-product" onchange="loadPackages()"></select></label>
            </div>
            <div class="matrix-scroll">
            <table>
                <thead><tr><th>版本</th><th>产品</th><th>平台</th><th>类型</th><th>文件名</th><th>大小</th><th>MD5</th><th>上传时间</th><th>操作</th></tr></thead>
                <tbody id="packages-table"></tbody>
            </table>
            </div>
        </div>
    </div>

    <!-- 部署 -->
    <div id="tab-deploy" class="hidden">
        <div class="card">
            <h2>创建部署任务</h2>
            <p class="deploy-flow-hint">顺序：<strong>平台</strong> → <strong>产品</strong> → <strong>版本号</strong>，下列机器表展示各产品当前版本（与总览一致）。</p>
            <div class="form-row">
                <label>平台<select id="deploy-platform" onchange="loadDeployVersions();loadDeployMachines()"><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
                <label>产品<select id="deploy-product" onchange="loadDeployVersions();loadDeployMachines()"></select></label>
                <label>版本号<select id="deploy-version"><option value="">请先选择平台与产品</option></select></label>
                <button class="btn btn-primary" onclick="createDeploy()">开始部署</button>
            </div>
            <div style="margin-top:14px">
                <label class="checkbox-label"><input type="checkbox" id="deploy-select-all" onchange="toggleSelectAll()"> 全选/取消全选（仅在线）</label>
            </div>
            <div class="matrix-scroll" style="margin-top:10px">
            <table>
                <thead><tr id="deploy-machines-head-row"><th width="48">选择</th><th>主机名</th><th>IP</th><th>状态</th><th class="col-pv">…</th></tr></thead>
                <tbody id="deploy-machines-table"></tbody>
            </table>
            </div>
        </div>
    </div>

    <!-- 任务记录 -->
    <div id="tab-tasks" class="hidden">
        <div class="card">
            <h2>部署任务列表</h2>
            <table>
                <thead><tr><th>序号</th><th>产品</th><th>版本</th><th>平台</th><th>状态</th><th>进度</th><th>成功</th><th>失败</th><th>创建时间</th><th>操作</th></tr></thead>
                <tbody id="tasks-table"></tbody>
            </table>
        </div>
        <div id="task-detail" class="hidden">
            <div class="card">
                <h2>任务详情 <span id="detail-task-id"></span> <button id="btn-force-stop" class="btn btn-danger hidden" onclick="forceStopTask()" style="margin-left:auto;font-size:13px;padding:6px 16px">强制停止</button></h2>
                <div class="progress-bar" style="margin-bottom:14px"><div class="fill" id="detail-progress" style="width:0"></div></div>
                <div id="detail-summary" style="font-size:13px;color:var(--text2);margin-bottom:14px"></div>
                <table>
                    <thead><tr><th>主机名</th><th>机器ID</th><th>IP</th><th>状态</th><th>实时进度</th><th>开始时间</th><th>结束时间</th><th>操作</th></tr></thead>
                    <tbody id="detail-logs-table"></tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<!-- 日志弹窗 -->
<div id="log-modal" class="modal-overlay hidden" onclick="if(event.target===this)closeLogModal()">
    <div class="modal-box">
        <button class="close-btn" onclick="closeLogModal()">关闭</button>
        <pre id="log-modal-content" style="margin:0;white-space:pre-wrap;"></pre>
    </div>
</div>

<script>
function showToast(msg, type) {
    const d = document.createElement('div');
    d.className = 'toast toast-' + (type || 'success');
    d.textContent = msg;
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 3000);
}

let PRODUCT_META = [];
function productLabel(pid) {
    const x = PRODUCT_META.find(p => p.id === pid);
    return (x && x.name) ? x.name : pid;
}
function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function productColCount() {
    const n = PRODUCT_META.length;
    return n === 0 ? 1 : n;
}
function matrixColspanDashboard() { return 5 + productColCount(); }
function matrixColspanMachines() { return 6 + productColCount(); }
function matrixColspanDeploy() { return 4 + productColCount(); }
function updateMachineMatrixHeaders() {
    const verTh = PRODUCT_META.length === 0
        ? '<th class="col-pv" title="未配置"><span class="th-pv-name" style="color:var(--text2)">产品版本</span><span class="th-pv-id">请先在「产品管理」添加</span></th>'
        : PRODUCT_META.map(p =>
            `<th class="col-pv" title="${escapeHtml(p.id)}"><span class="th-pv-name">${escapeHtml(p.name || p.id)}</span><span class="th-pv-id">${escapeHtml(p.id)}</span></th>`
        ).join('');
    const dr = document.getElementById('dashboard-head-row');
    if (dr) dr.innerHTML = `<th>主机名</th><th>IP</th><th>平台</th><th>状态</th>${verTh}<th>最后心跳</th>`;
    const mr = document.getElementById('machines-head-row');
    if (mr) mr.innerHTML = `<th>主机名</th><th>IP</th><th>平台</th><th>标签</th><th>状态</th>${verTh}<th>操作</th>`;
    const er = document.getElementById('deploy-machines-head-row');
    if (er) er.innerHTML = `<th width="48">选择</th><th>主机名</th><th>IP</th><th>状态</th>${verTh}`;
}
function singleProductVersionCell(m, pid, latestVersions, platform, colIdx) {
    const pv = m.product_versions || {};
    let ver = pv[pid];
    const hasPv = Object.keys(pv).length > 0;
    if ((ver === undefined || ver === '') && !hasPv && colIdx === 0 && m.current_version) {
        ver = m.current_version;
    }
    if (ver === undefined || ver === '' || ver === null) {
        return '<span class="pv-empty">—</span>';
    }
    const key = platform + '|' + pid;
    const lat = latestVersions[key];
    let badge = '';
    if (lat && ver === lat.version) badge = ' <span class="badge-latest">最新</span>';
    else if (lat && ver !== lat.version) badge = ' <span class="badge-outdated">落后</span>';
    return `<span class="pv-ver">${escapeHtml(ver)}${badge}</span>`;
}
function machineProductCells(m, latestVersions, platform) {
    if (PRODUCT_META.length === 0) {
        const pv = m.product_versions || {};
        const parts = Object.keys(pv).length
            ? Object.entries(pv).map(([k, v]) => `${k}: ${v}`).join(' · ')
            : (m.current_version || '');
        const txt = parts || '—';
        return `<td class="pv-cell"><span class="pv-ver" style="font-size:12px;color:var(--text2)" title="服务端未配置产品列时仅作概要">${escapeHtml(txt)}</span></td>`;
    }
    return PRODUCT_META.map((p, i) =>
        `<td class="pv-cell">${singleProductVersionCell(m, p.id, latestVersions, platform, i)}</td>`
    ).join('');
}

function headers() { return {"Content-Type": "application/json"}; }

async function api(path, opts = {}) {
    const resp = await fetch(path, {headers: headers(), ...opts});
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || resp.statusText); }
    return resp.json();
}

async function initProductsFromServer() {
    try {
        const c = await api("/api/config");
        PRODUCT_META = Array.isArray(c.products) ? c.products : [];
    } catch(e) { console.warn("initProductsFromServer", e); }
    const emptyOpt = '<option value="">（请先在「产品管理」添加产品）</option>';
    const opts = PRODUCT_META.length
        ? PRODUCT_META.map(p => `<option value="${p.id}">${(p.name||p.id)}</option>`).join("")
        : emptyOpt;
    const up = document.getElementById("upload-product");
    const dp = document.getElementById("deploy-product");
    const pf = document.getElementById("packages-filter-product");
    if (up) up.innerHTML = opts;
    if (dp) dp.innerHTML = opts;
    if (pf) {
        if (PRODUCT_META.length)
            pf.innerHTML = '<option value="">全部产品</option>' + PRODUCT_META.map(p => `<option value="${p.id}">${(p.name||p.id)}</option>`).join("");
        else
            pf.innerHTML = '<option value="">全部产品</option>';
    }
    updateMachineMatrixHeaders();
}

function switchTab(name, btn) {
    document.querySelectorAll("[id^='tab-']").forEach(el => el.classList.add("hidden"));
    document.getElementById("tab-" + name).classList.remove("hidden");
    document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    if (window._detailRefreshTimer) { clearTimeout(window._detailRefreshTimer); window._detailRefreshTimer = null; }
    if (name === "dashboard") loadDashboard();
    if (name === "machines") loadMachines();
    if (name === "products") loadProductsAdmin();
    if (name === "packages") loadPackages();
    if (name === "deploy") { loadDeployVersions(); loadDeployMachines(); }
    if (name === "tasks") loadTasks();
}

function getLatestVersions(packages) {
    const latest = {};
    packages.forEach(p => {
        const pid = p.product_id || 'default';
        const key = p.platform + '|' + pid;
        if (!latest[key] || p.uploaded_at > latest[key].uploaded_at) {
            latest[key] = p;
        }
    });
    return latest;
}

async function loadDashboard() {
    try {
        const [machines, packages] = await Promise.all([api("/api/machines"), api("/api/packages")]);
        const latestVersions = getLatestVersions(packages);
        document.getElementById("stat-total").textContent = machines.length;
        document.getElementById("stat-online").textContent = machines.filter(m => m.status === "online").length;
        document.getElementById("stat-offline").textContent = machines.filter(m => m.status === "offline").length;
        document.getElementById("stat-packages").textContent = packages.length;
        const sp = document.getElementById("stat-products");
        if (sp) sp.textContent = PRODUCT_META.length;
        const tb = document.getElementById("dashboard-table");
        if (machines.length === 0) {
            tb.innerHTML = '<tr><td colspan="'+matrixColspanDashboard()+'" style="text-align:center;color:var(--text2);padding:24px">暂无机器，客户端上线后将自动出现</td></tr>';
            return;
        }
        tb.innerHTML = machines.map(m => `<tr>
            <td>${escapeHtml(m.hostname||"-")}</td><td>${escapeHtml(m.ip)}</td><td>${escapeHtml(m.platform)}</td>
            <td><span class="badge badge-${m.status}">${m.status==="online"?"在线":"离线"}</span></td>
            ${machineProductCells(m, latestVersions, m.platform)}<td>${escapeHtml(m.last_heartbeat||"-")}</td>
        </tr>`).join("");
    } catch(e) { console.error("loadDashboard:", e); }
}

async function loadMachines() {
    try {
        const [machines, packages] = await Promise.all([api("/api/machines"), api("/api/packages")]);
        const latestVersions = getLatestVersions(packages);
        const tb = document.getElementById("machines-table");
        if (machines.length === 0) {
            tb.innerHTML = '<tr><td colspan="'+matrixColspanMachines()+'" style="text-align:center;color:var(--text2);padding:24px">暂无机器</td></tr>';
            return;
        }
        tb.innerHTML = machines.map(m => `<tr>
            <td>${escapeHtml(m.hostname||"-")}</td><td>${escapeHtml(m.ip)}</td><td>${escapeHtml(m.platform)}</td><td>${escapeHtml(m.tag||"-")}</td>
            <td><span class="badge badge-${m.status}">${m.status==="online"?"在线":"离线"}</span></td>
            ${machineProductCells(m, latestVersions, m.platform)}
            <td><button class="btn btn-danger" onclick="deleteMachine('${m.id}')">删除</button></td>
        </tr>`).join("");
    } catch(e) { console.error("loadMachines:", e); }
}

async function deleteMachine(id) {
    if (!confirm("确认删除该机器？")) return;
    try { await api("/api/machines/" + id, {method: "DELETE"}); showToast("已删除"); loadMachines(); } catch(e) { showToast(e.message, "error"); }
}

async function loadProductsAdmin() {
    try {
        const list = await api("/api/products");
        const tb = document.getElementById("products-admin-body");
        if (!list.length) {
            tb.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--text2);padding:20px">暂无产品</td></tr>';
            return;
        }
        tb.innerHTML = list.map(p => `<tr>
            <td><input type="text" class="pa-id" value="${escapeHtml(p.id)}" style="width:100%;max-width:220px;padding:8px 10px;border-radius:8px;border:1px solid rgba(92,184,240,.2);background:rgba(30,41,59,.7);color:var(--text)"/></td>
            <td><input type="text" class="pa-name" value="${escapeHtml(p.name)}" style="width:100%;max-width:260px;padding:8px 10px;border-radius:8px;border:1px solid rgba(92,184,240,.2);background:rgba(30,41,59,.7);color:var(--text)"/></td>
            <td><button type="button" class="btn btn-danger" onclick="this.closest('tr').remove()">删除</button></td>
        </tr>`).join("");
    } catch(e) {
        showToast(e.message, "error");
    }
}

function addProductAdminRow() {
    const tb = document.getElementById("products-admin-body");
    const empty = tb && tb.querySelector("td[colspan]");
    if (empty) tb.innerHTML = "";
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input type="text" class="pa-id" placeholder="例如 APP1、TYPT" pattern="[a-zA-Z0-9]+" style="width:100%;max-width:220px;padding:8px 10px;border-radius:8px;border:1px solid rgba(92,184,240,.2);background:rgba(30,41,59,.7);color:var(--text)"/></td>
        <td><input type="text" class="pa-name" placeholder="显示名称" style="width:100%;max-width:260px;padding:8px 10px;border-radius:8px;border:1px solid rgba(92,184,240,.2);background:rgba(30,41,59,.7);color:var(--text)"/></td>
        <td><button type="button" class="btn btn-danger" onclick="this.closest('tr').remove()">删除</button></td>`;
    tb.appendChild(tr);
}

async function saveProductsAdmin() {
    const tb = document.getElementById("products-admin-body");
    if (!tb) return;
    const products = [];
    for (const tr of tb.querySelectorAll("tr")) {
        if (tr.querySelector("td[colspan]")) continue;
        const idEl = tr.querySelector(".pa-id");
        const nameEl = tr.querySelector(".pa-name");
        if (!idEl) continue;
        const id = idEl.value.trim();
        const name = nameEl ? nameEl.value.trim() : "";
        if (!id && !name) continue;
        if (!id) {
            showToast("产品 ID 不能为空", "error");
            return;
        }
        if (!/^[a-zA-Z0-9]+$/.test(id)) {
            showToast("产品 ID 只能为英文字母与数字: " + id, "error");
            return;
        }
        products.push({ id, name: name || id });
    }
    try {
        await api("/api/products", { method: "PUT", body: JSON.stringify({ products }) });
        showToast("产品列表已保存");
        await initProductsFromServer();
        loadDashboard();
        const pkgTab = document.getElementById("tab-packages");
        if (pkgTab && !pkgTab.classList.contains("hidden")) loadPackages();
        const depTab = document.getElementById("tab-deploy");
        if (depTab && !depTab.classList.contains("hidden")) { loadDeployVersions(); loadDeployMachines(); }
    } catch(e) {
        showToast(e.message, "error");
    }
}

async function loadPackages() {
    try {
        const pkgs = await api("/api/packages");
        const filtEl = document.getElementById("packages-filter-product");
        const fid = filtEl ? filtEl.value : '';
        const list = fid ? pkgs.filter(p => (p.product_id || 'default') === fid) : pkgs;
        const tb = document.getElementById("packages-table");
        if (list.length === 0) {
            tb.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text2);padding:24px">'+(fid?'该筛选下暂无版本包':'暂无版本包')+'</td></tr>';
            return;
        }
        tb.innerHTML = list.map(p => {
            const typeLabel = p.package_type === 'incremental' ? '增量' : '全量';
            const typeColor = p.package_type === 'incremental' ? 'var(--blue)' : 'var(--accent)';
            const pid = p.product_id || 'default';
            return `<tr>
                <td>${p.version}</td><td>${productLabel(pid)}</td><td>${p.platform}</td><td><span style="color:${typeColor}">${typeLabel}</span></td><td>${p.filename}</td>
                <td>${(p.size/1024/1024).toFixed(1)} MB</td><td style="font-size:11px;color:var(--text2)">${p.md5}</td><td>${p.uploaded_at}</td>
                <td><button class="btn btn-danger" onclick="deletePackage('${p.id}')">删除</button></td>
            </tr>`;
        }).join("");
    } catch(e) { console.error("loadPackages:", e); }
}

async function deletePackage(id) {
    if (!confirm("确认删除此版本包？文件将被永久删除。")) return;
    try { await api("/api/packages/" + id, {method: "DELETE"}); showToast("已删除"); loadPackages(); } catch(e) { showToast(e.message, "error"); }
}

function uploadPackage() {
    const file = document.getElementById("upload-file").files[0];
    if (!file) { showToast("请选择文件", "error"); return; }
    const platform = document.getElementById("upload-platform").value;
    const version = document.getElementById("upload-version").value;
    const packageType = document.getElementById("upload-package-type").value;
    const productId = document.getElementById("upload-product").value;
    if (!productId) { showToast("请先在「产品管理」中添加产品", "error"); return; }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("platform", platform);
    formData.append("package_type", packageType);
    formData.append("product_id", productId);
    if (version) formData.append("version", version);
    document.getElementById("upload-progress").classList.remove("hidden");
    document.getElementById("upload-bar").style.width = "0";
    document.getElementById("upload-status").textContent = "准备上传...";
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = e => {
        if (e.lengthComputable) {
            const pct = (e.loaded/e.total*100).toFixed(1);
            document.getElementById("upload-bar").style.width = pct + "%";
            document.getElementById("upload-status").textContent = `上传中... ${pct}% (${(e.loaded/1024/1024).toFixed(1)}/${(e.total/1024/1024).toFixed(1)} MB)`;
        }
    };
    xhr.onload = () => {
        document.getElementById("upload-progress").classList.add("hidden");
        if (xhr.status === 201) {
            showToast("上传成功");
            loadPackages();
            document.getElementById("upload-file").value = "";
            document.getElementById("upload-version").value = "";
        } else {
            let msg = "上传失败";
            try { const r = JSON.parse(xhr.responseText); msg = r.error || msg; } catch(e) {}
            showToast(msg + " (HTTP " + xhr.status + ")", "error");
        }
    };
    xhr.onerror = () => {
        document.getElementById("upload-progress").classList.add("hidden");
        showToast("上传失败: 网络错误", "error");
    };
    xhr.open("POST", "/api/packages/upload");
    xhr.send(formData);
}

async function loadDeployVersions() {
    const platform = document.getElementById("deploy-platform").value;
    const productEl = document.getElementById("deploy-product");
    const product_id = productEl ? productEl.value : '';
    const sel = document.getElementById("deploy-version");
    if (!product_id) {
        sel.innerHTML = "<option value=''>请先在「产品管理」添加产品</option>";
        return;
    }
    sel.innerHTML = "<option value=''>加载中...</option>";
    try {
        const pkgs = await api("/api/packages");
        const filtered = pkgs.filter(p => p.platform === platform && (p.product_id || 'default') === product_id);
        if (filtered.length === 0) {
            sel.innerHTML = "<option value=''>该平台/产品暂无包，请先上传</option>";
        } else {
            sel.innerHTML = filtered.map(p =>
                `<option value="${p.version}">${p.version} (${p.filename}, ${(p.size/1024/1024).toFixed(1)}MB)</option>`
            ).join("");
        }
    } catch(e) {
        sel.innerHTML = "<option value=''>加载失败</option>";
        console.error("loadDeployVersions:", e);
    }
}

async function loadDeployMachines() {
    const platform = document.getElementById("deploy-platform").value;
    try {
        const [machines, packages] = await Promise.all([api("/api/machines?platform=" + platform), api("/api/packages")]);
        const latestVersions = getLatestVersions(packages);
        const tb = document.getElementById("deploy-machines-table");
        if (machines.length === 0) {
            tb.innerHTML = '<tr><td colspan="'+matrixColspanDeploy()+'" style="text-align:center;color:var(--text2);padding:24px">该平台暂无机器</td></tr>';
            return;
        }
        tb.innerHTML = machines.map(m => {
            const online = m.status === "online";
            const rowStyle = online ? "" : "opacity:.45";
            const cbAttr = online ? "checked" : "disabled";
            return `<tr style="${rowStyle}">
                <td><input type="checkbox" class="deploy-cb" value="${m.id}" ${cbAttr}></td>
                <td>${escapeHtml(m.hostname||"-")}</td><td>${escapeHtml(m.ip)}</td>
                <td><span class="badge badge-${m.status}">${online?"在线":"离线"}</span></td>
                ${machineProductCells(m, latestVersions, m.platform)}
            </tr>`;
        }).join("");
    } catch(e) { console.error("loadDeployMachines:", e); }
}

function toggleSelectAll() {
    const checked = document.getElementById("deploy-select-all").checked;
    document.querySelectorAll(".deploy-cb").forEach(cb => cb.checked = checked);
}

async function createDeploy() {
    const version = document.getElementById("deploy-version").value;
    const platform = document.getElementById("deploy-platform").value;
    const product_id = document.getElementById("deploy-product").value;
    const ids = Array.from(document.querySelectorAll(".deploy-cb:checked")).map(cb => cb.value);
    if (!product_id) { showToast("请先在「产品管理」中添加产品", "error"); return; }
    if (!version) { showToast("请选择版本号", "error"); return; }
    if (!ids.length) { showToast("请选择至少一台机器", "error"); return; }
    try {
        const r = await api("/api/deploy", {method: "POST", body: JSON.stringify({version, platform, product_id, machine_ids: ids})});
        showToast("部署任务已创建");
        switchTab("tasks", document.querySelectorAll(".tabs button")[5]);
        setTimeout(() => showTaskDetail(r.task_id), 600);
    } catch(e) { showToast(e.message, "error"); }
}

async function loadTasks() {
    try {
        const tasks = await api("/api/tasks");
        const tb = document.getElementById("tasks-table");
        if (tasks.length === 0) {
            tb.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text2);padding:24px">暂无部署任务</td></tr>';
            return;
        }
        tb.innerHTML = tasks.map((t, i) => {
            const seq = tasks.length - i;
            const pct = t.total > 0 ? ((t.success + t.failed) / t.total * 100).toFixed(0) : 0;
            const statusMap = {pending:"等待中",running:"部署中",finished:"已完成",stopped:"已停止"};
            const badgeMap = {pending:"pending",running:"running",finished:"finished",stopped:"stopped"};
            const canStop = t.status === "running" || t.status === "pending";
            const stopBtn = canStop ? `<button class="btn btn-danger" onclick="forceStopTaskFromList('${t.id}')" style="margin-left:6px">停止</button>` : "";
            const pid = t.product_id || 'default';
            return `<tr>
                <td>${seq}</td><td>${productLabel(pid)}</td><td>${t.version}</td><td>${t.platform}</td>
                <td><span class="badge badge-${badgeMap[t.status]||t.status}">${statusMap[t.status]||t.status}</span></td>
                <td><div class="progress-bar" style="width:100px"><div class="fill" style="width:${pct}%"></div></div> ${pct}%</td>
                <td style="color:var(--accent)">${t.success}</td><td style="color:var(--red)">${t.failed}</td>
                <td>${t.created_at}</td>
                <td><button class="btn btn-primary" onclick="showTaskDetail('${t.id}')">详情</button>${stopBtn}</td>
            </tr>`;
        }).join("");
    } catch(e) {
        console.error("loadTasks:", e);
        document.getElementById("tasks-table").innerHTML = '<tr><td colspan="10" style="color:var(--red)">加载失败: ' + e.message + '</td></tr>';
    }
}

async function forceStopTaskFromList(taskId) {
    if (!confirm("确认强制停止此任务？")) return;
    try {
        await api("/api/tasks/" + taskId + "/stop", {method: "POST"});
        showToast("任务已停止");
        loadTasks();
    } catch(e) { showToast(e.message, "error"); }
}

async function showTaskDetail(taskId) {
    try {
        const task = await api("/api/tasks/" + taskId);
        document.getElementById("task-detail").classList.remove("hidden");
        document.getElementById("detail-task-id").textContent = "(" + taskId + ")";
        const pct = task.total > 0 ? ((task.success + task.failed) / task.total * 100).toFixed(0) : 0;
        document.getElementById("detail-progress").style.width = pct + "%";
        document.getElementById("detail-summary").textContent = `产品: ${productLabel(task.product_id||'default')} | 版本: ${task.version||"-"} | 平台: ${task.platform||"-"} | 总计: ${task.total} | 成功: ${task.success} | 失败: ${task.failed} | 进度: ${pct}%`;
        const stopBtn = document.getElementById("btn-force-stop");
        if (task.status === "running" || task.status === "pending") {
            stopBtn.classList.remove("hidden");
            window._currentTaskId = taskId;
        } else {
            stopBtn.classList.add("hidden");
        }
        window._detailTaskId = taskId;
        const tb = document.getElementById("detail-logs-table");
        window._taskLogs = task.logs || [];
        const liveProgress = task.live_progress || {};
        if (window._taskLogs.length === 0 && Object.keys(liveProgress).length === 0) {
            tb.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text2);padding:16px">暂无执行记录</td></tr>';
        } else {
            const allMachineIds = new Set([...window._taskLogs.map(l => l.machine_id), ...Object.keys(liveProgress)]);
            const logMap = {};
            window._taskLogs.forEach((l) => { logMap[l.machine_id] = {...l}; });
            let rows = "";
            allMachineIds.forEach(mid => {
                const l = logMap[mid];
                const lp = liveProgress[mid] || {};
                const status = l ? l.status : (lp.percent >= 0 ? "running" : "pending");
                const statusLabel = {success:"成功",failed:"失败",running:"执行中",pending:"等待中",stopped:"已停止"}[status] || status;
                const phase = lp.phase || "";
                const pct = lp.percent || 0;
                const detail = lp.detail || "";
                let progressHtml = "";
                if (status === "running" && phase) {
                    progressHtml = `<div style="margin-top:4px"><div class="progress-bar" style="width:120px"><div class="fill" style="width:${pct}%"></div></div> <span style="font-size:11px;color:var(--text2)">${phase} ${pct}%</span>${detail ? '<br><span style="font-size:11px;color:var(--text2)">' + detail + '</span>' : ''}</div>`;
                }
                const safeMid = String(mid).replace(/'/g, "\\'");
                const logBtn = l ? `<button class="btn btn-primary" onclick="showLog('${safeMid}')">查看日志</button>` : "-";
                const hn = l && l.hostname ? l.hostname : "-";
                const ip = l && l.ip ? l.ip : "-";
                rows += `<tr>
                    <td>${hn}</td>
                    <td style="font-family:monospace;font-size:11px">${mid}</td>
                    <td style="font-family:monospace">${ip}</td>
                    <td><span class="badge badge-${status}">${statusLabel}</span></td>
                    <td>${progressHtml}</td>
                    <td>${l ? (l.started_at||"-") : "-"}</td><td>${l ? (l.finished_at||"-") : "-"}</td>
                    <td>${logBtn}</td>
                </tr>`;
            });
            tb.innerHTML = rows;
        }
        if (task.status === "running") {
            window._detailRefreshTimer = setTimeout(() => showTaskDetail(taskId), 5000);
        }
    } catch(e) {
        console.error("showTaskDetail:", e);
        showToast("加载任务详情失败: " + e.message, "error");
    }
}

function showLog(machineId) {
    const logs = window._taskLogs || [];
    const row = logs.find(x => x.machine_id === machineId);
    const body = row ? (row.log || "(无日志)") : "(找不到该机器的执行记录)";
    const tid = window._detailTaskId || "";
    const hdr = "========== 任务 / 机器概要 ==========\n"
        + "任务 ID: " + tid + "\n"
        + "机器 ID: " + (row && row.machine_id ? row.machine_id : machineId) + "\n"
        + "主机名: " + (row && row.hostname ? row.hostname : "-") + "\n"
        + "IP: " + (row && row.ip ? row.ip : "-") + "\n"
        + "状态: " + (row && row.status ? row.status : "-") + "\n"
        + "开始: " + (row && row.started_at ? row.started_at : "-") + "\n"
        + "结束: " + (row && row.finished_at ? row.finished_at : "-") + "\n"
        + "说明: 以下为客户端上报的完整日志（含 install_path、BASE_DIR、下载路径等）。\n"
        + "========================================\n\n";
    document.getElementById("log-modal-content").textContent = hdr + body;
    document.getElementById("log-modal").classList.remove("hidden");
}

function closeLogModal() {
    document.getElementById("log-modal").classList.add("hidden");
}

async function forceStopTask() {
    const taskId = window._currentTaskId;
    if (!taskId) return;
    if (!confirm("确认强制停止此任务？所有执行中的机器将被标记为失败。")) return;
    try {
        const r = await api("/api/tasks/" + taskId + "/stop", {method: "POST"});
        showToast("任务已停止: 成功" + r.success + "台, 失败" + r.failed + "台");
        showTaskDetail(taskId);
        loadTasks();
    } catch(e) { showToast(e.message, "error"); }
}

function refresh() { loadDashboard(); }
(async function(){ await initProductsFromServer(); refresh(); })();
setInterval(() => {
    const active = document.querySelector(".tabs button.active");
    if (!active) return;
    if (active.textContent === "总览") loadDashboard();
    if (active.textContent === "任务记录") loadTasks();
}, 5000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return Response(DASHBOARD_HTML, content_type="text/html; charset=utf-8")

# ══════════════════════════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════════════════════════

def get_local_ip():
    """Get the actual LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def truncate_log(path):
    """Clear log file on startup."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"[{now_str()}] Log cleared on startup\n")
    except Exception:
        pass

def stop_old_tasks():
    """Mark all pending/running tasks as stopped on server restart."""
    try:
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) as c FROM deployments WHERE status IN ('pending','running')"
        ).fetchone()["c"]
        if count > 0:
            conn.execute(
                "UPDATE deployments SET status='stopped', finished_at=? WHERE status IN ('pending','running')",
                (now_str(),)
            )
            conn.execute(
                "UPDATE deploy_logs SET status='failed', log=COALESCE(log,'') || '\n[服务端重启] 任务被自动终止', finished_at=? "
                "WHERE status NOT IN ('success','failed')",
                (now_str(),)
            )
            conn.commit()
            logger.info("已终止 %d 个未完成任务", count)
            print(f"[INFO] 已终止 {count} 个未完成的部署任务")
        conn.close()
    except Exception as e:
        logger.error("终止旧任务失败: %s", e)

if __name__ == "__main__":
    truncate_log(LOG_FILE)
    init_db()
    migrate_db()
    stop_old_tasks()
    recover_packages()

    t = threading.Thread(target=heartbeat_checker, daemon=True)
    t.start()

    host = config.get("host", "0.0.0.0")
    port = config.get("port", 61234)
    local_ip = get_local_ip()
    logger.info("=" * 50)
    logger.info("部署服务端启动")
    logger.info("监听地址: http://%s:%d", local_ip, port)
    logger.info("数据库: %s", DATABASE)
    logger.info("包目录: %s", PACKAGES_DIR)
    logger.info("管理应用: %s", ", ".join(a.get("name","") for a in APPS))
    logger.info("=" * 50)
    print(f"[OK] 服务端启动成功 http://{local_ip}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
