from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
CONFIG_PATH = os.path.join(STATE_DIR, "device_config.json")
STATUS_PATH = os.path.join(STATE_DIR, "cloud_update_status.json")
BACKUP_ROOT = os.path.join(HERE, "_cloud_update_backups")
DEFAULT_BASE_URL = "https://feedback-assistant-alpha.vercel.app"

BACKGROUND_SKIP = {
    "wowkids_agent_supervisor.py",
    "repair_windows_agent.py",
    "pair_windows_agent.py",
    "PAIR_WINDOWS_AGENT.bat",
    "REPAIR_WINDOWS_AGENT.bat",
    "USE_STABLE_VERSION.bat",
    "RETURN_TO_LATEST.bat",
    "vercel_update.py",
}


def _read_config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    token = str(data.get("deviceToken") or "").strip()
    base_url = str(data.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")
    if not token.startswith("wk_"):
        raise RuntimeError("saved WOWKIDS pairing is missing or invalid")
    return token, base_url


def _request(url, token, accept, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "x-wowkids-device-token": token,
            "User-Agent": "WOWKIDS-Rating-Assistant-Updater/3.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        raise RuntimeError(
            "Feedback Assistant update service returned HTTP {}: {}".format(
                exc.code, detail
            )
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Feedback Assistant update service is unavailable: {}".format(
                exc.reason
            )
        )


def _manifest(token, base_url, ref):
    query = urllib.parse.urlencode({"ref": ref})
    raw = _request(
        base_url + "/api/wowkids-update-manifest?" + query,
        token,
        "application/json",
        timeout=30,
    )
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("invalid cloud update manifest: {}".format(exc))
    if not data.get("ok") or not data.get("sha"):
        raise RuntimeError("cloud update manifest is incomplete")
    return data


def _file_bytes(token, base_url, ref, path):
    query = urllib.parse.urlencode({"ref": ref, "path": path})
    data = _request(
        base_url + "/api/wowkids-update-file?" + query,
        token,
        "application/octet-stream",
        timeout=30,
    )
    if len(data) > 220_000:
        raise RuntimeError("cloud update file is unexpectedly large: {}".format(path))
    return data


def _load_status():
    try:
        with open(STATUS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _same_bytes(path, payload):
    try:
        with open(path, "rb") as fh:
            return fh.read() == payload
    except Exception:
        return False


def _skip_path(path, background, ref):
    if background and path in BACKGROUND_SKIP:
        return True
    if ref != "main" and path in BACKGROUND_SKIP:
        return True
    return False


def _prune_backups(keep=4):
    try:
        entries = [
            os.path.join(BACKUP_ROOT, name)
            for name in os.listdir(BACKUP_ROOT)
            if os.path.isdir(os.path.join(BACKUP_ROOT, name))
        ]
        entries.sort(key=os.path.getmtime, reverse=True)
        for path in entries[keep:]:
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _install_file(path, payload, backup_root):
    target = os.path.join(HERE, *path.split("/"))
    if _same_bytes(target, payload):
        return False

    if os.path.isfile(target):
        backup_path = os.path.join(backup_root, *path.split("/"))
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        shutil.copy2(target, backup_path)

    os.makedirs(os.path.dirname(target) or HERE, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".wowkids_update_",
        dir=os.path.dirname(target) or HERE,
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, target)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--ref",
        default="main",
        choices=("main", "stable-current", "stable-working-2026-09-22"),
    )
    args = parser.parse_args(argv)

    token, base_url = _read_config()
    manifest = _manifest(token, base_url, args.ref)
    version = str(manifest.get("sha") or "")
    files = list(manifest.get("files") or [])

    previous = _load_status()
    if (
        args.ref == "main"
        and previous.get("requestedRef") == args.ref
        and previous.get("sourceVersion") == version
    ):
        if not args.quiet:
            print("Already up to date.")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(backup, exist_ok=True)

    changed = []
    for item in files:
        path = str(item.get("path") or "")
        if not path or _skip_path(path, args.background, args.ref):
            continue
        payload = _file_bytes(token, base_url, args.ref, path)
        if _install_file(path, payload, backup):
            changed.append(path)

    if not changed:
        shutil.rmtree(backup, ignore_errors=True)

    os.makedirs(STATE_DIR, exist_ok=True)
    status = {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "feedback-assistant",
        "sourceVersion": version,
        "requestedRef": args.ref,
        "changedFiles": changed,
        "background": bool(args.background),
    }
    with open(STATUS_PATH, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2)

    _prune_backups()

    if not args.quiet:
        if changed:
            print(
                "Feedback Assistant update installed ({} file{}).".format(
                    len(changed), "" if len(changed) == 1 else "s"
                )
            )
        else:
            print("Already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
