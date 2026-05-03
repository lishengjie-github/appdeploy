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
import sqlite3
import hashlib
import logging
import zipfile
import tarfile
import threading
import traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_file, Response

# ── 日志配置 ──────────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.log")
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "server_config.json")

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 61234,
    "packages_dir": "./packages",
    "database": "./deploy.db",
    "max_concurrent": 10,
    "heartbeat_timeout": 90,
    "apps": [
        {"name": "主程序", "windows": "myqtapp.exe", "linux": "myqtapp"},
        {"name": "辅助工具", "windows": "helper.exe", "linux": "helper"},
    ]
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            logger.info("已加载配置文件: %s", CONFIG_FILE)
            return cfg
        except Exception as e:
            logger.error("配置文件读取失败，使用默认配置: %s", e)
    else:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        logger.info("已生成默认配置文件: %s", CONFIG_FILE)
    return DEFAULT_CONFIG.copy()

config = load_config()
PACKAGES_DIR = os.path.abspath(config["packages_dir"])
DATABASE = os.path.abspath(config["database"])
MAX_CONCURRENT = config.get("max_concurrent", 10)
HEARTBEAT_TIMEOUT = config.get("heartbeat_timeout", 90)
APPS = config.get("apps", [])

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
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
    """数据库迁移：添加新列"""
    try:
        conn = get_db()
        # 检查 packages 表是否有 package_type 列
        cols = {row[1] for row in conn.execute("PRAGMA table_info(packages)").fetchall()}
        if "package_type" not in cols:
            conn.execute("ALTER TABLE packages ADD COLUMN package_type TEXT DEFAULT 'full'")
            conn.commit()
            logger.info("数据库迁移: packages 表添加 package_type 列")
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

def prune_versions(platform_type, keep=5):
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, filepath FROM packages WHERE platform=? ORDER BY uploaded_at DESC",
            (platform_type,)
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

def recover_packages():
    try:
        conn = get_db()
        existing = {row["filepath"] for row in conn.execute("SELECT filepath FROM packages").fetchall()}
        recovered = 0
        for platform_type in ("windows", "linux"):
            platform_dir = os.path.join(PACKAGES_DIR, platform_type)
            if not os.path.isdir(platform_dir):
                continue
            for version in os.listdir(platform_dir):
                version_dir = os.path.join(platform_dir, version)
                if not os.path.isdir(version_dir):
                    continue
                for filename in os.listdir(version_dir):
                    filepath = os.path.join(version_dir, filename)
                    if not os.path.isfile(filepath) or filepath in existing:
                        continue
                    pid = gen_id()
                    file_md5 = md5_file(filepath)
                    file_size = os.path.getsize(filepath)
                    mtime = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        "INSERT OR IGNORE INTO packages (id, version, platform, filename, filepath, md5, size, uploaded_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (pid, version, platform_type, filename, filepath, file_md5, file_size, mtime)
                    )
                    recovered += 1
                    logger.info("恢复包记录: %s/%s/%s", platform_type, version, filename)
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

        for mid in target_ids:
            execute_deployment(task_id, mid, version, platform_type)

        conn.execute("UPDATE deployments SET total=? WHERE id=?", (len(target_ids), task_id))
        conn.commit()
        conn.close()
        logger.info("部署任务 %s 已启动，版本=%s 平台=%s 共%d台", task_id, version, platform_type, len(target_ids))
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
        conn.close()
        logger.debug("查询机器列表: 共 %d 条", len(rows))
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        logger.error("查询机器列表失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/machines", methods=["POST"])
def add_machine():
    try:
        data = request.json
        if not data or not data.get("ip"):
            return jsonify({"error": "缺少 ip 字段"}), 400
        mid = gen_id()
        conn = get_db()
        conn.execute(
            "INSERT INTO machines (id, ip, hostname, platform, tag) VALUES (?,?,?,?,?)",
            (mid, data["ip"], data.get("hostname", ""), data.get("platform", "unknown"), data.get("tag", ""))
        )
        conn.commit()
        conn.close()
        logger.info("添加机器: id=%s ip=%s platform=%s", mid, data["ip"], data.get("platform"))
        return jsonify({"id": mid, "message": "机器已添加"}), 201
    except Exception as e:
        logger.error("添加机器失败: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/api/machines/<machine_id>", methods=["DELETE"])
