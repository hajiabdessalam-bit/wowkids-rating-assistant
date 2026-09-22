"""Shared plumbing for the Wowkids rating tool.

Everything that is not stage-specific lives here: the vendored-library
bootstrap, DPI handling, WeChat mini program window discovery and logging.
The helper functions in this module are read-only with respect to the mini
program - they look, they never click.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LIBS = os.path.join(HERE, "libs")
REPORTS = os.path.join(HERE, "reports")
LOGS = os.path.join(HERE, "logs")

# The mini program runs inside WeChat's mini program host process.
WECHAT_EXE_PREFIX = "wechatappex"
# Windows we are willing to automate; anything else is reported and refused.
WINDOW_TITLE_HINTS = ("wowkids", "\u7ba1\u7406\u5de5\u5177")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# The five rating categories, always in this order. Each entry is
# (english label, chinese label) exactly as the mini program shows them.
CATEGORY_PAIRS = [
    ("Making Skills", "\u52a8\u624b\u5236\u4f5c\u80fd\u529b"),
    ("Problem Solving", "\u95ee\u9898\u89e3\u51b3\u80fd\u529b"),
    ("Theory & Application", "\u7406\u8bba\u5b9e\u8df5\u80fd\u529b"),
    ("Creative Thinking", "\u521b\u610f\u601d\u7ef4\u80fd\u529b"),
    ("Interpersonal Skills", "\u4eba\u9645\u4ea4\u5f80\u80fd\u529b"),
]
CATEGORY_TERMS = [term for pair in CATEGORY_PAIRS for term in pair]
CATEGORY_SHORT = ["Making", "Problem", "Theory", "Creative", "Interpersonal"]
SUBMIT_TERMS = ("\u63d0\u4ea4", "Submit", "submit")

# The mini program is a Chromium view, so most nodes are Text/Image/Hyperlink.
INTERACTIVE_TYPES = {"Hyperlink", "Button", "RadioButton", "ListItem",
                     "CheckBox", "MenuItem", "TabItem", "Custom"}


# --------------------------------------------------------------------------
# vendored libraries
# --------------------------------------------------------------------------

def bootstrap_libs() -> str:
    """Make the vendored libraries importable and their DLLs loadable."""
    for path in (
        LIBS,
        os.path.join(LIBS, "win32"),
        os.path.join(LIBS, "win32", "lib"),
        os.path.join(LIBS, "pythonwin"),
        os.path.join(LIBS, "Pythonwin"),
    ):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)

    dll_dir = os.path.join(LIBS, "pywin32_system32")
    if os.path.isdir(dll_dir):
        try:
            os.add_dll_directory(dll_dir)
        except Exception:
            pass

    # pywin32 ships this to fix up its DLL search path when its post-install
    # step never ran (which is our case, since we unpack the wheel by hand).
    if os.path.exists(os.path.join(LIBS, "pywin32_bootstrap.py")):
        try:
            import pywin32_bootstrap  # noqa: F401
        except Exception:
            pass
    return LIBS


def set_dpi_awareness() -> str:
    """Use physical pixels so UI Automation rectangles match the cursor."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


