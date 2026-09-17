from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon
import validate_context as ctx
from probe_sections import find_expand_arrow, arrow_click_ok, centre
from visual_score_rows import detect_visual_score_rows

TEST_SCORE = 4


def _visible(nodes, client_rect):
    return [n for n in nodes if wkcommon.node_is_visibly_present(n, client_rect)[0]]


def _visual_headings(nodes, client_rect, screenshot_path, window_rect):
    return ctx._select_visual_heading_nodes(
        _visible(nodes, client_rect), screenshot_path, window_rect
    )


def _making_section(selected):
    making = selected.get("Making Skills")
    problem = selected.get("Problem Solving")
    if not making or not problem or not making.get("rect") or not problem.get("rect"):
        return None
    m = making["rect"]
    p = problem["rect"]
    if p["top"] <= m["top"] + m["height"]:
        return None
    return {
        "english": "Making Skills",
        "heading": {"rect": m},
        "band": (m["top"] + m["height"] - 4, p["top"] - 4),
        "band_bottom": p["top"] - 4,
    }


def _crop_diff(path_a, path_b, screen_point, window_rect, radius=18):
    try:
        from PIL import Image
        a = Image.open(path_a).convert("RGB")
        b = Image.open(path_b).convert("RGB")
    except Exception as exc:
        return {"ok": False, "reason": "cannot open screenshots: {}".format(exc)}

    x = int(screen_point[0] - window_rect["left"])
    y = int(screen_point[1] - window_rect["top"])
    box = (
        max(0, x - radius), max(0, y - radius),
        min(a.width, x + radius + 1), min(a.height, y + radius + 1),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return {"ok": False, "reason": "invalid comparison crop"}

    changed = 0
    strong = 0
    total_delta = 0
    total = 0
    for pa, pb in zip(a.crop(box).getdata(), b.crop(box).getdata()):
        d = abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2])
        total_delta += d
        total += 1
        if d >= 18:
            changed += 1
        if d >= 60:
            strong += 1

    mean_delta = (total_delta / float(total)) if total else 0.0
    ok = changed >= 10 and (strong >= 3 or mean_delta >= 1.5)
    return {
        "ok": ok,
        "changed_pixels": changed,
        "strong_pixels": strong,
        "mean_delta": round(mean_delta, 3),
        "box": box,
        "reason": "local radio area changed" if ok else "no reliable local radio change",
    }


