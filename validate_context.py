"""Zero-click page classifier for the Wowkids mini program.

Classifies the current visible page as one of:

    HOME, CLASS_ROSTER, RATING_FORM, STUDENT_PAGE, UNKNOWN

using only live UI Automation geometry and visibility state. It never clicks,
types, scrolls, submits or navigates. The only state changes it makes are the
same as the other tools: it may restore a minimised window so geometry is
readable, and it records H1 as the confirmed coordinate space with clicks
still disabled.

Outputs (one set per run):
    reports/context_<stamp>.txt
    reports/context_<stamp>.json
    reports/context_<stamp>.png
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


# A visible node whose name contains one of these markers means we are on the
# class roster. These override any rating-heading text: stale hidden rating
# strings must never turn the roster into a false RATING_FORM.
ROSTER_MARKERS = (
    "post all",
    "not rateing",
    "rated",
    "posted",
    "signed in",
    "gallery:",
    "parent feedback",
    "view comments",
    "total：",
    "total:",
    # 2026-09-23 WOWKIDS redesign. The new class page is already the
    # roster: pending students expose a solid purple "Reviews" action and the
    # page carries Students / Sign-in / Photos / Reviews column labels.
    "reviews",
    "group photo",
    "parent report",
    "students",
    "sign-in",
    "photos",
)

STUDENT_MARKERS = ("暂无数据", "no data", "学员")

HOME_MARKERS = (
    "switch account",
    "switch accounts",
    "calendar",
    "schedule",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

X_ALIGN_TOL = 24  # px: rating headings must share roughly the same left edge


def _marker_hits(visible, markers):
    """Return ``[(marker, node), ...]`` for every visible node matching a marker."""
    hits = []
    for node in visible:
        name = (node.get("name") or "").strip().lower()
        if not name:
            continue
        for marker in markers:
            if marker in name:
                hits.append((marker, node))
    return hits


def _heading_candidates(visible):
    """Map english label -> every visible UIA node carrying that heading.

    Chromium can keep more than one Page-Frame alive. Picking the smallest
    matching node is unsafe because that node may belong to a stale frame.
    Live classification therefore keeps all candidates and lets screenshot
    pixels decide which rectangle is actually rendered.
    """
    found = {}
    for english, chinese in wkcommon.CATEGORY_PAIRS:
        hits = []
        for node in visible:
            name = (node.get("name") or "").strip()
            if english.lower() in name.lower() or chinese in name:
                hits.append(node)
        if hits:
            found[english] = hits
    return found


def _visible_heading_nodes(visible):
    """Text-only heading selection for diagnostics/offline tests.

    The live classifier does not trust this selection; it uses
    ``_select_visual_heading_nodes`` below.
    """
    found = {}
    for english, hits in _heading_candidates(visible).items():
        hits = list(hits)
        hits.sort(key=lambda n: (
            n["rect"]["width"] * n["rect"]["height"], -n.get("depth", 0)))
        found[english] = hits[0]
    return found


def _rating_geometry_ok(headings):
    """Require >=3 visible headings with plausible rating-form geometry."""
    present = {e: n for e, n in headings.items() if n}
    if len(present) < 3:
        return False, "only {} of 5 headings visible (need >=3)".format(len(present))
    if "Making Skills" not in present:
        return False, "Making Skills heading not visible"
    if "Problem Solving" not in present:
        return False, "Problem Solving heading not visible"
    ordered = sorted(present.items(), key=lambda kv: kv[1]["rect"]["top"])
    ys = [n["rect"]["top"] for _, n in ordered]
    if any(ys[i] >= ys[i + 1] for i in range(len(ys) - 1)):
        return False, "headings not in increasing Y order"
    xs = [n["rect"]["left"] for _, n in ordered]
    spread = max(xs) - min(xs)
    if spread > X_ALIGN_TOL:
        return False, "headings not X-aligned (spread {}px)".format(spread)
    return True, ""



def _orange_support_for_node(node, image, window_rect):
    rect = node.get("rect") if node else None
    if not rect or image is None or not window_rect:
        return {"orange_pixels": 0, "fraction": 0.0, "supported": False}
    x0 = max(0, rect["left"] - window_rect["left"])
    y0 = max(0, rect["top"] - window_rect["top"])
    x1 = min(image.width, x0 + rect["width"])
    y1 = min(image.height, y0 + rect["height"])
    if x1 <= x0 or y1 <= y0:
        return {"orange_pixels": 0, "fraction": 0.0, "supported": False}
    crop = image.crop((x0, y0, x1, y1))
    total = crop.width * crop.height
    orange = 0
    for red, green, blue in crop.getdata():
        if (red >= 180 and 25 <= green <= 190 and blue <= 160
                and red >= green + 45 and red >= blue + 55):
            orange += 1
    fraction = (orange / total) if total else 0.0
    return {
        "orange_pixels": orange,
        "fraction": fraction,
        "supported": orange >= 40 and fraction >= 0.045,
    }


def _select_visual_heading_nodes(visible, screenshot_path, window_rect):
    """Choose the actually-rendered heading candidate for each category.

    Multiple stale Page-Frames may expose duplicate heading text. For every
    category score all visible UIA candidates against the captured pixels and
    select the rectangle with the strongest orange heading signal.
    """
    candidates = _heading_candidates(visible)
    selected = {}
    support = {}
    try:
        from PIL import Image
        image = Image.open(screenshot_path).convert("RGB")
    except Exception:
        image = None

    for english, nodes in candidates.items():
        scored = []
        for node in nodes:
            item = _orange_support_for_node(node, image, window_rect)
            scored.append((
                1 if item["supported"] else 0,
                item["fraction"],
                item["orange_pixels"],
                -(node["rect"]["width"] * node["rect"]["height"]),
                node, item,
            ))
        scored.sort(key=lambda row: row[:4], reverse=True)
        _ok, _fraction, _pixels, _area, node, item = scored[0]
        selected[english] = node
        support[english] = dict(item)
        support[english]["candidate_count"] = len(nodes)
        support[english]["selected_rect"] = node.get("rect")
        support[english]["selected_name"] = node.get("name")
    return selected, support


def _heading_orange_support(headings, screenshot_path, window_rect):
    """Compatibility wrapper for callers that already selected headings."""
    result = {}
    try:
        from PIL import Image
        image = Image.open(screenshot_path).convert("RGB")
    except Exception:
        image = None
    for english, node in headings.items():
        result[english] = _orange_support_for_node(node, image, window_rect)
    return result

def _crop_colour_fractions(image, rect, window_rect):
    if not rect or not window_rect:
        return None
    x0 = max(0, rect["left"] - window_rect["left"])
    y0 = max(0, rect["top"] - window_rect["top"])
    x1 = min(image.width, x0 + rect["width"])
    y1 = min(image.height, y0 + rect["height"])
    if x1 <= x0 or y1 <= y0:
        return None
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    total = crop.width * crop.height
    if not total:
        return None
    orange = purple = grey = 0
    for red, green, blue in crop.getdata():
        if (red >= 180 and 25 <= green <= 190 and blue <= 160
                and red >= green + 45 and red >= blue + 55):
            orange += 1
        if (50 <= red < 185 and blue >= 60 and blue > green + 20
                and red > green + 10):
            purple += 1
        if (abs(red - green) < 18 and abs(green - blue) < 18
                and 140 <= red <= 235):
            grey += 1
    return {
        "orange": orange / total,
        "purple": purple / total,
        "grey": grey / total,
    }


def _roster_visual_support(visible, screenshot_path, window_rect):
    """Verify roster badges against the captured pixels, not stale UIA text."""
    result = []
    if not screenshot_path or not window_rect:
        return result
    try:
        from PIL import Image
        image = Image.open(screenshot_path).convert("RGB")
    except Exception:
        return result

    for marker, node in _marker_hits(visible, ROSTER_MARKERS):
        colours = _crop_colour_fractions(image, node.get("rect"), window_rect)
        if not colours:
            continue
        name = (node.get("name") or "").strip().lower()
        supported = False
        kind = ""
        if "post all" in name:
            supported = colours["orange"] >= 0.35
            kind = "post-all"
        elif "posted" in name:
            supported = colours["orange"] >= 0.25
            kind = "posted"
        elif "signed in" in name or "gallery:" in name or name == "rated":
            supported = colours["purple"] >= 0.30
            kind = "purple-badge"
        elif "not rateing" in name:
            supported = colours["grey"] >= 0.40
            kind = "grey-badge"
        elif name == "reviews":
            # New roster pending-rating action. The button is a solid purple
            # pill, unlike the "Reviews 7/11" counters on the Home class cards.
            supported = colours["purple"] >= 0.20
            kind = "review-action"
        elif name == "students":
            # The redesigned roster has an orange Students title directly
            # above the table. This remains available even when every student
            # is already rated and no Reviews action is present.
            supported = colours["orange"] >= 0.025
            kind = "roster-title"
        if supported:
            result.append({
                "marker": marker,
                "name": node.get("name"),
                "kind": kind,
                "rect": node.get("rect"),
                "colours": colours,
            })
    return result


def classify_live_page(visible, heading_doc_ids=None, screenshot_path=None,
                       window_rect=None):
    """Classify the actually rendered mini-program page.

    UIA keeps prior Page-Frames alive and sometimes marks their descendants as
    visible.  Therefore live classification requires screenshot corroboration
    for the two dangerous states (RATING_FORM and CLASS_ROSTER).  Stale text by
    itself never unlocks an automation action.
    """
    headings, support = _select_visual_heading_nodes(
        visible, screenshot_path, window_rect)
    visually_supported = sorted(
        english for english, item in support.items() if item.get("supported"))

    # Geometry is checked only on screenshot-selected candidates. This avoids
    # stale duplicate headings from inactive Chromium Page-Frames. Pixel
    # corroboration is the stronger live-page signal, so stale document IDs do
    # not veto an otherwise visually proven rating form.
    supported_headings = {
        english: node for english, node in headings.items()
        if english in visually_supported
    }
    geometry_ok, geometry_reason = _rating_geometry_ok(supported_headings)

    strong_rating = (
        geometry_ok
        and "Making Skills" in visually_supported
        and "Problem Solving" in visually_supported
        and len(visually_supported) >= 3
    )

    roster_visual = _roster_visual_support(
        visible, screenshot_path, window_rect)
    has_post_all = any(item["kind"] == "post-all" for item in roster_visual)
    badge_count = sum(
        1 for item in roster_visual
        if item["kind"] in ("posted", "purple-badge", "grey-badge"))
    review_actions = [
        item for item in roster_visual
        if item["kind"] == "review-action"
    ]
    roster_titles = [
        item for item in roster_visual
        if item["kind"] == "roster-title"
    ]
    visible_names = {
        (node.get("name") or "").strip().casefold()
        for node in visible
        if (node.get("name") or "").strip()
    }
    new_roster_labels = {
        "students", "sign-in", "photos", "reviews"
    }
    new_roster_label_count = len(new_roster_labels & visible_names)

    strong_roster = (
        (has_post_all and badge_count >= 2)
        or (
            (len(review_actions) >= 1 or len(roster_titles) >= 1)
            and "students" in visible_names
            and new_roster_label_count >= 3
        )
    )

    base_signals = {
        "roster_markers": sorted({m for m, _ in _marker_hits(visible, ROSTER_MARKERS)}),
        "rating_headings_visible": sorted(headings),
        "rating_geometry": geometry_reason,
        "rating_visual_support": support,
        "rating_visual_supported": visually_supported,
        "roster_visual_support": roster_visual,
        "new_roster_label_count": new_roster_label_count,
        "student_markers": sorted({m for m, _ in _marker_hits(visible, STUDENT_MARKERS)}),
        "home_markers": sorted({m for m, _ in _marker_hits(visible, HOME_MARKERS)}),
    }

    if strong_rating:
        return (
            "RATING_FORM",
            ["rating headings visually verified on the captured screen ({} of 5)".format(
                len(visually_supported))],
            base_signals,
        )

    if strong_roster:
        if has_post_all:
            reason = "class roster visually verified (Post All + {} live status badges)".format(
                badge_count
            )
        else:
            reason = "new class roster visually verified ({} live Reviews action(s), {} live Students title(s), {} roster labels)".format(
                len(review_actions), len(roster_titles), new_roster_label_count
            )
        return (
            "CLASS_ROSTER",
            [reason],
            base_signals,
        )

    # Student and Home are non-action states, so text+geometry evidence is safe
    # enough here.  Put Student before Home because stale home markers are often
    # retained under other pages.
    student_markers = base_signals["student_markers"]
    if student_markers:
        return (
            "STUDENT_PAGE",
            ["visible student markers: {}".format(", ".join(student_markers))],
            base_signals,
        )

    home_markers = base_signals["home_markers"]
    if home_markers:
        return (
            "HOME",
            ["visible home markers: {}".format(", ".join(home_markers))],
            base_signals,
        )

    reasons = ["no visually verified actionable page; failing closed"]
    if base_signals["roster_markers"]:
        reasons.append("stale/unverified roster text ignored: {}".format(
            ", ".join(base_signals["roster_markers"])))
    if headings:
        reasons.append("rating headings lacked sufficient pixel corroboration")
    return "UNKNOWN", reasons, base_signals


def classify_page(visible, heading_doc_ids=None):
    """Return ``(state, reasons, signals)`` for a list of visible nodes.

    ``heading_doc_ids``, when supplied, maps each english label to the index of
    the Document subtree that owns its heading; if the visible headings come
    from more than one document, RATING_FORM is refused.
    """
    reasons = []
    signals = {
        "roster_markers": [],
        "rating_headings_visible": [],
        "rating_geometry": "",
        "student_markers": [],
        "home_markers": [],
    }

    roster_hits = _marker_hits(visible, ROSTER_MARKERS)
    roster_markers = sorted({m for m, _ in roster_hits})
    signals["roster_markers"] = roster_markers
    if roster_markers:
        reasons.append("visible roster markers: {}".format(", ".join(roster_markers)))
        return "CLASS_ROSTER", reasons, signals

    headings = _visible_heading_nodes(visible)
    visible_names = sorted(e for e, n in headings.items() if n)
    signals["rating_headings_visible"] = visible_names

    rating_valid = False
    if heading_doc_ids is not None:
        docs = {heading_doc_ids.get(e) for e in visible_names}
        docs.discard(None)
        if len(docs) > 1:
            signals["rating_geometry"] = "visible headings span multiple documents"
        else:
            rating_valid, signals["rating_geometry"] = _rating_geometry_ok(headings)
    else:
        rating_valid, signals["rating_geometry"] = _rating_geometry_ok(headings)

    if rating_valid:
        reasons.append("rating headings visible with valid geometry")
        return "RATING_FORM", reasons, signals
    reasons.append("rating form not confirmed: {}".format(signals["rating_geometry"]))

    student_hits = _marker_hits(visible, STUDENT_MARKERS)
    student_markers = sorted({m for m, _ in student_hits})
    signals["student_markers"] = student_markers
    if student_markers:
        reasons.append("visible student markers: {}".format(", ".join(student_markers)))
        return "STUDENT_PAGE", reasons, signals

    home_hits = _marker_hits(visible, HOME_MARKERS)
    home_markers = sorted({m for m, _ in home_hits})
    signals["home_markers"] = home_markers
    if home_markers:
        reasons.append("visible home markers: {}".format(", ".join(home_markers)))
        return "HOME", reasons, signals

    reasons.append("no decisive visible signal; failing closed")
    return "UNKNOWN", reasons, signals


def build_parent_map(nodes):
    """Map each node index to its parent index, using the pre-order depth list."""
    parents = {}
    stack = []
    for index, node in enumerate(nodes):
        depth = node.get("depth", 0)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            parents[index] = stack[-1][1]
        stack.append((depth, index))
    return parents


def document_index_of(node_index, nodes, parents):
    """Index of the Document subtree that owns ``node_index``, or ``None``."""
    cur = node_index
    seen = set()
    while cur is not None and cur not in seen:
        if nodes[cur].get("control_type") == "Document":
            return cur
        seen.add(cur)
        cur = parents.get(cur)
    return None


def _to_image(rect, window_rect):
    if not rect or not window_rect:
        return None
    return {
        "left": rect["left"] - window_rect["left"],
        "top": rect["top"] - window_rect["top"],
        "width": rect["width"],
        "height": rect["height"],
    }


def draw_overlay(raw_png, out_png, window_rect, client_rect, headings,
                 roster_nodes, student_nodes, home_nodes):
    """Draw the viewport and the signal rectangles onto the screenshot."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return "Pillow unavailable"
    try:
        image = Image.open(raw_png).convert("RGB")
    except Exception:
        return "cannot open screenshot"
    draw = ImageDraw.Draw(image)

    def outline(rect, colour, width=2):
        draw.rectangle(
            [rect["left"], rect["top"],
             rect["left"] + rect["width"], rect["top"] + rect["height"]],
            outline=colour, width=width)

    if window_rect:
        outline({"left": 0, "top": 0, "width": image.width - 1,
                 "height": image.height - 1}, (255, 215, 0), 2)
    if client_rect and window_rect:
        outline(_to_image(client_rect, window_rect), (0, 200, 0), 2)

    for node in headings.values():
        if node:
            rect = _to_image(node["rect"], window_rect)
            if rect:
                outline(rect, (255, 0, 0), 3)
    for _, node in roster_nodes:
        rect = _to_image(node["rect"], window_rect)
        if rect:
            outline(rect, (0, 0, 255), 2)
    for _, node in student_nodes + home_nodes:
        rect = _to_image(node["rect"], window_rect)
        if rect:
            outline(rect, (255, 0, 255), 2)

    image.save(out_png, "PNG")
    return "overlay written"