def delete_machine(machine_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM machines WHERE id=?", (machine_id,))
        conn.commit()
        conn.close()
        logger.info("删除机器: %s", machine_id)
        return jsonify({"message": "已删除"})
    except Exception as e:
        logger.error("删除机器失败: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/machines/import", methods=["POST"])
def import_machines():
    try:
        content_type = request.content_type or ""
        conn = get_db()
        count = 0
        if "json" in content_type:
            machines = request.json
            if not isinstance(machines, list):
                return jsonify({"error": "需要 JSON 数组"}), 400
            for m in machines:
                mid = gen_id()
                conn.execute(
                    "INSERT INTO machines (id, ip, hostname, platform, tag) VALUES (?,?,?,?,?)",
                    (mid, m.get("ip", ""), m.get("hostname", ""), m.get("platform", "unknown"), m.get("tag", ""))
                )
                count += 1
        else:
            csv_data = request.data.decode("utf-8")
            for line in csv_data.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 1 and parts[0]:
                    mid = gen_id()
                    conn.execute(
                        "INSERT INTO machines (id, ip, hostname, platform, tag) VALUES (?,?,?,?,?)",
                        (mid, parts[0], parts[1] if len(parts) > 1 else "",
                         parts[2] if len(parts) > 2 else "unknown",
                         parts[3] if len(parts) > 3 else "")
                    )
                    count += 1
        conn.commit()
        conn.close()
        logger.info("批量导入机器: %d 台", count)
        return jsonify({"message": f"已导入 {count} 台机器"})
    except Exception as e:
        logger.error("批量导入失败: %s\n%s", e, traceback.format_exc())
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

        dest_dir = os.path.join(PACKAGES_DIR, platform_type, version)
        os.makedirs(dest_dir, exist_ok=True)
        filepath = os.path.join(dest_dir, f.filename)
        f.save(filepath)

        file_md5 = md5_file(filepath)
        file_size = os.path.getsize(filepath)

        pid = gen_id()
        conn = get_db()
        conn.execute(
            "INSERT INTO packages (id, version, platform, filename, filepath, md5, size, package_type) VALUES (?,?,?,?,?,?,?,?)",
            (pid, version, platform_type, f.filename, filepath, file_md5, file_size, package_type)
        )
        conn.commit()
        conn.close()

        prune_versions(platform_type, keep=5)
        logger.info("包已上传: %s/%s (%s, 类型=%s, %.2fMB)", platform_type, version, f.filename, package_type, file_size / 1024 / 1024)
        return jsonify({
            "id": pid, "version": version, "platform": platform_type,
            "filename": f.filename, "md5": file_md5, "size": file_size, "package_type": package_type
        }), 201
    except Exception as e:
        logger.error("上传处理异常: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": f"服务器内部错误: {e}"}), 500