def enable_utf8_stdout() -> None:
    """Never crash a run just because the console codepage is cp936."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------
# window discovery
# --------------------------------------------------------------------------

def process_exe_name(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def window_rectangle(wrapper):
    try:
        rect = wrapper.rectangle()
        return {
            "left": rect.left,
            "top": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        }
    except Exception:
        return None


def is_minimized(wrapper) -> bool:
    try:
        return bool(ctypes.windll.user32.IsIconic(wrapper.handle))
    except Exception:
        return False


def wowkids_windows(desktop=None):
    """Every open window owned by the WeChat mini program host process."""
    from pywinauto import Desktop

    desktop = desktop or Desktop(backend="uia")
    found = []
    for wrapper in desktop.windows():
        try:
            info = wrapper.element_info
            pid = info.process_id
            exe = process_exe_name(pid)
            if not exe.lower().startswith(WECHAT_EXE_PREFIX):
                continue
            found.append(
                {
                    "wrapper": wrapper,
                    "pid": pid,
                    "exe": exe,
                    "title": info.name or "",
                    "class_name": info.class_name or "",
                    "framework": getattr(info, "framework_id", "") or "",
                    "rect": window_rectangle(wrapper),
                    "minimized": is_minimized(wrapper),
                }
            )
        except Exception:
            continue
    return found


def select_wowkids_window(windows):
    """Pick the single mini program window we are willing to drive.

    Returns ``(window_info, reason)``. ``window_info`` is ``None`` whenever
    the choice is not unambiguous - the caller must refuse to continue then.
    """
    if not windows:
        return None, "no WeChat mini program window found (is it open?)"

    hinted = [
        w
        for w in windows
        if any(hint.lower() in w["title"].lower() for hint in WINDOW_TITLE_HINTS)
    ]
    if len(hinted) == 1:
        return hinted[0], ""
    if len(hinted) > 1:
        return None, "{} windows match the Wowkids title".format(len(hinted))
    if len(windows) == 1:
        return windows[0], "matched only by process, title was {!r}".format(
            windows[0]["title"])
    return None, "{} WeChatAppEx windows open and none matched the title".format(
        len(windows))


# --------------------------------------------------------------------------
# UI Automation inspection helpers
# --------------------------------------------------------------------------

PATTERN_ATTRS = (
    "iface_invoke",
    "iface_selection",
    "iface_selection_item",
    "iface_toggle",
    "iface_value",
    "iface_range_value",
    "iface_expand_collapse",
    "iface_scroll",
    "iface_scroll_item",
    "iface_legacy_iaccessible",
    "iface_text",
    "iface_grid",
    "iface_grid_item",
    "iface_table",
    "iface_table_item",
    "iface_window",
    "iface_transform",
)


def pattern_names(wrapper) -> list:
    names = []
    for attr in PATTERN_ATTRS:
        try:
            if getattr(wrapper, attr, None) is not None:
                names.append(attr.replace("iface_", ""))
        except Exception:
            continue
    return names


def node_value(wrapper):
    try:
        return wrapper.get_value()
    except Exception:
        return None


def _uia_current_bool(wrapper, prop):
    """Read a ``Current*`` boolean off the UIA element, or ``None`` if absent.

    Distinguishes "offscreen" from "unknown": a missing property becomes
    ``None``, never a proof that the node is visible or offscreen.
    """
    try:
        return bool(getattr(wrapper.element_info.element, prop))
    except Exception:
        return None


def describe(wrapper) -> dict:
    info = wrapper.element_info
    return {
        "control_type": info.control_type or "",
        "name": info.name or "",
        "automation_id": info.automation_id or "",
        "class_name": info.class_name or "",
        "rect": window_rectangle(wrapper),
        "patterns": pattern_names(wrapper),
        "value": node_value(wrapper),
        "is_offscreen": _uia_current_bool(wrapper, "CurrentIsOffscreen"),
        "is_enabled": _uia_current_bool(wrapper, "CurrentIsEnabled"),
        "is_keyboard_focusable": _uia_current_bool(
            wrapper, "CurrentIsKeyboardFocusable"),
        "has_keyboard_focus": _uia_current_bool(
            wrapper, "CurrentHasKeyboardFocus"),
    }


def walk_described(root, max_nodes: int = 8000) -> list:
    """Depth-first walk; each entry is :func:`describe` plus ``depth``/``wrapper``."""
    nodes = []
    stack = [(root, 0)]
    while stack and len(nodes) < max_nodes:
        wrapper, depth = stack.pop()
        node = {"depth": depth, "wrapper": wrapper}
        try:
            node.update(describe(wrapper))
        except Exception as exc:
            node.update(
                {
                    "control_type": "?",
                    "name": "<describe failed: {}>".format(exc),
                    "automation_id": "",
                    "class_name": "",
                    "rect": None,
                    "patterns": [],
                    "value": None,
                }
            )
        nodes.append(node)
        try:
            children = wrapper.children()
        except Exception:
            children = []
        for child in reversed(children):
            stack.append((child, depth + 1))
    return nodes


def without_wrappers(nodes: list) -> list:
    return [
        {key: value for key, value in node.items() if key != "wrapper"}
        for node in nodes
    ]


def rect_center(rect):
    if not rect:
        return None
    return (
        rect["left"] + rect["width"] // 2,
        rect["top"] + rect["height"] // 2,
    )


VISIBLE_MIN_AREA = 20     # px^2: smallest intersection that still counts
VISIBLE_FRACTION = 0.35   # fraction of a node's area that must be on screen


def rect_intersection_area(rect, client):
    """Area (px^2) of the overlap between ``rect`` and the ``client`` viewport."""
    if not rect or not client:
        return 0
    x0 = max(rect["left"], client["left"])
    x1 = min(rect["left"] + rect["width"], client["left"] + client["width"])
    y0 = max(rect["top"], client["top"])
    y1 = min(rect["top"] + rect["height"], client["top"] + client["height"])
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def node_is_visibly_present(node, client_rect):
    """Return ``(ok, reason)`` for whether ``node`` is really on screen.

    A node only counts when its state and geometry agree: ``is_offscreen`` is
    not explicitly true, the rectangle is non-zero, and a meaningful fraction
    of it is inside the client viewport. A missing ``is_offscreen`` (``None``)
    is treated as unknown and falls back to geometry only.
    """
    if node.get("is_offscreen") is True:
        return False, "offscreen"
    rect = node.get("rect")
    if not rect or rect["width"] <= 0 or rect["height"] <= 0:
        return False, "empty-rect"
    if not client_rect:
        return False, "no-client-viewport"
    area = rect["width"] * rect["height"]
    inter = rect_intersection_area(rect, client_rect)
    if inter < VISIBLE_MIN_AREA:
        return False, "outside-viewport"
    if inter < area * VISIBLE_FRACTION:
        return False, "mostly-outside-viewport"
    return True, ""


def prepare_background_window(wrapper) -> str:
    """Make WOWKIDS capturable without stealing keyboard focus.

    Normal batch operation should leave the coach free to use other apps.
    If WOWKIDS was minimised, show it without activation. We deliberately do
    NOT call SetForegroundWindow here.
    """
    try:
        handle = wrapper.handle
    except Exception as exc:
        return "no window handle ({})".format(exc)
    try:
        if ctypes.windll.user32.IsIconic(handle):
            # SW_SHOWNOACTIVATE: restore/show without activating the window.
            ctypes.windll.user32.ShowWindow(handle, 4)
            time.sleep(0.35)
            return "shown without activation"
    except Exception as exc:
        return "background restore failed: {}".format(exc)
    return "already available"


def restore_window(wrapper) -> str:
    """Undo a minimised window so the Chromium view is laid out again.

    A minimised mini program reports a 0x0 window rectangle, which makes every
    geometry decision unreliable, so this runs before any measurement.
    """
    notes = []
    try:
        handle = wrapper.handle
    except Exception as exc:
        return "no window handle ({})".format(exc)
    try:
        if ctypes.windll.user32.IsIconic(handle):
            ctypes.windll.user32.ShowWindow(handle, 9)  # SW_RESTORE
            time.sleep(0.6)
            notes.append("restored from minimised")
    except Exception as exc:
        notes.append("restore failed: {}".format(exc))
    try:
        ctypes.windll.user32.SetForegroundWindow(handle)
        time.sleep(0.2)
        notes.append("raised")
    except Exception:
        pass
    return ", ".join(notes) if notes else "already visible"


# --------------------------------------------------------------------------
# rating page mapping (shared by the Stage 2 detector and the Stage 3 filler)
# --------------------------------------------------------------------------

BAND_GAP = 4          # shave this many pixels off each section band edge
ROW_TOLERANCE = 8     # two nodes share a row when their centres are this close
MIN_OPTION_AREA = 24
MAX_OPTION_WIDTH = 320
MAX_OPTION_HEIGHT = 110


def _area(rect):
    return rect["width"] * rect["height"] if rect else 0


def pick_heading(nodes: list, english: str, chinese: str):
    """Smallest, deepest node carrying this category's label."""
    hits = [
        node
        for node in nodes
        if english.lower() in (node["name"] or "").lower()
        or chinese in (node["name"] or "")
    ]
    if not hits:
        return None
    sized = [node for node in hits if node["rect"]]
    pool = sized or hits
    pool.sort(key=lambda node: (_area(node["rect"]) or 10 ** 9, -node["depth"]))
    return pool[0]


