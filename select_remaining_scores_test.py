from __future__ import annotations

import ctypes
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

ABORT_KEY = 0x1B  # ESC

CATEGORY_SCORES = [
    ("Problem Solving", 4),
    ("Theory & Application", 4),
    ("Creative Thinking", 3),
    ("Interpersonal Skills", 5),
]
CATEGORY_ORDER = [name for name, _cn in wkcommon.CATEGORY_PAIRS]


def esc_pressed():
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(ABORT_KEY) & 0x8000)
    except Exception:
        return False


def _visible(nodes, client_rect):
    return [
        node for node in nodes
        if wkcommon.node_is_visibly_present(node, client_rect)[0]
    ]


def _visual_headings(nodes, client_rect, screenshot_path, window_rect):
    return ctx._select_visual_heading_nodes(
        _visible(nodes, client_rect), screenshot_path, window_rect
    )


def _section_for(selected, category, client_rect):
    if category not in CATEGORY_ORDER:
        return None
    index = CATEGORY_ORDER.index(category)
    current = selected.get(category)
    if not current or not current.get("rect"):
        return None

    h = current["rect"]
    safe_bottom = (
        client_rect["top"] + client_rect["height"] - wkcommon.BOTTOM_EXCLUSION_BAND
    )
    if index + 1 < len(CATEGORY_ORDER):
        next_node = selected.get(CATEGORY_ORDER[index + 1])
        if not next_node or not next_node.get("rect"):
            return None
        next_top = next_node["rect"]["top"]
        if next_top <= h["top"] + h["height"]:
            return None
        band_bottom = next_top - 4
    else:
        band_bottom = safe_bottom - 4

    if band_bottom <= h["top"] + h["height"] + 30:
        return None

    return {
        "english": category,
        "heading": {"rect": h},
        "band": (h["top"] + h["height"] - 4, band_bottom),
        "band_bottom": band_bottom,
    }


def _park_mouse(mouse, window_rect):
    x = window_rect["left"] + window_rect["width"] - 26
    y = window_rect["top"] + 34
    try:
        mouse.move(coords=(x, y))
        time.sleep(0.15)
    except Exception:
        pass


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


