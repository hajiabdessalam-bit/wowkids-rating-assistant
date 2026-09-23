from __future__ import annotations

import calendar
import datetime as dt
import os
import re
import time

import validate_context as ctx
import wkcommon
from humanlike_engine import HumanLikeRatingSession, abort_pressed


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _text(value):
    return " ".join(str(value or "").strip().split())


def _norm(value):
    return _text(value).casefold()


def _norm_time(value):
    text = _text(value)
    for char in ("–", "—", "−", "~", "～"):
        text = text.replace(char, "-")
    return re.sub(r"\s+", "", text)


def _parse_iso_date(value):
    try:
        return dt.datetime.strptime(str(value), "%Y-%m-%d").date()
    except Exception:
        raise RuntimeError("queued WOWKIDS job has an invalid class date")


def _parse_visible_month(names):
    """Return (year, month) from live UIA text such as 'Sep 2026'."""
    candidates = []
    for raw in names:
        name = _text(raw)
        if not name:
            continue

        match = re.search(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
            r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
            r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})\b",
            name,
            re.I,
        )
        if match:
            token = match.group(1).casefold()
            month = MONTHS.get(token)
            if month:
                candidates.append((int(match.group(2)), month))

        match = re.search(r"\b(20\d{2})\s*年\s*(1[0-2]|0?[1-9])\s*月", name)
        if match:
            candidates.append((int(match.group(1)), int(match.group(2))))

    unique = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique[0] if len(unique) == 1 else None


def _month_delta(current, target):
    cy, cm = current
    ty, tm = target
    return (ty - cy) * 12 + (tm - cm)


def _calendar_cell(target_date):
    """0-based (row, column), Sunday-first, matching the WOWKIDS calendar."""
    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdayscalendar(target_date.year, target_date.month)
    for row, week in enumerate(weeks):
        for column, day in enumerate(week):
            if day == target_date.day:
                return row, column
    raise RuntimeError("target date is not representable in the calendar")


def _inside(rect, client, margin=0):
    if not rect or not client:
        return False
    cx = rect["left"] + rect["width"] // 2
    cy = rect["top"] + rect["height"] // 2
    return (
        client["left"] + margin <= cx
        < client["left"] + client["width"] - margin
        and client["top"] + margin <= cy
        < client["top"] + client["height"] - margin
    )


