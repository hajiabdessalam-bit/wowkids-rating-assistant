from __future__ import annotations

import json
import os
import time


HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
REQUEST_PATH = os.path.join(STATE_DIR, "update_requested.json")


def main(argv=None):
    """Compatibility command.

    Updates are now delivered inside the normal /api/wowkids-device polling
    response, which is the connection already proven reliable on this PC.
    This command simply records an immediate-update request and exits cleanly.
    The running/restarted agent will pick up the server update automatically.
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        with open(REQUEST_PATH, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "requestedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "channel": "agent-poll",
                },
                fh,
                indent=2,
            )
    except Exception:
        pass

    print("Updates are handled automatically through the agent polling channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