def main():
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop, mouse

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    shots = os.path.join(wkcommon.REPORTS, "shots")
    os.makedirs(shots, exist_ok=True)
    stamp = wkcommon.timestamp()
    report_path = os.path.join(
        wkcommon.REPORTS, "remaining_scores_{}.txt".format(stamp)
    )
    json_path = os.path.join(
        wkcommon.REPORTS, "remaining_scores_{}.json".format(stamp)
    )
    lines = []
    results = []

    def emit(text=""):
        lines.append(text)

    def finish(code, message):
        payload = {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message": message,
            "tests": [{"category": c, "score": s} for c, s in CATEGORY_SCORES],
            "results": results,
            "submitted": False,
            "submit_code_present": False,
        }
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(message)
        print("Submit clicked: NO")
        print("REPORT: {}".format(report_path))
        return code

    desktop = Desktop(backend="uia")
    target, reason = wkcommon.select_wowkids_window(wkcommon.wowkids_windows(desktop))
    if not target:
        return finish(2, "ABORT: {}".format(reason))

    wrapper = target["wrapper"]
    wkcommon.restore_window(wrapper)
    window_rect = wkcommon.window_rectangle(wrapper)
    client_rect = wkcommon.win32_client_rect(wrapper)
    if not window_rect or not client_rect:
        return finish(3, "ABORT: WOWKIDS window geometry unavailable.")

    _park_mouse(mouse, window_rect)
    start_shot = os.path.join(shots, "{}_remaining_start.png".format(stamp))
    emit("start shot: {}".format(wkcommon.capture_window(wrapper, start_shot)))
    nodes = wkcommon.walk_described(wrapper, 8000)
    page, reasons, _ = ctx.classify_live_page(
        _visible(nodes, client_rect), None, start_shot, window_rect
    )
    emit("page at start: {}".format(page))
    for reason_text in reasons:
        emit("  - {}".format(reason_text))
    if page != "RATING_FORM":
        return finish(4, "ABORT: current page is not a visually verified rating form.")

    # The previous successful stage may have left Making Skills expanded.
    # Normalize it back to collapsed before touching the remaining categories.
    selected, _support = _visual_headings(
        nodes, client_rect, start_shot, window_rect
    )
    making = _section_for(selected, "Making Skills", client_rect)
    if making:
        making_rows = detect_visual_score_rows(
            start_shot, window_rect, making["heading"]["rect"],
            making["band_bottom"], client_rect, wkcommon.BOTTOM_EXCLUSION_BAND,
        )
        if making_rows["layout"] == "vertical-visual":
            arrow_rect, arrow_reason = find_expand_arrow(
                start_shot, window_rect, making
            )
            ok, guard_reason = arrow_click_ok(
                arrow_rect, making, client_rect
            )
            emit("normalize Making arrow: {} {} / {}".format(
                arrow_rect, arrow_reason, guard_reason
            ))
            if not ok:
                return finish(
                    5,
                    "ABORT: Making Skills is expanded but its collapse arrow was not safely verified.",
                )
            if esc_pressed():
                return finish(90, "ABORT: ESC pressed.")
            p = centre(arrow_rect)
            mouse.click(button="left", coords=(p[0], p[1]))
            time.sleep(0.7)
            emit("collapsed Making Skills at {}".format(p))

    for category, score in CATEGORY_SCORES:
        if esc_pressed():
            return finish(90, "ABORT: ESC pressed before {}.".format(category))

        _park_mouse(mouse, window_rect)
        pre = os.path.join(
            shots, "{}_{}_pre.png".format(
                stamp, CATEGORY_ORDER.index(category) + 1
            )
        )
        emit("")
        emit("== {} -> score {} ==".format(category, score))
        emit("pre shot: {}".format(wkcommon.capture_window(wrapper, pre)))
        nodes = wkcommon.walk_described(wrapper, 8000)
        selected, support = _visual_headings(
            nodes, client_rect, pre, window_rect
        )
        if not support.get(category, {}).get("supported"):
            return finish(
                6,
                "ABORT: {} heading was not visually verified.".format(category),
            )
        section = _section_for(selected, category, client_rect)
        if not section:
            return finish(
                7,
                "ABORT: {} section geometry could not be built safely.".format(category),
            )

        rows = detect_visual_score_rows(
            pre, window_rect, section["heading"]["rect"],
            section["band_bottom"], client_rect, wkcommon.BOTTOM_EXCLUSION_BAND,
        )
        if rows["layout"] != "vertical-visual":
            arrow_rect, arrow_reason = find_expand_arrow(
                pre, window_rect, section
            )
            ok, guard_reason = arrow_click_ok(
                arrow_rect, section, client_rect
            )
            emit("expand arrow: {} {} / {}".format(
                arrow_rect, arrow_reason, guard_reason
            ))
            if not ok:
                return finish(
                    8,
                    "ABORT: {} expand arrow was not safely verified.".format(category),
                )
            p = centre(arrow_rect)
            mouse.click(button="left", coords=(p[0], p[1]))
            emit("expanded {} at {}".format(category, p))
            time.sleep(0.8)

        _park_mouse(mouse, window_rect)
        expanded = os.path.join(
            shots, "{}_{}_expanded.png".format(
                stamp, CATEGORY_ORDER.index(category) + 1
            )
        )
        emit("expanded shot: {}".format(wkcommon.capture_window(wrapper, expanded)))
        nodes2 = wkcommon.walk_described(wrapper, 8000)
        selected2, support2 = _visual_headings(
            nodes2, client_rect, expanded, window_rect
        )
        if not support2.get(category, {}).get("supported"):
            return finish(
                9,
                "ABORT: {} heading disappeared after expansion.".format(category),
            )
        section2 = _section_for(selected2, category, client_rect)
        if not section2:
            return finish(
                10,
                "ABORT: {} expanded geometry was not verified.".format(category),
            )
        rows2 = detect_visual_score_rows(
            expanded, window_rect, section2["heading"]["rect"],
            section2["band_bottom"], client_rect, wkcommon.BOTTOM_EXCLUSION_BAND,
        )
        emit("visual rows: {} ({})".format(rows2["layout"], rows2["reason"]))
        if rows2["layout"] != "vertical-visual" or len(rows2.get("rows", [])) != 5:
            return finish(
                11,
                "ABORT: {} did not expose a verified 1..5 score stack.".format(category),
            )

        target_row = rows2["rows"][score - 1]
        point = tuple(target_row["click_point"])
        safe_bottom = (
            client_rect["top"] + client_rect["height"] - wkcommon.BOTTOM_EXCLUSION_BAND
        )
        if not (
            client_rect["left"] <= point[0] < client_rect["left"] + client_rect["width"]
            and client_rect["top"] <= point[1] < safe_bottom
        ):
            return finish(
                12,
                "ABORT: {} score target is outside the safe client area.".format(category),
            )

        if esc_pressed():
            return finish(90, "ABORT: ESC pressed before score click.")

        emit("clicking {} score {} at {}".format(category, score, point))
        mouse.click(button="left", coords=point)
        time.sleep(0.5)
        _park_mouse(mouse, window_rect)
        time.sleep(0.15)

        selected_shot = os.path.join(
            shots, "{}_{}_selected.png".format(
                stamp, CATEGORY_ORDER.index(category) + 1
            )
        )
        emit("selected shot: {}".format(
            wkcommon.capture_window(wrapper, selected_shot)
        ))
        diff = _crop_diff(expanded, selected_shot, point, window_rect)
        emit("local visual verification: {}".format(diff))
        if not diff.get("ok"):
            return finish(
                13,
                "ABORT: {} score click was not visually verified.".format(category),
            )

        # Collapse the just-completed section so every next category starts
        # from the same compact layout and remains on-screen.
        nodes3 = wkcommon.walk_described(wrapper, 8000)
        selected3, support3 = _visual_headings(
            nodes3, client_rect, selected_shot, window_rect
        )
        if not support3.get(category, {}).get("supported"):
            return finish(
                14,
                "ABORT: {} heading was not verified after selecting the score.".format(category),
            )
        section3 = _section_for(selected3, category, client_rect)
        if not section3:
            return finish(
                15,
                "ABORT: {} collapse geometry was not verified.".format(category),
            )
        arrow_rect, arrow_reason = find_expand_arrow(
            selected_shot, window_rect, section3
        )
        ok, guard_reason = arrow_click_ok(
            arrow_rect, section3, client_rect
        )
        emit("collapse arrow: {} {} / {}".format(
            arrow_rect, arrow_reason, guard_reason
        ))
        if not ok:
            return finish(
                16,
                "ABORT: {} collapse arrow was not safely verified.".format(category),
            )
        p = centre(arrow_rect)
        mouse.click(button="left", coords=(p[0], p[1]))
        time.sleep(0.65)

        _park_mouse(mouse, window_rect)
        collapsed = os.path.join(
            shots, "{}_{}_collapsed.png".format(
                stamp, CATEGORY_ORDER.index(category) + 1
            )
        )
        emit("collapsed shot: {}".format(
            wkcommon.capture_window(wrapper, collapsed)
        ))
        nodes4 = wkcommon.walk_described(wrapper, 8000)
        selected4, support4 = _visual_headings(
            nodes4, client_rect, collapsed, window_rect
        )
        if not support4.get(category, {}).get("supported"):
            return finish(
                17,
                "ABORT: {} heading was not verified after collapse.".format(category),
            )
        section4 = _section_for(selected4, category, client_rect)
        if not section4:
            return finish(
                18,
                "ABORT: {} compact geometry was not restored.".format(category),
            )
        rows4 = detect_visual_score_rows(
            collapsed, window_rect, section4["heading"]["rect"],
            section4["band_bottom"], client_rect, wkcommon.BOTTOM_EXCLUSION_BAND,
        )
        if rows4["layout"] == "vertical-visual":
            return finish(
                19,
                "ABORT: {} still appears expanded after collapse.".format(category),
            )

        results.append({
            "category": category,
            "score": score,
            "click_point": list(point),
            "visual_change": diff,
            "collapsed_after": True,
        })
        emit("PASS: {} score {} selected and section collapsed.".format(
            category, score
        ))

    return finish(
        0,
        "SUCCESS: remaining four test ratings were selected and visually verified.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
