#!/usr/bin/env python3
"""
Build deployment .zip with forward-slash entry names (PKZIP / ApPNOTE).
PowerShell Compress-Archive uses backslashes, so some tools show 'linux\\file' as one name.

Sets ZipInfo.external_attr so Linux unzip preserves executable bits for shell scripts and
bundled linux/python/bin/* (otherwise install_server.sh thinks Python is missing).
"""
import stat
import sys
import zipfile
from pathlib import Path


def _unix_mode_for_member(arcname_posix: str) -> int:
    """Permission bits for Unix external_attr (applied << 16)."""
    n = arcname_posix.replace("\\", "/").lower()
    base = arcname_posix.rsplit("/", 1)[-1].lower()
    if base.endswith(".sh"):
        return stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    if "/python/bin/" in n:
        if base.startswith("python"):
            return stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    return stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: zip_exe_bundle.py <stage_dir> <out.zip>", file=sys.stderr)
        return 2
    stage = Path(sys.argv[1]).resolve()
    out_zip = Path(sys.argv[2]).resolve()
    if not stage.is_dir():
        print(f"[ERROR] not a directory: {stage}", file=sys.stderr)
        return 1
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in stage.rglob("*"):
            if path.is_file():
                arcname = path.relative_to(stage).as_posix()
                mode = _unix_mode_for_member(arcname)
                data = path.read_bytes()
                zi = zipfile.ZipInfo.from_file(path, arcname)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = mode << 16
                zf.writestr(zi, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
