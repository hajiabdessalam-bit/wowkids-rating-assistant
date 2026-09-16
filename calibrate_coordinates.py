"""Coordinate calibration - ZERO clicks.

It reads the live window rectangle, the client rectangle and a handful of
landmark nodes (page title, the Making Skills heading, the bottom navigation
tabs) and then draws all three candidate interpretations of each UIA rectangle
onto a real screenshot so the coordinate space can be confirmed by eye.

This script never calls the mouse. It may restore a minimised window, which is
the only state change it makes, and that is logged.

Outputs:
    reports/coordinate_debug_<stamp>.png   (screenshot with overlays)
    reports/coordinate_debug_<stamp>.txt   (numbers + recommendation)
    reports/coordinate_debug_<stamp>.json
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

COLOURS = {
    "H1": (255, 0, 0),        # UIA rect is already screen pixels
    "H2": (0, 128, 255),      # UIA rect is relative to the window rectangle
    "H3": (255, 0, 255),      # UIA rect is relative to the client area
}


def rect_text(rect) -> str:
    if not rect:
        return "-"
    return "{},{} {}x{}".format(
        rect["left"], rect["top"], rect["width"], rect["height"])


def to_image(rect, hypothesis, window_rect, client_rect):
    """Where this rectangle lands inside a window-sized screenshot."""
    if not rect or not window_rect:
        return None
    if hypothesis == "H1":
        left, top = rect["left"] - window_rect["left"], rect["top"] - window_rect["top"]
    elif hypothesis == "H2":
        left, top = rect["left"], rect["top"]
    else:
        if not client_rect:
            return None
        left = rect["left"] + client_rect["left"] - window_rect["left"]
        top = rect["top"] + client_rect["top"] - window_rect["top"]
    return {"left": left, "top": top, "width": rect["width"], "height": rect["height"]}


def to_screen(rect, hypothesis, window_rect, client_rect):
    if not rect or not window_rect:
        return None
    if hypothesis == "H1":
        return {"left": rect["left"], "top": rect["top"],
                "width": rect["width"], "height": rect["height"]}
    if hypothesis == "H2":
        return {"left": rect["left"] + window_rect["left"],
                "top": rect["top"] + window_rect["top"],
                "width": rect["width"], "height": rect["height"]}
    if not client_rect:
        return None
    return {"left": rect["left"] + client_rect["left"],
            "top": rect["top"] + client_rect["top"],
            "width": rect["width"], "height": rect["height"]}


def pick_landmarks(nodes: list) -> list:
    """Landmarks that are easy to recognise on a screenshot."""
    landmarks = []

    heading = wkcommon.pick_heading(nodes, "Making Skills", "\u52a8\u624b\u5236\u4f5c\u80fd\u529b")
    if heading:
        landmarks.append(("Making Skills heading", heading))

    for english, chinese in wkcommon.CATEGORY_PAIRS[1:]:
        node = wkcommon.pick_heading(nodes, english, chinese)
        if node:
            landmarks.append(("{} heading".format(english), node))

    title = None
    for node in nodes:
        name = (node["name"] or "").strip()
        if not node.get("rect"):
            continue
        if "Class Date" in name or "\u521b\u9020\u8005" in name:
            if not title or node["rect"]["top"] < title["rect"]["top"]:
                title = node
    if title:
        landmarks.append(("page title", title))
    else:
        texts = [
            node for node in nodes
            if node["control_type"] == "Text" and (node["name"] or "").strip()
            and node.get("rect")
        ]
        texts.sort(key=lambda node: node["rect"]["top"])
        if texts:
            landmarks.append(("topmost text", texts[0]))

    navs = []
    for node in nodes:
        name = (node["name"] or "").strip().lower()
        if name in wkcommon.NAV_NAMES and node.get("rect"):
            navs.append(node)
    navs.sort(key=lambda node: node["rect"]["top"])
    for index, node in enumerate(navs[-3:]):
        landmarks.append(("bottom nav {}".format(index + 1), node))
    return landmarks


def plausibility(hypothesis, landmarks, window_rect, client_rect, image_size) -> dict:
    inside = 0
    nav_bottom = 0
    nav_total = 0
    total = 0
    for label, node in landmarks:
        image_rect = to_image(node["rect"], hypothesis, window_rect, client_rect)
        if not image_rect:
            continue
        total += 1
        if (
            0 <= image_rect["left"]
            and 0 <= image_rect["top"]
            and image_rect["left"] + image_rect["width"] <= image_size[0]
            and image_rect["top"] + image_rect["height"] <= image_size[1]
        ):
            inside += 1
        if label.startswith("bottom nav"):
            nav_total += 1
            if image_rect["top"] >= image_size[1] * 0.75:
                nav_bottom += 1
    return {
        "hypothesis": hypothesis,
        "inside_image": inside,
        "landmarks": total,
        "bottom_nav_in_lower_quarter": nav_bottom,
        "bottom_nav_total": nav_total,
    }


def draw_overlay(image_path, landmarks, window_rect, client_rect, out_path, report) -> str:
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        return "Pillow unavailable: {}".format(exc)
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception as exc:
        return "cannot open screenshot: {}".format(exc)
    draw = ImageDraw.Draw(image)

    def outline(rect, colour, width=2):
        draw.rectangle(
            [rect["left"], rect["top"],
             rect["left"] + rect["width"], rect["top"] + rect["height"]],
            outline=colour, width=width)

    # window outline (yellow) and client outline (green)
    outline({"left": 0, "top": 0, "width": image.width - 1, "height": image.height - 1},
            (255, 215, 0), 2)
    if client_rect:
        outline(
            {
                "left": client_rect["left"] - window_rect["left"],
                "top": client_rect["top"] - window_rect["top"],
                "width": client_rect["width"],
                "height": client_rect["height"],
            },
            (0, 200, 0), 2)
        band_top = (
            client_rect["top"] - window_rect["top"]
            + client_rect["height"] - wkcommon.BOTTOM_EXCLUSION_BAND
        )
        draw.line([0, band_top, image.width, band_top], fill=(255, 140, 0), width=2)

    for index, (label, node) in enumerate(landmarks, start=1):
        for hypothesis, colour in COLOURS.items():
            image_rect = to_image(node["rect"], hypothesis, window_rect, client_rect)
            if not image_rect:
                continue
            outline(image_rect, colour, 2)
            draw.text((max(0, image_rect["left"]), max(0, image_rect["top"] - 11)),
                      "{}{}".format(index, hypothesis), fill=colour)
            report.append("  {} {:>4} image-rect={} screen-rect={}".format(
                label, hypothesis, rect_text(image_rect),
                rect_text(to_screen(node["rect"], hypothesis, window_rect, client_rect))))
    image.save(out_path, "PNG")
    return "overlay written"


def build_report(args) -> int:
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    base = os.path.join(wkcommon.REPORTS, "coordinate_debug_{}".format(stamp))
    txt_path, png_path, json_path = base + ".txt", base + ".png", base + ".json"
    raw_png = base + "_raw.png"

    out = []

    def emit(text: str = "") -> None:
        out.append(text)

    emit("Wowkids rating tool - coordinate calibration (no clicks)")
    emit("time: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
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
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        print("No window identified.")
        print("REPORT: {}".format(txt_path))
        return 2

    target = hinted[0]
    wrapper = target["wrapper"]
    note = wkcommon.restore_window(wrapper)
    window_rect = wkcommon.window_rectangle(wrapper)
    client_rect = wkcommon.win32_client_rect(wrapper)
    emit("window      : pid={} title={!r}".format(target["pid"], target["title"]))
    emit("window rect : {} ({})".format(rect_text(window_rect), note))
    emit("client rect : {}".format(rect_text(client_rect)))
    emit("bottom {0}px exclusion band starts at y={1}".format(
        wkcommon.BOTTOM_EXCLUSION_BAND,
        (client_rect["top"] + client_rect["height"] - wkcommon.BOTTOM_EXCLUSION_BAND)
        if client_rect else "?"))
    emit("")

    capture_note = wkcommon.capture_window(wrapper, raw_png)
    emit("screenshot  : {}".format(capture_note))
    emit("")

    nodes = wkcommon.walk_described(wrapper, args.max_nodes)
    validation = wkcommon.validate_rating_page(nodes)
    emit("page validation: {}".format("PASS" if validation["ok"] else "FAIL"))
    for reason in validation["reasons"]:
        emit("  - {}".format(reason))
    emit("  categories in one document: {}".format(validation["category_count"]))
    emit("")

    landmarks = pick_landmarks(nodes)
    if not landmarks:
        emit("no landmarks found (is the mini program showing a page?)")
    emit("== landmarks and rectangle interpretations ==")
    emit("  H1 = UIA rect is already screen pixels")
    emit("  H2 = UIA rect is relative to the top-left of the window rectangle")
    emit("  H3 = UIA rect is relative to the top-left of the client area")
    emit("")
    for index, (label, node) in enumerate(landmarks, start=1):
        emit("[{}] {} | {} {!r}".format(
            index, label, node["control_type"], node["name"][:60]))
        emit("  raw UIA rect = {} patterns={}".format(
            rect_text(node["rect"]), ",".join(node["patterns"])))
    emit("")
    emit("== per-landmark interpretations ==")
    draw_note = draw_overlay(raw_png, landmarks, window_rect, client_rect, png_path, out)
    emit("overlay: {}".format(draw_note))
    emit("")

    image_size = None
    try:
        from PIL import Image

        with Image.open(png_path) as image:
            image_size = image.size
    except Exception:
        pass
    if image_size is None:
        image_size = (max(1, window_rect["width"]), max(1, window_rect["height"]))

    emit("== hypothesis plausibility ==")
    scores = [
        plausibility(hypothesis, landmarks, window_rect, client_rect, image_size)
        for hypothesis in ("H1", "H2", "H3")
    ]
    for score in scores:
        emit("  {hypothesis}: {inside_image}/{landmarks} rects inside the image, "
             "bottom nav in lower quarter {bottom_nav_in_lower_quarter}/{bottom_nav_total}".format(
                 **score))
    best = max(
        scores,
        key=lambda score: (
            score["inside_image"],
            score["bottom_nav_in_lower_quarter"],
        ),
    )
    emit("")
    emit("recommended interpretation: {}".format(best["hypothesis"]))
    emit("confirm visually in {}".format(os.path.basename(png_path)))

    if args.confirm:
        chosen = str(args.confirm).upper()
        if chosen not in wkcommon.HYPOTHESES:
            emit("")
            emit("--confirm {} is not one of {}".format(
                args.confirm, ", ".join(wkcommon.HYPOTHESES)))
        else:
            saved = wkcommon.save_calibration(
                chosen, window_rect, client_rect, "manual confirm from overlay")
            emit("")
            emit("ACTIVATED: raw UIA rectangles are interpreted as {}".format(chosen))
            emit("written to {}".format(saved))

    validation_payload = {
        "ok": validation["ok"],
        "reasons": validation["reasons"],
        "category_count": validation["category_count"],
    }

    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "window_rect": window_rect,
                "client_rect": client_rect,
                "validation": validation_payload,
                "landmarks": [
                    {"label": label, "type": node["control_type"],
                     "name": node["name"], "rect": node["rect"],
                     "patterns": node["patterns"]}
                    for label, node in landmarks
                ],
                "plausibility": scores,
                "recommended": best["hypothesis"],
            },
            fh,
            ensure_ascii=False,
            indent=1,
        )

    print("landmarks: {}".format(len(landmarks)))
    print("page validation: {}".format("PASS" if validation["ok"] else "FAIL"))
    print("recommended interpretation: {}".format(best["hypothesis"]))
    if args.confirm:
        print("calibration file: {}".format(wkcommon.CALIBRATION_PATH))
    else:
        print("not activated yet - check the image, then run: "
              "run_calibrate.bat confirm <H1|H2|H3>")
    print("TEXT  : {}".format(txt_path))
    print("IMAGE : {}".format(png_path))
    print("JSON  : {}".format(json_path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Coordinate calibration (no clicks)")
    parser.add_argument("--max-nodes", type=int, default=12000)
    parser.add_argument("--confirm", default="",
                        help="activate a hypothesis, e.g. --confirm H1")
    return build_report(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())