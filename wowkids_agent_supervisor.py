from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ERROR_ALREADY_EXISTS = 183
SUPERVISOR_MUTEX = "Local\\WOWKIDSRatingAssistantSupervisorV1"


def pythonw_path():
    executable = os.path.abspath(sys.executable)
    folder = os.path.dirname(executable)
    candidate = os.path.join(folder, "pythonw.exe")
    return candidate if os.path.exists(candidate) else executable


def _single_instance():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.CreateMutexW(None, False, SUPERVISOR_MUTEX)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def _hidden_flags():
    if os.name != "nt":
        return 0
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def _silent_git_pull():
    """Best-effort update at logon/restart; never prevents the agent starting."""
    try:
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=HERE,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            creationflags=_hidden_flags(),
            check=False,
        )
    except Exception:
        pass


def _run_agent():
    return subprocess.Popen(
        [pythonw_path(), os.path.join(HERE, "wowkids_cloud_agent.py")],
        cwd=HERE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_hidden_flags(),
    )


def main():
    handle = _single_instance()
    if handle is None:
        return 0

    try:
        _silent_git_pull()

        while True:
            try:
                proc = _run_agent()
                proc.wait()
            except Exception:
                pass

            # A crash or accidental process exit should heal itself without
            # making the coach re-pair or reopen anything.
            time.sleep(8)
    finally:
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