class WowkidsHomeNavigator(HumanLikeRatingSession):
    """Navigate HOME/calendar -> date -> class -> roster.

    This class is deliberately incapable of rating, Submit, or Post All.
    The only action targets it can click are:
      * calendar month arrows;
      * one calendar day;
      * the queued class-time row;
      * the exact 'View comments' button.

    Supports both the 2026-09-22 layout and the redesigned 2026-09-23
    layout learned from a fresh human demonstration. In the redesign a
    selected day shows class cards directly under the calendar and clicking
    the queued class opens the roster immediately.
    """

    def live_snapshot(self, label):
        snap = self.snapshot(label)
        # Prior Home/popups remain marked visible by Chromium. Only the newest
        # Page-Frame may provide navigation targets, with pixel checks below.
        nodes = snap['nodes']
        frames = [i for i, n in enumerate(nodes) if n.get('control_type') == 'Document'
                  and n.get('name') == 'Page-Frame']
        page = []
        if frames:
            start = frames[-1]
            depth = nodes[start]['depth']
            for node in nodes[start+1:]:
                if node.get('depth', 0) <= depth:
                    break
                page.append(node)
        visible = self._visible(page)
        self._navigation_shot = snap['path']
        state, reasons, signals = ctx.classify_live_page(
            visible, None, snap["path"], self.window_rect
        )
        if state != 'CLASS_ROSTER' and not self._rendered_navigation_surface(visible):
            state, visible = 'UNKNOWN', []
        self.save_diagnostic(snap, 'navigation: ' + state)
        return snap, visible, state, reasons, signals

    def _colour_support(self, node, colour, minimum):
        try:
            from PIL import Image
            image = Image.open(self._navigation_shot).convert('RGB')
            fractions = ctx._crop_colour_fractions(image, node['rect'], self.window_rect)
            return bool(fractions and fractions[colour] >= minimum)
        except Exception:
            return False

    def _rendered_navigation_surface(self, visible):
        """Confirm that Home/date-popup pixels are really on screen.

        The expanded class popup can scroll far enough that its time bars leave
        the viewport. The old gate then threw away the live "View comments"
        node and navigation could never finish. Accept any of four
        screenshot-backed navigation signals instead:
          * orange Switch Accounts on Home;
          * a purple class-time bar;
          * a wide purple expanded-student row;
          * the orange View comments action.

        Stale UIA text alone still cannot pass this gate.
        """
        for node in visible:
            name = _norm(node.get("name"))
            rect = node.get("rect")
            if not rect:
                continue

            if (
                name == "switch accounts"
                and self._colour_support(node, "orange", 0.25)
            ):
                return True

            if (
                re.fullmatch(
                    r"\d{2}:\d{2}-\d{2}:\d{2}",
                    _norm_time(node.get("name")),
                )
                and self._colour_support(node, "purple", 0.30)
            ):
                return True

            if (
                name == "view comments"
                and self._colour_support(node, "orange", 0.25)
            ):
                return True

            # 2026-09-23 Home redesign: "View all courses" is a stable orange
            # action on the rendered calendar page. Selected-date class cards
            # use orange time text instead of the former purple popup bars.
            if (
                name == "view all courses"
                and self._colour_support(node, "orange", 0.08)
            ):
                return True

            if (
                re.fullmatch(
                    r"\d{2}:\d{2}-\d{2}:\d{2}",
                    _norm_time(node.get("name")),
                )
                and self._colour_support(node, "orange", 0.05)
            ):
                return True

            # Expanded student rows are broad purple bars. This keeps the
            # popup live after the class-time header has scrolled out of view.
            if (
                _text(node.get("name"))
                and rect["width"] >= int(self.client_rect["width"] * 0.55)
                and 22 <= rect["height"] <= 70
                and self._colour_support(node, "purple", 0.30)
            ):
                return True

        # Screenshot-only fallback for expanded/scrolled popups where UIA
        # exposes only narrow text children of the wide purple bars.
        if len(self._rendered_purple_band_groups()) >= 2:
            return True

        return False

    def _rendered_purple_band_groups(self, below_screen_y=None):
        """Detect wide purple class/student bars directly from rendered pixels."""
        try:
            from PIL import Image
            image = Image.open(self._navigation_shot).convert("RGB")
        except Exception:
            return []

        wx = self.window_rect["left"]
        wy = self.window_rect["top"]
        cx0 = max(0, self.client_rect["left"] - wx)
        cx1 = min(
            image.width,
            self.client_rect["left"] + self.client_rect["width"] - wx,
        )
        cy0 = max(0, self.client_rect["top"] - wy)
        cy1 = min(
            image.height,
            self.client_rect["top"] + self.client_rect["height"] - wy,
        )
        if below_screen_y is not None:
            cy0 = max(cy0, int(below_screen_y - wy) + 3)

        x0 = int(cx0 + (cx1 - cx0) * 0.05)
        x1 = int(cx0 + (cx1 - cx0) * 0.95)
        if x1 <= x0 or cy1 <= cy0:
            return []

        pixels = image.load()
        step = 3
        xs = list(range(x0, x1, step))
        sample_count = max(1, len(xs))
        hot_rows = []

        for y in range(cy0, cy1):
            purple = 0
            for x in xs:
                red, green, blue = pixels[x, y]
                if (
                    50 <= red < 185
                    and blue >= 60
                    and blue > green + 20
                    and red > green + 10
                ):
                    purple += 1
            if purple / sample_count >= 0.50:
                hot_rows.append(y)

        groups = []
        start = previous = None
        for y in hot_rows:
            if start is None:
                start = previous = y
                continue
            if y == previous + 1:
                previous = y
                continue
            groups.append((start, previous))
            start = previous = y
        if start is not None:
            groups.append((start, previous))

        return [
            (a + wy, b + wy)
            for a, b in groups
            if 12 <= (b - a + 1) <= 70
        ]

    def _rendered_expanded_rows(self, visible, target_time):
        """Rendered proof that the target class row has expanded."""
        time_nodes = self._time_candidates(visible, target_time)
        if time_nodes:
            bottom = max(
                node["rect"]["top"] + node["rect"]["height"]
                for node in time_nodes
                if node.get("rect")
            )
            return len(
                self._rendered_purple_band_groups(
                    below_screen_y=bottom
                )
            ) >= 2

        # If Chromium drops the time text after expansion, a collapsed popup
        # has only the class-time bars while an expanded popup has many rows.
        return len(self._rendered_purple_band_groups()) >= 4


    def _visible_names(self, visible):
        return [_text(node.get("name")) for node in visible if _text(node.get("name"))]

    def _click_point(self, point, label, settle=0.65):
        if abort_pressed():
            raise RuntimeError("STOP pressed (F10)")
        x, y = map(int, point)
        if not (
            self.client_rect["left"] + 15 <= x
            < self.client_rect["left"] + self.client_rect["width"] - 15
            and self.client_rect["top"] + 70 <= y
            < self.client_rect["top"] + self.client_rect["height"] - 55
        ):
            raise RuntimeError("{} click target is outside the safe viewport".format(label))
        self.click_at((x, y))
        time.sleep(settle)

    def _click_node(self, node, label, settle=0.65):
        rect = node.get("rect")
        if not _inside(rect, self.client_rect, margin=10):
            raise RuntimeError("{} target is not safely visible".format(label))
        point = (
            rect["left"] + rect["width"] // 2,
            rect["top"] + rect["height"] // 2,
        )
        self._click_point(point, label, settle=settle)
        return point

    def _calendar_month(self, visible):
        return _parse_visible_month(self._visible_names(visible))

    def _calendar_day_point(self, target_date):
        """Geometry fallback for the 2026-09-23 calendar redesign.

        The primary path still prefers a live UIA day node. These fractions are
        used only after the displayed month is verified and are calibrated from
        the user's new-interface demonstration (544x1024 client).
        """
        row, column = _calendar_cell(target_date)
        # New calendar centres: Sunday x≈85, +66px per column; first visible
        # week y≈498, +60px per row in the 544x1024 client.
        x_fraction = 0.136 + column * 0.1213
        y_fraction = 0.486 + row * 0.0590
        return (
            int(self.client_rect["left"] + self.client_rect["width"] * x_fraction),
            int(self.client_rect["top"] + self.client_rect["height"] * y_fraction),
        )

    def _month_arrow_point(self, direction):
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        # New month arrows sit beside "Sep 2026" around y≈404.
        x_fraction = 0.112 if direction < 0 else 0.892
        return (
            int(self.client_rect["left"] + self.client_rect["width"] * x_fraction),
            int(self.client_rect["top"] + self.client_rect["height"] * 0.395),
        )

    def _find_day_node(self, visible, target_date):
        expected = self._calendar_day_point(target_date)
        candidates = []
        wanted = str(target_date.day)
        for node in visible:
            if _text(node.get("name")) != wanted:
                continue
            rect = node.get("rect")
            if not _inside(rect, self.client_rect):
                continue
            cx = rect["left"] + rect["width"] // 2
            cy = rect["top"] + rect["height"] // 2
            # Restrict to the recorded calendar region, avoiding unrelated
            # counters containing the same number.
            rel_y = (cy - self.client_rect["top"]) / max(1, self.client_rect["height"])
            if not 0.42 <= rel_y <= 0.78:
                continue
            distance = abs(cx - expected[0]) + abs(cy - expected[1])
            candidates.append((distance, node))
        candidates.sort(key=lambda item: item[0])
        if candidates and candidates[0][0] <= 75:
            return candidates[0][1]
        return None

    def ensure_target_month(
        self, target_date, max_clicks=18, initial_snap=None, initial_visible=None
    ):
        target_month = (target_date.year, target_date.month)
        for attempt in range(max_clicks + 1):
            if attempt == 0 and initial_snap is not None and initial_visible is not None:
                snap, visible = initial_snap, initial_visible
                state, reasons = "HOME", []
            else:
                snap, visible, state, reasons, _signals = self.live_snapshot(
                    "home_month_{:02d}".format(attempt)
                )
            current = self._calendar_month(visible)
            if current == target_month:
                return snap, visible

            if current is None:
                raise RuntimeError(
                    "HOME is visible but the calendar month could not be verified"
                )

            delta = _month_delta(current, target_month)
            if delta == 0:
                return snap, visible
            if abs(delta) > 18:
                raise RuntimeError(
                    "target class month is too far from the displayed calendar"
                )

            if attempt == max_clicks:
                break

            self._click_point(
                self._month_arrow_point(-1 if delta < 0 else 1),
                "calendar month arrow",
                settle=0.45,
            )

        raise RuntimeError("calendar did not reach the queued class month")

    def open_target_date(
        self, target_date, initial_snap=None, initial_visible=None
    ):
        _snap, visible = self.ensure_target_month(
            target_date,
            initial_snap=initial_snap,
            initial_visible=initial_visible,
        )

        day_node = self._find_day_node(visible, target_date)
        if day_node:
            point = self._click_node(
                day_node, "calendar day {}".format(target_date.day), settle=0.22
            )
        else:
            # UIA may omit individual day labels in some Chromium builds. The
            # month itself was verified, so use the demonstrated calendar grid.
            point = self._calendar_day_point(target_date)
            self._click_point(
                point, "calendar day {}".format(target_date.day), settle=0.22
            )

        # Do not continue unless the selected-date surface exposes the queued
        # date. This accepts both the legacy popup and the 2026-09-23 inline
        # class-card list under the calendar.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            snap, visible, _state, _reasons, _signals = self.live_snapshot(
                "date_surface"
            )
            if self._date_surface_has_target(visible, target_date):
                return snap, visible, point
            time.sleep(0.08)
        raise RuntimeError("calendar day click did not open the queued date")

    def _new_date_surface_has_target(self, visible, target_date):
        names = [_text(node.get("name")) for node in visible if _text(node.get("name"))]
        month_short = calendar.month_abbr[target_date.month]
        month_long = calendar.month_name[target_date.month]
        pattern = re.compile(
            r"\b(?:{}|{})\s+{}\b".format(
                re.escape(month_short),
                re.escape(month_long),
                target_date.day,
            ),
            re.I,
        )
        return any(pattern.search(name) for name in names)

    def _date_surface_has_target(self, visible, target_date):
        return (
            self._popup_has_date(visible, target_date)
            or self._new_date_surface_has_target(visible, target_date)
        )

    def _popup_has_date(self, visible, target_date):
        y = target_date.year
        m = target_date.month
        d = target_date.day
        compact = [
            "{}年{:02d}月{:02d}日".format(y, m, d),
            "{}年{}月{}日".format(y, m, d),
            "{:04d}-{:02d}-{:02d}".format(y, m, d),
            "{:04d}/{:02d}/{:02d}".format(y, m, d),
        ]
        names = [_text(node.get("name")) for node in visible]
        date_present = any(any(token in name for token in compact) for name in names)
        # The modal's class bars must actually be purple in this capture.
        return date_present and any(
            re.fullmatch(r'\d{2}:\d{2}-\d{2}:\d{2}', _norm_time(n.get('name')))
            and self._colour_support(n, 'purple', 0.40) for n in visible)

    def _class_family_present(self, visible, wanted):
        wanted = _norm(wanted)
        if not wanted:
            return True
        # Queue uses 创造者Engineering while popup commonly shows
        # 创造者Engineering-1班. A containment match is intentional.
        return any(wanted in _norm(node.get("name")) for node in visible)

    def _time_candidates(self, visible, target_time):
        wanted = _norm_time(target_time)
        candidates = []
        for node in visible:
            name = _norm_time(node.get("name"))
            rect = node.get("rect")
            if name != wanted or not rect:
                continue
            if not _inside(rect, self.client_rect, margin=8):
                continue
            rel_y = (
                rect["top"] + rect["height"] // 2 - self.client_rect["top"]
            ) / max(1, self.client_rect["height"])
            if not 0.24 <= rel_y <= 0.72:
                continue
            if not self._colour_support(node, 'purple', 0.40):
                continue
            # Prefer compact text/row nodes around the time itself.
            area = rect["width"] * rect["height"]
            candidates.append((area, node))
        candidates.sort(key=lambda item: item[0])
        return [node for _area, node in candidates]


    def _new_time_candidates(self, visible, target_time):
        """Find the orange class-time row in the redesigned inline class list."""
        wanted = _norm_time(target_time)
        candidates = []
        for node in visible:
            if _norm_time(node.get("name")) != wanted:
                continue
            rect = node.get("rect")
            if not rect or not _inside(rect, self.client_rect, margin=8):
                continue
            rel_y = (
                rect["top"] + rect["height"] // 2 - self.client_rect["top"]
            ) / max(1, self.client_rect["height"])
            if not 0.28 <= rel_y <= 0.92:
                continue
            if not self._colour_support(node, "orange", 0.05):
                continue
            candidates.append((rect["top"], rect["width"] * rect["height"], node))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return [node for _top, _area, node in candidates]

    def _matching_new_class_cards(self, visible, target_time, class_name):
        """Require the queued name on the same rendered row as its time."""
        wanted = _norm(class_name)
        matches = []
        for time_node in self._new_time_candidates(visible, target_time):
            rect = time_node["rect"]
            centre_y = rect["top"] + rect["height"] // 2
            if wanted:
                same_row = [
                    node for node in visible
                    if wanted in _norm(node.get("name"))
                    and node.get("rect")
                    and _inside(node["rect"], self.client_rect, margin=8)
                    and abs(node["rect"]["top"] + node["rect"]["height"] // 2
                            - centre_y) <= 45
                    and node["rect"]["left"] > rect["left"]
                ]
                if not same_row:
                    continue
            matches.append(time_node)
        # Chromium can expose duplicate elements. Collapse identical geometry,
        # but never choose between two distinct matching cards.
        unique = {}
        for node in matches:
            rect = node["rect"]
            unique[(rect["left"], rect["top"], rect["width"], rect["height"])] = node
        return list(unique.values())

    def _seek_new_class_card(
        self, target_time, class_name, max_scrolls=8, initial_visible=None
    ):
        for attempt in range(max_scrolls + 1):
            if attempt == 0 and initial_visible is not None:
                visible = initial_visible
                state = "HOME"
            else:
                _snap, visible, state, _reasons, _signals = self.live_snapshot(
                    "seek_class_card_{:02d}".format(attempt)
                )
            if state == "CLASS_ROSTER":
                raise RuntimeError("class list changed to a roster during search")
            matches = self._matching_new_class_cards(
                visible, target_time, class_name
            )
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise RuntimeError("multiple visible cards match queued time and class")
            if attempt < max_scrolls:
                self._scroll(-4, settle=0.13)
        raise RuntimeError(
            "queued class {} at {} was not found after bounded scrolling".format(
                class_name, target_time
            )
        )

    def expand_target_class(
        self,
        target_date,
        target_time,
        class_name,
        initial_snap=None,
        initial_visible=None,
    ):
        if initial_snap is not None and initial_visible is not None:
            snap, visible = initial_snap, initial_visible
        else:
            snap, visible, _state, _reasons, _signals = self.live_snapshot(
                "class_surface_before_open"
            )
        legacy = self._popup_has_date(visible, target_date)
        modern = self._new_date_surface_has_target(visible, target_date)
        if not (legacy or modern):
            raise RuntimeError("the open class surface is for a different date")
        if legacy and not self._class_family_present(visible, class_name):
            raise RuntimeError("queued WOWKIDS class name is not visible on this date")

        # New interface: class cards are already visible directly below the
        # calendar. Click the centre-right portion of the queued card row (the
        # human demo clicked the class-name area), then wait for the roster.
        if modern and not legacy:
            candidate = self._seek_new_class_card(
                target_time, class_name, initial_visible=visible
            )
            rect = candidate["rect"]
            point = (
                int(self.client_rect["left"] + self.client_rect["width"] * 0.62),
                int(rect["top"] + rect["height"] // 2),
            )
            self._click_point(
                point,
                "queued class card {}".format(target_time),
                settle=0.16,
            )

            deadline = time.monotonic() + 8.0
            attempt = 0
            while time.monotonic() < deadline:
                after, visible_after, state_after, reasons_after, signals_after = (
                    self.live_snapshot(
                        "new_class_open_{:02d}".format(attempt)
                    )
                )
                if state_after == "CLASS_ROSTER":
                    return after, visible_after, point
                attempt += 1
                time.sleep(0.08)
            raise RuntimeError(
                "queued class card was clicked but the redesigned roster did not open"
            )

        candidates = self._time_candidates(visible, target_time)
        if not candidates:
            raise RuntimeError(
                "queued class time {} is not visible on this date".format(target_time)
            )

        point = self._click_node(
            candidates[0],
            "queued class time {}".format(target_time),
            settle=0.22,
        )

        # Expansion is verified by the appearance of student-like rows or the
        # View comments action. We intentionally do not assume row height.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            after, visible_after, _state, _reasons, _signals = self.live_snapshot(
                "class_popup_expanded"
            )
            if self._find_view_comments(visible_after):
                return after, visible_after, point
            if self._expanded_student_rows(visible_after, target_time):
                return after, visible_after, point
            time.sleep(0.08)
        raise RuntimeError("queued class row did not expand")

    def _expanded_student_rows(self, visible, target_time):
        """Verify expansion from rendered purple rows, then UIA geometry."""
        if self._rendered_expanded_rows(visible, target_time):
            return True

        time_nodes = self._time_candidates(visible, target_time)
        if not time_nodes:
            return False
        bottom = max(
            node["rect"]["top"] + node["rect"]["height"]
            for node in time_nodes
            if node.get("rect")
        )
        count = 0
        for node in visible:
            rect = node.get("rect")
            name = _text(node.get("name"))
            if not rect or not name:
                continue
            if rect["top"] <= bottom + 8:
                continue
            if (
                rect["width"] >= int(self.client_rect["width"] * 0.55)
                and 24 <= rect["height"] <= 65
            ):
                count += 1
        return count >= 3

    def _find_view_comments(self, visible):
        candidates = []
        for node in visible:
            name = _norm(node.get("name"))
            rect = node.get("rect")
            if name != "view comments" or not rect:
                continue
            if not self._colour_support(node, 'orange', 0.40):
                continue
            if not _inside(rect, self.client_rect, margin=8):
                continue
            candidates.append(node)
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            # Prefer the lowest live copy; it is the demonstrated orange button
            # at the bottom of the expanded class.
            candidates.sort(key=lambda n: n["rect"]["top"], reverse=True)
            return candidates[0]
        return None

    def open_roster_from_expanded(self, max_scrolls=8):
        for attempt in range(max_scrolls + 1):
            snap, visible, state, reasons, signals = self.live_snapshot(
                "seek_view_comments_{:02d}".format(attempt)
            )
            if state == "CLASS_ROSTER":
                return {
                    "alreadyRoster": True,
                    "snapshot": snap["path"],
                    "reasons": reasons,
                    "signals": signals,
                }

            button = self._find_view_comments(visible)
            if button:
                point = self._click_node(button, "View comments", settle=0.7)
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    after, visible_after, state_after, reasons_after, signals_after = (
                        self.live_snapshot("after_view_comments")
                    )
                    if state_after == "CLASS_ROSTER":
                        return {
                            "alreadyRoster": False,
                            "clickPoint": list(point),
                            "snapshot": after["path"],
                            "reasons": reasons_after,
                            "signals": signals_after,
                        }
                    time.sleep(0.30)
                raise RuntimeError("View comments click did not open the class roster")

            if attempt < max_scrolls:
                # Human demonstration used one 3-notch downward burst. Use the
                # same small bounded motion and re-check after every burst.
                self._scroll(-4, settle=0.13)

        raise RuntimeError("View comments was not found after bounded scrolling")

    def go_home_from_roster(self, timeout=6.0):
        """Return a completed class roster to WOWKIDS Home.

        The bottom navigation bar exposes a visible "Home" tab. Using that is
        safer and more deterministic than replaying browser/back history or
        guessing how many modal layers are open.

        This method never touches Post All or any student card.
        """
        snap, visible, state, reasons, signals = self.live_snapshot(
            "batch_return_home_before"
        )
        if state == "HOME" or self._calendar_month(visible) is not None:
            return {
                "alreadyHome": True,
                "snapshot": snap["path"],
            }

        # Require a live roster before clicking the bottom navigation.
        roster_items = (signals or {}).get("roster_visual_support", []) or []
        has_post_all = any(
            item.get("kind") == "post-all" for item in roster_items
        )
        if state != "CLASS_ROSTER" and not has_post_all:
            raise RuntimeError(
                "cannot return to Home: current page is not a verified roster"
            )

        candidates = []
        bottom_start = (
            self.client_rect["top"] + int(self.client_rect["height"] * 0.82)
        )
        for node in visible:
            if _norm(node.get("name")) != "home":
                continue
            rect = node.get("rect")
            if not rect or not _inside(rect, self.client_rect, margin=4):
                continue
            cy = rect["top"] + rect["height"] // 2
            if cy < bottom_start:
                continue
            candidates.append(node)

        if not candidates:
            raise RuntimeError(
                "verified roster is open, but the bottom Home tab was not found"
            )

        candidates.sort(
            key=lambda node: node["rect"]["top"],
            reverse=True,
        )
        home_node = candidates[0]
        home_rect = home_node["rect"]
        home_point = (
            home_rect["left"] + home_rect["width"] // 2,
            home_rect["top"] + home_rect["height"] // 2,
        )
        # The general page-content click gate excludes the bottom navigation.
        # This separate gate is limited to the live, orange Home label there.
        if not (
            self._colour_support(home_node, "orange", 0.03)
            and self.client_rect["left"] + 10 <= home_point[0]
            < self.client_rect["left"] + self.client_rect["width"] // 3
            and self.client_rect["top"] + self.client_rect["height"] - 90
            <= home_point[1]
            < self.client_rect["top"] + self.client_rect["height"] - 8
        ):
            raise RuntimeError("bottom Home tab lacks live rendered proof")
        if abort_pressed():
            raise RuntimeError("STOP pressed (F10)")
        self.click_at(home_point)
        time.sleep(0.18)

        deadline = time.monotonic() + float(timeout)
        attempt = 0
        while time.monotonic() < deadline:
            after, visible_after, state_after, _reasons, _signals = (
                self.live_snapshot(
                    "batch_return_home_{:02d}".format(attempt)
                )
            )
            if (
                state_after == "HOME"
                or self._calendar_month(visible_after) is not None
            ):
                return {
                    "alreadyHome": False,
                    "snapshot": after["path"],
                }
            attempt += 1
            time.sleep(0.15)

        raise RuntimeError(
            "bottom Home tab was clicked but the calendar Home page was not verified"
        )


    def navigate_to_roster(self, job):
        target_date = _parse_iso_date(job.get("target_date"))
        target_time = _text(job.get("class_time"))
        class_name = _text(job.get("wowkids_class_name"))
        if not target_time:
            raise RuntimeError("queued WOWKIDS job has no class time")

        # State-machine loop supports starting from HOME or from an already-open
        # date popup after a supervisor restart.
        for stage in range(10):
            if abort_pressed():
                raise RuntimeError("STOP pressed (F10)")

            snap, visible, state, reasons, signals = self.live_snapshot(
                "nav_state_{:02d}".format(stage)
            )
            if state == "CLASS_ROSTER":
                return {
                    "state": "CLASS_ROSTER",
                    "snapshot": snap["path"],
                    "reasons": reasons,
                    "signals": signals,
                }

            if self._date_surface_has_target(visible, target_date):
                if self._find_view_comments(visible):
                    return self.open_roster_from_expanded()
                if self._expanded_student_rows(visible, target_time):
                    return self.open_roster_from_expanded()
                self.expand_target_class(
                    target_date,
                    target_time,
                    class_name,
                    initial_snap=snap,
                    initial_visible=visible,
                )
                return self.open_roster_from_expanded()

            month = self._calendar_month(visible)
            if state == "HOME" or month is not None:
                date_snap, date_visible, _point = self.open_target_date(
                    target_date,
                    initial_snap=snap,
                    initial_visible=visible,
                )
                self.expand_target_class(
                    target_date,
                    target_time,
                    class_name,
                    initial_snap=date_snap,
                    initial_visible=date_visible,
                )
                return self.open_roster_from_expanded()

            raise RuntimeError(
                "WOWKIDS is open, but it is not on Home/calendar, the queued "
                "date popup, or the class roster"
            )

        raise RuntimeError("navigation state machine exceeded its safe limit")


def selftest():
    checks = []

    def check(name, condition):
        checks.append((name, bool(condition)))
        print("[{}] {}".format("PASS" if condition else "FAIL", name))

    check("parse Sep 2026", _parse_visible_month(["Sep 2026"]) == (2026, 9))
    check("parse September 2026", _parse_visible_month(["September 2026"]) == (2026, 9))
    check("parse Chinese month", _parse_visible_month(["2026年9月"]) == (2026, 9))
    check("month delta forward", _month_delta((2026, 9), (2026, 11)) == 2)
    check("month delta backward", _month_delta((2026, 9), (2026, 8)) == -1)

    sep19 = dt.date(2026, 9, 19)
    row, column = _calendar_cell(sep19)
    check("Sep 19 2026 is Saturday column", column == 6)
    check("Sep 19 2026 is third calendar row", row == 2)

    failed = [name for name, ok in checks if not ok]
    if failed:
        print("FAILED: {}".format(", ".join(failed)))
        return 1
    print("all home-navigation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
