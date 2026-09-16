"""Stage 3: fill the five ratings for ONE student and stop before Submit.

Safety model
------------
* The script only clicks the centres of rating options it mapped from the live
  UI Automation tree. There is no Submit code path in this file at all.
* Every click point must fall inside the live window rectangle and inside the
  band of the section it belongs to, otherwise the run aborts.
* Geometry is re-read from the tree for every step; nothing is stored between
  runs and no absolute coordinate is hard-coded.
* ESC aborts immediately, checked before and after every click.
* The run aborts if: fewer than five headings, headings out of order, a section
  without a clean five-option row, a missing/zero rectangle, an off-window
  option, or any change of page identity.

Usage
-----
    python fill_one_student.py --student DemoStudent --scores 4,4,4,3,5 --dry-run
    python fill_one_student.py --student DemoStudent --scores 4,4,4,3,5
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon  # noqa: E402

TEST_STUDENT = "DemoStudent"
TEST_SCORES = [4, 4, 4, 3, 5]
ABORT_KEY = 0x1B  # ESC
EXPECTED_TITLE = "Wowkids\u7ba1\u7406\u5de5\u5177"


def esc_pressed() -> bool:
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(ABORT_KEY) & 0x8000)
    except Exception:
        return False


def cursor_pos():
    try:
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y
    except Exception:
        return None


def rect_text(rect) -> str:
    if not rect:
        return "-"
    return "{},{} {}x{}".format(
        rect["left"], rect["top"], rect["width"], rect["height"])


def resolve_click_point(window_rect, element_rect):
    """Centre of ``element_rect`` in screen pixels, plus which space it was in.

    UI Automation normally reports element rectangles in screen pixels; some
    Chromium builds report them relative to the window. We detect the case per
    element and record it, so nothing is assumed.
    """
    if not window_rect or not element_rect:
        return None, None, "missing-rect"
    centre_x = element_rect["left"] + element_rect["width"] // 2
    centre_y = element_rect["top"] + element_rect["height"] // 2
    looks_relative = (
        0 <= centre_x <= window_rect["width"]
        and 0 <= centre_y <= window_rect["height"]
    )
    if looks_relative:
        return (
            window_rect["left"] + centre_x,
            window_rect["top"] + centre_y,
            "window-relative",
        )
    return centre_x, centre_y, "screen-pixels"


def click_point_for(window_rect, element_rect):
    """``(point_or_None, mode)`` where ``point`` is an ``(x, y)`` tuple."""
    x, y, mode = resolve_click_point(window_rect, element_rect)
    if x is None:
        return None, mode
    return (x, y), mode


def inside_window(window_rect, point, margin: int = 2) -> bool:
    if not window_rect or not point:
        return False
    x, y = point
    return (
        window_rect["left"] + margin
        <= x
        <= window_rect["left"] + window_rect["width"] - margin
        and window_rect["top"] + margin
        <= y
        <= window_rect["top"] + window_rect["height"] - margin
    )


def pick_option_row(section):
    """The cleanest five-option row in a section, or ``(None, reason)``."""
    if not section["rows"]:
        return None, "no nodes inside the section band"
    best = max(section["rows"], key=lambda row: len(row["items"]))
    items = best["items"]
    if len(items) == 5:
        return items, ""
    if len(items) < 5:
        return None, "only {} option-sized nodes in the row".format(len(items))
    widths = sorted(item["rect"]["width"] for item in items)
    heights = sorted(item["rect"]["height"] for item in items)
    median_w = widths[len(widths) // 2]
    median_h = heights[len(heights) // 2]
    filtered = [
        item
        for item in items
        if abs(item["rect"]["width"] - median_w) <= 4
        and abs(item["rect"]["height"] - median_h) <= 4
    ]
    if len(filtered) == 5:
        return filtered, "filtered {} size outliers".format(len(items) - 5)
    return None, "row has {} candidates, not an unambiguous five".format(len(items))


def section_snapshot(section) -> dict:
    items = []
    for row in section["rows"]:
        for item in row["items"]:
            items.append(
                {
                    "name": item["name"],
                    "type": item["control_type"],
                    "rect": item["rect"],
                    "patterns": sorted(item["patterns"]),
                }
            )
    return {"items": items}


def layout_of(nodes, section) -> str:
    """How this section currently presents its options.

    Uses the same measured definition as the probe: only five stacked rows with
    increasing Y and aligned X count as the accordion ("vertical") layout.
    """
    score = wkcommon.detect_score_rows(nodes, section)
    if score["layout"] == "vertical":
        return "vertical"
    if score["layout"] == "horizontal-rejected":
        return "horizontal"
    return "collapsed"


def run(args) -> int:
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop, mouse

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    os.makedirs(wkcommon.LOGS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report_path = os.path.join(wkcommon.REPORTS, "fill_{}.txt".format(stamp))
    json_path = os.path.join(wkcommon.REPORTS, "fill_{}.json".format(stamp))
    log = wkcommon.Logger(os.path.join(wkcommon.LOGS, "fill_{}.log".format(stamp)))
    processed_path = os.path.join(wkcommon.LOGS, "processed.csv")

    scores = args.scores
    if len(scores) != 5 or any(not 1 <= score <= 5 for score in scores):
        log("ABORT: scores must be five values in 1..5, got {}".format(scores))
        return 4

    plan = []
    results = []

    def finish(code, note):
        payload = {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "student": args.student,
            "scores": scores,
            "dry_run": bool(args.dry_run),
            "verdict": note,
            "plan": plan,
            "results": results,
        }
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("Stage 3 fill report - {}\n".format(note))
            fh.write("student: {} | scores: {} | dry_run: {}\n\n".format(
                args.student, scores, bool(args.dry_run)))
            for entry in plan:
                fh.write("PLAN  {:<24} score={} option={} rect={} point={} ({})\n".format(
                    entry["category"], entry["score"], entry["option_index"],
                    rect_text(entry["rect"]), entry["point"], entry["point_mode"]))
            for entry in results:
                fh.write("CLICK {:<24} clicked={} cursor_ok={} page_ok={} changed={} {}\n".format(
                    entry["category"], entry["point"], entry["cursor_ok"],
                    entry["page_ok"], entry["changed"], entry["notes"]))
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        log("verdict: {}".format(note))
        log("report: {}".format(report_path))
        print("")
        print("VERDICT: {}".format(note))
        print("REPORT : {}".format(report_path))
        print("JSON   : {}".format(json_path))
        log.close()
        return code

    desktop = Desktop(backend="uia")
    candidates = wkcommon.wowkids_windows(desktop)
    hinted = [
        item
        for item in candidates
        if any(hint.lower() in item["title"].lower()
               for hint in wkcommon.WINDOW_TITLE_HINTS)
    ]
    if len(hinted) != 1:
        log("ABORT: expected exactly one Wowkids window, got {} of {} windows".format(
            len(hinted), len(candidates)))
        return finish(2, "ABORTED: window not identified")

    target = hinted[0]
    wrapper = target["wrapper"]
    log("window: pid={} title={!r}".format(target["pid"], target["title"]))
    if target["title"].strip() != EXPECTED_TITLE:
        log("note: window title {!r} differs from {!r}".format(
            target["title"], EXPECTED_TITLE))

    note = wkcommon.restore_window(wrapper)
    window_rect = wkcommon.window_rectangle(wrapper)
    log("window rect {} ({})".format(rect_text(window_rect), note))
    if not window_rect or window_rect["width"] <= 0 or window_rect["height"] <= 0:
        log("ABORT: window has no usable geometry")
        return finish(5, "ABORTED: no window geometry")

    nodes = wkcommon.walk_described(wrapper, args.max_nodes)
    mapping = wkcommon.map_rating_sections(nodes)
    if mapping["missing"]:
        log("ABORT: not a rating page, missing {}".format(mapping["missing"]))
        for frame in wkcommon.frame_inventory(nodes):
            log("  frame {!r} rect={} nodes={} sample={}".format(
                frame["name"], rect_text(frame["rect"]), frame["node_count"],
                " | ".join(frame["texts"][:6])))
        return finish(3, "ABORTED: five rating headings not found")
    if not mapping["order_ok"]:
        log("ABORT: headings are not in the expected top-to-bottom order")
        return finish(3, "ABORTED: heading order unexpected")
    log("rating page detected: 5/5 headings, order ok")

    layouts = {section["english"]: layout_of(nodes, section)
               for section in mapping["sections"]}
    log("section layouts: {}".format(layouts))
    unsupported = {
        name: layout for name, layout in layouts.items() if layout != "horizontal"
    }
    if unsupported:
        log("ABORT: accordion layout not wired up yet - no clicks performed.")
        log("       run run_probe.bat first so the expansion step can be mapped.")
        return finish(3, "ABORTED: accordion layout not implemented yet ({})".format(
            ", ".join("{}={}".format(name, layout)
                      for name, layout in unsupported.items())))

    for section, score in zip(mapping["sections"], scores):
        row, row_note = pick_option_row(section)
        if row is None:
            log("ABORT: section {} - {}".format(section["english"], row_note))
            return finish(3, "ABORTED: {} has no clean 5-option row".format(
                section["english"]))
        item = row[score - 1]
        point, mode = click_point_for(window_rect, item["rect"])
        if point is None:
            log("ABORT: {} option {} has no rectangle".format(
                section["english"], score))
            return finish(5, "ABORTED: missing option rectangle")
        if not inside_window(window_rect, point):
            log("ABORT: {} option {} would click outside the window ({} {})".format(
                section["english"], score, point, mode))
            return finish(5, "ABORTED: option outside window")
        band = section["band"]
        if not (band[0] - 2 <= item["rect"]["top"] <= band[1] + 20):
            log("ABORT: {} option {} sits outside its section band".format(
                section["english"], score))
            return finish(5, "ABORTED: option outside its section")
        plan.append(
            {
                "category": section["english"],
                "chinese": section["chinese"],
                "score": score,
                "option_index": score - 1,
                "control_type": item["control_type"],
                "name": item["name"],
                "rect": item["rect"],
                "point": [point[0], point[1]],
                "point_mode": mode,
                "row_note": row_note,
            }
        )
        log("PLAN  {:<24} score={} -> option[{}] {} {!r} rect={} click={} ({}) {}".format(
            section["english"], score, score - 1, item["control_type"],
            item["name"][:24], rect_text(item["rect"]), point, mode,
            ("[" + row_note + "]") if row_note else ""))

    if not args.force_dup and os.path.exists(processed_path):
        with open(processed_path, encoding="utf-8") as fh:
            for line in fh:
                parts = [part.strip() for part in line.split(",")]
                if (
                    len(parts) >= 3
                    and parts[1].lower() == args.student.lower()
                    and parts[2] in ("filled", "submitted")
                ):
                    log("ABORT: {} was already {} at {} (use --force-dup)".format(
                        args.student, parts[2], parts[0]))
                    return finish(6, "ABORTED: student already processed")

    if args.dry_run:
        log("dry run complete: plan printed, nothing clicked")
        with open(processed_path, "a", encoding="utf-8") as fh:
            fh.write("{},{},planned,{}\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), args.student,
                ",".join(str(score) for score in scores)))
        return finish(0, "PLAN ONLY (dry run)")

    verdict = "COMPLETED"
    for step, entry in enumerate(plan):
        if esc_pressed():
            verdict = "ABORTED: ESC pressed before {}".format(entry["category"])
            break

        nodes = wkcommon.walk_described(wrapper, args.max_nodes)
        fresh = wkcommon.map_rating_sections(nodes)
        if fresh["missing"] or not fresh["order_ok"]:
            verdict = "ABORTED: page identity changed before {}".format(entry["category"])
            break
        section = fresh["sections"][step]
        row, row_note = pick_option_row(section)
        if row is None:
            verdict = "ABORTED: {} lost its 5-option row ({})".format(
                entry["category"], row_note)
            break
        item = row[entry["score"] - 1]
        point, mode = click_point_for(window_rect, item["rect"])
        if point is None or not inside_window(window_rect, point):
            verdict = "ABORTED: {} option moved off-window".format(entry["category"])
            break

        before = section_snapshot(section)
        result = {
            "category": entry["category"],
            "score": entry["score"],
            "point": [point[0], point[1]],
            "point_mode": mode,
            "rect": item["rect"],
            "cursor_ok": False,
            "page_ok": False,
            "changed": False,
            "notes": "",
        }
        log("click {:<24} score={} at {} ({})".format(
            entry["category"], entry["score"], point, mode))
        try:
            mouse.click(button="left", coords=(point[0], point[1]))
        except Exception as exc:
            log("ABORT: click failed: {}".format(exc))
            results.append(result)
            verdict = "ABORTED: click failed on {}".format(entry["category"])
            break

        time.sleep(args.settle)
        after_cursor = cursor_pos()
        if after_cursor:
            result["cursor"] = list(after_cursor)
            result["cursor_ok"] = (
                abs(after_cursor[0] - point[0]) <= 3
                and abs(after_cursor[1] - point[1]) <= 3
            )

        if esc_pressed():
            results.append(result)
            verdict = "ABORTED: ESC pressed after {}".format(entry["category"])
            break

        nodes_after = wkcommon.walk_described(wrapper, args.max_nodes)
        after_map = wkcommon.map_rating_sections(nodes_after)
        if after_map["missing"] or not after_map["order_ok"]:
            result["notes"] = "page identity changed after the click"
            results.append(result)
            verdict = "ABORTED: page changed after {}".format(entry["category"])
            break
        result["page_ok"] = True
        section_after = after_map["sections"][step]
        result["changed"] = before != section_snapshot(section_after)
        row_after, _ = pick_option_row(section_after)
        result["row_ok_after"] = row_after is not None
        if row_after:
            result["row_names_after"] = [item["name"] for item in row_after]
        if not result["changed"]:
            result["notes"] = "section unchanged in the tree (may still be selected visually)"
        results.append(result)
        log("verify {:<24} page_ok={} changed={} cursor_ok={} {}".format(
            entry["category"], result["page_ok"], result["changed"],
            result["cursor_ok"], result["notes"]))

    with open(processed_path, "a", encoding="utf-8") as fh:
        status = "filled" if verdict == "COMPLETED" else "partial"
        fh.write("{},{},{},{}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), args.student, status,
            ",".join(str(score) for score in scores)))

    return finish(0 if verdict == "COMPLETED" else 7, verdict)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 3 single-student fill")
    parser.add_argument("--student", default=TEST_STUDENT)
    parser.add_argument(
        "--scores",
        default=",".join(str(score) for score in TEST_SCORES),
        help="five comma separated values in 1..5 "
             "(Making,Problem,Theory,Creative,Interpersonal)",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="build and print the plan, click nothing")
    parser.add_argument("--force-dup", action="store_true",
                        help="allow a student already listed in logs/processed.csv")
    parser.add_argument("--settle", type=float, default=0.6,
                        help="seconds to wait after each click")
    parser.add_argument("--max-nodes", type=int, default=12000)
    args = parser.parse_args()
    args.scores = [
        int(part) for part in str(args.scores).replace(" ", "").split(",") if part
    ]
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())