def main() -> int:
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop, mouse

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    shots = os.path.join(wkcommon.REPORTS, "shots")
    os.makedirs(shots, exist_ok=True)
    stamp = wkcommon.timestamp()
    report_path = os.path.join(wkcommon.REPORTS, "select_one_{}.txt".format(stamp))
    json_path = os.path.join(wkcommon.REPORTS, "select_one_{}.json".format(stamp))
    lines = []

    def emit(text=""):
        lines.append(text)

    desktop = Desktop(backend="uia")
    target, reason = wkcommon.select_wowkids_window(wkcommon.wowkids_windows(desktop))
    if not target:
        print("ABORT: {}".format(reason))
        return 2

    wrapper = target["wrapper"]
    wkcommon.restore_window(wrapper)
    window_rect = wkcommon.window_rectangle(wrapper)
    client_rect = wkcommon.win32_client_rect(wrapper)
    if not window_rect or not client_rect:
        print("ABORT: WOWKIDS window geometry unavailable.")
        return 3

    pre = os.path.join(shots, "{}_select_pre.png".format(stamp))
    emit("pre shot: {}".format(wkcommon.capture_window(wrapper, pre)))
    nodes = wkcommon.walk_described(wrapper, 8000)
    visible = _visible(nodes, client_rect)
    page, reasons, _ = ctx.classify_live_page(visible, None, pre, window_rect)
    emit("page before: {}".format(page))
    for r in reasons:
        emit("  - {}".format(r))
    if page != "RATING_FORM":
        print("ABORT: current page is not a visually verified rating form. Nothing clicked.")
        return 4

    selected, _support = _visual_headings(nodes, client_rect, pre, window_rect)
    section = _making_section(selected)
    if not section:
        print("ABORT: Making Skills geometry not verified. Nothing clicked.")
        return 5

    rows = detect_visual_score_rows(
        pre, window_rect, section["heading"]["rect"], section["band_bottom"],
        client_rect, wkcommon.BOTTOM_EXCLUSION_BAND,
    )

    if rows["layout"] != "vertical-visual":
        arrow_rect, arrow_reason = find_expand_arrow(pre, window_rect, section)
        ok, guard_reason = arrow_click_ok(arrow_rect, section, client_rect)
        emit("arrow: {} {} / {}".format(arrow_rect, arrow_reason, guard_reason))
        if not ok:
            print("ABORT: safe Making Skills arrow was not verified. Nothing clicked.")
            return 6
        p = centre(arrow_rect)
        mouse.click(button="left", coords=(p[0], p[1]))
        emit("expanded Making Skills at {}".format(p))
        time.sleep(0.9)

    expanded = os.path.join(shots, "{}_select_expanded.png".format(stamp))
    emit("expanded shot: {}".format(wkcommon.capture_window(wrapper, expanded)))
    nodes2 = wkcommon.walk_described(wrapper, 8000)
    selected2, support2 = _visual_headings(nodes2, client_rect, expanded, window_rect)
    section2 = _making_section(selected2)
    if not section2 or not support2.get("Making Skills", {}).get("supported"):
        print("ABORT: Making Skills heading was not visually verified after expansion. No rating selected.")
        return 7

    rows2 = detect_visual_score_rows(
        expanded, window_rect, section2["heading"]["rect"], section2["band_bottom"],
        client_rect, wkcommon.BOTTOM_EXCLUSION_BAND,
    )
    emit("visual rows: {} ({})".format(rows2["layout"], rows2["reason"]))
    if rows2["layout"] != "vertical-visual" or len(rows2.get("rows", [])) != 5:
        print("ABORT: the 1..5 visual score rows were not verified. No rating selected.")
        return 8

    target_row = rows2["rows"][TEST_SCORE - 1]
    point = tuple(target_row["click_point"])
    safe_bottom = client_rect["top"] + client_rect["height"] - wkcommon.BOTTOM_EXCLUSION_BAND
    if not (client_rect["left"] <= point[0] < client_rect["left"] + client_rect["width"]
            and client_rect["top"] <= point[1] < safe_bottom):
        print("ABORT: score target is outside the safe client area. No rating selected.")
        return 9

    emit("selecting ONLY Making Skills score {} at {}".format(TEST_SCORE, point))
    mouse.click(button="left", coords=point)
    time.sleep(0.7)

    after = os.path.join(shots, "{}_select_after.png".format(stamp))
    emit("after shot: {}".format(wkcommon.capture_window(wrapper, after)))
    diff = _crop_diff(expanded, after, point, window_rect)
    emit("local visual verification: {}".format(diff))

    nodes3 = wkcommon.walk_described(wrapper, 8000)
    selected3, support3 = _visual_headings(nodes3, client_rect, after, window_rect)
    heading_ok = bool(support3.get("Making Skills", {}).get("supported"))
    emit("Making Skills still visible after click: {}".format(heading_ok))

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "score": TEST_SCORE,
        "click_point": list(point),
        "visual_change": diff,
        "heading_ok_after": heading_ok,
        "submitted": False,
        "submit_code_present": False,
    }
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    if diff["ok"] and heading_ok:
        print("SUCCESS: Making Skills score {} was selected and visually verified.".format(TEST_SCORE))
        print("Submit clicked: NO")
        print("No other category was changed by this test.")
        print("REPORT: {}".format(report_path))
        return 0

    print("The score click was sent, but visual verification was inconclusive.")
    print("Submit clicked: NO")
    print("REPORT: {}".format(report_path))
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