def collect_rows(nodes: list, band, heading) -> list:
    """Option-sized nodes inside ``band``, grouped into rows left to right."""
    inside = []
    heading_rect = heading["rect"]
    for node in nodes:
        rect = node["rect"]
        if not rect or rect["width"] <= 0 or rect["height"] <= 0:
            continue
        if _area(rect) < MIN_OPTION_AREA:
            continue
        if rect["width"] > MAX_OPTION_WIDTH or rect["height"] > MAX_OPTION_HEIGHT:
            continue
        if heading_rect and rect == heading_rect:
            continue
        centre_y = rect["top"] + rect["height"] / 2.0
        if not (band[0] <= centre_y < band[1]):
            continue
        inside.append(node)

    inside.sort(key=lambda node: node["rect"]["top"] + node["rect"]["height"] / 2.0)
    rows = []
    for node in inside:
        centre_y = node["rect"]["top"] + node["rect"]["height"] / 2.0
        if rows and abs(centre_y - rows[-1]["centre_y"]) <= ROW_TOLERANCE:
            rows[-1]["items"].append(node)
            count = len(rows[-1]["items"])
            rows[-1]["centre_y"] = (
                rows[-1]["centre_y"] * (count - 1) + centre_y) / count
        else:
            rows.append({"centre_y": centre_y, "items": [node]})
    for row in rows:
        row["items"].sort(key=lambda node: node["rect"]["left"])
    rows.sort(key=lambda row: row["centre_y"])
    return rows


