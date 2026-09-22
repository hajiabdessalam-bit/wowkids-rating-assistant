from __future__ import annotations

import ctypes
import json
import os
import re
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


def assessment_payload_ready(snap, window_rect, client_rect, student=None):
    """Verify the assessment shell has real lesson content before discovery.

    IMPORTANT: ability headings are often BELOW the initial viewport. The
    proven rating engine is responsible for scrolling down and discovering
    those headings. This gate must therefore never require ability cards to be
    visible at the top of the page.

    It only rejects the transient blank WOWKIDS shell seen in failed runs:
      * newest Page-Frame must be the assessment page;
      * expected student identity, when supplied, must be visibly rendered;
      * at least one real rendered lesson-content text node must exist below
        the student card.

    This preserves the older working discovery/rating automation.
    """
    from PIL import Image

    nodes = snap["nodes"]
    frames = [
        i for i, node in enumerate(nodes)
        if node.get("control_type") == "Document"
        and node.get("name") == "Page-Frame"
    ]
    if not frames:
        return False, "no assessment document"

    start = frames[-1]
    depth = nodes[start].get("depth", 0)
    page = []
    for node in nodes[start + 1:]:
        if node.get("depth", 0) <= depth:
            break
        page.append(node)

    names = [str(node.get("name") or "").strip() for node in page]
    if not any(
        "课堂评价" in name or "assessment" in name.casefold()
        for name in names
    ):
        return False, "assessment page not present"

    visible = [
        node for node in page
        if wkcommon.node_is_visibly_present(node, client_rect)[0]
    ]

    try:
        image = Image.open(snap["path"]).convert("RGB")
    except Exception:
        return False, "assessment screenshot unavailable"

    def has_ink(node, minimum_ink=12):
        rect = node.get("rect")
        if not rect:
            return False
        x0 = rect["left"] - window_rect["left"]
        y0 = rect["top"] - window_rect["top"]
        x1 = x0 + rect["width"]
        y1 = y0 + rect["height"]
        if (
            x0 < 0 or y0 < 0
            or x1 > image.width or y1 > image.height
            or x1 <= x0 or y1 <= y0
        ):
            return False

        crop = image.crop((x0, y0, x1, y1))
        total = crop.width * crop.height
        if not total:
            return False

        ink = 0
        light = 0
        for red, green, blue in crop.getdata():
            if max(red, green, blue) < 175:
                ink += 1
            if min(red, green, blue) > 220:
                light += 1

        return ink >= minimum_ink and light >= total * 0.20

    # If we know which student was opened, verify that name is not merely a
    # stale Chromium node but is visibly rendered in the live upper form.
    if student:
        wanted = str(student).strip().casefold()
        identity_nodes = []
        for node in visible:
            name = str(node.get("name") or "").strip()
            rect = node.get("rect")
            if not name or not rect:
                continue
            if rect["top"] >= client_rect["top"] + 430:
                continue
            if wanted and wanted in name.casefold():
                identity_nodes.append(node)

        if not identity_nodes:
            return False, "student identity is not populated yet"
        if not any(has_ink(node, minimum_ink=8) for node in identity_nodes):
            return False, "student identity text is not rendered yet"

    # The blank-shell failure had the assessment title/student shell but no
    # lesson payload. Require rendered content lower in the form. Do NOT
    # require ability headings here: they can be far below the fold.
    ignored_exact = {
        "in-class assessment",
        "课堂评价",
        str(student or "").strip().casefold(),
    }
    content_nodes = []
    for node in visible:
        name = str(node.get("name") or "").strip()
        rect = node.get("rect")
        if not name or not rect:
            continue
        folded = name.casefold()
        if folded in ignored_exact:
            continue
        if rect["top"] < client_rect["top"] + 260:
            continue
        if rect["top"] >= client_rect["top"] + client_rect["height"] - 70:
            continue
        # Real lesson payload can be a lesson title, class/date line, Chinese
        # lesson text, or descriptive copy. Avoid tiny icon/arrow labels.
        if len(name) < 4:
            continue
        if has_ink(node):
            content_nodes.append(node)

    if not content_nodes:
        return False, "lesson content is not rendered yet"

    return True, "student and lesson payload loaded"

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


