#!/usr/bin/env python3
"""
Run once online before packaging: download Linux manylinux wheels into
<LINUX_PACK_ROOT>/vendor/wheels (default LINUX_PACK_ROOT=linux).

Why not plain ``pip download`` on Windows? Wheels are platform-specific. This script
calls ``pip download`` with ``--platform``, ``--python-version``, ``--abi``, and
``--only-binary=:all:`` so artifacts match the *target* Linux (x86_64 manylinux by
default; use LINUX_PACK_ROOT=linux_arm + LINUX_WHEEL_PLATFORM for AArch64).

Requirements live under <LINUX_PACK_ROOT>/requirements-linux-server.txt.

Env:
  LINUX_PACK_ROOT             linux (default) or linux_arm (paths parallel to linux/)
  LINUX_WHEEL_PLATFORM        manylinux2014_x86_64 or manylinux2014_aarch64 (auto if unset)
  LINUX_WHEEL_PY=312   LINUX_WHEEL_ABI=cp312
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pack_paths() -> tuple[Path, Path]:
    root_name = (os.environ.get("LINUX_PACK_ROOT", "linux") or "linux").strip() or "linux"
    wheel_dir = ROOT / root_name / "vendor" / "wheels"
    req = ROOT / root_name / "requirements-linux-server.txt"
    return wheel_dir, req


def _default_platform(pack_root_name: str) -> str:
    explicit = (os.environ.get("LINUX_WHEEL_PLATFORM") or "").strip()
    if explicit:
        return explicit
    if pack_root_name == "linux_arm":
        return "manylinux2014_aarch64"
    return "manylinux2014_x86_64"


def _pip_download(cmd):
    print("[..]", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    wheel_dir, req = _pack_paths()
    if not req.is_file():
        print(f"[ERROR] Missing {req}", file=sys.stderr)
        return 1
    pack_root_name = req.parent.name
    plat = _default_platform(pack_root_name)
    print(f"[..] LINUX_PACK_ROOT={pack_root_name}  platform={plat}", flush=True)

    wheel_dir.mkdir(parents=True, exist_ok=True)
    for p in list(wheel_dir.glob("*.whl")) + list(wheel_dir.glob("*.tar.gz")):
        try:
            p.unlink()
        except OSError:
            pass

    py_tag = os.environ.get("LINUX_WHEEL_PY", "312")
    abi = os.environ.get("LINUX_WHEEL_ABI", f"cp{py_tag}")

    # pip 23+ requires --only-binary=:all: when using --platform with transitive deps
    tries = [
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "-r",
            str(req),
            "-d",
            str(wheel_dir),
            "--platform",
            plat,
            "--python-version",
            py_tag,
            "--implementation",
            "cp",
            "--abi",
            abi,
            "--only-binary",
            ":all:",
        ],
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "-r",
            str(req),
            "-d",
            str(wheel_dir),
            "--platform",
            plat,
            "--python-version",
            py_tag,
            "--implementation",
            "cp",
            "--only-binary",
            ":all:",
        ],
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "-r",
            str(req),
            "-d",
            str(wheel_dir),
            "--platform",
            plat,
            "--only-binary",
            ":all:",
        ],
    ]

    rc = 1
    for cmd in tries:
        rc = _pip_download(cmd)
        if rc == 0:
            break
        print("[..] retrying with looser constraints ...", file=sys.stderr)

    if rc != 0 and sys.platform.startswith("linux"):
        print("[..] native Linux download (no cross-platform flags) ...", file=sys.stderr)
        rc = _pip_download(
            [sys.executable, "-m", "pip", "download", "-r", str(req), "-d", str(wheel_dir)]
        )

    if rc != 0:
        print(
            "[ERROR] pip download failed. On Windows, install pip 23+ or run this script inside WSL/Ubuntu.",
            file=sys.stderr,
        )
        return rc

    n = len(list(wheel_dir.glob("*.whl")))
    if n == 0:
        print("[WARN] No .whl files in wheelhouse.", file=sys.stderr)
        return 1
    print(f"[OK] {n} wheel(s) in {wheel_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
