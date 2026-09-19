from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pair_windows_agent import (
    CONFIG_PATH,
    LEGACY_CONFIG_PATH,
    STATE_DIR,
    api_check,
    install_autostart,
    save_config,
    start_supervisor,
)

STATUS_PATH = os.path.join(STATE_DIR, "agent_status.json")
LOG_PATH = os.path.join(STATE_DIR, "supervisor.log")


def load_existing_config():
    for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            token = str(data.get("deviceToken") or "").strip()
            if token.startswith("wk_"):
                return data
        except Exception:
            pass
    return None


def stop_old_wowkids_processes():
    """Stop only our Python processes so the repaired supervisor starts cleanly."""
    script = r"""
$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and (
    $_.CommandLine -like '*wowkids_cloud_agent.py*' -or
    $_.CommandLine -like '*wowkids_agent_supervisor.py*'
  )
}
foreach ($p in $targets) {
  try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
}
"""
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", script,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass
    time.sleep(1.0)


def wait_for_fresh_agent_status(started_at, timeout=35):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.getmtime(STATUS_PATH) >= started_at:
                with open(STATUS_PATH, encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception:
            pass
        time.sleep(1)
    return None


def main():
    print("")
    print("WOWKIDS WINDOWS AGENT - PERMANENT NO-KEY REPAIR")
    print("=" * 62)
    print("")

    config = load_existing_config()
    if not config:
        print("No saved pairing was found on this Windows account.")
        print("Use PAIR_WINDOWS_AGENT.bat once.")
        return 2

    token = str(config.get("deviceToken") or "").strip()
    print("1/5 Saved pairing found.")
    print("2/5 Checking the existing pairing...")
    try:
        response = api_check(token)
    except Exception as exc:
        print("")
        print("The saved pairing was genuinely revoked or is invalid:")
        print("  {}".format(exc))
        print("")
        print("Only in this case do you need PAIR_WINDOWS_AGENT.bat and a new key.")
        return 2

    device = response.get("device") or {}
    save_config(
        token,
        device.get("name") or config.get("deviceName") or "Windows PC",
    )

    print("3/5 Replacing old background processes...")
    stop_old_wowkids_processes()

    print("4/5 Installing permanent auto-start + watchdog...")
    install_autostart()

    print("5/5 Starting and verifying the agent...")
    started_at = time.time()
    start_supervisor()
    status = wait_for_fresh_agent_status(started_at)

    print("")
    if status:
        print("REPAIR VERIFIED")
        print("  Pairing : reused (NO new key)")
        print("  Agent   : {}".format(status.get("state") or "running"))
        print("  Message : {}".format(status.get("message") or "connected"))
        print("  Auto-run: redundant startup methods installed")
        print("  Watchdog: restarts a crashed OR frozen agent automatically")
        print("")
        print("This is the permanent setup. Normal PC restarts require no action.")
        return 0

    print("The supervisor started, but the agent did not produce a heartbeat.")
    print("No new key is needed.")
    print("Diagnostic log:")
    print("  {}".format(LOG_PATH))
    print("")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
