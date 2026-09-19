from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pair_windows_agent import (
    CONFIG_PATH,
    LEGACY_CONFIG_PATH,
    api_check,
    install_autostart,
    save_config,
    start_supervisor,
)


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


def main():
    print("")
    print("WOWKIDS WINDOWS AGENT - NO-KEY REPAIR")
    print("=" * 55)
    print("")

    config = load_existing_config()
    if not config:
        print("No saved pairing was found on this Windows account.")
        print("Use PAIR_WINDOWS_AGENT.bat once.")
        return 2

    token = str(config.get("deviceToken") or "").strip()
    print("Saved pairing found. Checking it...")
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
    save_config(token, device.get("name") or config.get("deviceName") or "Windows PC")
    install_autostart()
    start_supervisor()

    print("")
    print("REPAIRED SUCCESSFULLY")
    print("  Pairing : reused (NO new key)")
    print("  Auto-run: installed")
    print("  Watchdog: started")
    print("")
    print("From now on, normal Windows restarts should require nothing from you.")
    print("Open the matching WOWKIDS roster and the queued job should take over.")
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
