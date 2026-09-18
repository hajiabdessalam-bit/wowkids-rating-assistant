from __future__ import annotations

import ctypes
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

ABORT_KEY = 0x1B
CATEGORY_ORDER = [name for name, _cn in wkcommon.CATEGORY_PAIRS]


def esc_pressed():
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(ABORT_KEY) & 0x8000)
    except Exception:
        return False


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

    changed = strong = total_delta = total = 0
    for pa, pb in zip(a.crop(box).getdata(), b.crop(box).getdata()):
        delta = abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2])
        total_delta += delta
        total += 1
        if delta >= 18:
            changed += 1
        if delta >= 60:
            strong += 1

    mean_delta = total_delta / float(total) if total else 0.0
    ok = changed >= 10 and (strong >= 3 or mean_delta >= 1.5)
    return {
        "ok": ok,
        "changed_pixels": changed,
        "strong_pixels": strong,
        "mean_delta": round(mean_delta, 3),
        "box": box,
        "reason": "local radio area changed" if ok else "no reliable local radio change",
    }


class WowkidsRatingSession:
    """Visual/UIA driver for the five rating accordions.

    This class deliberately contains no Submit or Post All method. Its scope is
    limited to expanding/collapsing rating accordions and selecting score rows.
    """

    def __init__(self, reports_dir=None, max_nodes=8000, raise_window=True):
        wkcommon.enable_utf8_stdout()
        wkcommon.bootstrap_libs()
        wkcommon.set_dpi_awareness()

        from pywinauto import Desktop, mouse

        self.mouse = mouse
        self.max_nodes = max_nodes
        self.reports_dir = reports_dir or wkcommon.REPORTS
        self.shots_dir = os.path.join(self.reports_dir, "shots")
        os.makedirs(self.shots_dir, exist_ok=True)

        desktop = Desktop(backend="uia")
        target, reason = wkcommon.select_wowkids_window(
            wkcommon.wowkids_windows(desktop)
        )
        if not target:
            raise RuntimeError(reason)

        self.target = target
        self.wrapper = target["wrapper"]
        if raise_window:
            wkcommon.restore_window(self.wrapper)
        self.window_rect = wkcommon.window_rectangle(self.wrapper)
        self.client_rect = wkcommon.win32_client_rect(self.wrapper)
        if not self.window_rect or not self.client_rect:
            raise RuntimeError("WOWKIDS window geometry unavailable")

    def _visible(self, nodes):
        return [
            node for node in nodes
            if wkcommon.node_is_visibly_present(node, self.client_rect)[0]
        ]

    def park_mouse(self):
        x = self.window_rect["left"] + self.window_rect["width"] - 26
        y = self.window_rect["top"] + 34
        try:
            self.mouse.move(coords=(x, y))
            time.sleep(0.12)
        except Exception:
            pass

    def snapshot(self, label):
        self.park_mouse()
        path = os.path.join(
            self.shots_dir,
            "{}_{}.png".format(wkcommon.timestamp(), label.replace(" ", "_")),
        )
        capture_note = wkcommon.capture_window(self.wrapper, path)
        nodes = wkcommon.walk_described(self.wrapper, self.max_nodes)
        selected, support = ctx._select_visual_heading_nodes(
            self._visible(nodes), path, self.window_rect
        )
        return {
            "path": path,
            "capture": capture_note,
            "nodes": nodes,
            "selected": selected,
            "support": support,
        }

    def verify_rating_form(self):
        snap = self.snapshot("verify_form")
        state, reasons, signals = ctx.classify_live_page(
            self._visible(snap["nodes"]), None, snap["path"], self.window_rect
        )
        return state == "RATING_FORM", {
            "state": state,
            "reasons": reasons,
            "signals": signals,
            "snapshot": snap,
        }

    def section_for(self, selected, category):
        if category not in CATEGORY_ORDER:
            return None
        index = CATEGORY_ORDER.index(category)
        current = selected.get(category)
        if not current or not current.get("rect"):
            return None

        h = current["rect"]
        safe_bottom = (
            self.client_rect["top"] + self.client_rect["height"]
            - wkcommon.BOTTOM_EXCLUSION_BAND
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

    def rows_for(self, snap, category):
        if not snap["support"].get(category, {}).get("supported"):
            return None, "heading not visually supported", None
        section = self.section_for(snap["selected"], category)
        if not section:
            return None, "section geometry unavailable", None
        rows = detect_visual_score_rows(
            snap["path"], self.window_rect, section["heading"]["rect"],
            section["band_bottom"], self.client_rect,
            wkcommon.BOTTOM_EXCLUSION_BAND,
        )
        return rows, "", section

    def _safe_arrow_click(self, snap, section):
        arrow_rect, arrow_reason = find_expand_arrow(
            snap["path"], self.window_rect, section
        )
        ok, guard_reason = arrow_click_ok(
            arrow_rect, section, self.client_rect
        )
        if not ok:
            raise RuntimeError(
                "accordion arrow not verified: {} / {}".format(
                    arrow_reason, guard_reason
                )
            )
        if esc_pressed():
            raise RuntimeError("ESC pressed")
        point = centre(arrow_rect)
        self.mouse.click(button="left", coords=(point[0], point[1]))
        time.sleep(0.75)
        return point

    def ensure_expanded(self, category):
        snap = self.snapshot("{}_before_expand".format(category))
        rows, reason, section = self.rows_for(snap, category)
        if rows and rows["layout"] == "vertical-visual":
            return snap, rows, {"changed": False, "point": None}
        if not section:
            raise RuntimeError("{}: {}".format(category, reason))
        point = self._safe_arrow_click(snap, section)
        after = self.snapshot("{}_expanded".format(category))
        rows_after, reason_after, _section_after = self.rows_for(after, category)
        if not rows_after or rows_after["layout"] != "vertical-visual":
            raise RuntimeError(
                "{} did not expose verified 1..5 rows: {}".format(
                    category,
                    rows_after["reason"] if rows_after else reason_after,
                )
            )
        return after, rows_after, {"changed": True, "point": point}

    def ensure_collapsed(self, category):
        snap = self.snapshot("{}_before_collapse".format(category))
        rows, reason, section = self.rows_for(snap, category)
        if not rows or rows["layout"] != "vertical-visual":
            return snap, {"changed": False, "point": None}
        if not section:
            raise RuntimeError("{}: {}".format(category, reason))
        point = self._safe_arrow_click(snap, section)
        after = self.snapshot("{}_collapsed".format(category))
        rows_after, _reason_after, _section_after = self.rows_for(after, category)
        if rows_after and rows_after["layout"] == "vertical-visual":
            raise RuntimeError("{} still appears expanded".format(category))
        return after, {"changed": True, "point": point}

    def select_score(self, category, score, collapse_after=True):
        if category not in CATEGORY_ORDER:
            raise ValueError("unknown category {}".format(category))
        if not 1 <= int(score) <= 5:
            raise ValueError("score must be in 1..5")

        expanded, rows, expand_info = self.ensure_expanded(category)
        if len(rows.get("rows", [])) != 5:
            raise RuntimeError("{} has no verified five-row stack".format(category))
        point = tuple(rows["rows"][int(score) - 1]["click_point"])

        safe_bottom = (
            self.client_rect["top"] + self.client_rect["height"]
            - wkcommon.BOTTOM_EXCLUSION_BAND
        )
        if not (
            self.client_rect["left"] <= point[0]
            < self.client_rect["left"] + self.client_rect["width"]
            and self.client_rect["top"] <= point[1] < safe_bottom
        ):
            raise RuntimeError("{} score target outside safe area".format(category))

        # Capture a mouse-neutral baseline so cursor movement cannot masquerade
        # as a selected-radio visual change.
        baseline = self.snapshot("{}_score{}_baseline".format(category, score))
        if esc_pressed():
            raise RuntimeError("ESC pressed")
        self.mouse.click(button="left", coords=point)
        time.sleep(0.5)
        after = self.snapshot("{}_score{}_after".format(category, score))
        diff = _crop_diff(
            baseline["path"], after["path"], point, self.window_rect
        )
        if not diff["ok"]:
            raise RuntimeError(
                "{} score {} click was not visually verified".format(category, score)
            )
        if not after["support"].get(category, {}).get("supported"):
            raise RuntimeError("{} heading disappeared after score click".format(category))

        collapse_info = {"changed": False, "point": None}
        if collapse_after:
            _collapsed, collapse_info = self.ensure_collapsed(category)

        return {
            "category": category,
            "score": int(score),
            "click_point": list(point),
            "visual_change": diff,
            "expanded_by_test": bool(expand_info["changed"]),
            "collapsed_after": bool(collapse_after),
            "collapse_action": collapse_info,
        }

    def fill_fresh_form(self, scores, collapse_each=True):
        scores = [int(value) for value in scores]
        if len(scores) != 5 or any(value < 1 or value > 5 for value in scores):
            raise ValueError("scores must contain five values in 1..5")

        ok, details = self.verify_rating_form()
        if not ok:
            raise RuntimeError(
                "not a visually verified rating form: {}".format(details["state"])
            )

        # Normalize any open accordion before starting. This does not change
        # selected ratings.
        for category in CATEGORY_ORDER:
            self.ensure_collapsed(category)

        results = []
        for category, score in zip(CATEGORY_ORDER, scores):
            if esc_pressed():
                raise RuntimeError("ESC pressed")
            results.append(
                self.select_score(category, score, collapse_after=collapse_each)
            )
        return results
