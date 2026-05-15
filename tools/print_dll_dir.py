"""Emit a directory containing vcruntime140.dll + vcruntime140_1.dll for PyInstaller --add-binary.

Official Windows builds often place these next to python.exe; older layouts use DLLs/.
Conda/Miniconda typically uses Library/bin. venv inherits from base_prefix.
Used by build.bat.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _unique_dirs(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        try:
            r = p.resolve()
        except OSError:
            continue
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def candidate_dirs() -> list[Path]:
    exe_dir = Path(sys.executable).resolve().parent
    roots: list[Path] = [exe_dir, exe_dir / "DLLs"]
    base = Path(getattr(sys, "base_prefix", sys.prefix)).resolve()
    if base != exe_dir:
        roots.extend(
            [
                base,
                base / "DLLs",
                base / "Library" / "bin",
            ]
        )
    return _unique_dirs(roots)


def find_vc_runtime_dir() -> Path | None:
    for d in candidate_dirs():
        if (d / "vcruntime140.dll").is_file() and (d / "vcruntime140_1.dll").is_file():
            return d
    return None


if __name__ == "__main__":
    found = find_vc_runtime_dir()
    if found:
        print(found, end="")
    else:
        sys.exit(1)