def map_rating_sections(nodes: list) -> dict:
    """Locate the five sections and their option rows.

    Returns ``{missing, order_ok, sections}``. ``sections`` is empty when any
    heading is missing, so callers can refuse without guessing.
    """
    found = []
    missing = []
    for english, chinese in CATEGORY_PAIRS:
        node = pick_heading(nodes, english, chinese)
        if node is None:
            missing.append(english)
        found.append((english, chinese, node))
    if missing:
        return {"missing": missing, "order_ok": False, "sections": []}

    ordered = sorted(
        [item for item in found if item[2] and item[2]["rect"]],
        key=lambda item: item[2]["rect"]["top"],
    )
    expected = [pair[0] for pair in CATEGORY_PAIRS]
    order_ok = [item[0] for item in ordered] == expected
    page_bottom = max(
        (node["rect"]["top"] + node["rect"]["height"]
         for node in nodes if node["rect"]),
        default=0,
    )

    sections = []
    for index, (english, chinese, node) in enumerate(ordered):
        rect = node["rect"]
        if index + 1 < len(ordered):
            next_rect = ordered[index + 1][2]["rect"]
            band = (rect["top"] + rect["height"] - BAND_GAP,
                    next_rect["top"] - BAND_GAP)
        else:
            band = (rect["top"] + rect["height"] - BAND_GAP,
                    min(page_bottom, rect["top"] + rect["height"] + 320) + BAND_GAP)
        sections.append(
            {
                "english": english,
                "chinese": chinese,
                "heading": {
                    "control_type": node["control_type"],
                    "name": node["name"],
                    "rect": rect,
                    "patterns": node["patterns"],
                },
                "heading_node": node,
                "band": band,
                "rows": collect_rows(nodes, band, node),
            }
        )
    return {"missing": [], "order_ok": order_ok, "sections": sections}


