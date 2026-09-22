from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
CONFIG_PATH = os.path.join(STATE_DIR, "device_config.json")
STATUS_PATH = os.path.join(STATE_DIR, "cloud_update_status.json")
BACKUP_ROOT = os.path.join(HERE, "_cloud_update_backups")
DEFAULT_BASE_URL = "https://feedback-assistant-alpha.vercel.app"
MAX_ARCHIVE_BYTES = 1_600_000

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


def _urllib_once(url, token, timeout=60):
    headers = {
        "Accept": "application/zip",
        "x-wowkids-device-token": token,
        "User-Agent": "WOWKIDS-Rating-Assistant-Updater/4.0",
        "Connection": "close",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")

    context = ssl.create_default_context()
    try:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    except Exception:
        pass

    with urllib.request.urlopen(
        req,
        timeout=timeout,
        context=context,
    ) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise RuntimeError("cloud update archive is unexpectedly large")
        return data


def _curl_once(url, token, timeout=90):
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl.exe is not available")

    fd, cfg_path = tempfile.mkstemp(
        prefix="wowkids_update_",
        suffix=".curlcfg",
    )
    os.close(fd)
    fd, out_path = tempfile.mkstemp(
        prefix="wowkids_update_",
        suffix=".zip",
    )
    os.close(fd)

    try:
        with open(cfg_path, "w", encoding="utf-8") as fh:
            fh.write('url = "{}"\n'.format(url.replace('"', "")))
            fh.write('header = "Accept: application/zip"\n')
            fh.write(
                'header = "x-wowkids-device-token: {}"\n'.format(
                    token.replace('"', "")
                )
            )
            fh.write(
                'header = "User-Agent: WOWKIDS-Rating-Assistant-Updater/4.0"\n'
            )
            fh.write('location\n')
            fh.write('fail\n')
            fh.write('silent\n')
            fh.write('show-error\n')
            fh.write('http1.1\n')
            fh.write('connect-timeout = 20\n')
            fh.write('max-time = {}\n'.format(int(timeout)))
            fh.write('retry = 3\n')
            fh.write('retry-all-errors\n')
            fh.write('output = "{}"\n'.format(out_path.replace("\\", "/")))

        result = subprocess.run(
            [curl, "--config", cfg_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout + 15,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "curl updater failed: {}".format(
                    (result.stderr or "").strip()[:300]
                )
            )

        with open(out_path, "rb") as fh:
            data = fh.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise RuntimeError("cloud update archive is unexpectedly large")
        return data
    finally:
        for path in (cfg_path, out_path):
            try:
                os.remove(path)
            except Exception:
                pass


def _download_archive(token, base_url, ref):
    query = urllib.parse.urlencode({"ref": ref})
    url = base_url + "/api/wowkids-update?" + query

    errors = []
    for attempt in range(5):
        try:
            data = _urllib_once(url, token)
            if len(data) < 1000 or not data.startswith(b"PK"):
                raise RuntimeError(
                    "Feedback Assistant returned an invalid update archive"
                )
            return data, "urllib"
        except Exception as exc:
            errors.append("urllib {}: {}".format(attempt + 1, exc))
            if attempt < 4:
                time.sleep(1.0 + attempt * 1.25)

    # Windows sometimes tears down Python/OpenSSL handshakes while the normal
    # agent HTTPS traffic still works. curl.exe gives us a separate TLS stack
    # and HTTP/1.1 fallback without exposing the token on the command line.
    try:
        data = _curl_once(url, token)
        if len(data) < 1000 or not data.startswith(b"PK"):
            raise RuntimeError(
                "curl received an invalid update archive"
            )
        return data, "curl"
    except Exception as exc:
        errors.append("curl: {}".format(exc))

    raise RuntimeError(
        "all Feedback Assistant update transports failed: {}".format(
            " | ".join(errors[-3:])
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
            if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
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


def _should_skip(rel, background=False, ref="main"):
    if background and rel in BACKGROUND_SKIP:
        return True
    if ref != "main" and rel in BACKGROUND_SKIP:
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


def _apply(files, background=False, ref="main"):
    changed = []
    for rel, payload in files.items():
        if _should_skip(rel, background=background, ref=ref):
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
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--ref",
        default="main",
        choices=("main", "stable-current", "stable-working-2026-09-22"),
    )
    args = parser.parse_args(argv)

    token, base_url = _read_config()
    data, transport = _download_archive(token, base_url, args.ref)
    files = _archive_files(data)
    changed = _apply(
        files,
        background=args.background,
        ref=args.ref,
    )

    os.makedirs(STATE_DIR, exist_ok=True)
    status = {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "feedback-assistant",
        "requestedRef": args.ref,
        "changedFiles": changed,
        "background": bool(args.background),
        "transport": transport,
    }
    with open(STATUS_PATH, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2)

    if not args.quiet:
        if changed:
            print(
                "Feedback Assistant update installed ({} file{} via {}).".format(
                    len(changed),
                    "" if len(changed) == 1 else "s",
                    transport,
                )
            )
        else:
            print("Already up to date via {}.".format(transport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
