from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
CONFIG_PATH = os.path.join(STATE_DIR, "device_config.json")
BACKUP_ROOT = os.path.join(HERE, "_cloud_update_backups")
DEFAULT_BASE_URL = "https://feedback-assistant-alpha.vercel.app"
MAX_ARCHIVE_BYTES = 4_200_000

SKIP_ALWAYS = {
    "REPAIR_WINDOWS_AGENT.bat",
}
SKIP_PREFIXES = (
    ".git/",
    "reports/",
    "logs/",
    "libs/",
    "__pycache__/",
    ".venv/",
    "venv/",
    "_cloud_update_backups/",
)
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


def _download_archive(token, base_url):
    url = base_url + "/api/wowkids-update"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/zip",
            "x-wowkids-device-token": token,
            "User-Agent": "WOWKIDS-Rating-Assistant-Updater/2.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise RuntimeError("cloud update archive is unexpectedly large")
            if len(data) < 1000 or not data.startswith(b"PK"):
                raise RuntimeError("cloud update did not return a valid ZIP archive")
            source = response.headers.get("X-WOWKIDS-Update-Channel") or "main"
            return data, source
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


def _archive_files(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [
            name for name in archive.namelist()
            if name and not name.endswith("/")
        ]
        roots = {name.split("/", 1)[0] for name in names if "/" in name}
        if len(roots) != 1:
            raise RuntimeError("update archive has an unexpected layout")
        root = next(iter(roots)) + "/"

        files = {}
        for name in names:
            if not name.startswith(root):
                continue
            rel = name[len(root):].replace("\\", "/")
            if not rel or rel.startswith("../") or "/../" in rel:
                continue
            files[rel] = archive.read(name)

    required = {
        "wowkids_cloud_agent.py",
        "humanlike_engine.py",
        "rating_engine.py",
        "class_controller.py",
        "wkcommon.py",
    }
    missing = sorted(required - set(files))
    if missing:
        raise RuntimeError(
            "update archive is missing required files: {}".format(
                ", ".join(missing)
            )
        )
    return files


def _should_skip(rel, background=False):
    rel = rel.replace("\\", "/")
    if rel in SKIP_ALWAYS:
        return True
    if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
        return True
    if background and rel in BACKGROUND_SKIP:
        return True
    return False


def _same_bytes(path, payload):
    try:
        with open(path, "rb") as fh:
            return fh.read() == payload
    except Exception:
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


def _apply(files, background=False):
    changed = []
    for rel, payload in files.items():
        if _should_skip(rel, background=background):
            continue
        target = os.path.join(HERE, *rel.split("/"))
        if _same_bytes(target, payload):
            continue
        changed.append((rel, target, payload))

    if not changed:
        return []

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(backup, exist_ok=True)

    for rel, target, _payload in changed:
        if os.path.isfile(target):
            backup_path = os.path.join(backup, *rel.split("/"))
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            shutil.copy2(target, backup_path)

    for rel, target, payload in changed:
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

    with open(
        os.path.join(backup, "_changed_files.json"),
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(
            {
                "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "files": [rel for rel, _target, _payload in changed],
            },
            fh,
            indent=2,
        )

    _prune_backups()
    return [rel for rel, _target, _payload in changed]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--background",
        action="store_true",
        help="Update only agent runtime files safe to replace under the supervisor.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    token, base_url = _read_config()
    data, source = _download_archive(token, base_url)
    archive_hash = hashlib.sha256(data).hexdigest()[:12]
    files = _archive_files(data)
    changed = _apply(files, background=args.background)

    state = {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "archiveHash": archive_hash,
        "changedFiles": changed,
        "background": bool(args.background),
    }
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(
        os.path.join(STATE_DIR, "cloud_update_status.json"),
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(state, fh, indent=2)

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
