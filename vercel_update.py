from __future__ import annotations

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ref", default="main")
    parser.parse_args(argv)

    # Updates are now delivered inside the normal /api/wowkids-device poll
    # response. This file remains only as a compatibility shim for older BATs.
    if "--quiet" not in (argv or []):
        print("Updates are delivered automatically through the WOWKIDS job channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
