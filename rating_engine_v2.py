from __future__ import annotations

import time

import wkcommon
from rating_engine import WowkidsRatingSession as _BaseSession, CATEGORY_ORDER
from visual_score_rows import detect_visual_score_rows


class WowkidsRatingSessionV2(_BaseSession):
    """Scroll-aware version of the guarded rating session.

    The original engine worked for categories already high enough in the
    viewport. Lower categories need to be brought upward before their five
    rating rows can fit inside the safe click area. This class only adds
    scrolling and safer section-band construction. It still has no Submit or
    Post All capability.
    """

    def _scroll_content(self, wheel_dist):
        x = self.client_rect["left"] + self.client_rect["width"] // 2
        y = self.client_rect["top"] + min(470, self.client_rect["height"] - 220)
        self.mouse.move(coords=(x, y))
        time.sleep(0.08)
        self.mouse.scroll(coords=(x, y), wheel_dist=wheel_dist)
        time.sleep(0.55)

    def bring_category_into_view(self, category, max_steps=10):
        if category not in CATEGORY_ORDER:
            raise ValueError("unknown category {}".format(category))

        preferred_top = self.client_rect["top"] + 135
        preferred_bottom = self.client_rect["top"] + 340

        for attempt in range(max_steps + 1):
            snap = self.snapshot("{}_position_{}".format(category, attempt))
            node = snap["selected"].get(category)
            supported = bool(snap["support"].get(category, {}).get("supported"))

            if supported and node and node.get("rect"):
                top = node["rect"]["top"]
                if preferred_top <= top <= preferred_bottom:
                    return snap
                # Negative wheel distance scrolls down the page (content up).
                if top > preferred_bottom:
                    self._scroll_content(-3)
                else:
                    self._scroll_content(3)
                continue

            # We process categories from top to bottom. If the requested heading
            # is not rendered yet, move farther down the form.
            self._scroll_content(-3)

        raise RuntimeError("{} could not be positioned safely in the viewport".format(category))

    def section_for(self, selected, category, support=None):
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
        band_bottom = safe_bottom - 4

        if index + 1 < len(CATEGORY_ORDER):
            next_name = CATEGORY_ORDER[index + 1]
            next_node = selected.get(next_name)
            next_supported = True if support is None else bool(
                support.get(next_name, {}).get("supported")
            )
            if next_supported and next_node and next_node.get("rect"):
                next_top = next_node["rect"]["top"]
                if next_top > h["top"] + h["height"]:
                    band_bottom = min(band_bottom, next_top - 4)

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
        section = self.section_for(
            snap["selected"], category, support=snap.get("support")
        )
        if not section:
            return None, "section geometry unavailable", None
        rows = detect_visual_score_rows(
            snap["path"], self.window_rect, section["heading"]["rect"],
            section["band_bottom"], self.client_rect,
            wkcommon.BOTTOM_EXCLUSION_BAND,
        )
        return rows, "", section

    def ensure_expanded(self, category):
        snap = self.bring_category_into_view(category)
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
