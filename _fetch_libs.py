"""Vendor the third-party libraries this tool needs.

Downloads wheels straight from PyPI and unpacks them into ``libs/`` so the
tool does not depend on a working ``pip`` or on administrator rights.

Run once, or again any time ``libs/`` looks broken. Nothing here touches the
mini program: it is pure file work.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIBS = os.path.join(HERE, "libs")
WHEEL_CACHE = os.path.join(HERE, "_wheels")

INTERPRETER_TAG = "cp{}{}".format(sys.version_info.major, sys.version_info.minor)
ARCH = "win_amd64" if sys.maxsize > 2 ** 32 else "win32"

# name -> exact version to use, or None for "latest compatible".
# pywinauto 0.6.9 is the last release and is known-good with the comtypes 1.2
# series; newer comtypes releases changed their API, so that one is pinned.
PACKAGES = {
    "comtypes": "1.2.1",
    "pywinauto": None,
    "six": None,
    "pywin32": None,
    "pillow": None,
}

PURE_WHEEL_SUFFIXES = ("-py3-none-any.whl", "-py2.py3-none-any.whl")
# Packages whose wheels are built per interpreter and architecture.
BINARY_PACKAGES = ("pywin32", "pillow")


def ssl_context() -> ssl.SSLContext:
    """TLS context that also works through the bundled sandbox proxy."""
    try:
        import pip._vendor.certifi as certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_json(url: str, ctx: ssl.SSLContext) -> dict:
    with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
        return json.load(resp)


def pick_wheel(name: str, files: list) -> dict:
    wheels = [f for f in files if f.get("filename", "").endswith(".whl")]
    if name in BINARY_PACKAGES:
        candidates = [
            f
            for f in wheels
            if INTERPRETER_TAG in f["filename"] and ARCH in f["filename"]
        ]
    else:
        candidates = [
            f for f in wheels if f["filename"].endswith(PURE_WHEEL_SUFFIXES)
        ]
    if not candidates:
        raise SystemExit("No compatible wheel found for {}".format(name))
    return candidates[0]


def download(name: str, ctx: ssl.SSLContext) -> str:
    data = fetch_json("https://pypi.org/pypi/{}/json".format(name), ctx)
    version = PACKAGES.get(name) or data["info"]["version"]
    files = data["releases"].get(version)
    if not files:
        raise SystemExit("Version {} of {} is not on PyPI".format(version, name))
    wheel = pick_wheel(name, files)
    os.makedirs(WHEEL_CACHE, exist_ok=True)
    target = os.path.join(WHEEL_CACHE, wheel["filename"])
    if os.path.exists(target):
        print("  cached    {} {}".format(name, version))
    else:
        print("  download  {} {}".format(name, version))
        with urllib.request.urlopen(wheel["url"], timeout=180, context=ctx) as resp:
            blob = resp.read()
        with open(target, "wb") as fh:
            fh.write(blob)
    return target


def unpack(wheel_path: str) -> None:
    os.makedirs(LIBS, exist_ok=True)
    with zipfile.ZipFile(wheel_path) as zf:
        zf.extractall(LIBS)


def verify() -> None:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import wkcommon

    wkcommon.bootstrap_libs()

    import comtypes  # noqa: F401
    import pywinauto  # noqa: F401
    import win32api  # noqa: F401

    print("  pywinauto {} + comtypes {} + pywin32 ready".format(
        pywinauto.__version__, comtypes.__version__))


def main() -> int:
    ctx = ssl_context()
    print("Preparing libraries in {}".format(LIBS))
    print("Python {} ({})".format(sys.version.split()[0], ARCH))
    for name in PACKAGES:
        unpack(download(name, ctx))
    verify()
    print("Libraries ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())