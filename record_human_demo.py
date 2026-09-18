from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import sys
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon


WH_MOUSE_LL = 14
HC_ACTION = 0
WM_LBUTTONDOWN = 0x0201
WM_MOUSEWHEEL = 0x020A
VK_F10 = 0x79


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


def _rect_copy(rect):
    return dict(rect) if rect else None


def _inside(rect, x, y):
    if not rect:
        return False
    return (
        rect["left"] <= x < rect["left"] + rect["width"]
        and rect["top"] <= y < rect["top"] + rect["height"]
    )


class DemoRecorder:
    def __init__(self):
        wkcommon.enable_utf8_stdout()
        wkcommon.bootstrap_libs()
        self.dpi_mode = wkcommon.set_dpi_awareness()

        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        target, reason = wkcommon.select_wowkids_window(
            wkcommon.wowkids_windows(desktop)
        )
        if not target:
            raise RuntimeError(reason)

        self.target = target
        self.wrapper = target["wrapper"]
        self.handle = self.wrapper.handle
        wkcommon.restore_window(self.wrapper)

        self.started_wall = time.strftime("%Y-%m-%d %H:%M:%S")
        self.started_monotonic = time.monotonic()
        self.stamp = wkcommon.timestamp()

        self.demo_root = os.path.join(HERE, "demos")
        self.demo_dir = os.path.join(self.demo_root, "demo_{}".format(self.stamp))
        self.shots_dir = os.path.join(self.demo_dir, "screenshots")
        os.makedirs(self.shots_dir, exist_ok=True)

        self.events_path = os.path.join(self.demo_dir, "events.json")
        self.metadata_path = os.path.join(self.demo_dir, "metadata.json")
        self.summary_path = os.path.join(self.demo_dir, "summary.txt")

        self.events = []
        self.events_lock = threading.RLock()
        self.capture_lock = threading.Lock()
        self.sequence_lock = threading.Lock()
        self.sequence = 0
        self.running = False
        self.hook = None
        self.hook_proc = None
        self.pending = []

        self.wheel_lock = threading.RLock()
        self.wheel_event = None
        self.wheel_timer = None

        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32

        self._write_metadata(final=False)

    def _next_id(self):
        with self.sequence_lock:
            self.sequence += 1
            return self.sequence

    def _geometry(self, x=None, y=None):
        window_rect = wkcommon.window_rectangle(self.wrapper)
        client_rect = wkcommon.win32_client_rect(self.wrapper)
        data = {
            "window_rect": _rect_copy(window_rect),
            "client_rect": _rect_copy(client_rect),
        }
        if x is not None and y is not None:
            data["screen_point"] = [int(x), int(y)]
            if window_rect:
                data["window_relative"] = [
                    int(x - window_rect["left"]),
                    int(y - window_rect["top"]),
                ]
            if client_rect:
                data["client_relative"] = [
                    int(x - client_rect["left"]),
                    int(y - client_rect["top"]),
                ]
        return data

    def _capture(self, filename):
        path = os.path.join(self.shots_dir, filename)
        with self.capture_lock:
            note = wkcommon.capture_window(self.wrapper, path)
        return os.path.relpath(path, self.demo_dir).replace("\\", "/"), note

    def _save_events(self):
        with self.events_lock:
            tmp = self.events_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.events, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.events_path)

    def _write_metadata(self, final=False):
        payload = {
            "format_version": 1,
            "purpose": "WOWKIDS human demonstration recording",
            "privacy": "LOCAL ONLY - may contain student information; do not commit",
            "started": self.started_wall,
            "finished": time.strftime("%Y-%m-%d %H:%M:%S") if final else None,
            "dpi_mode": self.dpi_mode,
            "process": self.target.get("exe", ""),
            "pid": self.target.get("pid"),
            "window_title": self.target.get("title", ""),
            "window_class": self.target.get("class_name", ""),
            "initial_window_rect": self.target.get("rect"),
            "event_count": len(self.events),
            "stop_key": "F10",
            "capture_policy": {
                "click": "screenshot immediately before LEFT mouse down and about 500 ms after",
                "scroll": "one event per wheel burst; screenshot before first wheel movement and after burst settles",
            },
        }
        with open(self.metadata_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def _new_event(self, kind, x, y):
        event_id = self._next_id()
        event = {
            "id": event_id,
            "kind": kind,
            "status": "capturing",
            "time_wall": time.strftime("%Y-%m-%d %H:%M:%S"),
            "time_from_start_ms": int(
                (time.monotonic() - self.started_monotonic) * 1000
            ),
        }
        event.update(self._geometry(x, y))
        return event

    def _append_event(self, event):
        with self.events_lock:
            self.events.append(event)
            self._save_events()

    def _finish_event(self, event, after_delay=0.50):
        def worker():
            try:
                time.sleep(after_delay)
                filename = "{:04d}_{}_after.png".format(
                    event["id"], event["kind"]
                )
                after, note = self._capture(filename)
                with self.events_lock:
                    event["after_screenshot"] = after
                    event["after_capture_note"] = note
                    event["geometry_after"] = self._geometry()
                    event["status"] = "complete"
                    self._save_events()
            except Exception as exc:
                with self.events_lock:
                    event["status"] = "after-capture-failed"
                    event["error"] = str(exc)
                    self._save_events()

        thread = threading.Thread(target=worker, daemon=True)
        self.pending.append(thread)
        thread.start()

    def record_click(self, x, y):
        client_rect = wkcommon.win32_client_rect(self.wrapper)
        if not _inside(client_rect, x, y):
            return

        event = self._new_event("left_click", x, y)
        filename = "{:04d}_click_before.png".format(event["id"])
        before, note = self._capture(filename)
        event["before_screenshot"] = before
        event["before_capture_note"] = note
        event["button"] = "left"
        self._append_event(event)
        self._finish_event(event, 0.50)

    def _finalize_wheel(self, event):
        with self.wheel_lock:
            if self.wheel_event is not event:
                return
            self.wheel_event = None
            self.wheel_timer = None

        try:
            filename = "{:04d}_scroll_after.png".format(event["id"])
            after, note = self._capture(filename)
            with self.events_lock:
                event["after_screenshot"] = after
                event["after_capture_note"] = note
                event["geometry_after"] = self._geometry()
                event["status"] = "complete"
                self._save_events()
        except Exception as exc:
            with self.events_lock:
                event["status"] = "after-capture-failed"
                event["error"] = str(exc)
                self._save_events()

    def record_wheel(self, x, y, delta):
        client_rect = wkcommon.win32_client_rect(self.wrapper)
        if not _inside(client_rect, x, y):
            return

        with self.wheel_lock:
            if self.wheel_event is None:
                event = self._new_event("scroll", x, y)
                filename = "{:04d}_scroll_before.png".format(event["id"])
                before, note = self._capture(filename)
                event["before_screenshot"] = before
                event["before_capture_note"] = note
                event["wheel_delta"] = 0
                event["wheel_notches"] = 0.0
                event["raw_wheel_messages"] = 0
                self.wheel_event = event
                self._append_event(event)
            else:
                event = self.wheel_event

            event["wheel_delta"] += int(delta)
            event["wheel_notches"] = round(event["wheel_delta"] / 120.0, 3)
            event["raw_wheel_messages"] += 1
            event["last_screen_point"] = [int(x), int(y)]
            event["last_time_from_start_ms"] = int(
                (time.monotonic() - self.started_monotonic) * 1000
            )
            self._save_events()

            if self.wheel_timer is not None:
                try:
                    self.wheel_timer.cancel()
                except Exception:
                    pass
            self.wheel_timer = threading.Timer(
                0.45, self._finalize_wheel, args=(event,)
            )
            self.wheel_timer.daemon = True
            self.wheel_timer.start()

    def _mouse_callback(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and self.running:
                info = ctypes.cast(
                    l_param, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents
                x, y = int(info.pt.x), int(info.pt.y)

                if int(w_param) == WM_LBUTTONDOWN:
                    self.record_click(x, y)
                elif int(w_param) == WM_MOUSEWHEEL:
                    raw = (int(info.mouseData) >> 16) & 0xFFFF
                    delta = ctypes.c_short(raw).value
                    self.record_wheel(x, y, delta)
        except Exception:
            # A recorder must never interfere with the user's actual click.
            pass

        return self.user32.CallNextHookEx(
            self.hook, n_code, w_param, l_param
        )

    def _install_hook(self):
        self.hook_proc = HOOKPROC(self._mouse_callback)
        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD
        ]
        self.user32.SetWindowsHookExW.restype = ctypes.c_void_p
        module = self.kernel32.GetModuleHandleW(None)
        self.hook = self.user32.SetWindowsHookExW(
            WH_MOUSE_LL, self.hook_proc, module, 0
        )
        if not self.hook:
            raise ctypes.WinError()

    def _uninstall_hook(self):
        if self.hook:
            try:
                self.user32.UnhookWindowsHookEx(self.hook)
            except Exception:
                pass
            self.hook = None

    def _f10_pressed(self):
        return bool(self.user32.GetAsyncKeyState(VK_F10) & 0x8000)

    def _zip_demo(self):
        zip_path = self.demo_dir + ".zip"
        try:
            with zipfile.ZipFile(
                zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as zf:
                for root, _dirs, files in os.walk(self.demo_dir):
                    for name in files:
                        full = os.path.join(root, name)
                        arc = os.path.relpath(full, self.demo_dir)
                        zf.write(full, arc)
            return zip_path
        except Exception:
            return None

    def run(self):
        print("")
        print("WOWKIDS HUMAN DEMO RECORDER")
        print("=" * 62)
        print("This records YOUR normal mouse clicks and scrolling inside WOWKIDS.")
        print("It does NOT click, scroll, rate, submit, or control WOWKIDS itself.")
        print("")
        print("Private data stays under:")
        print("  {}".format(self.demo_dir))
        print("")
        print("How to make this demo useful:")
        print("  - Rate ONE student normally from start to finish.")
        print("  - Use your normal scrolling and clicks.")
        print("  - Different description lengths are GOOD; we want real variation.")
        print("  - When finished, press F10.")
        print("")
        input("Press ENTER when the student's rating form is ready... ")

        wkcommon.restore_window(self.wrapper)
        time.sleep(0.35)

        start_name = "0000_start.png"
        start_rel, start_note = self._capture(start_name)
        self.start_screenshot = start_rel
        self.start_capture_note = start_note

        self._install_hook()
        self.running = True

        print("")
        print("RECORDING NOW.")
        print("Rate the student exactly as you normally do.")
        print("Press F10 when the demonstration is finished.")
        print("")

        msg = wt.MSG()
        try:
            while self.running:
                while self.user32.PeekMessageW(
                    ctypes.byref(msg), None, 0, 0, 1
                ):
                    self.user32.TranslateMessage(ctypes.byref(msg))
                    self.user32.DispatchMessageW(ctypes.byref(msg))

                if self._f10_pressed():
                    self.running = False
                    break
                time.sleep(0.015)
        except KeyboardInterrupt:
            self.running = False
        finally:
            self._uninstall_hook()

        with self.wheel_lock:
            if self.wheel_timer is not None:
                try:
                    self.wheel_timer.cancel()
                except Exception:
                    pass
                self.wheel_timer = None
            pending_wheel = self.wheel_event
            self.wheel_event = None

        if pending_wheel is not None:
            self._finalize_wheel(pending_wheel)

        for thread in list(self.pending):
            try:
                thread.join(timeout=1.2)
            except Exception:
                pass

        try:
            end_rel, end_note = self._capture("9999_end.png")
        except Exception as exc:
            end_rel, end_note = None, "capture failed: {}".format(exc)

        self._save_events()
        self._write_metadata(final=True)

        complete = sum(
            1 for event in self.events if event.get("status") == "complete"
        )
        clicks = sum(1 for event in self.events if event.get("kind") == "left_click")
        scrolls = sum(1 for event in self.events if event.get("kind") == "scroll")

        with open(self.summary_path, "w", encoding="utf-8") as fh:
            fh.write("WOWKIDS human demonstration\n")
            fh.write("started: {}\n".format(self.started_wall))
            fh.write("events: {} ({} clicks, {} scroll bursts)\n".format(
                len(self.events), clicks, scrolls
            ))
            fh.write("complete captures: {}\n".format(complete))
            fh.write("start screenshot: {} ({})\n".format(
                getattr(self, "start_screenshot", None),
                getattr(self, "start_capture_note", ""),
            ))
            fh.write("end screenshot: {} ({})\n".format(end_rel, end_note))
            fh.write("privacy: contains real UI screenshots; keep local/private\n")

        zip_path = self._zip_demo()

        print("")
        print("RECORDED SUCCESSFULLY.")
        print("  clicks       : {}".format(clicks))
        print("  scroll bursts: {}".format(scrolls))
        print("  total events : {}".format(len(self.events)))
        print("")
        print("Demo folder:")
        print("  {}".format(self.demo_dir))
        if zip_path:
            print("")
            print("Private ZIP ready to share in this chat:")
            print("  {}".format(zip_path))
        print("")
        print("Do NOT upload this demo to the public GitHub repository.")
        return 0


def main():
    try:
        recorder = DemoRecorder()
        return recorder.run()
    except Exception as exc:
        wkcommon.enable_utf8_stdout()
        print("")
        print("RECORDER STOPPED: {}".format(exc))
        print("Nothing was changed in WOWKIDS by the recorder.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