@app.route("/api/download/<platform_type>/<version>/<filename>")
def download_package(platform_type, version, filename):
    try:
        filepath = os.path.join(PACKAGES_DIR, platform_type, version, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "文件不存在"}), 404
        logger.info("文件下载: %s/%s/%s", platform_type, version, filename)
        return send_file(filepath, as_attachment=True)
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
        machine_ids = data.get("machine_ids", [])

        if not version or not platform_type:
            return jsonify({"error": "缺少 version 或 platform"}), 400

        conn = get_db()
        pkg = conn.execute(
            "SELECT * FROM packages WHERE version=? AND platform=?", (version, platform_type)
        ).fetchone()
        if not pkg:
            conn.close()
            return jsonify({"error": f"未找到 {platform_type}/{version} 的包"}), 404

        if not machine_ids:
            rows = conn.execute(
                "SELECT id FROM machines WHERE platform=? AND status='online'", (platform_type,)
            ).fetchall()
            machine_ids = [r["id"] for r in rows]

        if not machine_ids:
            conn.close()
            return jsonify({"error": "没有可部署的目标机器"}), 400

        task_id = gen_id()
        conn.execute(
            "INSERT INTO deployments (id, version, platform, target_ids, status, total) VALUES (?,?,?,?,?,?)",
            (task_id, version, platform_type, json.dumps(machine_ids), "pending", len(machine_ids))
        )
        conn.commit()
        conn.close()

        with task_lock:
            future = executor.submit(start_deployment, task_id)
            active_tasks[task_id] = future

        logger.info("创建部署任务: %s, 版本=%s, 平台=%s, 目标=%d台", task_id, version, platform_type, len(machine_ids))
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
            "SELECT * FROM deploy_logs WHERE task_id=? ORDER BY started_at", (task_id,)
        ).fetchall()
        conn.close()
        result = dict(task)
        result["logs"] = [dict(l) for l in logs]
        with progress_lock:
            result["live_progress"] = deploy_progress.get(task_id, {})
        return jsonify(result)
    except Exception as e:
        logger.error("查询任务详情失败: %s", e)
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
            "UPDATE deployments SET status='finished', success=?, failed=?, finished_at=? WHERE id=?",
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

        conn = get_db()
        existing = conn.execute("SELECT * FROM machines WHERE id=?", (client_id,)).fetchone()

        if existing:
            conn.execute(
                "UPDATE machines SET ip=?, hostname=?, platform=?, status='online', "
                "current_version=?, last_heartbeat=? WHERE id=?",
                (ip, hostname, platform_type, qt_version, now_str(), client_id)
            )
        else:
            if not client_id:
                client_id = gen_id()
            conn.execute(
                "INSERT OR REPLACE INTO machines (id, ip, hostname, platform, status, current_version, last_heartbeat) "
                "VALUES (?,?,?,?,?,?,?)",
                (client_id, ip, hostname, platform_type, "online", qt_version, now_str())
            )
            logger.info("新客户端注册: id=%s ip=%s hostname=%s platform=%s", client_id, ip, hostname, platform_type)
        conn.commit()
        conn.close()
        return jsonify({"client_id": client_id, "status": "ok"})
    except Exception as e:
        logger.error("心跳处理异常: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500

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

            pkg = conn.execute(
                "SELECT * FROM packages WHERE version=? AND platform=?",
                (task["version"], task["platform"])
            ).fetchone()
            if not pkg:
                continue

            conn.close()
            pkg_dict = dict(pkg)
            return jsonify({
                "task_id": task["id"],
                "version": task["version"],
                "platform": task["platform"],
                "filename": pkg_dict["filename"],
                "md5": pkg_dict["md5"],
                "package_type": pkg_dict.get("package_type", "full"),
                "download_url": f"/api/download/{task['platform']}/{task['version']}/{pkg_dict['filename']}"
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
            task = conn.execute("SELECT version FROM deployments WHERE id=?", (task_id,)).fetchone()
            if task:
                conn.execute(
                    "UPDATE machines SET current_version=?, last_deploy=? WHERE id=?",
                    (task["version"], now_str(), client_id)
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
        with progress_lock:
            if task_id not in deploy_progress:
                deploy_progress[task_id] = {}
            deploy_progress[task_id][client_id] = {
                "phase": data.get("phase", ""),
                "percent": data.get("percent", 0),
                "detail": data.get("detail", ""),
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
    return jsonify({"apps": APPS, "port": config.get("port", 61234)})

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
th{background:rgba(51,65,85,.5);color:#cbd5e1;font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:1px}
tr{transition:all .2s}
tr:hover{background:rgba(92,201,176,.04)}
.badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:.5px}
.badge-online{background:rgba(92,201,176,.12);color:var(--accent);box-shadow:0 0 10px rgba(92,201,176,.15)}
.badge-offline{background:rgba(232,96,96,.12);color:var(--red);box-shadow:0 0 10px rgba(232,96,96,.15)}
.badge-running{background:rgba(92,184,240,.12);color:var(--blue);box-shadow:0 0 10px rgba(92,184,240,.15);animation:pulse 2s ease infinite}
.badge-finished{background:rgba(92,201,176,.12);color:var(--accent);box-shadow:0 0 10px rgba(92,201,176,.15)}
.badge-failed{background:rgba(232,96,96,.12);color:var(--red);box-shadow:0 0 10px rgba(232,96,96,.15)}
.badge-pending{background:rgba(232,184,64,.12);color:var(--yellow);box-shadow:0 0 10px rgba(232,184,64,.15)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.btn{padding:9px 20px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;transition:all .25s;position:relative;overflow:hidden}
.btn::after{content:'';position:absolute;top:50%;left:50%;width:0;height:0;background:rgba(255,255,255,.15);border-radius:50%;transform:translate(-50%,-50%);transition:width .4s,height .4s}
.btn:active::after{width:200px;height:200px}
.btn:active{transform:scale(.95)}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#080c14;box-shadow:0 4px 15px rgba(92,201,176,.3)}
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
.modal-box{background:rgba(16,24,40,.95);border:1px solid var(--border);border-radius:16px;padding:28px;width:85%;max-width:900px;max-height:80vh;overflow-y:auto;font-family:Consolas,"Courier New",monospace;font-size:13px;white-space:pre-wrap;word-break:break-all;position:relative;color:var(--text);backdrop-filter:blur(20px);box-shadow:0 20px 60px rgba(0,0,0,.5),0 0 40px rgba(92,201,176,.05)}
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
        <div class="subtitle">跨平台内网部署管理平台</div>
    </div>
</div>
<div class="tabs">
    <button class="active" onclick="switchTab('dashboard',this)">总览</button>
    <button onclick="switchTab('machines',this)">机器管理</button>
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
            <div class="stat-card"><div class="num" id="stat-packages">-</div><div class="label">版本包</div></div>
        </div>
        <div class="card">
            <h2>机器列表</h2>
            <table>
                <thead><tr><th>主机名</th><th>IP</th><th>平台</th><th>状态</th><th>当前版本</th><th>最后心跳</th></tr></thead>
                <tbody id="dashboard-table"></tbody>
            </table>
        </div>
    </div>

    <!-- 机器管理 -->
    <div id="tab-machines" class="hidden">
        <div class="card">
            <h2>添加机器</h2>
            <div class="form-row">
                <label>IP<input type="text" id="add-ip" placeholder="192.168.1.10"></label>
                <label>主机名<input type="text" id="add-hostname" placeholder="PC-001"></label>
                <label>平台<select id="add-platform"><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
                <label>标签<input type="text" id="add-tag" placeholder="车间A"></label>
                <button class="btn btn-primary" onclick="addMachine()">添加</button>
            </div>
        </div>
        <div class="card">
            <h2>批量导入（CSV: ip,hostname,platform,tag）</h2>
            <div class="form-row">
                <label style="flex:1">CSV 内容<textarea id="import-csv" rows="4" style="width:100%;resize:vertical" placeholder="192.168.1.10,PC-001,windows,车间A&#10;192.168.1.11,PC-002,linux,车间B"></textarea></label>
                <button class="btn btn-primary" onclick="importMachines()">导入</button>
            </div>
        </div>
        <div class="card">
            <h2>机器列表</h2>
            <table>
                <thead><tr><th>主机名</th><th>IP</th><th>平台</th><th>标签</th><th>状态</th><th>当前版本</th><th>操作</th></tr></thead>
                <tbody id="machines-table"></tbody>
            </table>
        </div>
    </div>

    <!-- 版本包 -->
    <div id="tab-packages" class="hidden">
        <div class="card">
            <h2>上传版本包</h2>
            <div class="form-row">
                <label>平台<select id="upload-platform"><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
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
            <table>
                <thead><tr><th>版本</th><th>平台</th><th>类型</th><th>文件名</th><th>大小</th><th>MD5</th><th>上传时间</th><th>操作</th></tr></thead>
                <tbody id="packages-table"></tbody>
            </table>
        </div>
    </div>

    <!-- 部署 -->
    <div id="tab-deploy" class="hidden">
        <div class="card">
            <h2>创建部署任务</h2>
            <div class="form-row">
                <label>版本号<select id="deploy-version"><option value="">请先选择平台</option></select></label>
                <label>平台<select id="deploy-platform" onchange="loadDeployVersions();loadDeployMachines()"><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
                <button class="btn btn-primary" onclick="createDeploy()">开始部署</button>
            </div>
            <div style="margin-top:14px">
                <label class="checkbox-label"><input type="checkbox" id="deploy-select-all" onchange="toggleSelectAll()"> 全选/取消全选</label>
            </div>
            <table style="margin-top:10px">
                <thead><tr><th>选择</th><th>主机名</th><th>IP</th><th>状态</th><th>当前版本</th></tr></thead>
                <tbody id="deploy-machines-table"></tbody>
            </table>
        </div>
    </div>

    <!-- 任务记录 -->
    <div id="tab-tasks" class="hidden">
        <div class="card">
            <h2>部署任务列表</h2>
            <table>
                <thead><tr><th>任务ID</th><th>版本</th><th>平台</th><th>状态</th><th>进度</th><th>成功</th><th>失败</th><th>创建时间</th><th>操作</th></tr></thead>
                <tbody id="tasks-table"></tbody>
            </table>
        </div>
        <div id="task-detail" class="hidden">
            <div class="card">
                <h2>任务详情 <span id="detail-task-id"></span> <button id="btn-force-stop" class="btn btn-danger hidden" onclick="forceStopTask()" style="margin-left:auto;font-size:13px;padding:6px 16px">强制停止</button></h2>
                <div class="progress-bar" style="margin-bottom:14px"><div class="fill" id="detail-progress" style="width:0"></div></div>
                <div id="detail-summary" style="font-size:13px;color:var(--text2);margin-bottom:14px"></div>
                <table>
                    <thead><tr><th>机器ID</th><th>状态</th><th>实时进度</th><th>开始时间</th><th>结束时间</th><th>操作</th></tr></thead>
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

function headers() { return {"Content-Type": "application/json"}; }

async function api(path, opts = {}) {
    const resp = await fetch(path, {headers: headers(), ...opts});
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || resp.statusText); }
    return resp.json();
}

function switchTab(name, btn) {
    document.querySelectorAll("[id^='tab-']").forEach(el => el.classList.add("hidden"));
    document.getElementById("tab-" + name).classList.remove("hidden");
    document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    if (window._detailRefreshTimer) { clearTimeout(window._detailRefreshTimer); window._detailRefreshTimer = null; }
    if (name === "dashboard") loadDashboard();
    if (name === "machines") loadMachines();
    if (name === "packages") loadPackages();
    if (name === "deploy") { loadDeployVersions(); loadDeployMachines(); }
    if (name === "tasks") loadTasks();
}

async function loadDashboard() {
    try {
        const [machines, packages] = await Promise.all([api("/api/machines"), api("/api/packages")]);
        document.getElementById("stat-total").textContent = machines.length;
        document.getElementById("stat-online").textContent = machines.filter(m => m.status === "online").length;
        document.getElementById("stat-offline").textContent = machines.filter(m => m.status === "offline").length;
        document.getElementById("stat-packages").textContent = packages.length;
        const tb = document.getElementById("dashboard-table");
        if (machines.length === 0) {
            tb.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:24px">暂无机器，请在"机器管理"中添加</td></tr>';
            return;
        }
        tb.innerHTML = machines.map(m => `<tr>
            <td>${m.hostname||"-"}</td><td>${m.ip}</td><td>${m.platform}</td>
            <td><span class="badge badge-${m.status}">${m.status==="online"?"在线":"离线"}</span></td>
            <td>${m.current_version||"-"}</td><td>${m.last_heartbeat||"-"}</td>
        </tr>`).join("");
    } catch(e) { console.error("loadDashboard:", e); }
}

async function loadMachines() {
    try {
        const machines = await api("/api/machines");
        const tb = document.getElementById("machines-table");
        if (machines.length === 0) {
            tb.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text2);padding:24px">暂无机器</td></tr>';
            return;
        }
        tb.innerHTML = machines.map(m => `<tr>
            <td>${m.hostname||"-"}</td><td>${m.ip}</td><td>${m.platform}</td><td>${m.tag||"-"}</td>
            <td><span class="badge badge-${m.status}">${m.status==="online"?"在线":"离线"}</span></td>
            <td>${m.current_version||"-"}</td>
            <td><button class="btn btn-danger" onclick="deleteMachine('${m.id}')">删除</button></td>
        </tr>`).join("");
    } catch(e) { console.error("loadMachines:", e); }
}

async function addMachine() {
    const data = {ip: document.getElementById("add-ip").value, hostname: document.getElementById("add-hostname").value,
        platform: document.getElementById("add-platform").value, tag: document.getElementById("add-tag").value};
    if (!data.ip) { showToast("请输入IP", "error"); return; }
    try { await api("/api/machines", {method: "POST", body: JSON.stringify(data)}); showToast("机器已添加"); loadMachines(); } catch(e) { showToast(e.message, "error"); }
}

async function deleteMachine(id) {
    if (!confirm("确认删除该机器？")) return;
    try { await api("/api/machines/" + id, {method: "DELETE"}); showToast("已删除"); loadMachines(); } catch(e) { showToast(e.message, "error"); }
}

async function importMachines() {
    const csv = document.getElementById("import-csv").value;
    if (!csv.trim()) { showToast("请输入CSV数据", "error"); return; }
    try {
        const r = await api("/api/machines/import", {method: "POST", body: csv, headers: {"Content-Type": "text/csv"}});
        showToast(r.message); loadMachines();
    } catch(e) { showToast(e.message, "error"); }
}

async function loadPackages() {
    try {
        const pkgs = await api("/api/packages");
        const tb = document.getElementById("packages-table");
        if (pkgs.length === 0) {
            tb.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text2);padding:24px">暂无版本包</td></tr>';
            return;
        }
        tb.innerHTML = pkgs.map(p => {
            const typeLabel = p.package_type === 'incremental' ? '增量' : '全量';
            const typeColor = p.package_type === 'incremental' ? 'var(--blue)' : 'var(--accent)';
            return `<tr>
                <td>${p.version}</td><td>${p.platform}</td><td><span style="color:${typeColor}">${typeLabel}</span></td><td>${p.filename}</td>
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
    const formData = new FormData();
    formData.append("file", file);
    formData.append("platform", platform);
    formData.append("package_type", packageType);
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
    const sel = document.getElementById("deploy-version");
    sel.innerHTML = "<option value=''>加载中...</option>";
    try {
        const pkgs = await api("/api/packages");
        const filtered = pkgs.filter(p => p.platform === platform);
        if (filtered.length === 0) {
            sel.innerHTML = "<option value=''>暂无版本包，请先上传</option>";
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
        const machines = await api("/api/machines?platform=" + platform);
        const tb = document.getElementById("deploy-machines-table");
        if (machines.length === 0) {
            tb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text2);padding:24px">该平台暂无机器</td></tr>';
            return;
        }
        tb.innerHTML = machines.map(m => `<tr>
            <td><input type="checkbox" class="deploy-cb" value="${m.id}" checked></td>
            <td>${m.hostname||"-"}</td><td>${m.ip}</td>
            <td><span class="badge badge-${m.status}">${m.status==="online"?"在线":"离线"}</span></td>
            <td>${m.current_version||"-"}</td>
        </tr>`).join("");
    } catch(e) { console.error("loadDeployMachines:", e); }
}

function toggleSelectAll() {
    const checked = document.getElementById("deploy-select-all").checked;
    document.querySelectorAll(".deploy-cb").forEach(cb => cb.checked = checked);
}

async function createDeploy() {
    const version = document.getElementById("deploy-version").value;
    const platform = document.getElementById("deploy-platform").value;
    const ids = Array.from(document.querySelectorAll(".deploy-cb:checked")).map(cb => cb.value);
    if (!version) { showToast("请选择版本号", "error"); return; }
    if (!ids.length) { showToast("请选择至少一台机器", "error"); return; }
    try {
        const r = await api("/api/deploy", {method: "POST", body: JSON.stringify({version, platform, machine_ids: ids})});
        showToast("部署任务已创建");
        switchTab("tasks", document.querySelectorAll(".tabs button")[4]);
        setTimeout(() => showTaskDetail(r.task_id), 600);
    } catch(e) { showToast(e.message, "error"); }
}

async function loadTasks() {
    try {
        const tasks = await api("/api/tasks");
        const tb = document.getElementById("tasks-table");
        if (tasks.length === 0) {
            tb.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text2);padding:24px">暂无部署任务</td></tr>';
            return;
        }
        tb.innerHTML = tasks.map(t => {
            const pct = t.total > 0 ? ((t.success + t.failed) / t.total * 100).toFixed(0) : 0;
            const statusMap = {pending:"等待中",running:"部署中",finished:"已完成"};
            const badgeMap = {pending:"pending",running:"running",finished:"finished"};
            return `<tr>
                <td style="font-family:monospace">${t.id}</td><td>${t.version}</td><td>${t.platform}</td>
                <td><span class="badge badge-${badgeMap[t.status]||t.status}">${statusMap[t.status]||t.status}</span></td>
                <td><div class="progress-bar" style="width:100px"><div class="fill" style="width:${pct}%"></div></div> ${pct}%</td>
                <td style="color:var(--accent)">${t.success}</td><td style="color:var(--red)">${t.failed}</td>
                <td>${t.created_at}</td>
                <td><button class="btn btn-primary" onclick="showTaskDetail('${t.id}')">详情</button></td>
            </tr>`;
        }).join("");
    } catch(e) {
        console.error("loadTasks:", e);
        document.getElementById("tasks-table").innerHTML = '<tr><td colspan="9" style="color:var(--red)">加载失败: ' + e.message + '</td></tr>';
    }
}

async function showTaskDetail(taskId) {
    try {
        const task = await api("/api/tasks/" + taskId);
        document.getElementById("task-detail").classList.remove("hidden");
        document.getElementById("detail-task-id").textContent = "(" + taskId + ")";
        const pct = task.total > 0 ? ((task.success + task.failed) / task.total * 100).toFixed(0) : 0;
        document.getElementById("detail-progress").style.width = pct + "%";
        document.getElementById("detail-summary").textContent = `总计: ${task.total} | 成功: ${task.success} | 失败: ${task.failed} | 进度: ${pct}%`;
        const stopBtn = document.getElementById("btn-force-stop");
        if (task.status === "running" || task.status === "pending") {
            stopBtn.classList.remove("hidden");
            window._currentTaskId = taskId;
        } else {
            stopBtn.classList.add("hidden");
        }
        const tb = document.getElementById("detail-logs-table");
        window._taskLogs = task.logs || [];
        const liveProgress = task.live_progress || {};
        if (window._taskLogs.length === 0 && Object.keys(liveProgress).length === 0) {
            tb.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text2);padding:16px">暂无执行记录</td></tr>';
        } else {
            const allMachineIds = new Set([...window._taskLogs.map(l => l.machine_id), ...Object.keys(liveProgress)]);
            const logMap = {};
            window._taskLogs.forEach((l, i) => { logMap[l.machine_id] = {...l, _index: i}; });
            let rows = "";
            allMachineIds.forEach(mid => {
                const l = logMap[mid];
                const lp = liveProgress[mid] || {};
                const status = l ? l.status : (lp.percent >= 0 ? "running" : "pending");
                const statusLabel = {success:"成功",failed:"失败",running:"执行中",pending:"等待中"}[status] || status;
                const phase = lp.phase || "";
                const pct = lp.percent || 0;
                const detail = lp.detail || "";
                let progressHtml = "";
                if (status === "running" && phase) {
                    progressHtml = `<div style="margin-top:4px"><div class="progress-bar" style="width:120px"><div class="fill" style="width:${pct}%"></div></div> <span style="font-size:11px;color:var(--text2)">${phase} ${pct}%</span>${detail ? '<br><span style="font-size:11px;color:var(--text2)">' + detail + '</span>' : ''}</div>`;
                }
                const logBtn = l ? `<button class="btn btn-primary" onclick="showLog(${l._index})">查看日志</button>` : "-";
                rows += `<tr>
                    <td style="font-family:monospace">${mid}</td>
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

function showLog(index) {
    const log = (window._taskLogs && window._taskLogs[index]) ? window._taskLogs[index].log || "(无日志)" : "(无日志)";
    document.getElementById("log-modal-content").textContent = log;
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
refresh();
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

if __name__ == "__main__":
    init_db()
    migrate_db()
    recover_packages()

    t = threading.Thread(target=heartbeat_checker, daemon=True)
    t.start()

    host = config.get("host", "0.0.0.0")
    port = config.get("port", 61234)
    logger.info("=" * 50)
    logger.info("部署服务端启动")
    logger.info("监听地址: http://%s:%d", host, port)
    logger.info("数据库: %s", DATABASE)
    logger.info("包目录: %s", PACKAGES_DIR)
    logger.info("管理应用: %s", ", ".join(a.get("name","") for a in APPS))
    logger.info("=" * 50)
    app.run(host=host, port=port, debug=False, threaded=True)