def _node_json(node):
    return {
        "name": node.get("name"),
        "control_type": node.get("control_type"),
        "class_name": node.get("class_name"),
        "rect": node.get("rect"),
        "is_offscreen": node.get("is_offscreen"),
    }


def record_h1(window_rect, client_rect, out):
    """Record H1 as confirmed, keeping clicks disabled."""
    existing = None
    try:
        with open(wkcommon.CALIBRATION_PATH, encoding="utf-8") as fh:
            existing = json.load(fh)
    except Exception:
        existing = None
    if existing and existing.get("clicks_enabled") is True:
        out.append("calibration: an existing clicks-enabled calibration was left untouched")
        return
    path = wkcommon.save_calibration(
        "H1", window_rect, client_rect,
        "validate_context.py auto-record", clicks_enabled=False)
    out.append("calibration: recorded H1 (clicks_enabled=false) -> {}".format(path))


def build_report(args):
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()

    from pywinauto import Desktop

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    base = os.path.join(wkcommon.REPORTS, "context_{}".format(stamp))
    txt_path, json_path, png_path = base + ".txt", base + ".json", base + ".png"
    raw_png = base + "_raw.png"

    out = []

    def emit(text=""):
        out.append(text)

    emit("Wowkids rating tool - page context classifier (zero clicks)")
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
    emit("window : pid={} title={!r}".format(target["pid"], target["title"]))
    emit("window rect : {},{} {}x{} ({})".format(
        window_rect["left"], window_rect["top"],
        window_rect["width"], window_rect["height"], note))
    emit("client rect : {},{} {}x{}".format(
        client_rect["left"], client_rect["top"],
        client_rect["width"], client_rect["height"]) if client_rect else "-")
    emit("")

    capture_note = wkcommon.capture_window(wrapper, raw_png)
    emit("screenshot : {}".format(capture_note))
    emit("")

    nodes = wkcommon.walk_described(wrapper, args.max_nodes)
    visible = []
    for node in nodes:
        ok, _ = wkcommon.node_is_visibly_present(node, client_rect)
        if ok:
            visible.append(node)
    emit("tree nodes : {} total, {} visibly present".format(len(nodes), len(visible)))
    emit("")

    parents = build_parent_map(nodes)
    node_index = {id(node): i for i, node in enumerate(nodes)}
    visible_heads = _visible_heading_nodes(visible)
    heading_doc_ids = {}
    for english, node in visible_heads.items():
        idx = node_index.get(id(node))
        heading_doc_ids[english] = (
            document_index_of(idx, nodes, parents) if idx is not None else None)

    state, reasons, signals = classify_live_page(
        visible, heading_doc_ids, raw_png, window_rect)

    emit("== classification ==")
    emit("state: {}".format(state))
    for reason in reasons:
        emit("  - {}".format(reason))
    emit("")

    emit("== per-heading visibility ==")
    heading_report = {}
    for english, chinese in wkcommon.CATEGORY_PAIRS:
        node = wkcommon.pick_heading(nodes, english, chinese)
        if node is None:
            emit("  {:<22} NOT FOUND".format(english))
            heading_report[english] = {"found": False}
            continue
        ok, reason = wkcommon.node_is_visibly_present(node, client_rect)
        emit("  {:<22} {} offscreen={} rect={} {}".format(
            english, "VISIBLE" if ok else "hidden",
            node.get("is_offscreen"),
            "{},{} {}x{}".format(node["rect"]["left"], node["rect"]["top"],
                                 node["rect"]["width"], node["rect"]["height"]),
            "" if ok else "(" + reason + ")"))
        heading_report[english] = {
            "found": True,
            "visible": ok,
            "reason": reason,
            "name": node["name"],
            "rect": node["rect"],
            "is_offscreen": node.get("is_offscreen"),
        }
    emit("")

    visual_support = signals.get("rating_visual_support", {})
    if visual_support:
        emit("== visual heading corroboration ==")
        for english, item in visual_support.items():
            emit("  {:<22} supported={} orange={} fraction={:.3f}".format(
                english, item.get("supported"), item.get("orange_pixels", 0),
                item.get("fraction", 0.0)))
        emit("")

    roster_visual = signals.get("roster_visual_support", [])
    if roster_visual:
        emit("== visual roster corroboration ==")
        for item in roster_visual:
            emit("  {:<12} {!r} rect={}".format(
                item.get("kind", ""), item.get("name", ""), item.get("rect")))
        emit("")

    roster_nodes = _marker_hits(visible, ROSTER_MARKERS)
    student_nodes = _marker_hits(visible, STUDENT_MARKERS)
    home_nodes = _marker_hits(visible, HOME_MARKERS)

    emit("== visible signal details ==")
    emit("roster markers : {}".format(
        ", ".join(sorted({m for m, _ in roster_nodes})) or "none"))
    for marker, node in roster_nodes:
        emit("   {:<16} {} {!r}".format(
            marker, node["control_type"], (node["name"] or "")[:60]))
    emit("student markers: {}".format(
        ", ".join(sorted({m for m, _ in student_nodes})) or "none"))
    emit("home markers   : {}".format(
        ", ".join(sorted({m for m, _ in home_nodes})) or "none"))
    emit("")

    draw_note = draw_overlay(raw_png, png_path, window_rect, client_rect,
                             visible_heads, roster_nodes, student_nodes, home_nodes)
    emit("overlay : {}".format(draw_note))
    emit("")

    record_h1(window_rect, client_rect, out)

    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "window": {
            "pid": target["pid"],
            "title": target["title"],
            "class_name": target.get("class_name"),
        },
        "window_rect": window_rect,
        "client_rect": client_rect,
        "total_nodes": len(nodes),
        "visible_nodes": len(visible),
        "classification": state,
        "reasons": reasons,
        "signals": signals,
        "heading_report": heading_report,
        "roster_nodes": [_node_json(n) for _, n in roster_nodes],
        "student_nodes": [_node_json(n) for _, n in student_nodes],
        "home_nodes": [_node_json(n) for _, n in home_nodes],
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print("classification: {}".format(state))
    for reason in reasons:
        print("  - {}".format(reason))
    print("TEXT : {}".format(txt_path))
    print("JSON : {}".format(json_path))
    print("PNG  : {}".format(png_path))
    return 0


