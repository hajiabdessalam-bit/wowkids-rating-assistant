from __future__ import annotations

import ctypes
import os
import time

import wkcommon
import validate_context as ctx
from rating_engine import WowkidsRatingSession as _BaseSession, _crop_diff
from visual_score_rows import detect_visual_score_rows


CATEGORY_ORDER = [name for name, _cn in wkcommon.CATEGORY_PAIRS]
ABORT_KEYS = (0x1B, 0x79)  # ESC or F10


def abort_pressed():
    try:
        return any(
            bool(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000)
            for key in ABORT_KEYS
        )
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
    form. Submit is available only through an explicit verified method that
    requires every discovered ability to have succeeded first.
    """

    def park_mouse(self):
        """Do not move the user's physical cursor just to take screenshots.

        The base engine parked the cursor before every capture. That made the
        user feel locked out while a long positioning loop was running.
        PrintWindow/BitBlt captures do not need us to relocate the cursor.
        """
        return

    def _scroll(self, wheel_dist, settle=0.34):
        """Scroll WOWKIDS with a background wheel message only.

        We deliberately have NO physical-mouse fallback. If Windows cannot send
        a background wheel event, fail closed rather than taking control of the
        user's cursor.
        """
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        x = self.client_rect["left"] + self.client_rect["width"] // 2
        y = self.client_rect["top"] + min(500, self.client_rect["height"] - 220)
        wheel_dist = int(wheel_dist)
        delta = wheel_dist * 120

        try:
            import win32api
            import win32con
            import win32gui

            hwnd = win32gui.WindowFromPoint((x, y)) or self.wrapper.handle
            wparam = (delta & 0xFFFF) << 16
            lparam = win32api.MAKELONG(x & 0xFFFF, y & 0xFFFF)
            win32gui.PostMessage(hwnd, win32con.WM_MOUSEWHEEL, wparam, lparam)
        except Exception as exc:
            raise RuntimeError(
                "background scrolling unavailable; refusing to take over the mouse: {}".format(exc)
            )

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

        # A stale Chromium frame can occasionally place a different category's
        # UIA rectangle over the same orange heading. Never let two category
        # names claim the same rendered heading band.
        for index in range(len(items) - 1):
            name_a, node_a = items[index]
            name_b, node_b = items[index + 1]
            top_a = node_a["rect"]["top"]
            top_b = node_b["rect"]["top"]
            if abs(top_a - top_b) <= 18:
                raise RuntimeError(
                    "ambiguous live heading: {} and {} overlap the same visual row".format(
                        name_a, name_b
                    )
                )
        return items

    def _scroll_to_top(self):
        # Use a few large background wheel messages rather than dozens of
        # physical mouse-wheel actions.
        for _ in range(6):
            self._scroll(12, settle=0.12)

    def discover_categories(self, max_steps=30):
        """Scan the unmodified form and return abilities actually on this lesson.

        This phase scrolls only. It does not expand, rate, or submit anything.
        """
        self._scroll_to_top()
        found = []

        for step in range(max_steps):
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")

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
        """Find a category without ever blindly scrolling past it.

        The first version always scrolled down when the target heading was not
        visible. With two expanded accordions above it, a 4-notch wheel step
        could jump completely over the next heading. The following snapshots
        still did not contain the target, so it kept moving down forever.

        This search is directional:
          * visible categories BEFORE the target -> move down;
          * visible categories AFTER the target  -> we overshot, move up;
          * visible target -> nudge it into a broad safe work zone;
          * visible Submit while target missing -> definitely move up.

        All search moves are only 1-2 wheel notches.
        """
        if category not in CATEGORY_ORDER:
            raise ValueError("unknown category {}".format(category))

        target_index = CATEGORY_ORDER.index(category)
        safe_top = self.client_rect["top"] + 105
        safe_bottom = self.client_rect["top"] + 360
        direction = -1  # categories are processed top-to-bottom

        for attempt in range(max_steps):
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")

            snap = self.snapshot("{}_position_{:02d}".format(category, attempt))
            visible_pairs = self._supported_categories(snap)
            visible = dict(visible_pairs)
            node = visible.get(category)

            if node and node.get("rect"):
                top = node["rect"]["top"]

                if safe_top <= top <= safe_bottom:
                    return snap

                if top > safe_bottom:
                    direction = -1
                    self._scroll(-1, settle=0.20)
                else:
                    direction = 1
                    self._scroll(1, settle=0.20)
                continue

            visible_indices = [
                CATEGORY_ORDER.index(name)
                for name, _node in visible_pairs
                if name in CATEGORY_ORDER
            ]

            if visible_indices:
                if any(index > target_index for index in visible_indices):
                    # A later ability is already visible: target is above us.
                    direction = 1
                elif any(index < target_index for index in visible_indices):
                    # Only earlier abilities are visible: target is below us.
                    direction = -1
            elif _orange_submit_button(snap["path"], self.window_rect):
                # We reached the bottom without seeing the target. Reverse.
                direction = 1

            self._scroll(direction * 2, settle=0.22)

        raise RuntimeError(
            "{} was not found after {} small controlled scrolls; stopped safely".format(
                category, max_steps
            )
        )

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
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")
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
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")
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
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
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

    def _classify_snapshot(self, snap):
        visible = self._visible(snap["nodes"])
        return ctx.classify_live_page(
            visible, None, snap["path"], self.window_rect
        )

    @staticmethod
    def _live_roster_evidence(signals):
        """Use screenshot-corroborated roster pixels, never stale text alone."""
        items = (signals or {}).get("roster_visual_support", []) or []
        has_post_all = any(item.get("kind") == "post-all" for item in items)
        badges = [
            item for item in items
            if item.get("kind") in ("posted", "purple-badge", "grey-badge")
        ]
        return {
            "has_post_all": has_post_all,
            "badge_count": len(badges),
            "items": items,
            # After Submit, one live status badge plus the visually verified
            # Post All control is enough to prove we are back on the roster.
            # We OBSERVE Post All here; we never click it.
            "verified": bool(has_post_all and len(badges) >= 1),
        }

    def submit_verified_student(self, categories, results, timeout=8.0):
        """Submit exactly once, but only after a fully verified fill.

        Safety gates:
          * one successful result for every discovered category;
          * category order must match exactly;
          * every score click must have passed the visual-change check;
          * the target must be the large rating-form Submit button, never the
            much smaller roster Post All button;
          * after clicking, the app must return to a visually verified roster.
        """
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        expected = list(categories)
        actual = [item.get("category") for item in results]
        if actual != expected:
            raise RuntimeError(
                "refusing Submit: verified result categories do not match the lesson"
            )
        if len(results) != len(expected) or not expected:
            raise RuntimeError(
                "refusing Submit: not every discovered ability was verified"
            )
        for item in results:
            change = item.get("visual_change") or {}
            if not change.get("ok"):
                raise RuntimeError(
                    "refusing Submit: {} has no verified selected-state change".format(
                        item.get("category", "unknown ability")
                    )
                )

        # fill_discovered already seeks the bottom, but re-check the live page
        # instead of reusing an old rectangle.
        submit_rect = None
        submit_snap = None
        for _ in range(6):
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
            snap = self.snapshot("submit_live_check")
            rect = _orange_submit_button(snap["path"], self.window_rect)
            if rect:
                submit_rect, submit_snap = rect, snap
                break
            self._scroll(-3, settle=0.20)

        if not submit_rect:
            raise RuntimeError("refusing Submit: large orange Submit button not found")

        # Guard against accidentally treating the small orange Post All roster
        # button as Submit.
        if submit_rect["width"] < 280 or submit_rect["height"] < 20:
            raise RuntimeError("refusing Submit: orange target is not the large form button")

        cx = submit_rect["left"] + submit_rect["width"] // 2
        cy = submit_rect["top"] + submit_rect["height"] // 2
        if not (
            self.client_rect["left"] + 30 <= cx
            < self.client_rect["left"] + self.client_rect["width"] - 30
            and self.client_rect["top"] + 80 <= cy
            < self.client_rect["top"] + self.client_rect["height"] - 20
        ):
            raise RuntimeError("refusing Submit: button centre is outside the safe client")

        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        self.mouse.click(button="left", coords=(cx, cy))

        # From this point onward the Submit click has definitely been issued.
        # Do not later report "Submit clicked: NO" just because Chromium's stale
        # accessibility tree confused the page classifier.
        deadline = time.monotonic() + float(timeout)
        last_state = "UNKNOWN"
        last_reasons = []
        last_signals = {}
        last_snap = submit_snap
        form_gone_streak = 0
        attempt = 0

        while time.monotonic() < deadline:
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
            time.sleep(0.40)
            attempt += 1
            snap = self.snapshot("after_submit_{:02d}".format(attempt))
            state, reasons, signals = self._classify_snapshot(snap)
            last_state = state
            last_reasons = reasons
            last_signals = signals or {}
            last_snap = snap

            roster = self._live_roster_evidence(signals)
            if state == "CLASS_ROSTER" or roster["verified"]:
                return {
                    "clicked": True,
                    "accepted": True,
                    "point": [int(cx), int(cy)],
                    "submit_rect": submit_rect,
                    "verified_return_to_roster": True,
                    "verification": (
                        "classifier"
                        if state == "CLASS_ROSTER"
                        else "visual Post All + live roster badge"
                    ),
                    "state": state,
                    "reasons": reasons,
                    "roster_evidence": roster,
                    "snapshot": snap["path"],
                }

            # A stale retained "Switch account" node can make classify_live_page
            # say HOME even though the rendered rating form has disappeared.
            # Treat two consecutive snapshots with no large form Submit and no
            # visually supported rating headings as proof that the Submit action
            # was accepted. This is sufficient for the one-student test, but the
            # future class loop will still require roster evidence before it
            # opens another student.
            submit_still_visible = bool(
                _orange_submit_button(snap["path"], self.window_rect)
            )
            live_headings = (signals or {}).get("rating_visual_supported", []) or []
            if not submit_still_visible and len(live_headings) < 2:
                form_gone_streak += 1
            else:
                form_gone_streak = 0

            if form_gone_streak >= 2:
                return {
                    "clicked": True,
                    "accepted": True,
                    "point": [int(cx), int(cy)],
                    "submit_rect": submit_rect,
                    "verified_return_to_roster": False,
                    "verification": "rating form disappeared after Submit",
                    "state": state,
                    "reasons": reasons,
                    "roster_evidence": roster,
                    "snapshot": snap["path"],
                }

        # If the rating form and its large Submit button are still there, the
        # click did not visibly take effect. Otherwise the click happened but
        # navigation was ambiguous; report the click truthfully and stop before
        # doing anything else. Post All is never touched in either case.
        submit_still_visible = False
        if last_snap:
            submit_still_visible = bool(
                _orange_submit_button(last_snap["path"], self.window_rect)
            )
        if submit_still_visible:
            raise RuntimeError(
                "Submit click was issued, but the rating form still appears active"
            )

        return {
            "clicked": True,
            "accepted": True,
            "point": [int(cx), int(cy)],
            "submit_rect": submit_rect,
            "verified_return_to_roster": False,
            "verification": "Submit click changed the page; roster not yet proven",
            "state": last_state,
            "reasons": last_reasons,
            "roster_evidence": self._live_roster_evidence(last_signals),
            "snapshot": last_snap["path"] if last_snap else None,
        }
