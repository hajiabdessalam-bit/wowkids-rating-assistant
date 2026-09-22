from __future__ import annotations

"""Compatibility shim for older installers.

Windows source updates no longer use GitHub, archive downloads, or a separate
Vercel updater request.  The running WOWKIDS cloud agent receives one verified
file at a time inside its normal /api/wowkids-device polling response — the
same connection that already carries rating jobs reliably.

Older bootstrap BAT files still call `vercel_update.py` as a Step 3 check.
Keep this tiny script so those installers finish successfully instead of
opening another TLS connection.
"""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ref", default="main")
    parser.parse_args(argv)

    # Intentionally no network access here.
    if "--quiet" not in (argv or []):
        print("Updates are handled automatically through the normal WOWKIDS job connection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
