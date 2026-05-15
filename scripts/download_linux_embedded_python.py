#!/usr/bin/env python3
"""
Download astral-sh python-build-standalone (glibc) and extract to <LINUX_PACK_ROOT>/python/.

Default arch is x86_64 -> linux/python. For ARM64 (aarch64) use CPYTHON_LINUX_ARCH and
LINUX_PACK_ROOT=linux_arm to fill linux_arm/python/ (parallel to linux/).

Env:
  LINUX_PACK_ROOT             Subdir under project (default: linux). Use linux_arm for AArch64 pack.
  CPYTHON_LINUX_ARCH          x86_64 (default) or aarch64 (aka arm64)
  CPYTHON_LINUX_TARBALL_URL   Override download URL (full HTTPS URL)
  LINUX_PYTHON_STRIPPED=1     Use *_stripped.tar.gz (smaller)
  GITHUB_RELEASE_MIRROR       Extra mirror prefixes, comma-separated
  CPYTHON_SKIP_MIRRORS=1      Only use the primary URL
  CPYTHON_MIRRORS_FIRST=1     Try ghproxy mirrors before github.com
  CPYTHON_DOWNLOAD_TIMEOUT    Seconds per request (default 300)
  CPYTHON_DOWNLOAD_RETRIES    Retries per URL (default 3)
"""
from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Release tag must match download_linux_offline_deps.py (cp312)
_BUILD_TAG = "20260414"
_PY_VER = "3.12.13+20260414"


def _pack_root() -> str:
    r = (os.environ.get("LINUX_PACK_ROOT", "linux") or "linux").strip()
    return r or "linux"


def _norm_arch() -> str:
    a = (os.environ.get("CPYTHON_LINUX_ARCH", "x86_64") or "x86_64").strip().lower()
    if a in ("aarch64", "arm64"):
        return "aarch64"
    if a in ("x86_64", "amd64", "x64"):
        return "x86_64"
    print(
        f"[ERROR] CPYTHON_LINUX_ARCH must be x86_64 or aarch64 (got {a!r})",
        file=sys.stderr,
    )
    raise SystemExit(1)


def default_tarball_url(arch: str, stripped: bool) -> str:
    triplet = f"{arch}-unknown-linux-gnu"
    suf = "_stripped" if stripped else ""
    name = f"cpython-{_PY_VER}-{triplet}-install_only{suf}.tar.gz"
    return (
        f"https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{_BUILD_TAG}/{name}"
    )

# Third-party mirrors (prefix replaces leading https://github.com). Use CPYTHON_MIRRORS_FIRST=1
# to try mirrors before github.com. Default is direct GitHub first to avoid long proxy hangs.
# (gitclone.com/github.com was removed: release asset downloads are often broken/500.)
DEFAULT_MIRROR_PREFIXES = (
    "https://mirror.ghproxy.com/https://github.com",
    "https://ghproxy.net/https://github.com",
    "https://gh-proxy.com/https://github.com",
    "https://ghfast.top/https://github.com",
    "https://github.moeyy.xyz/https://github.com",
    "https://gh.api.99988866.xyz/https://github.com",
)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def iter_download_urls(primary: str) -> list[str]:
    """Default: github.com first, then mirrors. CPYTHON_SKIP_MIRRORS=1 -> primary only."""
    if _truthy("CPYTHON_SKIP_MIRRORS"):
        return [primary]

    env_mirrors = os.environ.get("GITHUB_RELEASE_MIRROR", "").strip()
    prefixes: list[str] = []
    if env_mirrors:
        prefixes.extend(p.strip() for p in env_mirrors.split(",") if p.strip())
    prefixes.extend(DEFAULT_MIRROR_PREFIXES)

    mirror_urls: list[str] = []
    if primary.startswith("https://github.com"):
        for pref in prefixes:
            mirror_urls.append(primary.replace("https://github.com", pref.rstrip("/"), 1))

    mirrors_first = _truthy("CPYTHON_MIRRORS_FIRST")
    ghf = os.environ.get("CPYTHON_GITHUB_FIRST", "").strip().lower()
    if ghf == "0":
        mirrors_first = True
    elif ghf in ("1", "true", "yes"):
        mirrors_first = False

    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    if primary.startswith("https://github.com"):
        if mirrors_first:
            for mu in mirror_urls:
                add(mu)
            add(primary)
        else:
            add(primary)
            for mu in mirror_urls:
                add(mu)
    else:
        add(primary)
    return out


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }


def download_file_urllib(url: str, dest: Path) -> None:
    timeout = int(os.environ.get("CPYTHON_DOWNLOAD_TIMEOUT", "300"))
    req = urllib.request.Request(url, headers=_request_headers())
    chunk_sz = 256 * 1024
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_milestone = -1
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        nread = 0
        while True:
            buf = r.read(chunk_sz)
            if not buf:
                break
            f.write(buf)
            nread += len(buf)
            milestone = nread // (10 * 1024 * 1024)
            if milestone > last_milestone:
                last_milestone = milestone
                print(f"    [..] {nread / (1024 * 1024):.0f} MiB...", flush=True)


def download_file_curl(url: str, dest: Path) -> None:
    """Windows 10+ ships curl; sometimes works when urllib hangs on TLS."""
    exe = shutil.which("curl")
    if not exe:
        raise FileNotFoundError("curl not in PATH")
    dest.parent.mkdir(parents=True, exist_ok=True)
    timeout = int(os.environ.get("CPYTHON_DOWNLOAD_TIMEOUT", "300"))
    cmd = [
        exe,
        "-fsSL",
        "--connect-timeout",
        "30",
        "--max-time",
        str(timeout),
        "-L",
        "-o",
        str(dest),
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"curl exit {r.returncode}")


def try_download(urls: list[str], tmp: Path) -> str:
    retries = max(1, int(os.environ.get("CPYTHON_DOWNLOAD_RETRIES", "3")))
    last_err: Exception | None = None
    for url in urls:
        for attempt in range(1, retries + 1):
            print(
                f"[..] Trying ({attempt}/{retries}): {url[:100]}{'...' if len(url) > 100 else ''}",
                flush=True,
            )
            try:
                download_file_urllib(url, tmp)
                size_mb = tmp.stat().st_size / (1024 * 1024)
                print(f"[OK] Downloaded {size_mb:.1f} MiB", flush=True)
                return url
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                OSError,
            ) as e:
                last_err = e
                if isinstance(e, urllib.error.HTTPError):
                    print(f"    [WARN] HTTP {e.code}: {e.reason!r} ({url[:80]}...)", file=sys.stderr)
                else:
                    print(f"    [WARN] {e}", file=sys.stderr)
                try:
                    print("    [..] retry this URL with curl...", flush=True)
                    download_file_curl(url, tmp)
                    size_mb = tmp.stat().st_size / (1024 * 1024)
                    print(f"[OK] Downloaded {size_mb:.1f} MiB (curl)", flush=True)
                    return url
                except Exception as ce:
                    print(f"    [WARN] curl: {ce}", file=sys.stderr)
                if attempt < retries:
                    time.sleep(2 * attempt)
        print(f"    [..] Next mirror/URL...", file=sys.stderr)
    if last_err:
        raise last_err
    raise RuntimeError("no download URLs")