def _syn(control_type, name, left, top, width, height, offscreen=False, depth=8):
    return {
        "control_type": control_type, "name": name, "depth": depth,
        "rect": {"left": left, "top": top, "width": width, "height": height},
        "is_offscreen": offscreen,
    }


def selftest():
    checks = []

    def check(label, condition, detail=""):
        checks.append((label, bool(condition), detail))
        print("[{}] {}".format("PASS" if condition else "FAIL", label),
              (" - " + detail) if detail else "")

    def heading_nodes():
        return [
            _syn("Text", "Making Skills / 动手制作能力", 40, 150, 200, 24),
            _syn("Text", "Problem Solving / 问题解决能力", 40, 300, 220, 24),
            _syn("Text", "Theory & Application / 理论实践能力", 40, 450, 240, 24),
            _syn("Text", "Creative Thinking / 创意思维能力", 40, 600, 220, 24),
            _syn("Text", "Interpersonal Skills / 人际交往能力", 40, 750, 240, 24),
        ]

    roster = heading_nodes()[:3] + [
        _syn("Hyperlink", "Post All", 0, 850, 80, 30),
        _syn("Text", "Not rateing", 200, 200, 60, 15),
        _syn("Text", "Signed in", 200, 300, 40, 10),
    ]
    state, _, _ = classify_page(roster)
    check("roster outweighs rating headings", state == "CLASS_ROSTER", state)

    state, _, _ = classify_page(heading_nodes())
    check("five visible headings -> RATING_FORM", state == "RATING_FORM", state)

    state, _, _ = classify_page([
        _syn("Text", "学员", 40, 100, 40, 20),
        _syn("Text", "No Data", 40, 200, 80, 20)])
    check("student page markers -> STUDENT_PAGE", state == "STUDENT_PAGE", state)

    state, _, _ = classify_page([
        _syn("Hyperlink", "Switch Accounts", 0, 20, 120, 30),
        _syn("Text", "September", 40, 100, 80, 20)])
    check("home markers -> HOME", state == "HOME", state)

    state, _, _ = classify_page([_syn("Text", "WOWKIDS", 0, 0, 80, 20)])
    check("nothing recognisable -> UNKNOWN", state == "UNKNOWN", state)

    offscreen_heads = [
        heading_nodes()[0],
        _syn("Text", "Problem Solving / 问题解决能力", 40, 300, 220, 24,
             offscreen=True)]
    state, _, _ = classify_page(offscreen_heads)
    check("offscreen heading is not counted", state != "RATING_FORM", state)

    state, _, _ = classify_page(
        heading_nodes(),
        heading_doc_ids={"Making Skills": 0, "Problem Solving": 0,
                         "Theory & Application": 0, "Creative Thinking": 1,
                         "Interpersonal Skills": 1})
    check("headings across two documents refused", state != "RATING_FORM", state)

    failed = [label for label, ok, _ in checks if not ok]
    print("")
    if failed:
        print("{} check(s) FAILED: {}".format(len(failed), ", ".join(failed)))
        return 1
    print("all checks passed")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Wowkids page context classifier")
    parser.add_argument("--max-nodes", type=int, default=12000)
    parser.add_argument("--selftest", action="store_true",
                        help="run the offline classification self-test and exit")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    return build_report(args)


if __name__ == "__main__":
    raise SystemExit(main())