def frame_inventory(nodes: list) -> list:
    """Document frames plus the text each one contains (diagnostics)."""
    frames = []
    for index, node in enumerate(nodes):
        if node["control_type"] != "Document":
            continue
        depth = node["depth"]
        subtree = []
        for other in nodes[index + 1:]:
            if other["depth"] <= depth:
                break
            subtree.append(other)
        texts = []
        for item in subtree:
            name = (item["name"] or "").strip()
            if item["control_type"] == "Text" and name and name not in texts:
                texts.append(name)
        frames.append(
            {
                "name": node["name"],
                "rect": node["rect"],
                "node_count": len(subtree),
                "texts": texts[:40],
            }
        )
    return frames


# --------------------------------------------------------------------------
# navigation exclusion, score rows, and page validation
# --------------------------------------------------------------------------

# Anything with these names is never a valid automation target while probing.
NAV_NAMES = ("home", "course", "student", "\u5b66\u5458", "\u9996\u9875", "\u8bfe\u7a0b")
FORBIDDEN_NAMES = NAV_NAMES + (
    "post all",
    "\u63d0\u4ea4",
    "submit",
    "gallery",
)

SCORE_ROW_TOLERANCE = 8      # px: same row when vertical centres are this close
SCORE_ROW_X_ALIGNMENT = 12   # px: left edges of the five rows must line up
BOTTOM_EXCLUSION_BAND = 140  # px: never click inside this part of the client area


def win32_client_rect(wrapper):
    """Client area of the window, in screen pixels."""
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetClientRect(wrapper.handle)
        screen_x, screen_y = win32gui.ClientToScreen(wrapper.handle, (left, top))
        return {
            "left": screen_x,
            "top": screen_y,
            "width": right - left,
            "height": bottom - top,
        }
    except Exception:
        return None


def group_rows_by_y(nodes: list, tolerance: int = SCORE_ROW_TOLERANCE) -> list:
    """Group nodes into rows by vertical centre; each row sorted left to right."""
    ordered = sorted(
        [node for node in nodes if node.get("rect")],
        key=lambda node: node["rect"]["top"] + node["rect"]["height"] / 2.0,
    )
    rows = []
    for node in ordered:
        centre_y = node["rect"]["top"] + node["rect"]["height"] / 2.0
        if rows and abs(centre_y - rows[-1]["centre_y"]) <= tolerance:
            rows[-1]["items"].append(node)
            count = len(rows[-1]["items"])
            rows[-1]["centre_y"] = (
                rows[-1]["centre_y"] * (count - 1) + centre_y) / count
        else:
            rows.append({"centre_y": centre_y, "items": [node]})
    for row in rows:
        row["items"].sort(key=lambda node: node["rect"]["left"])
    rows.sort(key=lambda row: row["centre_y"])
    return rows


def nodes_inside_band(nodes: list, band) -> list:
    inside = []
    for node in nodes:
        rect = node.get("rect")
        if not rect or rect["width"] <= 0 or rect["height"] <= 0:
            continue
        centre_y = rect["top"] + rect["height"] / 2.0
        if band[0] <= centre_y < band[1]:
            inside.append(node)
    return inside


