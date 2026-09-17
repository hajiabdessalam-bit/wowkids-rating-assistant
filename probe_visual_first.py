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


def _visible(nodes, client_rect):
    return [
        node for node in nodes
        if wkcommon.node_is_visibly_present(node, client_rect)[0]
    ]


def _visual_headings(nodes, client_rect, screenshot_path, window_rect):
    visible = _visible(nodes, client_rect)
    return ctx._select_visual_heading_nodes(visible, screenshot_path, window_rect)


def _making_section(selected, client_rect):
    making = selected.get("Making Skills")
    problem = selected.get("Problem Solving")
    if not making or not making.get("rect") or not problem or not problem.get("rect"):
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


def main() -> int:
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop, mouse

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    shots = os.path.join(wkcommon.REPORTS, "shots")
    os.makedirs(shots, exist_ok=True)
    stamp = wkcommon.timestamp()
    text_path = os.path.join(wkcommon.REPORTS, "visual_probe_{}.txt".format(stamp))
    json_path = os.path.join(wkcommon.REPORTS, "visual_probe_{}.json".format(stamp))
    lines = []

    def emit(text=""):
        lines.append(text)

    emit("WOWKIDS visual score-row probe - Making Skills only")
    emit("Allowed: expand Making Skills once. Forbidden: select rating, Submit, Post All.")
    emit("")

    desktop = Desktop(backend="uia")
    windows = wkcommon.wowkids_windows(desktop)
    target, reason = wkcommon.select_wowkids_window(windows)
    if not target:
        emit("ABORT: {}".format(reason))
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("No WOWKIDS window identified. Nothing clicked.")
        print("REPORT: {}".format(text_path))
        return 2

    wrapper = target["wrapper"]
    wkcommon.restore_window(wrapper)
    window_rect = wkcommon.window_rectangle(wrapper)
    client_rect = wkcommon.win32_client_rect(wrapper)
    emit("window rect: {}".format(window_rect))
    emit("client rect: {}".format(client_rect))

    pre_shot = os.path.join(shots, "{}_visual_pre.png".format(stamp))
    emit("pre shot: {}".format(wkcommon.capture_window(wrapper, pre_shot)))
    nodes = wkcommon.walk_described(wrapper, 8000)
    visible = _visible(nodes, client_rect)
    state, reasons, _signals = ctx.classify_live_page(
        visible, None, pre_shot, window_rect)
    emit("page: {}".format(state))
    for item in reasons:
        emit("  - {}".format(item))
    if state != "RATING_FORM":
        emit("ABORT: not a visually verified rating form. Nothing clicked.")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("Page classification: {} - nothing clicked.".format(state))
        print("REPORT: {}".format(text_path))
        return 3

    selected, support = _visual_headings(nodes, client_rect, pre_shot, window_rect)
    section = _making_section(selected, client_rect)
    if not section:
        emit("ABORT: could not build live Making Skills section from visually selected headings.")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("Making Skills geometry not verified - nothing clicked.")
        print("REPORT: {}".format(text_path))
        return 4

    heading_rect = section["heading"]["rect"]
    emit("Making heading: {}".format(heading_rect))
    emit("Making band: {} .. {}".format(int(section["band"][0]), int(section["band"][1])))

    before = detect_visual_score_rows(
        pre_shot, window_rect, heading_rect, section["band_bottom"],
        client_rect, wkcommon.BOTTOM_EXCLUSION_BAND)
    emit("before visual rows: {} ({})".format(before["layout"], before["reason"]))

    clicked = False
    arrow_rect = None
    if before["layout"] != "vertical-visual":
        arrow_rect, arrow_reason = find_expand_arrow(pre_shot, window_rect, section)
        emit("accordion arrow: {} ({})".format(arrow_rect, arrow_reason))
        ok, guard_reason = arrow_click_ok(arrow_rect, section, client_rect)
        if not ok:
            emit("ABORT: safe arrow guard failed: {}. Nothing clicked.".format(guard_reason))
            with open(text_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            print("Safe accordion arrow not verified - nothing clicked.")
            print("REPORT: {}".format(text_path))
            return 5
        point = centre(arrow_rect)
        emit("clicking ONLY accordion arrow at {}".format(point))
        mouse.click(button="left", coords=(point[0], point[1]))
        clicked = True
        time.sleep(1.0)
    else:
        emit("Making Skills was already expanded; no click needed.")

    post_shot = os.path.join(shots, "{}_visual_post.png".format(stamp))
    emit("post shot: {}".format(wkcommon.capture_window(wrapper, post_shot)))
    nodes_after = wkcommon.walk_described(wrapper, 8000)
    visible_after = _visible(nodes_after, client_rect)
    state_after, reasons_after, _signals_after = ctx.classify_live_page(
        visible_after, None, post_shot, window_rect)
    emit("page after: {}".format(state_after))
    for item in reasons_after:
        emit("  - {}".format(item))
    if state_after != "RATING_FORM":
        emit("ABORT: page changed or could not be verified after accordion action.")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("Post-click page not verified as RATING_FORM. No rating was selected.")
        print("REPORT: {}".format(text_path))
        return 6

    selected_after, support_after = _visual_headings(
        nodes_after, client_rect, post_shot, window_rect)
    section_after = _making_section(selected_after, client_rect)
    if not section_after:
        emit("ABORT: could not rebuild Making Skills section after expansion.")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("Post-click Making Skills geometry not verified.")
        print("REPORT: {}".format(text_path))
        return 7

    after = detect_visual_score_rows(
        post_shot, window_rect, section_after["heading"]["rect"],
        section_after["band_bottom"], client_rect,
        wkcommon.BOTTOM_EXCLUSION_BAND)
    emit("after visual rows: {} ({})".format(after["layout"], after["reason"]))
    for row in after.get("rows", []):
        emit("  score {} -> click {} stars={} radio={}".format(
            row["score"], tuple(row["click_point"]), row["star_count"],
            row["radio_rect"]))

    result = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "clicked_accordion": clicked,
        "page_before": state,
        "page_after": state_after,
        "heading_support_before": support,
        "heading_support_after": support_after,
        "before": before,
        "after": after,
        "arrow_rect": arrow_rect,
        "rating_options_clicked": 0,
        "submitted": False,
    }
    with open(text_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    if after["layout"] == "vertical-visual":
        print("SUCCESS: Making Skills visually verified as expanded.")
        print("Detected score rows: 1, 2, 3, 4, 5")
        for row in after["rows"]:
            print("  score {} -> safe radio target {}".format(
                row["score"], tuple(row["click_point"])))
        print("Rating options clicked: NONE")
        print("Submit clicked: NO")
        print("REPORT: {}".format(text_path))
        return 0

    print("Making Skills expanded action completed, but visual 1..5 score rows were NOT verified.")
    print("No rating was selected.")
    print("REPORT: {}".format(text_path))
    return 8


if __name__ == "__main__":
    raise SystemExit(main())
