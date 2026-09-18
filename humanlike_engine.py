from __future__ import annotations

import ctypes
import os
import time

import wkcommon
import validate_context as ctx
from rating_engine import WowkidsRatingSession as _BaseSession, _crop_diff
from visual_score_rows import detect_visual_score_rows


CATEGORY_ORDER = [name for name, _cn in wkcommon.CATEGORY_PAIRS]
ABORT_KEY = 0x1B


def esc_pressed():
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(ABORT_KEY) & 0x8000)
    except Exception:
        return False


def _orange_submit_button(screenshot_path, window_rect):
    """Find the large solid orange Submit button from rendered pixels only.

    Category headings are also orange, but they are thin glyphs. Submit is a
    wide solid rounded rectangle, so requiring hundreds of orange pixels on
    many adjacent scanlines separates it cleanly from headings and icons.
    """
    try:
        from PIL import Image
        image = Image.open(screenshot_path).convert("RGB")
    except Exception:
        return None

    width, height = image.size
    qualifying_rows = []
    row_bounds = {}
    px = image.load()

    for y in range(height):
        xs = []
        for x in range(width):
            r, g, b = px[x, y]
            if (
                r >= 230
                and 55 <= g <= 150
                and b <= 100
                and r >= g + 80
                and r >= b + 120
            ):
                xs.append(x)
        if len(xs) >= 250:
            qualifying_rows.append(y)
            row_bounds[y] = (min(xs), max(xs))

    if not qualifying_rows:
        return None

    groups = []
    start = previous = qualifying_rows[0]
    for y in qualifying_rows[1:]:
        if y <= previous + 1:
            previous = y
        else:
            groups.append((start, previous))
            start = previous = y
    groups.append((start, previous))

    candidates = []
    for top, bottom in groups:
        if bottom - top + 1 < 20:
            continue
        left = min(row_bounds[y][0] for y in range(top, bottom + 1) if y in row_bounds)
        right = max(row_bounds[y][1] for y in range(top, bottom + 1) if y in row_bounds)
        if right - left + 1 < 280:
            continue
        candidates.append(
            {
                "left": left + window_rect["left"],
                "top": top + window_rect["top"],
                "width": right - left + 1,
                "height": bottom - top + 1,
            }
        )

    if not candidates:
        return None

    # The form Submit button is the lowest wide orange rectangle.
    candidates.sort(key=lambda rect: rect["top"], reverse=True)
    return candidates[0]


