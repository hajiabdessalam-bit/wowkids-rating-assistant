from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "device_config.json")
DEFAULT_BASE_URL = "https://feedback-assistant-alpha.vercel.app"


def api_check(token):
    req = urllib.request.Request(
        DEFAULT_BASE_URL + "/api/wowkids-device",
        headers={
            "Accept": "application/json",
            "x-wowkids-device-token": token,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
            message = payload.get("error") or raw
        except Exception:
            message = raw
        raise RuntimeError(
            "Pairing token was rejected: {}".format(message or exc.code)
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Could not reach Feedback Assistant: {}".format(exc.reason)
        )


def pythonw_path():
    executable = os.path.abspath(sys.executable)
    folder = os.path.dirname(executable)
    candidates = [
        os.path.join(folder, "pythonw.exe"),
        executable,
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return executable


def install_startup():
    startup = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )
    if not startup or not os.path.isdir(startup):
        raise RuntimeError("Windows Startup folder was not found")

    path = os.path.join(startup, "WOWKIDS Rating Assistant Agent.vbs")
    pythonw = pythonw_path()
    agent = os.path.join(HERE, "wowkids_cloud_agent.py")

    def vbs_quote(value):
        return str(value).replace('"', '""')

    content = (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        'shell.CurrentDirectory = "{}"\r\n'.format(vbs_quote(HERE))
        + 'shell.Run """{}"" ""{}""", 0, False\r\n'.format(
            vbs_quote(pythonw), vbs_quote(agent)
        )
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def start_agent():
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    subprocess.Popen(
        [pythonw_path(), os.path.join(HERE, "wowkids_cloud_agent.py")],
        cwd=HERE,
        close_fds=True,
        creationflags=flags,
    )


def main():
    print("")
    print("WOWKIDS WINDOWS AGENT - ONE-TIME PAIRING")
    print("=" * 58)
    print("")
    print("In Feedback Assistant:")
    print("  Open a class -> WOWKIDS -> Pair PC")
    print("  Copy the one-time token that starts with wk_")
    print("")
    token = input("Paste pairing token: ").strip()
    if not token.startswith("wk_"):
        print("That does not look like a WOWKIDS pairing token.")
        return 2

    print("")
    print("Checking token...")
    response = api_check(token)
    device = response.get("device") or {}

    config = {
        "baseUrl": DEFAULT_BASE_URL,
        "deviceToken": token,
        "deviceName": device.get("name") or "Windows PC",
    }
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)

    startup_path = install_startup()
    start_agent()

    print("")
    print("PAIRED SUCCESSFULLY")
    print("  Device : {}".format(config["deviceName"]))
    print("  Startup: installed")
    print("")
    print("You do not need to run this again.")
    print("The agent will start quietly when you sign into Windows.")
    print("It will only act when a queued Feedback Assistant job matches")
    print("the WOWKIDS class roster currently open on this computer.")
    print("")
    print("Post All is NEVER part of the automation.")
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
