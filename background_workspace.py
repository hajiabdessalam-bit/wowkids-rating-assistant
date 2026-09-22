from __future__ import annotations

import ctypes
import json
import os
import time

import wkcommon


STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.dirname(os.path.abspath(__file__)),
    "WOWKIDSRatingAssistant",
)
STATE_PATH = os.path.join(STATE_DIR, "workspace_mode.json")

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

LWA_ALPHA = 0x00000002

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

DEFAULT_ALPHA = 8  # ~3% visible: effectively invisible but still rendered.


def _user32():
    return ctypes.windll.user32


def _get_exstyle(hwnd):
    user32 = _user32()
    getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    getter.restype = ctypes.c_ssize_t
    return int(getter(int(hwnd), GWL_EXSTYLE))


def _set_exstyle(hwnd, value):
    user32 = _user32()
    setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    setter.restype = ctypes.c_ssize_t
    setter(int(hwnd), GWL_EXSTYLE, ctypes.c_ssize_t(int(value)))
    return _get_exstyle(hwnd)


def _pid_for_hwnd(hwnd):
    pid = ctypes.c_ulong()
    _user32().GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _save_state(payload):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _clear_state():
    try:
        os.remove(STATE_PATH)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _layered_attributes(hwnd):
    """Return existing layered alpha metadata, or None when not configured."""
    color_key = ctypes.c_ulong()
    alpha = ctypes.c_ubyte()
    flags = ctypes.c_ulong()
    ok = _user32().GetLayeredWindowAttributes(
        int(hwnd),
        ctypes.byref(color_key),
        ctypes.byref(alpha),
        ctypes.byref(flags),
    )
    if not ok:
        return None
    return {
        "color_key": int(color_key.value),
        "alpha": int(alpha.value),
        "flags": int(flags.value),
    }


def _restore_from_state(state):
    hwnd = int(state.get("hwnd") or 0)
    pid = int(state.get("pid") or 0)
    if not hwnd or not _user32().IsWindow(hwnd):
        _clear_state()
        return False, "stale workspace state removed"
    if pid and _pid_for_hwnd(hwnd) != pid:
        _clear_state()
        return False, "stale workspace process removed"

    original = int(state.get("original_exstyle") or 0)
    _set_exstyle(hwnd, original)

    attrs = state.get("original_layered_attributes")
    if (original & WS_EX_LAYERED) and isinstance(attrs, dict):
        _user32().SetLayeredWindowAttributes(
            hwnd,
            int(attrs.get("color_key") or 0),
            int(attrs.get("alpha") or 255),
            int(attrs.get("flags") or LWA_ALPHA),
        )

    insert_after = HWND_TOPMOST if (original & WS_EX_TOPMOST) else HWND_NOTOPMOST
    _user32().SetWindowPos(
        hwnd,
        insert_after,
        0,
        0,
        0,
        0,
        SWP_NOMOVE
        | SWP_NOSIZE
        | SWP_NOACTIVATE
        | SWP_FRAMECHANGED
        | SWP_SHOWWINDOW,
    )
    _clear_state()
    return True, "workspace mode restored"


def recover_previous_workspace_mode():
    """Undo workspace mode after a prior agent crash/restart, if necessary."""
    state = _load_state()
    if not state.get("enabled"):
        return False, "no previous workspace mode"
    try:
        return _restore_from_state(state)
    except Exception as exc:
        return False, "workspace recovery failed: {}".format(exc)