def _centre_toast_change(before_path, after_path, window_rect, client_rect):
    """Detect the large central success toast shown after a real Submit.

    The human demonstration shows WOWKIDS briefly renders a translucent square
    with a check mark and 成功 in the middle of the form.  We do not OCR it;
    we only require a strong visual change in a central area that does not
    include the orange Submit button itself.
    """
    try:
        from PIL import Image, ImageChops, ImageStat

        a = Image.open(before_path).convert("RGB")
        b = Image.open(after_path).convert("RGB")
    except Exception:
        return False

    cx = client_rect["left"] + client_rect["width"] // 2 - window_rect["left"]
    cy = client_rect["top"] + int(client_rect["height"] * 0.52) - window_rect["top"]
    half_w = min(125, client_rect["width"] // 4)
    half_h = 120
    box = (
        max(0, int(cx - half_w)),
        max(0, int(cy - half_h)),
        min(a.width, int(cx + half_w)),
        min(a.height, int(cy + half_h)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return False

    diff = ImageChops.difference(a.crop(box), b.crop(box))
    stat = ImageStat.Stat(diff)
    mean = sum(stat.mean) / 3.0
    extrema = diff.getbbox()
    return bool(extrema and mean >= 4.0)


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._assessment_ready_confirmed = False
        self._assessment_ready_student = None

    def park_mouse(self):
        """Do not move the user's physical cursor just to take screenshots.

        The base engine parked the cursor before every capture. That made the
        user feel locked out while a long positioning loop was running.
        PrintWindow/BitBlt captures do not need us to relocate the cursor.
        """
        return

    def click_at(self, point):
        """Click WOWKIDS without moving or depending on the physical cursor.

        pywinauto.mouse.click moves the system cursor to the target first. If
        the coach happens to move the mouse at the same moment, the click can
        land somewhere else. WeChat's Chromium surface accepts normal Windows
        button messages, so send the click directly to the child window under
        the verified target point instead.

        There is deliberately NO physical-mouse fallback here. Every caller
        already verifies the resulting UI state; if a background click is not
        accepted, stop safely instead of fighting the user's cursor.
        """
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        x, y = (int(point[0]), int(point[1]))
        try:
            import win32api
            import win32con
            import win32gui

            root = int(self.wrapper.handle)
            hwnd = int(win32gui.WindowFromPoint((x, y)) or root)
            if hwnd != root and not win32gui.IsChild(root, hwnd):
                hwnd = root

            cx, cy = win32gui.ScreenToClient(hwnd, (x, y))
            lparam = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)

            # Move only the target window's logical mouse state. The real
            # Windows cursor stays exactly where the user left it.
            win32gui.SendMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
            win32gui.SendMessage(
                hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam
            )
            win32gui.SendMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
            return
        except Exception as exc:
            raise RuntimeError(
                "background WOWKIDS click unavailable; no physical click was "
                "attempted: {}".format(exc)
            )

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
    def capture_only(self, label):
        """Capture rendered pixels without walking Chromium's full UIA tree.

        A full snapshot can traverse thousands of stale WeChat accessibility
        nodes. For waits that only need pixel proof (star rows / radio-state
        change), that traversal is unnecessary and was a major latency source.
        """
        self.window_rect = wkcommon.window_rectangle(self.wrapper)
        self.client_rect = wkcommon.win32_client_rect(self.wrapper)
        if not self.window_rect or not self.client_rect:
            raise RuntimeError("WOWKIDS window geometry unavailable")

        path = os.path.join(
            self.shots_dir,
            "{}_{}.png".format(
                wkcommon.timestamp(), label.replace(" ", "_")
            ),
        )
        capture_note = wkcommon.capture_window(self.wrapper, path)
        return {
            "path": path,
            "capture": capture_note,
            "nodes": [],
            "selected": {},
            "support": {},
        }


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

    def save_diagnostic(self, snap, reason):
        path = os.path.splitext(snap['path'])[0] + '.json'
        with open(path, 'w', encoding='utf-8') as output:
            json.dump({
                'reason': reason, 'window': self.window_rect,
                'client': self.client_rect, 'screenshot': snap['path'],
                'capture': snap.get('capture'), 'support': snap.get('support'),
                'nodes': wkcommon.without_wrappers(snap['nodes']),
            }, output, ensure_ascii=False, indent=2)
        return path

    def wait_for_assessment_ready(self, student=None, timeout=25.0):
        """Wait for real lesson content, not merely the empty assessment shell.

        This is a state-based gate, not a fixed delay. Once the current session
        has verified the student/lesson payload, later discovery/rating steps
        reuse that proof instead of repeatedly scrolling to the top and waiting
        again.
        """
        wanted = str(student or "").strip().casefold()
        cached = str(self._assessment_ready_student or "").strip().casefold()
        if self._assessment_ready_confirmed and (not wanted or wanted == cached):
            return self.snapshot("assessment_ready_cached")

        self._scroll_to_top()
        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            if abort_pressed():
                raise RuntimeError('STOP pressed (ESC/F10)')
            snap = self.snapshot('assessment_ready_{:02d}'.format(attempt))
            ready, reason = assessment_payload_ready(
                snap, self.window_rect, self.client_rect, student)
            if ready:
                self._assessment_ready_confirmed = True
                self._assessment_ready_student = student
                return snap
            if attempt == 0 or time.monotonic() >= deadline:
                self.save_diagnostic(snap, reason)
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    'WOWKIDS assessment did not finish loading: {}. '
                    'No ratings or Submit were attempted. Diagnostic: {}'.format(
                        reason, os.path.splitext(snap['path'])[0] + '.json'))
            attempt += 1
            time.sleep(0.20)

    def discover_categories(self, max_steps=30):
        """Scan the unmodified form and return abilities actually on this lesson.

        This phase scrolls only. It does not expand, rate, or submit anything.
        """
        self.wait_for_assessment_ready()
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
            diagnostic = self.save_diagnostic(snap, 'no rendered abilities found')
            raise RuntimeError("no visually verified rating abilities were discovered; "
                               "diagnostic: {}".format(diagnostic))

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
            return snap, rows, {"changed": False, "point": None, "waitSeconds": 0.0}

        point = self._description_card_point(snap, category)
        supported = dict(self._supported_categories(snap))
        heading = supported.get(category)
        if not heading or not heading.get("rect"):
            raise RuntimeError(
                "{} heading geometry disappeared before expansion".format(category)
            )
        heading_rect = dict(heading["rect"])

        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")
        self.click_at(point)

        # Pixel-only wait: do not traverse thousands of Chromium UIA nodes
        # merely to see whether the 1..5 stars have appeared.
        started = time.monotonic()
        deadline = started + 1.50
        attempt = 0
        last_rows = None
        while time.monotonic() < deadline:
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
            time.sleep(0.05 if attempt == 0 else 0.06)
            after = self.capture_only(
                "{}_expanded_{:02d}".format(category, attempt)
            )
            band_bottom = (
                self.client_rect["top"]
                + self.client_rect["height"]
                - wkcommon.BOTTOM_EXCLUSION_BAND
                - 4
            )
            rows_after = detect_visual_score_rows(
                after["path"],
                self.window_rect,
                heading_rect,
                band_bottom,
                self.client_rect,
                wkcommon.BOTTOM_EXCLUSION_BAND,
            )
            last_rows = rows_after
            if rows_after and rows_after.get("layout") == "vertical-visual":
                return after, rows_after, {
                    "changed": True,
                    "point": list(point),
                    "waitSeconds": round(time.monotonic() - started, 3),
                }
            attempt += 1

        detail = (
            last_rows.get("reason")
            if last_rows
            else "timed out waiting for rendered score rows"
        )
        raise RuntimeError(
            "{} did not expose a verified 1..5 star stack: {}".format(
                category, detail
            )
        )

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

        # The radio usually paints much faster than the old fixed 450 ms wait.
        # Continue as soon as the SAME local visual-change check succeeds.
        started = time.monotonic()
        deadline = started + 1.00
        attempt = 0
        last_after = None
        last_diff = {"ok": False}
        while time.monotonic() < deadline:
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
            time.sleep(0.06 if attempt == 0 else 0.08)
            after = self.capture_only(
                "{}_score{}_after_{:02d}".format(category, score, attempt)
            )
            diff = _crop_diff(
                baseline["path"], after["path"], point, self.window_rect
            )
            last_after, last_diff = after, diff
            if diff["ok"]:
                return {
                    "category": category,
                    "score": score,
                    "click_point": list(point),
                    "expanded_by_run": bool(expand_info["changed"]),
                    "expand_wait_seconds": expand_info.get("waitSeconds", 0.0),
                    "score_wait_seconds": round(time.monotonic() - started, 3),
                    "visual_change": diff,
                    "left_expanded": True,
                }
            attempt += 1

        raise RuntimeError(
            "{} score {} did not produce a verified radio-state change".format(
                category, score
            )
        )

    def fill_discovered(self, categories, scores):
        if list(scores.keys()) != list(categories):
            raise ValueError("score mapping must match the discovered category order")

        self.wait_for_assessment_ready()
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

    def submit_verified_student(self, categories, results, timeout=10.0):
        """Submit the current student's verified ratings automatically.

        Post All is structurally forbidden here: the only clickable orange
        target must be the very wide form Submit button (>=70% of client width).
        The small roster Post All button can never pass that geometry gate.
        """
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        expected = list(categories)
        actual = [item.get("category") for item in results]
        if actual != expected or len(results) != len(expected) or not expected:
            raise RuntimeError(
                "refusing Submit: not every discovered ability was verified"
            )
        for item in results:
            if not (item.get("visual_change") or {}).get("ok"):
                raise RuntimeError(
                    "refusing Submit: {} has no verified selected-state change".format(
                        item.get("category", "unknown ability")
                    )
                )

        # Locate the live, WIDE form Submit button. Never click any smaller
        # orange button such as Post All.
        submit_rect = None
        submit_snap = None
        for _ in range(8):
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
            snap = self.snapshot("submit_live_check")
            rect = _orange_submit_button(snap["path"], self.window_rect)
            if rect and rect["width"] >= int(self.client_rect["width"] * 0.70):
                submit_rect, submit_snap = rect, snap
                break
            self._scroll(-2, settle=0.18)

        if not submit_rect:
            raise RuntimeError(
                "refusing Submit: the wide rating-form Submit button was not found"
            )

        cx = submit_rect["left"] + submit_rect["width"] // 2
        cy = submit_rect["top"] + submit_rect["height"] // 2
        if not (
            self.client_rect["left"] + 30 <= cx
            < self.client_rect["left"] + self.client_rect["width"] - 30
            and self.client_rect["top"] + int(self.client_rect["height"] * 0.60) <= cy
            < self.client_rect["top"] + self.client_rect["height"] - 10
        ):
            raise RuntimeError(
                "refusing Submit: wide button is not in the lower form area"
            )

        # No console confirmation prompt means WOWKIDS can remain foreground.
        # Raise it immediately before the one real click.
        wkcommon.restore_window(self.wrapper)
        time.sleep(0.12)
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        before_path = submit_snap["path"]
        self.click_at((int(cx), int(cy)))
        click_issued = True

        toast_seen = False
        last_state = "UNKNOWN"
        last_reasons = []
        last_signals = {}
        last_snap = submit_snap
        attempt = 0
        deadline = time.monotonic() + float(timeout)

        while time.monotonic() < deadline:
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
            time.sleep(0.15)
            attempt += 1
            snap = self.snapshot("after_submit_{:02d}".format(attempt))
            last_snap = snap

            if _centre_toast_change(
                before_path, snap["path"], self.window_rect, self.client_rect
            ):
                toast_seen = True

            state, reasons, signals = self._classify_snapshot(snap)
            last_state, last_reasons, last_signals = state, reasons, signals or {}
            roster = self._live_roster_evidence(signals)

            if state == "CLASS_ROSTER" or roster["verified"]:
                return {
                    "clicked": True,
                    "accepted": True,
                    "success_toast_seen": toast_seen,
                    "verified_return_to_roster": True,
                    "verification": (
                        "classifier"
                        if state == "CLASS_ROSTER"
                        else "visual Post All + live roster badge"
                    ),
                    "point": [int(cx), int(cy)],
                    "submit_rect": submit_rect,
                    "state": state,
                    "reasons": reasons,
                    "roster_evidence": roster,
                    "snapshot": snap["path"],
                }

            # The human demo proves the success toast appears before navigation.
            # Once seen, the Submit itself is confirmed even if stale Chromium
            # nodes temporarily confuse page classification.
            if toast_seen and attempt >= 2:
                # Keep observing briefly for the roster, but no more clicks.
                if attempt >= 8:
                    return {
                        "clicked": True,
                        "accepted": True,
                        "success_toast_seen": True,
                        "verified_return_to_roster": False,
                        "verification": "visual success toast",
                        "point": [int(cx), int(cy)],
                        "submit_rect": submit_rect,
                        "state": state,
                        "reasons": reasons,
                        "roster_evidence": roster,
                        "snapshot": snap["path"],
                    }

            # If there was no visible reaction at all, allow ONE retry only
            # while the same wide Submit button is still visibly present.
            if attempt == 3 and not toast_seen:
                retry_rect = _orange_submit_button(
                    snap["path"], self.window_rect
                )
                retry_roster = self._live_roster_evidence(signals)
                if (
                    retry_rect
                    and retry_rect["width"] >= int(self.client_rect["width"] * 0.70)
                    and not retry_roster["verified"]
                ):
                    rcx = retry_rect["left"] + retry_rect["width"] // 2
                    rcy = retry_rect["top"] + retry_rect["height"] // 2
                    wkcommon.restore_window(self.wrapper)
                    time.sleep(0.08)
                    self.click_at((int(rcx), int(rcy)))
                    before_path = snap["path"]

        # We did issue a Submit click, but never observed the success toast or
        # roster. Report that truthfully and stop. Absolutely no Post All click.
        return {
            "clicked": click_issued,
            "accepted": bool(toast_seen),
            "success_toast_seen": toast_seen,
            "verified_return_to_roster": False,
            "verification": (
                "visual success toast"
                if toast_seen
                else "Submit click issued; success not visually verified"
            ),
            "point": [int(cx), int(cy)],
            "submit_rect": submit_rect,
            "state": last_state,
            "reasons": last_reasons,
            "roster_evidence": self._live_roster_evidence(last_signals),
            "snapshot": last_snap["path"] if last_snap else None,
        }
