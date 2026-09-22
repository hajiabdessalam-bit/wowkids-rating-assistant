from __future__ import annotations

from background_workspace import recover_previous_workspace_mode


def main():
    restored, reason = recover_previous_workspace_mode()
    print(reason)
    return 0 if restored or "no previous" in reason else 1


if __name__ == "__main__":
    raise SystemExit(main())
