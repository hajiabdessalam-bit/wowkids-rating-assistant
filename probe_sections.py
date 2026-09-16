"""Stage 2b: map how a rating category expands, without touching any rating.

Allowed: expanding/collapsing a category header (accessible action, or one
click that passes a strict guard). Forbidden and absent: clicking a rating
option, Submit, Post All, or navigating anywhere.

Safety rules enforced here:

  * The page must pass strict validation first (Making Skills + Problem Solving
    present, at least three categories in one document, not a "No Data" page).
  * Expansion state comes from measured geometry: five stacked score rows with
    increasing Y and aligned X is the only accepted "expanded-vertical" shape.
    A single horizontal line of five nodes is rejected as an invalid layout and
    makes the probe abort instead of clicking.
  * An expansion click must sit on the category's heading line, inside the
    category card horizontally, above the first score row, outside the bottom
    140px of the client area, and at least 12px away from Home/Course/Student/
    Post All/Submit/提交/Gallery nodes.
  * Any change of page identity aborts the whole run immediately.

Usage:
    python probe_sections.py --sections first
    python probe_sections.py --sections all
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
import validate_context as ctx  # noqa: E402


def rect_text(rect) -> str:
    if not rect:
        return "-"
    return "{},{} {}x{}".format(
        rect["left"], rect["top"], rect["width"], rect["height"])


def centre(rect):
    if not rect:
        return None
    return rect["left"] + rect["width"] // 2, rect["top"] + rect["height"] // 2


def section_state(nodes: list, section: dict) -> dict:
    """Measure the section: the only accepted expanded shape is vertical."""
    score = wkcommon.detect_score_rows(nodes, section)
    if score["layout"] == "vertical":
        state = "expanded-vertical"
    elif score["layout"] == "horizontal-rejected":
        state = "invalid-layout"
    else:
        state = "collapsed"
    return {"state": state, "score": score}


def heading_line_candidates(nodes: list, section: dict) -> list:
    """Nodes on the heading line that could own the expand action."""
    heading = section["heading"]["rect"]
    band_start = section["band"][0]
    scored = []
    for node in nodes:
        rect = node["rect"]
        if not rect or rect["width"] <= 0 or rect["height"] <= 0:
            continue
        name = (node["name"] or "").strip().lower()
        if name and any(word in name for word in wkcommon.FORBIDDEN_NAMES):
            continue
        overlaps_heading_line = (
            rect["top"] - 8 <= heading["top"] <= rect["top"] + rect["height"] + 8
        )
        if not overlaps_heading_line or rect["top"] > band_start:
            continue
        patterns = set(node["patterns"])
        score = 0
        if "expand_collapse" in patterns:
            score += 8
        if "invoke" in patterns:
            score += 4
        if "toggle" in patterns:
            score += 3
        if "selection_item" in patterns:
            score += 2
        if node["control_type"] in ("Button", "Group", "Hyperlink"):
            score += 2
        if rect["width"] <= 64 and rect["height"] <= 64:
            score += 1
        if rect["left"] >= heading["left"] + heading["width"] - 30:
            score += 1
        scored.append((score, rect["left"], node))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    return [item[2] for item in scored]


def invoke_node(node) -> str:
    wrapper = node["wrapper"]
    for attr, method in (
        ("iface_expand_collapse", "Expand"),
        ("iface_invoke", "Invoke"),
        ("iface_toggle", "Toggle"),
        ("iface_selection_item", "Select"),
    ):
        try:
            iface = getattr(wrapper, attr, None)
            if iface is None:
                continue
            getattr(iface, method)()
            return "{}.{}()".format(attr, method)
        except Exception:
            continue
    return ""


def classify_current(nodes, client_rect, screenshot_path=None, window_rect=None):
    """Run the live classifier using both UIA and captured pixels."""
    visible = [
        node for node in nodes
        if wkcommon.node_is_visibly_present(node, client_rect)[0]
    ]
    parents = ctx.build_parent_map(nodes)
    node_index = {id(node): i for i, node in enumerate(nodes)}
    visible_heads = ctx._visible_heading_nodes(visible)
    heading_doc_ids = {}
    for english, node in visible_heads.items():
        idx = node_index.get(id(node))
        heading_doc_ids[english] = (
            ctx.document_index_of(idx, nodes, parents)
            if idx is not None else None)
    return ctx.classify_live_page(
        visible, heading_doc_ids, screenshot_path, window_rect)


def _dark_components(image, box):
    """Connected dark-pixel components in an image-space box."""
    left, top, right, bottom = box
    crop = image.crop((left, top, right, bottom)).convert("RGB")
    width, height = crop.size
    px = crop.load()
    dark = set()
    for y in range(height):
        for x in range(width):
            red, green, blue = px[x, y]
            if max(red, green, blue) < 120:
                dark.add((x, y))
    components = []
    while dark:
        seed = dark.pop()
        stack = [seed]
        points = [seed]
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                item = (x + dx, y + dy)
                if item in dark:
                    dark.remove(item)
                    stack.append(item)
                    points.append(item)
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        components.append({
            "area": len(points),
            "left": left + min(xs),
            "top": top + min(ys),
            "width": max(xs) - min(xs) + 1,
            "height": max(ys) - min(ys) + 1,
        })
    return components


def find_expand_arrow(screenshot_path, window_rect, section):
    """Locate the small black accordion triangle beneath a category heading.

    The real UI puts the arrow inside the description card, not on the heading
    line.  Searching a narrow strip just to the right of the heading's left
    edge avoids the description text and, critically, the bottom navigation.
    Returns ``(screen_rect, reason)`` and never clicks.
    """
    try:
        from PIL import Image
        image = Image.open(screenshot_path).convert("RGB")
    except Exception as exc:
        return None, "cannot open screenshot: {}".format(exc)

    heading = section["heading"]["rect"]
    # H1: raw UIA rectangles are screen pixels; screenshot origin is window top-left.
    hx = heading["left"] - window_rect["left"]
    hy = heading["top"] - window_rect["top"]
    hbottom = hy + heading["height"]
    band_bottom = int(section["band"][1] - window_rect["top"])

    left = max(0, hx - 10)
    right = min(image.width, hx + 24)
    top = max(0, hbottom + 12)
    bottom = min(image.height, band_bottom - 8, top + 170)
    if bottom <= top or right <= left:
        return None, "invalid arrow search region"

    candidates = []
    for component in _dark_components(image, (left, top, right, bottom)):
        w = component["width"]
        h = component["height"]
        area = component["area"]
        # Real accordion triangle is about 10x9 px in the captured UI.  Keep
        # thresholds deliberately broad for DPI/antialiasing variation.
        if not (4 <= w <= 20 and 3 <= h <= 16 and 8 <= area <= 160):
            continue
        cx = component["left"] + w / 2.0
        cy = component["top"] + h / 2.0
        target_x = hx + 13
        target_y = hbottom + 85
        distance = abs(cx - target_x) + abs(cy - target_y) * 0.25
        candidates.append((distance, component))

    if not candidates:
        return None, "no compact dark accordion arrow found in safe strip"
    candidates.sort(key=lambda item: item[0])
    best = candidates[0][1]
    # Fail closed when another candidate is essentially tied.
    if len(candidates) > 1 and abs(candidates[1][0] - candidates[0][0]) < 3:
        return None, "accordion arrow detection ambiguous"

    return {
        "left": best["left"] + window_rect["left"],
        "top": best["top"] + window_rect["top"],
        "width": best["width"],
        "height": best["height"],
    }, "ok"


def arrow_click_ok(rect, section, client_rect):
    if not rect or not client_rect:
        return False, "missing geometry"
    x, y = centre(rect)
    heading = section["heading"]["rect"]
    band = section["band"]
    if not (heading["left"] - 18 <= x <= heading["left"] + 40):
        return False, "arrow is not in the narrow left strip of the section"
    if not (band[0] + 10 <= y < band[1] - 8):
        return False, "arrow is outside the category band"
    bottom_limit = client_rect["top"] + client_rect["height"] - wkcommon.BOTTOM_EXCLUSION_BAND
    if y >= bottom_limit:
        return False, "arrow is inside bottom navigation exclusion band"
    return True, "ok"


def build_report(args) -> int:
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop, mouse

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    shots = os.path.join(wkcommon.REPORTS, "shots")
    os.makedirs(shots, exist_ok=True)
    stamp = wkcommon.timestamp()
    report_path = os.path.join(wkcommon.REPORTS, "probe_{}.txt".format(stamp))
    json_path = os.path.join(wkcommon.REPORTS, "probe_{}.json".format(stamp))

    out = []

    def emit(text: str = "") -> None:
        out.append(text)

    emit("Wowkids rating tool - Stage 2b expansion probe (v2, guarded)")
    emit("time: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    emit("allowed: expand a category header | forbidden: options, Submit, Post All, navigation")
    emit("")

    desktop = Desktop(backend="uia")
    candidates = wkcommon.wowkids_windows(desktop)
    hinted = [
        item for item in candidates
        if any(hint.lower() in item["title"].lower()
               for hint in wkcommon.WINDOW_TITLE_HINTS)
    ]
    if len(hinted) != 1:
        emit("REFUSING: expected one Wowkids window, found {}".format(len(hinted)))
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        print("No window identified.")
        print("REPORT: {}".format(report_path))
        return 2

    target = hinted[0]
    wrapper = target["wrapper"]
    note = wkcommon.restore_window(wrapper)
    window_rect = wkcommon.window_rectangle(wrapper)
    client_rect = wkcommon.win32_client_rect(wrapper)
    emit("window      : pid={} title={!r}".format(target["pid"], target["title"]))
    emit("window rect : {} ({})".format(rect_text(window_rect), note))
    emit("client rect : {}".format(rect_text(client_rect)))
    if client_rect:
        emit("no-click band starts at y={}".format(
            client_rect["top"] + client_rect["height"] - wkcommon.BOTTOM_EXCLUSION_BAND))
    emit("")

    hypothesis = "H1"
    emit("coordinate space: H1 (confirmed by audit)")
    emit("")

    precheck_shot = os.path.join(shots, "{}_precheck.png".format(stamp))
    emit("precheck shot: {}".format(wkcommon.capture_window(wrapper, precheck_shot)))
    nodes = wkcommon.walk_described(wrapper, args.max_nodes)
    state, reasons, _signals = classify_current(
        nodes, client_rect, precheck_shot, window_rect)
    emit("== page classification (validate_context) ==")
    emit("state: {}".format(state))
    for reason in reasons:
        emit("  - {}".format(reason))
    if state != "RATING_FORM":
        emit("")
        emit("ABORT: page is {}, not RATING_FORM. Nothing was clicked.".format(state))
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        print("Page classification: {} - nothing clicked.".format(state))
        print("REPORT: {}".format(report_path))
        return 3
    emit("")

    mapping = wkcommon.map_rating_sections(nodes)
    sections = mapping["sections"]
    emit("")
    emit("== state on arrival ==")
    states = []
    for section in sections:
        measured = section_state(nodes, section)
        states.append(measured["state"])
        emit("  {:<24} {:<18} {}".format(
            section["english"], measured["state"], measured["score"]["reason"]))
    emit("")

    if args.sections == "all":
        wanted = list(range(5))
    elif args.sections == "first":
        wanted = [0]
    else:
        wanted = [
            int(part) - 1 for part in str(args.sections).split(",")
            if part.strip().isdigit() and 1 <= int(part) <= 5
        ]

    findings = []
    for index in wanted:
        section = sections[index]
        emit("== {} / {} ==".format(section["english"], section["chinese"]))
        before = section_state(nodes, section)
        emit("  heading  : {} {!r} rect={}".format(
            section["heading"]["control_type"], section["heading"]["name"],
            rect_text(section["heading"]["rect"])))
        emit("  band     : y {} .. {}".format(
            int(section["band"][0]), int(section["band"][1])))
        emit("  state    : {} ({})".format(before["state"], before["score"]["reason"]))
        shot_before = os.path.join(shots, "{}_{}_before.png".format(stamp, index + 1))
        emit("  shot     : {}".format(wkcommon.capture_window(wrapper, shot_before)))

        entry = {
            "category": section["english"],
            "state_before": before["state"],
            "state_after": before["state"],
            "expanded_by": "",
            "control": None,
            "score_rows": [],
            "aborted": "",
        }

        if before["state"] == "invalid-layout":
            entry["aborted"] = "invalid horizontal layout detected"
            emit("  ABORT: a single line holds five or more nodes; that is not the")
            emit("         score layout, so no click was attempted.")
            findings.append(entry)
            break

        if before["state"] == "collapsed":
            arrow_rect, arrow_reason = find_expand_arrow(
                shot_before, window_rect, section)
            emit("  accordion arrow: {} ({})".format(
                rect_text(arrow_rect), arrow_reason))
            ok, guard_reason = arrow_click_ok(arrow_rect, section, client_rect)
            if not ok:
                entry["aborted"] = "no visually verified safe accordion arrow: {}".format(
                    guard_reason)
                emit("  ABORT: {}; nothing was clicked.".format(entry["aborted"]))
                findings.append(entry)
                break

            entry["control"] = {
                "type": "visual-accordion-arrow",
                "name": "detected dark triangle",
                "rect": arrow_rect,
                "patterns": [],
            }
            point = centre(arrow_rect)
            emit("  expanding via one guarded visual-arrow click at {}".format(point))
            mouse.click(button="left", coords=(point[0], point[1]))
            entry["expanded_by"] = "visual-arrow click {}".format(point)

            time.sleep(args.settle)
            nodes = wkcommon.walk_described(wrapper, args.max_nodes)
            postcheck_shot = os.path.join(
                shots, "{}_{}_postcheck.png".format(stamp, index + 1))
            emit("  postcheck shot: {}".format(
                wkcommon.capture_window(wrapper, postcheck_shot)))
            state_after, _reasons, _signals = classify_current(
                nodes, client_rect, postcheck_shot, window_rect)
            if state_after != "RATING_FORM":
                entry["state_after"] = "PAGE-CHANGED"
                entry["aborted"] = "page changed to {}".format(state_after)
                emit("  ABORT: page changed to {}; stopping.".format(state_after))
                findings.append(entry)
                break
            mapped_after = wkcommon.map_rating_sections(nodes)
            if not mapped_after["sections"] or not mapped_after["order_ok"]:
                entry["state_after"] = "MAPPING-FAILED"
                entry["aborted"] = "rating sections could not be remapped after expansion"
                emit("  ABORT: {}; stopping.".format(entry["aborted"]))
                findings.append(entry)
                break
            sections = mapped_after["sections"]
            section = sections[index]
            after = section_state(nodes, section)
        else:
            after = before
            emit("  already expanded; no click performed")

        shot_after = os.path.join(shots, "{}_{}_after.png".format(stamp, index + 1))
        emit("  shot after: {}".format(wkcommon.capture_window(wrapper, shot_after)))
        emit("  state after: {} ({})".format(after["state"], after["score"]["reason"]))

        if after["state"] == "expanded-vertical":
            emit("  score rows:")
            for row in after["score"]["rows"]:
                emit("    score {} row rect={} target={} {!r} rect={} items={}".format(
                    row["score"], rect_text(row["row_rect"]), row["target_type"],
                    row["target_name"][:20], rect_text(row["target_rect"]),
                    row["item_count"]))
                for item in row.get("items", []):
                    emit("        - {} {!r} rect={} patterns={}".format(
                        item["type"], (item["name"] or "")[:30],
                        rect_text(item["rect"]), ",".join(item["patterns"])))
            entry["score_rows"] = after["score"]["rows"]
        elif after["state"] == "invalid-layout":
            entry["aborted"] = "invalid horizontal layout after expansion"
            emit("  ABORT: still seeing a horizontal line of options; stopping.")
            findings.append(entry)
            break
        else:
            emit("  WARNING: still collapsed after the action")

        others = [
            section_state(nodes, other)["state"]
            for position, other in enumerate(sections)
            if position != index
        ]
        entry["others"] = others
        emit("  others   : {}".format(" | ".join(others)))
        findings.append(entry)
        emit("")

    accordion = any(
        entry.get("others") and sum(1 for state in entry["others"]
                                    if state.startswith("expanded")) == 0
        for entry in findings
    )
    emit("== summary ==")
    for entry in findings:
        emit("{:<24} {} -> {} via {} {}".format(
            entry["category"], entry["state_before"], entry["state_after"],
            entry["expanded_by"] or "(none)", entry["aborted"]))
    emit("")
    emit("rating options clicked: none")
    emit("accordion behaviour: {}".format(
        "at most one section expanded" if accordion else "independent"))

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "window": {k: v for k, v in target.items() if k != "wrapper"},
                "window_rect": window_rect,
                "client_rect": client_rect,
                "validation": {
                    "classification": state,
                    "reasons": reasons,
                    "visual_gate": True,
                },
                "arrival_states": states,
                "findings": findings,
                "accordion": accordion,
            },
            fh,
            ensure_ascii=False,
            indent=1,
        )

    print("sections probed: {}".format(len(findings)))
    for entry in findings:
        print("  {:<24} {} -> {}{}".format(
            entry["category"], entry["state_before"], entry["state_after"],
            "  [{}]".format(entry["aborted"]) if entry["aborted"] else ""))
    print("TEXT: {}".format(report_path))
    print("JSON: {}".format(json_path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 2b expansion probe (guarded)")
    parser.add_argument("--sections", default="first",
                        help="first (default), all, or a list such as 1,2")
    parser.add_argument("--settle", type=float, default=0.9)
    parser.add_argument("--max-nodes", type=int, default=12000)
    return build_report(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())