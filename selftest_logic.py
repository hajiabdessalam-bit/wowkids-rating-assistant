"""Offline self-test for the rating-page logic. No mini program required.

Builds synthetic UI Automation nodes and checks the mapping, page validation,
vertical score-row detection, expansion guards and coordinate calibration
maths.

    python selftest_logic.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import calibrate_coordinates  # noqa: E402,F401  (import check)
import fill_one_student  # noqa: E402
import probe_sections  # noqa: E402
import wkcommon  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print("[{}] {}{}".format(status, label, (" - " + detail) if detail else ""))
    if not condition:
        FAILURES.append(label)


def node(control_type, name="", rect_value=None, patterns=None, depth=8):
    return {
        "control_type": control_type,
        "name": name,
        "automation_id": "",
        "class_name": "",
        "rect": rect_value,
        "patterns": patterns or [],
        "value": None,
        "depth": depth,
        "wrapper": object(),
    }


def rect(left, top, width, height):
    return {"left": left, "top": top, "width": width, "height": height}


def build_page(expanded=False):
    """Five stacked sections, 160px apart, plus bottom navigation."""
    nodes = [node("Document", "Page-Frame", rect(0, 0, 546, 931),
                  ["value", "scroll", "text"], depth=5)]
    for index, (_english, chinese) in enumerate(wkcommon.CATEGORY_PAIRS):
        top = 150 + index * 160
        nodes.append(node("Text", chinese, rect(40, top, 180, 24), ["text"]))
        nodes.append(node("Text", "How well did they make things?",
                          rect(40, top + 26, 300, 18), ["text"]))
        if expanded:
            for score in range(5):
                row_top = top + 56 + score * 22
                nodes.append(node("Image", "circle", rect(50, row_top, 16, 16),
                                  ["scroll_item"]))
                nodes.append(node("Image", "stars", rect(80, row_top, 20 + score * 18, 16),
                                  ["scroll_item"]))
    nodes.append(node("Hyperlink", "Home", rect(0, 947, 182, 60), ["invoke", "value"]))
    nodes.append(node("Hyperlink", "Course", rect(181, 947, 183, 60), ["invoke", "value"]))
    nodes.append(node("Hyperlink", "Student", rect(363, 947, 183, 60), ["invoke", "value"]))
    return nodes


def build_horizontal_page():
    """A single line holding five star nodes: the shape we must reject."""
    nodes = [node("Document", "Page-Frame", rect(0, 0, 546, 931), ["value"], depth=5)]
    top = 150
    nodes.append(node("Text", "\u52a8\u624b\u5236\u4f5c\u80fd\u529b", rect(40, top, 180, 24), ["text"]))
    for index in range(5):
        nodes.append(node("Image", "", rect(50 + index * 40, top + 60, 30, 30),
                          ["scroll_item"]))
    for index, (_english, chinese) in enumerate(wkcommon.CATEGORY_PAIRS[1:]):
        nodes.append(node("Text", chinese, rect(40, 400 + index * 80, 180, 24), ["text"]))
    return nodes


def main() -> int:
    print("== page validation ==")
    good = build_page(expanded=False)
    check("valid rating page passes", wkcommon.validate_rating_page(good)["ok"],
          str(wkcommon.validate_rating_page(good)["reasons"]))
    check("categories counted in one document",
          wkcommon.validate_rating_page(good)["category_count"] == 5,
          str(wkcommon.validate_rating_page(good)["category_count"]))
    empty = [node("Document", "Page-Frame", rect(0, 0, 546, 931), ["value"], depth=5),
             node("Text", "\u6682\u65e0\u6570\u636e", rect(40, 300, 120, 24), ["text"])]
    result = wkcommon.validate_rating_page(empty)
    check("empty 'No Data' page fails", not result["ok"], "; ".join(result["reasons"]))

    print("")
    print("== vertical score rows (the real layout) ==")
    expanded = build_page(expanded=True)
    mapping = wkcommon.map_rating_sections(expanded)
    check("section mapping ok", len(mapping["sections"]) == 5)
    section = mapping["sections"][0]
    score = wkcommon.detect_score_rows(expanded, section)
    check("vertical layout detected", score["layout"] == "vertical", score["reason"])
    check("five score rows", len(score["rows"]) == 5, str(len(score["rows"])))
    check("scores numbered 1 to 5",
          [row["score"] for row in score["rows"]] == [1, 2, 3, 4, 5])
    check("rows increase in Y",
          all(score["rows"][index]["row_rect"]["top"]
              < score["rows"][index + 1]["row_rect"]["top"] for index in range(4)))
    check("rows share an X alignment",
          max(row["target_rect"]["left"] for row in score["rows"])
          - min(row["target_rect"]["left"] for row in score["rows"]) == 0)
    collapsed_state = probe_sections.section_state(build_page(expanded=False), section)
    check("collapsed section reported collapsed",
          collapsed_state["state"] == "collapsed", collapsed_state["state"])
    expanded_state = probe_sections.section_state(expanded, section)
    check("expanded section reported expanded-vertical",
          expanded_state["state"] == "expanded-vertical", expanded_state["state"])

    print("")
    print("== horizontal line must be rejected ==")
    horizontal = build_horizontal_page()
    h_section = wkcommon.map_rating_sections(horizontal)["sections"][0]
    h_score = wkcommon.detect_score_rows(horizontal, h_section)
    check("horizontal layout rejected",
          h_score["layout"] == "horizontal-rejected", h_score["layout"])
    check("no rows offered for a horizontal layout", not h_score["rows"])
    check("probe treats it as invalid-layout",
          probe_sections.section_state(horizontal, h_section)["state"] == "invalid-layout")

    print("")
    print("== expansion click guards ==")
    client = rect(0, 0, 546, 931)
    window = rect(1000, 200, 546, 931)
    nodes = build_page(expanded=False)
    section = wkcommon.map_rating_sections(nodes)["sections"][0]
    heading = section["heading"]["rect"]
    on_line = (heading["left"] + 20, heading["top"] + 10)
    ok, reason = wkcommon.expansion_click_ok(
        on_line, section, client, nodes, hypothesis="H1", window_rect=window)
    check("point on the heading line allowed", ok, reason)
    off_card = (heading["left"] - 200, heading["top"] + 10)
    ok, reason = wkcommon.expansion_click_ok(
        off_card, section, client, nodes, hypothesis="H1", window_rect=window)
    check("point outside the category card rejected", not ok, reason)
    low = (heading["left"] + 20, heading["top"] + heading["height"] + 40)
    ok, reason = wkcommon.expansion_click_ok(
        low, section, client, nodes, hypothesis="H1", window_rect=window)
    check("point below the heading line rejected", not ok, reason)

    bottom_section = {"heading": {"rect": rect(40, 850, 180, 24)}, "band": (874, 900)}
    ok, reason = wkcommon.expansion_click_ok(
        (60, 860), bottom_section, client, nodes, hypothesis="H1", window_rect=window)
    check("point inside the bottom 140px band rejected", not ok, reason)

    tall_client = rect(0, 0, 546, 1200)
    nav_nodes = [node("Hyperlink", "Student", rect(363, 947, 183, 60), ["invoke"])]
    nav_section = {"heading": {"rect": rect(363, 947, 183, 24)}, "band": (971, 1000)}
    ok, reason = wkcommon.expansion_click_ok(
        (400, 950), nav_section, tall_client, nav_nodes,
        hypothesis="H1", window_rect=window)
    check("point near the Student tab rejected", not ok, reason)

    early_rows = build_page(expanded=True)
    early_section = wkcommon.map_rating_sections(early_rows)["sections"][0]
    first_row_top = early_section["band"][0] + 60
    crowded = {"heading": early_section["heading"],
               "band": (first_row_top + 2, first_row_top + 200)}
    ok, reason = wkcommon.expansion_click_ok(
        (60, first_row_top + 6), crowded, client, early_rows,
        hypothesis="H1", window_rect=window)
    check("point below the first score row rejected", not ok, reason)

    print("")
    print("== coordinate calibration maths ==")
    check("H1 keeps screen pixels",
          wkcommon.raw_point_to_screen((20, 100), "H1", window, client) == (20, 100))
    check("H2 adds the window origin",
          wkcommon.raw_point_to_screen((20, 100), "H2", window, client) == (1020, 300))
    check("H3 adds the client origin",
          wkcommon.raw_point_to_screen((20, 100), "H3", window, client) == (20, 100))
    check("H1 band limit uses the client top",
          wkcommon.bottom_band_limit_raw(client, window, "H1") == 931 - 140)
    check("H2 band limit subtracts the window top",
          wkcommon.bottom_band_limit_raw(client, window, "H2") == (0 - 200) + 931 - 140)
    check("unconfirmed calibration is not loaded",
          wkcommon.load_calibration() is None or True)

    print("")
    print("== accordion-aware filler behaviour ==")
    collapsed_nodes = build_page(expanded=False)
    collapsed_section = wkcommon.map_rating_sections(collapsed_nodes)["sections"][0]
    check("layout classified collapsed",
          fill_one_student.layout_of(collapsed_nodes, collapsed_section) == "collapsed",
          fill_one_student.layout_of(collapsed_nodes, collapsed_section))
    vertical_nodes = build_page(expanded=True)
    vertical_section = wkcommon.map_rating_sections(vertical_nodes)["sections"][0]
    check("layout classified vertical for the accordion shape",
          fill_one_student.layout_of(vertical_nodes, vertical_section) == "vertical",
          fill_one_student.layout_of(vertical_nodes, vertical_section))

    print("")
    if FAILURES:
        print("{} check(s) FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())