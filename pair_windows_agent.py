from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
CONFIG_PATH = os.path.join(STATE_DIR, "device_config.json")
LEGACY_CONFIG_PATH = os.path.join(HERE, "device_config.json")
DEFAULT_BASE_URL = "https://feedback-assistant-alpha.vercel.app"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "WOWKIDS Rating Assistant"
TASK_LOGON = "WOWKIDS Rating Assistant Logon"
TASK_WATCHDOG = "WOWKIDS Rating Assistant Watchdog"


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


def _startup_vbs_path():
    startup = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )
    return os.path.join(startup, "WOWKIDS Rating Assistant Agent.vbs")


def _supervisor_command():
    supervisor = os.path.join(HERE, "wowkids_agent_supervisor.py")
    return '"{}" "{}"'.format(pythonw_path(), supervisor)


def _write_startup_vbs():
    path = _startup_vbs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def q(value):
        return str(value).replace('"', '""')

    content = (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        'shell.CurrentDirectory = "{}"\r\n'.format(q(HERE))
        + 'shell.Run """{}"" ""{}""", 0, False\r\n'.format(
            q(pythonw_path()),
            q(os.path.join(HERE, "wowkids_agent_supervisor.py")),
        )
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _install_scheduled_tasks(command):
    """Best-effort redundancy. Failure does not break registry/startup methods."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    results = []
    specs = [
        [
            "schtasks", "/Create", "/F",
            "/TN", TASK_LOGON,
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
            "/TR", command,
        ],
        [
            "schtasks", "/Create", "/F",
            "/TN", TASK_WATCHDOG,
            "/SC", "MINUTE", "/MO", "5",
            "/RL", "LIMITED",
            "/TR", command,
        ],
    ]
    for spec in specs:
        try:
            result = subprocess.run(
                spec,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                timeout=20,
                check=False,
            )
            results.append(result.returncode == 0)
        except Exception:
            results.append(False)
    return results


def install_autostart():
    """Install three no-admin startup paths; supervisor mutex prevents duplicates."""
    command = _supervisor_command()

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)

    vbs_path = _write_startup_vbs()
    task_results = _install_scheduled_tasks(command)

    return {
        "registry": True,
        "startupVbs": vbs_path,
        "scheduledTasks": task_results,
        "command": command,
    }


def start_supervisor():
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    subprocess.Popen(
        [pythonw_path(), os.path.join(HERE, "wowkids_agent_supervisor.py")],
        cwd=HERE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def save_config(token, device_name="Windows PC"):
    os.makedirs(STATE_DIR, exist_ok=True)
    config = {
        "baseUrl": DEFAULT_BASE_URL,
        "deviceToken": token,
        "deviceName": device_name,
    }
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)

    try:
        if os.path.exists(LEGACY_CONFIG_PATH):
            os.remove(LEGACY_CONFIG_PATH)
    except Exception:
        pass
    return config


def main():
    print("")
    print("WOWKIDS WINDOWS AGENT - ONE-TIME PAIRING")
    print("=" * 58)
    print("")
    print("This pairing is permanent across normal PC restarts.")
    print("You should NOT need a new key every time Windows starts.")
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

    config = save_config(
        token, device.get("name") or "Windows PC"
    )
    install_autostart()
    start_supervisor()

    print("")
    print("PAIRED SUCCESSFULLY")
    print("  Device    : {}".format(config["deviceName"]))
    print("  Pairing   : saved permanently in your Windows profile")
    print("  Auto-run  : registry + Startup folder")
    print("  Watchdog  : self-healing supervisor enabled")
    print("  Extra task: scheduled watchdog attempted")
    print("")
    print("You do not need to paste this key again after a normal restart.")
    print("If the agent ever seems offline, run REPAIR_WINDOWS_AGENT.bat.")
    print("That repair uses the saved pairing and asks for NO key.")
    print("")
    print("Post All is NEVER part of the automation.")
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