class BackgroundWorkspaceMode:
    """Keep WOWKIDS rendered while leaving the real desktop usable.

    Chromium stops painting fully occluded windows. Instead of forcing WOWKIDS
    to the foreground, temporarily keep it topmost but almost fully transparent,
    click-through, and non-activating. Direct SendMessage/PostMessage actions
    still target WOWKIDS while the coach can work normally in Chrome underneath.

    The original extended window style is persisted so an agent restart can
    restore it safely.
    """

    def __init__(self, wrapper, alpha=None):
        self.wrapper = wrapper
        self.hwnd = int(wrapper.handle)
        self.pid = _pid_for_hwnd(self.hwnd)
        self.alpha = self._resolve_alpha(alpha)
        self.enabled = False
        self.original_exstyle = None
        self.original_layered_attributes = None

    @staticmethod
    def _resolve_alpha(value):
        if value is None:
            value = os.environ.get("WOWKIDS_BACKGROUND_ALPHA", DEFAULT_ALPHA)
        try:
            value = int(value)
        except Exception:
            value = DEFAULT_ALPHA
        return max(1, min(80, value))

    def enable(self):
        if self.enabled:
            return {
                "enabled": True,
                "alpha": self.alpha,
                "reason": "already enabled",
            }

        if os.environ.get("WOWKIDS_WORKSPACE_MODE", "1").strip().lower() in (
            "0", "false", "off", "no"
        ):
            return {
                "enabled": False,
                "reason": "disabled by WOWKIDS_WORKSPACE_MODE",
            }

        if not _user32().IsWindow(self.hwnd):
            raise RuntimeError("WOWKIDS window closed before workspace mode")

        # A minimized Chromium window is treated as hidden. Restore it without
        # activating it before applying the click-through workspace mode.
        wkcommon.prepare_background_window(self.wrapper)
        time.sleep(0.12)

        self.original_exstyle = _get_exstyle(self.hwnd)
        self.original_layered_attributes = (
            _layered_attributes(self.hwnd)
            if self.original_exstyle & WS_EX_LAYERED
            else None
        )

        state = {
            "enabled": True,
            "hwnd": self.hwnd,
            "pid": self.pid,
            "original_exstyle": self.original_exstyle,
            "original_layered_attributes": self.original_layered_attributes,
            "alpha": self.alpha,
            "enabled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_state(state)

        new_style = (
            self.original_exstyle
            | WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_NOACTIVATE
        )
        try:
            _set_exstyle(self.hwnd, new_style)
            if not _user32().SetLayeredWindowAttributes(
                self.hwnd, 0, self.alpha, LWA_ALPHA
            ):
                raise RuntimeError("SetLayeredWindowAttributes failed")

            if not _user32().SetWindowPos(
                self.hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE
                | SWP_NOSIZE
                | SWP_NOACTIVATE
                | SWP_FRAMECHANGED
                | SWP_SHOWWINDOW,
            ):
                raise RuntimeError("SetWindowPos(HWND_TOPMOST) failed")

            self.enabled = True
            return {
                "enabled": True,
                "alpha": self.alpha,
                "reason": "transparent topmost click-through mode active",
            }
        except Exception:
            try:
                _restore_from_state(state)
            except Exception:
                pass
            raise

    def restore(self):
        if self.original_exstyle is None:
            state = _load_state()
            if (
                state.get("enabled")
                and int(state.get("hwnd") or 0) == self.hwnd
                and int(state.get("pid") or 0) == self.pid
            ):
                restored, reason = _restore_from_state(state)
                self.enabled = False
                return {"restored": restored, "reason": reason}
            return {"restored": False, "reason": "nothing to restore"}

        state = {
            "enabled": True,
            "hwnd": self.hwnd,
            "pid": self.pid,
            "original_exstyle": self.original_exstyle,
            "original_layered_attributes": self.original_layered_attributes,
        }
        restored, reason = _restore_from_state(state)
        self.enabled = False
        return {"restored": restored, "reason": reason}


def enable_for_current_wowkids():
    """Return an enabled guard for the current WOWKIDS window, or None."""
    try:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        target, reason = wkcommon.select_wowkids_window(
            wkcommon.wowkids_windows(desktop)
        )
        if not target:
            return None, reason
        guard = BackgroundWorkspaceMode(target["wrapper"])
        info = guard.enable()
        if not info.get("enabled"):
            return None, info.get("reason") or "workspace mode disabled"
        return guard, info.get("reason") or "workspace mode active"
    except Exception as exc:
        return None, "workspace mode unavailable: {}".format(exc)
