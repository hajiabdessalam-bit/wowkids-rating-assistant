"""Stage 2: read-only detector for a student's rating page.

Answers, without touching anything:

  * is the mini program showing a rating page (all five headings)?
  * are the headings in the expected top-to-bottom order?
  * for each heading, which nodes sit in its section band, grouped into rows?
  * do those nodes expose UI Automation patterns, or are they plain text that
    will need a window-relative click?

Exit codes: 0 = mapped, 2 = no window, 3 = not a rating page (then it lists the
page frames it *can* see, so we know which page is actually open).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon  # noqa: E402


def rect_text(rect) -> str:
    if not rect:
        return "-"
    return "{},{} {}x{}".format(
        rect["left"], rect["top"], rect["width"], rect["height"])


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
    report_path = os.path.join(
        wkcommon.REPORTS, "rating_page_{}.txt".format(stamp))
    json_path = os.path.join(
        wkcommon.REPORTS, "rating_page_{}.json".format(stamp))

    out = []

    def emit(text: str = "") -> None:
        out.append(text)

    emit("Wowkids rating tool - Stage 2 rating page detector (read-only)")
    emit("time: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    emit("dpi awareness: {}".format(dpi_mode))
    emit("")

    if target is None:
        emit("REFUSING: {}".format(reason))
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        print("No usable Wowkids window: {}".format(reason))
        print("REPORT: {}".format(report_path))
        return 2

    wrapper = target["wrapper"]
    note = wkcommon.restore_window(wrapper)
    window_rect = wkcommon.window_rectangle(wrapper)
    minimized = wkcommon.is_minimized(wrapper)

    emit("window   : pid={} title={!r}".format(target["pid"], target["title"]))
    emit("rect     : {} ({})".format(rect_text(window_rect), note))
    if minimized:
        emit("WARNING  : window still minimised; geometry may be unreliable")
    emit("")

    nodes = wkcommon.walk_described(wrapper, args.max_nodes)
    mapping = wkcommon.map_rating_sections(nodes)

    if mapping["missing"]:
        emit("NOT A RATING PAGE - missing headings: {}".format(
            ", ".join(mapping["missing"])))
        emit("")
        emit("== page frames that ARE present ==")
        for frame in wkcommon.frame_inventory(nodes):
            emit("- name={!r} rect={} nodes={}".format(
                frame["name"], rect_text(frame["rect"]), frame["node_count"]))
            if frame["texts"]:
                emit("    text: {}".format(" | ".join(frame["texts"][:14])))
        emit("")
        emit("Open a student's rating page (five categories + stars) and run again.")
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        print("Not a rating page. Missing: {}".format(", ".join(mapping["missing"])))
        print("REPORT: {}".format(report_path))
        return 3

    sections = mapping["sections"]
    emit("== rating page detected ==")
    emit("headings found : 5/5")
    emit("expected order : {}".format("YES" if mapping["order_ok"] else "NO"))
    emit("nodes inspected: {}".format(len(nodes)))
    emit("")

    for section in sections:
        emit("-- {} / {}".format(section["english"], section["chinese"]))
        emit("   heading : {} {!r} rect={} patterns={}".format(
            section["heading"]["control_type"], section["heading"]["name"],
            rect_text(section["heading"]["rect"]),
            ",".join(section["heading"]["patterns"])))
        emit("   band    : y {} .. {}".format(
            int(section["band"][0]), int(section["band"][1])))
        if not section["rows"]:
            emit("   rows    : NONE")
            emit("")
            continue
        for row_index, row in enumerate(section["rows"]):
            emit("   row {} (y={:.0f}, {} items):".format(
                row_index, row["centre_y"], len(row["items"])))
            for item_index, item in enumerate(row["items"]):
                emit("      [{}] {} name={!r} rect={} patterns={}".format(
                    item_index, item["control_type"], item["name"][:40],
                    rect_text(item["rect"]), ",".join(item["patterns"])))
        emit("")

    five_each = []
    patternful = False
    for section in sections:
        best = max(section["rows"], key=lambda r: len(r["items"])) if section["rows"] else None
        five_each.append(bool(best and len(best["items"]) >= 5))
        if best and any(
                set(item["patterns"]) & {"invoke", "selection_item", "toggle", "selection"}
                for item in best["items"]):
            patternful = True

    emit("== verdict ==")
    emit("rating page visible       : YES")
    emit("five headings in order    : {}".format("YES" if mapping["order_ok"] else "NO"))
    emit("a 5-option row per section: {}".format(
        "YES" if all(five_each) else "NO ({})".format(", ".join(
            section["english"] for section, ok in zip(sections, five_each) if not ok))))
    emit("options expose patterns   : {}".format(
        "YES" if patternful else "NO - needs window-relative click"))

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "window_rect": window_rect,
        "window_minimized": minimized,
        "order_ok": mapping["order_ok"],
        "node_count": len(nodes),
        "frames": wkcommon.frame_inventory(nodes),
        "sections": [],
    }
    for section in sections:
        payload["sections"].append(
            {
                "english": section["english"],
                "chinese": section["chinese"],
                "heading": section["heading"],
                "band": list(section["band"]),
                "rows": [
                    {
                        "centre_y": row["centre_y"],
                        "items": wkcommon.without_wrappers(row["items"]),
                    }
                    for row in section["rows"]
                ],
            }
        )

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print("rating page detected: YES (headings 5/5, order {})".format(
        "ok" if mapping["order_ok"] else "UNEXPECTED"))
    print("sections with a 5-item row: {}/5".format(sum(1 for ok in five_each if ok)))
    print("REPORT: {}".format(report_path))
    print("JSON  : {}".format(json_path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 2 rating page detector")
    parser.add_argument("--max-nodes", type=int, default=12000)
    return build_report(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())