def detect_score_rows(nodes: list, section: dict) -> dict:
    """Find the five stacked score rows of a section.

    The real layout is vertical:
        circle + one star
        circle + two stars
        ... up to five stars
    A single horizontal line holding five nodes is explicitly NOT accepted.
    """
    inside = nodes_inside_band(nodes, section["band"])
    heading_rect = section["heading"]["rect"]
    candidates = [
        node
        for node in inside
        if node["rect"] != heading_rect and node["rect"]["height"] <= 60
    ]
    rows = group_rows_by_y(candidates)
    if not rows:
        return {"layout": "none", "rows": [], "reason": "nothing inside the band"}

    wide = [row for row in rows if len(row["items"]) >= 5]
    if wide and len(rows) < 5:
        return {
            "layout": "horizontal-rejected",
            "rows": [],
            "reason": "one line holds {} nodes; that is not the score layout".format(
                len(wide[0]["items"])),
        }

    def looks_like_option_row(row) -> bool:
        """A score row carries a marker: an image/radio or a small text node."""
        for item in row["items"]:
            if item["control_type"] in (
                "Image", "RadioButton", "Button", "CheckBox", "ListItem", "MenuItem"
            ):
                return True
            if item["control_type"] == "Text" and item["rect"]["width"] <= 40:
                return True
        return False

    rows = [row for row in rows if looks_like_option_row(row)]
    if len(rows) < 5:
        return {
            "layout": "none",
            "rows": [],
            "reason": "only {} option-like rows inside the band".format(len(rows)),
        }

    # Keep rows whose leftmost item lines up with the others.
    rows_by_left = sorted(rows, key=lambda row: row["items"][0]["rect"]["left"])
    clusters = []
    for row in rows_by_left:
        left = row["items"][0]["rect"]["left"]
        if clusters and abs(left - clusters[-1]["left"]) <= SCORE_ROW_X_ALIGNMENT:
            clusters[-1]["rows"].append(row)
            count = len(clusters[-1]["rows"])
            clusters[-1]["left"] = (
                clusters[-1]["left"] * (count - 1) + left) / count
        else:
            clusters.append({"left": left, "rows": [row]})
    clusters.sort(key=lambda cluster: -len(cluster["rows"]))
    best = clusters[0] if clusters else None
    if not best or len(best["rows"]) < 5:
        return {
            "layout": "none",
            "rows": [],
            "reason": "only {} vertically aligned rows found".format(
                len(best["rows"]) if best else 0),
        }
    ordered = sorted(best["rows"], key=lambda row: row["centre_y"])
    if len(ordered) == 5:
        chosen = ordered
    else:
        # More than five candidates: keep the five with the most even spacing.
        best_window = None
        best_variance = None
        for start in range(len(ordered) - 4):
            window = ordered[start:start + 5]
            gaps = [
                window[index + 1]["centre_y"] - window[index]["centre_y"]
                for index in range(4)
            ]
            mean = sum(gaps) / len(gaps)
            variance = sum((gap - mean) ** 2 for gap in gaps) / len(gaps)
            if best_variance is None or variance < best_variance:
                best_variance = variance
                best_window = window
        chosen = best_window
    ys = [row["centre_y"] for row in chosen]
    if not all(ys[index] < ys[index + 1] for index in range(4)):
        return {"layout": "none", "rows": [], "reason": "y order is not increasing"}
    lefts = [row["items"][0]["rect"]["left"] for row in chosen]
    if max(lefts) - min(lefts) > SCORE_ROW_X_ALIGNMENT:
        return {"layout": "none", "rows": [], "reason": "rows are not x aligned"}

    result = []
    for index, row in enumerate(chosen):
        leftmost = row["items"][0]
        result.append(
            {
                "score": index + 1,
                "row_rect": {
                    "left": min(item["rect"]["left"] for item in row["items"]),
                    "top": min(item["rect"]["top"] for item in row["items"]),
                    "width": max(item["rect"]["left"] + item["rect"]["width"]
                                 for item in row["items"])
                    - min(item["rect"]["left"] for item in row["items"]),
                    "height": max(item["rect"]["top"] + item["rect"]["height"]
                                  for item in row["items"])
                    - min(item["rect"]["top"] for item in row["items"]),
                },
                "target_rect": leftmost["rect"],
                "target_type": leftmost["control_type"],
                "target_name": leftmost["name"],
                "item_count": len(row["items"]),
                "items": [
                    {
                        "type": item["control_type"],
                        "name": item["name"],
                        "rect": item["rect"],
                        "patterns": item["patterns"],
                    }
                    for item in row["items"]
                ],
            }
        )
    return {"layout": "vertical", "rows": result, "reason": ""}


