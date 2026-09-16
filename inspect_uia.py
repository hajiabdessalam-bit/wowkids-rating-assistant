"""Stage 1: inspect the Wowkids mini program through Windows UI Automation.

Read-only. Finds the mini program window, un-minimises it if needed, dumps the
whole UI Automation tree into ``reports/`` and prints a short verdict:

  * are the five rating headings exposed?
  * are the star choices exposed as real controls?
  * is the Submit button exposed?
  * which containers can be scrolled through UI Automation?

Nothing is clicked, nothing is typed, no student data is changed.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon  # noqa: E402


def screen_metrics() -> dict:
    user32 = ctypes.windll.user32
    return {
        "screen": (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)),
        "virtual_screen": (user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)),
    }


def window_dpi(handle) -> int:
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(handle))
    except Exception:
        return 0


def foreground_window_title() -> str:
    user32 = ctypes.windll.user32
    try:
        handle = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(handle)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def contains_any(text: str, terms) -> bool:
    haystack = (text or "").lower()
    return any(term.lower() in haystack for term in terms)


def is_star_like(node: dict) -> bool:
    name = (node["name"] or "").strip()
    if name in {"1", "2", "3", "4", "5"}:
        return True
    lowered = name.lower()
    return "star" in lowered or "\u661f" in name


def rect_text(node: dict) -> str:
    rect = node["rect"]
    if not rect:
        return "-"
    return "{},{} {}x{}".format(
        rect["left"], rect["top"], rect["width"], rect["height"])


def one_line(node: dict) -> str:
    value = node.get("value")
    value_text = value[:60] if isinstance(value, str) else value
    return "{}{} | name={!r} | auto_id={!r} | class={!r} | rect={} | value={!r} | patterns={}".format(
        "  " * node["depth"],
        node["control_type"],
        node["name"][:120],
        node["automation_id"][:60],
        node["class_name"][:60],
        rect_text(node),
        value_text,
        ",".join(node["patterns"]),
    )


def build_report(args) -> int:
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    dpi_mode = wkcommon.set_dpi_awareness()

    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    candidates = wkcommon.wowkids_windows(desktop)
    target, reason = wkcommon.select_wowkids_window(candidates)

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report_path = os.path.join(wkcommon.REPORTS, "uia_dump_{}.txt".format(stamp))
    json_path = os.path.join(wkcommon.REPORTS, "uia_dump_{}.json".format(stamp))

    out_lines = []

    def emit(text: str = "") -> None:
        out_lines.append(text)

    emit("Wowkids rating tool - Stage 1 UI Automation inspection")
    emit("time: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    emit("python: {}".format(sys.version.split()[0]))
    emit("dpi awareness: {}".format(dpi_mode))
    emit("screen metrics: {}".format(screen_metrics()))
    emit("foreground window: {!r}".format(foreground_window_title()))
    emit("")

    emit("== WeChat mini program windows found: {} ==".format(len(candidates)))
    for index, item in enumerate(candidates):
        emit("{}. pid={} exe={!r} title={!r} class={!r} framework={!r}".format(
            index, item["pid"], item["exe"], item["title"], item["class_name"],
            item["framework"]))
        emit("   rect={} minimized={}".format(item["rect"], item["minimized"]))
    emit("")

    if target is None:
        emit("REFUSING TO CONTINUE: {}".format(reason))
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out_lines) + "\n")
        print("No usable Wowkids window: {}".format(reason))
        print("REPORT: {}".format(report_path))
        return 2

    wrapper = target["wrapper"]
    note = wkcommon.restore_window(wrapper)
    target["rect"] = wkcommon.window_rectangle(wrapper)
    target["minimized"] = wkcommon.is_minimized(wrapper)

    emit("== chosen window ==")
    emit("pid={} exe={!r}".format(target["pid"], target["exe"]))
    emit("title={!r}".format(target["title"]))
    emit("class={!r} framework={!r}".format(target["class_name"], target["framework"]))
    emit("rect={}".format(target["rect"]))
    emit("minimized={} (window handling: {})".format(target["minimized"], note))
    emit("window dpi={}".format(window_dpi(wrapper.handle)))
    emit("")

    nodes = wkcommon.walk_described(wrapper, args.max_nodes)
    emit("== UI Automation tree: {} nodes (limit {}) ==".format(
        len(nodes), args.max_nodes))
    emit("")
    for node in nodes:
        emit(one_line(node))

    emit("")
    emit("== page frames ==")
    for node in nodes:
        if node["control_type"] == "Document":
            emit("{}{} | name={!r} | auto_id={!r} | rect={} | value={!r}".format(
                "  " * node["depth"], node["control_type"], node["name"],
                node["automation_id"], rect_text(node), node["value"]))

    emit("")
    emit("== rating heading candidates ==")
    heading_hits = [
        node for node in nodes
        if contains_any(node["name"], wkcommon.CATEGORY_TERMS + wkcommon.CATEGORY_SHORT)
    ]
    for node in heading_hits:
        emit("{}{} | name={!r} | rect={} | patterns={}".format(
            "  " * node["depth"], node["control_type"], node["name"],
            rect_text(node), ",".join(node["patterns"])))
    if not heading_hits:
        emit("(none found on the currently visible page)")

    emit("")
    emit("== star / score control candidates ==")
    star_hits = [node for node in nodes if is_star_like(node)]
    for node in star_hits[:200]:
        emit("{}{} | name={!r} | rect={} | patterns={}".format(
            "  " * node["depth"], node["control_type"], node["name"],
            rect_text(node), ",".join(node["patterns"])))
    if not star_hits:
        emit("(no 1-5 / star-like controls exposed)")

    emit("")
    emit("== submit button candidates ==")
    submit_hits = [
        node for node in nodes
        if contains_any(node["name"], wkcommon.SUBMIT_TERMS)
    ]
    for node in submit_hits[:60]:
        emit("{}{} | name={!r} | rect={} | patterns={}".format(
            "  " * node["depth"], node["control_type"], node["name"],
            rect_text(node), ",".join(node["patterns"])))
    if not submit_hits:
        emit("(no submit-like element exposed)")

    emit("")
    emit("== scrollable containers ==")
    scroll_hits = [
        node for node in nodes
        if any(p in node["patterns"] for p in ("scroll", "scroll_item"))
    ]
    for node in scroll_hits[:80]:
        emit("{}{} | name={!r} | rect={} | patterns={}".format(
            "  " * node["depth"], node["control_type"], node["name"][:80],
            rect_text(node), ",".join(node["patterns"])))
    if not scroll_hits:
        emit("(no scrollable element exposed)")

    clickable_stars = [
        node for node in star_hits
        if node["control_type"] in wkcommon.INTERACTIVE_TYPES
    ]
    emit("")
    emit("== verdict ==")
    emit("headings exposed      : {}".format(
        "YES ({} hits)".format(len(heading_hits)) if heading_hits else "NO"))
    emit("star controls exposed : {}".format(
        "YES ({} hits)".format(len(clickable_stars)) if clickable_stars
        else "NO (only text/images)" if star_hits else "NO"))
    emit("submit exposed        : {}".format("YES" if submit_hits else "NO"))
    emit("scrolling via UIA     : {}".format("YES" if scroll_hits else "NO"))

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "window": {k: v for k, v in target.items() if k != "wrapper"},
                "node_count": len(nodes),
                "nodes": wkcommon.without_wrappers(nodes),
            },
            fh,
            ensure_ascii=False,
            indent=1,
        )

    print("nodes inspected      : {}".format(len(nodes)))
    print("headings exposed     : {}".format(len(heading_hits)))
    print("star-like controls   : {}".format(len(star_hits)))
    print("submit candidates    : {}".format(len(submit_hits)))
    print("scrollable elements  : {}".format(len(scroll_hits)))
    print("REPORT: {}".format(report_path))
    print("JSON  : {}".format(json_path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1 UIA inspector")
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=8000,
        help="safety limit for how much of the tree is dumped",
    )
    return build_report(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())