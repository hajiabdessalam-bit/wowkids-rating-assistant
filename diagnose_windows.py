"""Diagnostic: which window really shows the mini program?

Read-only. It lists every top-level window (Win32) with its process, class,
rectangle, minimised/visible state and focus, then summarises the UI Automation
content of every WeChat-related window so we can tell which one is showing the
rating page and which one is a stale leftover.

Rewrites nothing, clicks nothing.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon  # noqa: E402

user32 = ctypes.windll.user32

INTERESTING_EXE = ("wechat", "weixin")
INTERESTING_CLASS = ("chrome_widgetwin",)


def win32_windows() -> list:
    """Every top-level window with the metadata we care about."""
    windows = []
    foreground = user32.GetForegroundWindow()
    enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def callback(hwnd, _lparam):
        try:
            pid = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title_len = user32.GetWindowTextLengthW(hwnd)
            title_buf = ctypes.create_unicode_buffer(title_len + 1)
            user32.GetWindowTextW(hwnd, title_buf, title_len + 1)
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "pid": int(pid.value),
                    "exe": wkcommon.process_exe_name(int(pid.value)),
                    "title": title_buf.value,
                    "class": class_buf.value,
                    "rect": {
                        "left": rect.left,
                        "top": rect.top,
                        "width": rect.right - rect.left,
                        "height": rect.bottom - rect.top,
                    },
                    "visible": bool(user32.IsWindowVisible(hwnd)),
                    "minimized": bool(user32.IsIconic(hwnd)),
                    "foreground": int(hwnd) == int(foreground),
                }
            )
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return windows


def is_interesting(window: dict) -> bool:
    exe = (window["exe"] or "").lower()
    title = (window["title"] or "").lower()
    klass = (window["class"] or "").lower()
    if any(token in exe for token in INTERESTING_EXE):
        return True
    if any(token in title for token in
           ("wowkids", "\u7ba1\u7406\u5de5\u5177", "\u5fae\u4fe1", "wechat")):
        return True
    return any(token in klass for token in INTERESTING_CLASS)


def summarise(wrapper, max_nodes: int) -> dict:
    nodes = wkcommon.walk_described(wrapper, max_nodes)
    documents = [
        {
            "depth": node["depth"],
            "name": node["name"],
            "auto_id": node["automation_id"],
            "rect": node["rect"],
        }
        for node in nodes
        if node["control_type"] == "Document"
    ]
    names = []
    for node in nodes:
        if node["control_type"] != "Text":
            continue
        name = (node["name"] or "").strip()
        if not name or name in names:
            continue
        names.append(name)
        if len(names) >= 25:
            break
    joined = " ".join(node["name"] or "" for node in nodes)
    found = [
        english
        for english, chinese in wkcommon.CATEGORY_PAIRS
        if english.lower() in joined.lower() or chinese in joined
    ]
    submit = any(
        term.lower() in joined.lower() for term in wkcommon.SUBMIT_TERMS
    )
    return {
        "node_count": len(nodes),
        "documents": documents,
        "sample_text": names,
        "rating_headings_found": found,
        "submit_like_found": submit,
    }


def build_report(args) -> int:
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report_path = os.path.join(
        wkcommon.REPORTS, "windows_{}.txt".format(stamp))
    json_path = os.path.join(wkcommon.REPORTS, "windows_{}.json".format(stamp))

    out = []

    def emit(text: str = "") -> None:
        out.append(text)

    windows = win32_windows()
    desktop = Desktop(backend="uia")
    uia_by_handle = {}
    for wrapper in desktop.windows():
        try:
            uia_by_handle[int(wrapper.handle)] = wrapper
        except Exception:
            continue

    emit("Wowkids rating tool - window diagnostic (read-only)")
    emit("time: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    emit("top-level windows: {}".format(len(windows)))
    emit("")
    emit("== all visible top-level windows ==")
    for window in sorted(
            [w for w in windows if w["visible"]],
            key=lambda w: (w["exe"] or "").lower()):
        emit("{:<28} pid={:<7} class={!r:<26} rect={},{} {}x{} min={} fg={} title={!r}".format(
            (window["exe"] or "?")[:28], window["pid"], window["class"][:26],
            window["rect"]["left"], window["rect"]["top"],
            window["rect"]["width"], window["rect"]["height"],
            window["minimized"], window["foreground"], window["title"][:50]))

    emit("")
    emit("== WeChat-related windows, UI Automation content ==")
    interesting = [w for w in windows if is_interesting(w)]
    summaries = []
    for window in interesting:
        emit("")
        emit("--- {} pid={} title={!r}".format(
            window["exe"], window["pid"], window["title"]))
        emit("    class={!r} rect={},{} {}x{} minimised={} visible={} foreground={}".format(
            window["class"], window["rect"]["left"], window["rect"]["top"],
            window["rect"]["width"], window["rect"]["height"],
            window["minimized"], window["visible"], window["foreground"]))
        wrapper = uia_by_handle.get(window["hwnd"])
        if wrapper is None:
            emit("    UIA: not exposed as a top-level UIA window")
            summaries.append({"window": window, "uia": None})
            continue
        try:
            summary = summarise(wrapper, args.max_nodes)
        except Exception as exc:
            emit("    UIA walk failed: {}".format(exc))
            summaries.append({"window": window, "uia": {"error": str(exc)}})
            continue
        emit("    nodes={} documents={} headings={} submit_like={}".format(
            summary["node_count"], len(summary["documents"]),
            summary["rating_headings_found"], summary["submit_like_found"]))
        for doc in summary["documents"]:
            emit("      document depth={} name={!r} auto_id={!r} rect={}".format(
                doc["depth"], doc["name"], doc["auto_id"], doc["rect"]))
        emit("      sample text: {}".format(" | ".join(summary["sample_text"][:12])))
        summaries.append({"window": window, "uia": summary})

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "windows": summaries}, fh, ensure_ascii=False, indent=1)

    print("windows listed   : {}".format(len(windows)))
    print("wechat-like      : {}".format(len(interesting)))
    for item in summaries:
        uia = item.get("uia") or {}
        print("  {} pid={} nodes={} headings={} title={!r}".format(
            item["window"]["exe"], item["window"]["pid"],
            uia.get("node_count", "-"), uia.get("rating_headings_found", "-"),
            item["window"]["title"][:40]))
    print("REPORT: {}".format(report_path))
    print("JSON  : {}".format(json_path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Window diagnostic")
    parser.add_argument("--max-nodes", type=int, default=2500)
    return build_report(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())