def validate_rating_page(nodes: list) -> dict:
    """Strict gate that must pass before any expansion click is allowed."""
    reasons = []
    found = {}
    for english, chinese in CATEGORY_PAIRS:
        found[english] = pick_heading(nodes, english, chinese)
    if found["Making Skills"] is None:
        reasons.append("Making Skills heading not found")
    if found["Problem Solving"] is None:
        reasons.append("Problem Solving heading not found")

    best_frame = None
    best_count = 0
    for frame in frame_inventory(nodes):
        joined = " ".join(frame["texts"])
        count = sum(
            1
            for english, chinese in CATEGORY_PAIRS
            if english.lower() in joined.lower() or chinese in joined
        )
        if count > best_count:
            best_count = count
            best_frame = frame
    if best_count < 3:
        reasons.append(
            "only {} of the 5 categories are in one document".format(best_count))

    joined_all = " ".join(node["name"] or "" for node in nodes)
    if "\u6682\u65e0\u6570\u636e" in joined_all or "No Data" in joined_all:
        if best_count < 3:
            reasons.append("page shows 'No Data' without the rating categories")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "found": found,
        "category_count": best_count,
        "frame": best_frame,
    }


def forbidden_rects(nodes: list) -> list:
    """Rectangles that automation must never click into."""
    rects = []
    for node in nodes:
        name = (node["name"] or "").strip().lower()
        if not name or not node.get("rect"):
            continue
        if any(word == name or word in name for word in FORBIDDEN_NAMES):
            rects.append({"name": node["name"], "rect": node["rect"]})
    return rects


# --------------------------------------------------------------------------
# coordinate space calibration
# --------------------------------------------------------------------------

CALIBRATION_PATH = os.path.join(HERE, "calibration.json")
HYPOTHESES = ("H1", "H2", "H3")


