#!/usr/bin/env python3
"""
将 appdeploy 目录打成发布用 zip（不含运行日志、数据库、构建缓存等）。
输出: dist/appdeploy-<时间>.zip
"""
from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# 目录：任意路径片段命中即跳过整棵子树
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build_temp",
    }
)

# 按文件名排除（任意层级）
SKIP_FILES = frozenset(
    {
        "deploy.db",
        "python-embed.zip",
        "get-pip.py",
    }
)

SKIP_SUFFIXES = (".log", ".pyc")


def skip_path(rel: Path) -> bool:
    if any(part in SKIP_DIR_NAMES for part in rel.parts):
        return True
    name = rel.name
    if name in SKIP_FILES:
        return True
    if name.endswith(SKIP_SUFFIXES):
        return True
    return False


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_zip = DIST / f"appdeploy-{stamp}.zip"

    count = 0
    with zipfile.ZipFile(
        out_zip, "w", compression=zipfile.ZIP_DEFLATED
    ) as zf:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            # 就地修剪 walk，跳过无需进入的目录
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for fn in filenames:
                full = Path(dirpath) / fn
                try:
                    rel = full.relative_to(ROOT)
                except ValueError:
                    continue
                if skip_path(rel):
                    continue
                zf.write(full, arcname=str(Path("appdeploy") / rel))
                count += 1

    print(f"已生成: {out_zip}")
    print(f"共加入 {count} 个文件")


if __name__ == "__main__":
    main()
