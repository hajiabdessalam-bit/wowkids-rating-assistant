from __future__ import annotations

"""Compatibility shim.

WOWKIDS updates no longer open a separate HTTPS/Git/Vercel connection.
The running cloud agent receives one small update file at a time inside the
same /api/wowkids-device polling response that already works reliably on this
PC. Keep this file so older BAT files can call it safely.
"""

import argparse


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

    if not args.quiet:
        print(
            "Updates are delivered automatically through the normal "
            "Feedback Assistant polling connection."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