def load_calibration():
    """The confirmed coordinate space, or ``None`` when unconfirmed.

    Clicks stay gated: a calibration only counts when ``clicks_enabled`` is
    explicitly true, so recording H1 alone never unlocks a mouse action.
    """
    try:
        with open(CALIBRATION_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if (data.get("hypothesis") in HYPOTHESES
                and data.get("confirmed")
                and data.get("clicks_enabled") is True):
            return data
    except Exception:
        pass
    return None


def save_calibration(hypothesis: str, window_rect, client_rect, source: str,
                     clicks_enabled: bool = False) -> str:
    data = {
        "hypothesis": hypothesis,
        "confirmed": True,
        "clicks_enabled": bool(clicks_enabled),
        "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "window_rect_at_confirmation": window_rect,
        "client_rect_at_confirmation": client_rect,
    }
    with open(CALIBRATION_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return CALIBRATION_PATH


def raw_point_to_screen(point, hypothesis, window_rect, client_rect):
    """Convert a raw UIA point into screen pixels using the confirmed space."""
    if point is None or not window_rect:
        return None
    x, y = point
    if hypothesis == "H1":          # raw rects are already screen pixels
        return x, y
    if hypothesis == "H2":          # relative to the window rectangle
        return x + window_rect["left"], y + window_rect["top"]
    if not client_rect:             # H3: relative to the client area
        return None
    return x + client_rect["left"], y + client_rect["top"]


def bottom_band_limit_raw(client_rect, window_rect, hypothesis):
    """Y of the bottom-exclusion band, expressed in raw UIA coordinates."""
    if not client_rect:
        return None
    if hypothesis == "H1":
        origin = client_rect["top"]
    elif hypothesis == "H2":
        origin = client_rect["top"] - (window_rect["top"] if window_rect else 0)
    else:
        origin = 0
    return origin + client_rect["height"] - BOTTOM_EXCLUSION_BAND


def expansion_click_ok(point, section, client_rect, nodes, hypothesis=None,
                       window_rect=None, margin: int = 12):
    """Decide whether an expansion click is allowed. Returns ``(ok, reason)``.

    All comparisons happen in raw UIA coordinates, which is the space the
    heading, band and exclusion rectangles are measured in.
    """
    if point is None:
        return False, "no click point"
    heading = section["heading"]["rect"]
    x, y = point

    if not (heading["left"] - 8 <= x <= heading["left"] + heading["width"] + 8):
        return False, "click is not horizontally inside the category card"

    if not (heading["top"] - 8 <= y <= heading["top"] + heading["height"] + 8):
        return False, "click is not on the heading line"

    limit = bottom_band_limit_raw(client_rect, window_rect, hypothesis)
    if limit is not None and y >= limit:
        return False, "click is inside the bottom {0}px exclusion band".format(
            BOTTOM_EXCLUSION_BAND)

    score = detect_score_rows(nodes, section)
    if score["layout"] == "vertical":
        first_row_top = score["rows"][0]["row_rect"]["top"]
        if y >= first_row_top - 4:
            return False, "click is not above the first score row"

    for item in forbidden_rects(nodes):
        rect = item["rect"]
        if (
            rect["left"] - margin <= x <= rect["left"] + rect["width"] + margin
            and rect["top"] - margin <= y <= rect["top"] + rect["height"] + margin
        ):
            return False, "click is within {0}px of {1!r}".format(margin, item["name"])

    return True, "ok"


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

class Logger:
    """Writes the same line to the console and to a log file."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def __call__(self, message: str) -> None:
        line = "{} {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), message)
        try:
            print(line)
        except Exception:
            pass
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


# --------------------------------------------------------------------------
# screenshots (Windows 10 compatible: PrintWindow, then BitBlt fallback)
# --------------------------------------------------------------------------

PW_RENDERFULLCONTENT = 2
CAPTUREBLT = 0x40000000


def _bitmap_to_png(bits, width, height, path) -> bool:
    try:
        from PIL import Image
    except Exception:
        return False
    image = Image.frombuffer(
        "RGB", (width, height), bits, "raw", "BGRX", 0, 1)
    image.save(path, "PNG")
    return True


def _looks_black(bits, width, height) -> bool:
    """True when the capture came back empty (common for GPU composited windows)."""
    if not bits:
        return True
    step = max(1, (len(bits) // 4096) // 4 * 4)
    sampled = 0
    bright = 0
    for index in range(0, len(bits) - 4, step):
        sampled += 1
        if bits[index] or bits[index + 1] or bits[index + 2]:
            bright += 1
    if sampled == 0:
        return True
    return bright < max(1, sampled // 200)


def capture_window(wrapper, path: str) -> str:
    """Save a PNG of the window; returns a short status string."""
    try:
        import win32con
        import win32gui
        import win32ui
    except Exception as exc:
        return "win32 modules unavailable: {}".format(exc)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        handle = wrapper.handle
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return "window has no size"

        window_dc = win32gui.GetWindowDC(handle)
        mfc_dc = win32ui.CreateDCFromHandle(window_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)

        ctypes.windll.user32.PrintWindow(handle, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        bits = bitmap.GetBitmapBits(True)
        mode = "PrintWindow"
        if _looks_black(bits, width, height):
            desktop_dc = win32gui.GetWindowDC(win32gui.GetDesktopWindow())
            screen_dc = win32ui.CreateDCFromHandle(desktop_dc)
            save_dc.BitBlt((0, 0), (width, height), screen_dc, (left, top),
                           win32con.SRCCOPY | CAPTUREBLT)
            bits = bitmap.GetBitmapBits(True)
            mode = "BitBlt"
            screen_dc.DeleteDC()
            win32gui.ReleaseDC(win32gui.GetDesktopWindow(), desktop_dc)

        saved = _bitmap_to_png(bits, width, height, path)
        if not saved:
            bmp_path = os.path.splitext(path)[0] + ".bmp"
            bitmap.SaveBitmapFile(save_dc, bmp_path)
            path = bmp_path
            mode += "+BMP"

        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(handle, window_dc)
        black = _looks_black(bits, width, height)
        return "{} {}{}".format(
            mode, "BLACK?" if black else "ok", " -> " + os.path.basename(path))
    except Exception as exc:
        return "capture failed: {}".format(exc)
