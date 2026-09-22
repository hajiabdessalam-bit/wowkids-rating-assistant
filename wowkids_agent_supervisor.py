from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
AGENT_STATUS = os.path.join(STATE_DIR, "agent_status.json")
SUPERVISOR_STATUS = os.path.join(STATE_DIR, "supervisor_status.txt")
LOG_PATH = os.path.join(STATE_DIR, "supervisor.log")

ERROR_ALREADY_EXISTS = 183
SUPERVISOR_MUTEX = "Local\\WOWKIDSRatingAssistantSupervisorV2"
STALE_AGENT_SECONDS = 95
CHECK_EVERY_SECONDS = 8
UPDATE_EVERY_SECONDS = 300


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(message):
    os.makedirs(STATE_DIR, exist_ok=True)
    line = "[{}] {}\n".format(_stamp(), message)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        with open(SUPERVISOR_STATUS, "w", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


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
    """Best-effort background update with Schannel -> OpenSSL fallback."""
    commands = [
        ["git", "pull", "--ff-only"],
        [
            "git", "-c", "http.sslBackend=openssl",
            "-c", "http.version=HTTP/1.1",
            "pull", "--ff-only",
        ],
    ]
    last_code = None
    for index, command in enumerate(commands):
        try:
            result = subprocess.run(
                command,
                cwd=HERE,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
                creationflags=_hidden_flags(),
                check=False,
            )
            last_code = result.returncode
            if result.returncode == 0:
                _log(
                    "git pull succeeded{}".format(
                        " with OpenSSL fallback" if index else ""
                    )
                )
                return True
        except Exception as exc:
            _log(
                "git pull attempt {} failed: {}".format(
                    index + 1, exc
                )
            )
    _log("git pull failed; last exit code {}".format(last_code))
    return False


def _run_agent():
    os.makedirs(STATE_DIR, exist_ok=True)
    log_handle = open(LOG_PATH, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [pythonw_path(), os.path.join(HERE, "wowkids_cloud_agent.py")],
        cwd=HERE,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        close_fds=True,
        creationflags=_hidden_flags(),
    )
    _log("agent launched pid={}".format(proc.pid))
    return proc, log_handle


def _status_age_seconds():
    try:
        return max(0.0, time.time() - os.path.getmtime(AGENT_STATUS))
    except Exception:
        return None


def _stop_process(proc):
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    handle = _single_instance()
    if handle is None:
        return 0

    _log("supervisor started")
    try:
        _silent_git_pull()
        last_update_check = time.monotonic()

        while True:
            proc = None
            log_handle = None
            launched_at = time.time()
            try:
                proc, log_handle = _run_agent()

                while proc.poll() is None:
                    time.sleep(CHECK_EVERY_SECONDS)

                    if (
                        time.monotonic() - last_update_check
                        >= UPDATE_EVERY_SECONDS
                    ):
                        _silent_git_pull()
                        last_update_check = time.monotonic()

                    age = _status_age_seconds()

                    # Give a fresh agent enough time for its first API poll.
                    if age is None and time.time() - launched_at < 45:
                        continue

                    if age is not None and age <= STALE_AGENT_SECONDS:
                        _log(
                            "healthy: agent heartbeat {:.0f}s old".format(age)
                        )
                        continue

                    # A living but silent process is worse than a crash because
                    # queued jobs wait forever. Recycle it automatically.
                    if age is None:
                        _log("agent produced no heartbeat; restarting it")
                    else:
                        _log(
                            "agent heartbeat stale ({:.0f}s); restarting it".format(
                                age
                            )
                        )
                    _stop_process(proc)
                    break

                code = proc.poll()
                _log("agent exited code={}; restart in 5s".format(code))
            except Exception as exc:
                _log("supervisor loop error: {}".format(exc))
                if proc is not None:
                    _stop_process(proc)
            finally:
                try:
                    if log_handle is not None:
                        log_handle.close()
                except Exception:
                    pass

            time.sleep(5)
    finally:
        _log("supervisor stopped")
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