def dedupe_install_only_python_tree(dest: Path) -> None:
    """
    python-build-standalone tarballs use symlinks (e.g. libpython3.12.so -> .so.1.0,
    python3 -> python3.12). Extracting on Windows turns those into full duplicate files.

    Remove byte-identical duplicates and keep the canonical files only:
      lib/libpython3.12.so.1.0  (SONAME; required at runtime)
      bin/python3.12

    Install scripts (install_server.sh / install_linux.sh) recreate symlinks on Linux.
    Do not remove libpython3.so (small linker script, not a duplicate of the SONAME).
    """
    lib = dest / "lib"
    soname = lib / "libpython3.12.so.1.0"
    dev = lib / "libpython3.12.so"
    if (
        soname.is_file()
        and dev.is_file()
        and not dev.is_symlink()
        and soname.stat().st_size == dev.stat().st_size
        and filecmp.cmp(soname, dev, shallow=False)
    ):
        print(
            f"[..] Dedup: remove duplicate {dev.relative_to(dest)} "
            f"(identical to {soname.name}; symlink recreated on Linux install)",
            flush=True,
        )
        dev.unlink()

    bin_dir = dest / "bin"
    main = bin_dir / "python3.12"
    if not main.is_file():
        return
    for name in ("python3", "python"):
        alt = bin_dir / name
        if (
            alt.is_file()
            and not alt.is_symlink()
            and main.stat().st_size == alt.stat().st_size
            and filecmp.cmp(main, alt, shallow=False)
        ):
            print(
                f"[..] Dedup: remove duplicate bin/{name} "
                f"(identical to python3.12; symlink recreated on Linux install)",
                flush=True,
            )
            alt.unlink()


def main() -> int:
    pack_root = _pack_root()
    arch = _norm_arch()
    dest = ROOT / pack_root / "python"
    use_strip = os.environ.get("LINUX_PYTHON_STRIPPED", "").strip() in ("1", "true", "yes")
    primary = os.environ.get("CPYTHON_LINUX_TARBALL_URL", "").strip()
    if not primary:
        primary = default_tarball_url(arch, use_strip)

    urls = iter_download_urls(primary)
    tmp = ROOT / f"build_temp_cpython_{arch}.tar.gz"
    staging = ROOT / f"build_temp_cpython_extract_{arch}"

    print(
        f"[..] Pack: {pack_root}/python  arch={arch}  ({len(urls)} URL(s))",
        flush=True,
    )
    print("[..] Candidate URLs: %d total (default: GitHub first unless CPYTHON_MIRRORS_FIRST=1)" % len(urls), flush=True)
    try:
        used = try_download(urls, tmp)
        print(f"[OK] Source: {used}")
    except Exception as e:
        print(f"[ERROR] All downloads failed: {e}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return 1

    if not tmp.is_file() or tmp.stat().st_size < 1_000_000:
        print("[ERROR] Downloaded file too small or missing", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return 1

    if dest.exists():
        print(f"[..] Removing old {dest}")
        shutil.rmtree(dest)

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    print("[..] Extracting tarball...")
    try:
        with tarfile.open(tmp, "r:*") as tf:
            tf.extractall(staging)
    except Exception as e:
        print(f"[ERROR] Extract failed: {e}", file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        tmp.unlink(missing_ok=True)
        return 1
    tmp.unlink(missing_ok=True)

    children = [p for p in staging.iterdir() if p.name not in ("._", ".DS_Store")]
    if len(children) != 1 or not children[0].is_dir():
        print(f"[ERROR] Unexpected tarball layout under {staging}: {children}", file=sys.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    shutil.move(str(children[0]), str(dest))
    shutil.rmtree(staging, ignore_errors=True)

    dedupe_install_only_python_tree(dest)

    bin_dir = dest / "bin"
    ok = False
    if bin_dir.is_dir():
        for name in ("python3", "python3.12"):
            p = bin_dir / name
            if p.is_file():
                ok = True
                print(f"[OK] Found {p.relative_to(ROOT)}")
                break
    if not ok:
        print(f"[WARN] No python3 in {bin_dir}; listing:", list(bin_dir.glob("*")) if bin_dir.is_dir() else "missing")
    print(f"[OK] Portable Linux Python -> {dest}")
    print("     Target install_server.sh / install_linux.sh will use this first.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dedupe-existing":
        for sub in ("linux", "linux_arm"):
            p = ROOT / sub / "python"
            if p.is_dir():
                print(f"[..] Dedupe existing {sub}/python", flush=True)
                dedupe_install_only_python_tree(p)
            else:
                print(f"[..] Skip (missing): {p.relative_to(ROOT)}", flush=True)
        raise SystemExit(0)
    raise SystemExit(main())