class HumanLikeRatingSession(_BaseSession):
    """Rating-form driver learned from a human demonstration.

    The human workflow exposed three important invariants:
      * not every lesson necessarily displays all five known abilities;
      * clicking inside the white description card expands an ability;
      * selected accordions can stay open while moving to the next ability.

    Therefore this engine discovers the abilities actually rendered on the
    current lesson and never assumes fixed card heights or a fixed 5-category
    form.  It deliberately contains NO Submit action.
    """

    def _scroll(self, wheel_dist, settle=0.50):
        if esc_pressed():
            raise RuntimeError("ESC pressed")
        x = self.client_rect["left"] + self.client_rect["width"] // 2
        y = self.client_rect["top"] + min(500, self.client_rect["height"] - 220)
        self.mouse.move(coords=(x, y))
        time.sleep(0.05)
        self.mouse.scroll(coords=(x, y), wheel_dist=int(wheel_dist))
        time.sleep(settle)

    def _supported_categories(self, snap):
        items = []
        for category in CATEGORY_ORDER:
            node = snap["selected"].get(category)
            item = snap["support"].get(category, {})
            if not item.get("supported") or not node or not node.get("rect"):
                continue
            rect = node["rect"]
            # Require the heading to overlap the real client viewport.
            if wkcommon.rect_intersection_area(rect, self.client_rect) < 20:
                continue
            items.append((category, node))
        items.sort(key=lambda pair: pair[1]["rect"]["top"])
        return items

    def _scroll_to_top(self):
        # The demo page is only a few viewports tall. Overscrolling upward is
        # harmless and gives us a deterministic starting point without trusting
        # Chromium's stale scroll/UIA state.
        for _ in range(12):
            self._scroll(7, settle=0.16)

    def discover_categories(self, max_steps=30):
        """Scan the unmodified form and return abilities actually on this lesson.

        This phase scrolls only. It does not expand, rate, or submit anything.
        """
        self._scroll_to_top()
        found = []

        for step in range(max_steps):
            if esc_pressed():
                raise RuntimeError("ESC pressed")

            snap = self.snapshot("discover_{:02d}".format(step))
            for category, _node in self._supported_categories(snap):
                if category not in found:
                    found.append(category)

            if _orange_submit_button(snap["path"], self.window_rect):
                break

            self._scroll(-5, settle=0.26)
        else:
            raise RuntimeError("could not reach the bottom of the rating form safely")

        if not found:
            raise RuntimeError("no visually verified rating abilities were discovered")

        # Preserve actual page order from the scan. This intentionally allows a
        # lesson to expose a subset (for example four of the five known skills).
        self._scroll_to_top()
        return found

    def _position_category(self, category, max_steps=24):
        """Bring one live heading near the upper-middle safe work area."""
        preferred_top = self.client_rect["top"] + 125
        preferred_bottom = self.client_rect["top"] + 280

        for attempt in range(max_steps):
            if esc_pressed():
                raise RuntimeError("ESC pressed")

            snap = self.snapshot("{}_position_{:02d}".format(category, attempt))
            supported = dict(self._supported_categories(snap))
            node = supported.get(category)

            if node and node.get("rect"):
                top = node["rect"]["top"]
                if preferred_top <= top <= preferred_bottom:
                    return snap
                if top > preferred_bottom:
                    self._scroll(-2, settle=0.28)
                else:
                    self._scroll(2, settle=0.28)
                continue

            # We process the discovered abilities top-to-bottom, so when the
            # next heading is not rendered yet, move farther down.
            self._scroll(-3, settle=0.30)

        raise RuntimeError("{} could not be positioned safely".format(category))

    def _section_for_live(self, snap, category):
        supported = self._supported_categories(snap)
        current = None
        for name, node in supported:
            if name == category:
                current = node
                break
        if not current or not current.get("rect"):
            return None

        h = current["rect"]
        safe_bottom = (
            self.client_rect["top"]
            + self.client_rect["height"]
            - wkcommon.BOTTOM_EXCLUSION_BAND
            - 4
        )

        next_tops = [
            node["rect"]["top"]
            for name, node in supported
            if name != category
            and node["rect"]["top"] > h["top"] + h["height"] + 12
        ]
        band_bottom = min(next_tops) - 4 if next_tops else safe_bottom
        band_bottom = min(band_bottom, safe_bottom)

        if band_bottom <= h["top"] + h["height"] + 35:
            return None

        return {
            "english": category,
            "heading": {"rect": h},
            "band": (h["top"] + h["height"] - 4, band_bottom),
            "band_bottom": band_bottom,
        }

    def _rows_for_live(self, snap, category):
        section = self._section_for_live(snap, category)
        if not section:
            return None, "live section geometry unavailable", None

        rows = detect_visual_score_rows(
            snap["path"],
            self.window_rect,
            section["heading"]["rect"],
            section["band_bottom"],
            self.client_rect,
            wkcommon.BOTTOM_EXCLUSION_BAND,
        )
        return rows, "", section

    def _description_card_point(self, snap, category):
        """Return a conservative point inside the category description card.

        We intentionally do not target the tiny triangle. In both recorded
        students, a normal click inside the white description card toggled the
        accordion. The point is derived from the live heading, not a fixed Y.
        """
        supported = dict(self._supported_categories(snap))
        node = supported.get(category)
        if not node or not node.get("rect"):
            raise RuntimeError("{} heading is not visually verified".format(category))

        h = node["rect"]
        x = self.client_rect["left"] + int(self.client_rect["width"] * 0.46)
        y = h["top"] + h["height"] + 56

        safe_bottom = (
            self.client_rect["top"]
            + self.client_rect["height"]
            - wkcommon.BOTTOM_EXCLUSION_BAND
        )
        if not (
            self.client_rect["left"] + 35 <= x
            < self.client_rect["left"] + self.client_rect["width"] - 35
            and self.client_rect["top"] + 95 <= y < safe_bottom
        ):
            raise RuntimeError("{} description-card target is outside safe area".format(category))

        # The click must remain before the next live category heading.
        below = [
            other["rect"]["top"]
            for name, other in self._supported_categories(snap)
            if name != category
            and other["rect"]["top"] > h["top"] + h["height"]
        ]
        if below and y >= min(below) - 12:
            y = h["top"] + h["height"] + 34
        if below and y >= min(below) - 8:
            raise RuntimeError("{} description card is too compressed to click safely".format(category))

        return (int(x), int(y))

    def ensure_expanded_humanlike(self, category):
        snap = self._position_category(category)
        rows, _reason, _section = self._rows_for_live(snap, category)
        if rows and rows.get("layout") == "vertical-visual":
            return snap, rows, {"changed": False, "point": None}

        point = self._description_card_point(snap, category)
        if esc_pressed():
            raise RuntimeError("ESC pressed")
        self.mouse.click(button="left", coords=point)
        time.sleep(0.55)

        after = self.snapshot("{}_expanded".format(category))
        rows_after, reason_after, _section_after = self._rows_for_live(after, category)
        if not rows_after or rows_after.get("layout") != "vertical-visual":
            detail = rows_after.get("reason") if rows_after else reason_after
            raise RuntimeError(
                "{} did not expose a verified 1..5 star stack: {}".format(
                    category, detail
                )
            )

        return after, rows_after, {"changed": True, "point": list(point)}

    def select_score_humanlike(self, category, score):
        score = int(score)
        if category not in CATEGORY_ORDER:
            raise ValueError("unknown category {}".format(category))
        if not 1 <= score <= 5:
            raise ValueError("{} score must be in 1..5".format(category))

        _expanded, rows, expand_info = self.ensure_expanded_humanlike(category)
        if len(rows.get("rows", [])) != 5:
            raise RuntimeError("{} does not have five verified score rows".format(category))

        point = tuple(rows["rows"][score - 1]["click_point"])
        safe_bottom = (
            self.client_rect["top"]
            + self.client_rect["height"]
            - wkcommon.BOTTOM_EXCLUSION_BAND
        )
        if not (
            self.client_rect["left"] <= point[0]
            < self.client_rect["left"] + self.client_rect["width"]
            and self.client_rect["top"] <= point[1] < safe_bottom
        ):
            raise RuntimeError("{} score target is outside safe area".format(category))

        baseline = self.snapshot("{}_score{}_baseline".format(category, score))
        if esc_pressed():
            raise RuntimeError("ESC pressed")
        self.mouse.click(button="left", coords=point)
        time.sleep(0.45)
        after = self.snapshot("{}_score{}_after".format(category, score))

        diff = _crop_diff(
            baseline["path"], after["path"], point, self.window_rect
        )
        if not diff["ok"]:
            raise RuntimeError(
                "{} score {} did not produce a verified radio-state change".format(
                    category, score
                )
            )

        return {
            "category": category,
            "score": score,
            "click_point": list(point),
            "expanded_by_run": bool(expand_info["changed"]),
            "visual_change": diff,
            "left_expanded": True,
        }

    def fill_discovered(self, categories, scores):
        if list(scores.keys()) != list(categories):
            raise ValueError("score mapping must match the discovered category order")

        self._scroll_to_top()
        results = []
        for category in categories:
            if esc_pressed():
                raise RuntimeError("ESC pressed")
            results.append(
                self.select_score_humanlike(category, scores[category])
            )

        # Move to the bottom so the user can visually inspect the final form.
        for _ in range(18):
            snap = self.snapshot("finish_seek_submit")
            if _orange_submit_button(snap["path"], self.window_rect):
                return results, snap
            self._scroll(-5, settle=0.22)

        raise RuntimeError("ratings were selected but Submit area was not found")

    def find_submit_only(self):
        """Return the visible Submit rectangle; this method NEVER clicks it."""
        snap = self.snapshot("submit_probe")
        return _orange_submit_button(snap["path"], self.window_rect), snap
