from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon
import validate_context as ctx
from humanlike_engine import HumanLikeRatingSession, abort_pressed


RATINGS_PATH = os.path.join(HERE, "ratings.csv")
EXAMPLE_PATH = os.path.join(HERE, "ratings.example.csv")

COLUMN_FOR_CATEGORY = {
    "Making Skills": "Making",
    "Problem Solving": "ProblemSolving",
    "Theory & Application": "Theory",
    "Creative Thinking": "Creative",
    "Interpersonal Skills": "Interpersonal",
}

STATUS_NOT_RATING = "NOT_RATING"
STATUS_RATED = "RATED"
STATUS_POSTED = "POSTED"
STATUS_UNKNOWN = "UNKNOWN"


def _norm(text):
    return " ".join((text or "").strip().casefold().split())


def _centre(rect):
    return (
        int(rect["left"] + rect["width"] / 2),
        int(rect["top"] + rect["height"] / 2),
    )


def _distance_x(rect_a, rect_b):
    ax, _ = _centre(rect_a)
    bx, _ = _centre(rect_b)
    return abs(ax - bx)


def _parse_score(value, student, column):
    text = (value or "").strip()
    if not text:
        return None
    try:
        score = int(text)
    except ValueError:
        raise ValueError(
            "{}: {} must be blank or a number 1-5".format(student, column)
        )
    if not 1 <= score <= 5:
        raise ValueError(
            "{}: {} must be from 1 to 5".format(student, column)
        )
    return score


def load_ratings():
    if not os.path.exists(RATINGS_PATH):
        if not os.path.exists(EXAMPLE_PATH):
            raise RuntimeError("ratings.example.csv is missing")
        shutil.copyfile(EXAMPLE_PATH, RATINGS_PATH)
        try:
            os.startfile(RATINGS_PATH)
        except Exception:
            pass
        raise RuntimeError(
            "ratings.csv was created and opened. Replace the demo rows with "
            "your students and scores, save it, then run the assistant again."
        )

    with open(RATINGS_PATH, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise RuntimeError("ratings.csv has no header row")

        canonical = {_norm(name): name for name in reader.fieldnames if name}
        required = [
            "student", "making", "problemsolving", "theory",
            "creative", "interpersonal",
        ]
        missing = [name for name in required if name not in canonical]
        if missing:
            raise RuntimeError(
                "ratings.csv is missing columns: {}".format(", ".join(missing))
            )

        rows = []
        seen = set()
        for raw in reader:
            student = (raw.get(canonical["student"]) or "").strip()
            if not student:
                continue
            key = _norm(student)
            if key in seen:
                raise RuntimeError(
                    "ratings.csv contains duplicate student {!r}".format(student)
                )
            seen.add(key)
            rows.append(
                {
                    "Student": student,
                    "Making": _parse_score(
                        raw.get(canonical["making"]), student, "Making"
                    ),
                    "ProblemSolving": _parse_score(
                        raw.get(canonical["problemsolving"]),
                        student, "ProblemSolving",
                    ),
                    "Theory": _parse_score(
                        raw.get(canonical["theory"]), student, "Theory"
                    ),
                    "Creative": _parse_score(
                        raw.get(canonical["creative"]), student, "Creative"
                    ),
                    "Interpersonal": _parse_score(
                        raw.get(canonical["interpersonal"]),
                        student, "Interpersonal",
                    ),
                }
            )

    if not rows:
        raise RuntimeError("ratings.csv contains no students")
    return rows


def _expand_number_spec(spec, count):
    chosen = []
    seen = set()

    def add(index):
        if 1 <= index <= count and index not in seen:
            seen.add(index)
            chosen.append(index)

    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left), int(right)
            step = 1 if end >= start else -1
            for value in range(start, end + step, step):
                add(value)
        else:
            add(int(token))
    return chosen


def choose_students(rows):
    print("")
    print("Students in your local ratings.csv:")
    for index, row in enumerate(rows, 1):
        scores = [
            row["Making"], row["ProblemSolving"], row["Theory"],
            row["Creative"], row["Interpersonal"],
        ]
        display = ",".join("-" if v is None else str(v) for v in scores)
        print("  {:>2}. {:<24} [{}]".format(index, row["Student"], display))

    print("")
    print("Choose who to rate THIS RUN:")
    print("  ALL            = everyone above")
    print("  FIRST 5        = first five")
    print("  1,3,6-8        = only those numbered students")
    print("  ENTER          = cancel")
    print("")

    while True:
        raw = input("Students: ").strip()
        if not raw:
            return []
        upper = raw.upper()

        if upper == "ALL":
            return list(rows)

        match = re.fullmatch(r"FIRST\s+(\d+)", upper)
        if match:
            limit = max(0, int(match.group(1)))
            return list(rows[:limit])

        try:
            indices = _expand_number_spec(raw.replace(" ", ""), len(rows))
        except Exception:
            indices = []
        if indices:
            return [rows[index - 1] for index in indices]

        print("I did not understand that selection. Try ALL, FIRST 5, or 1,3,6-8.")


class RosterNavigator(HumanLikeRatingSession):
    """Safe roster navigator.

    It has no Post All click method. The only roster click it can issue is a
    point associated with an explicitly selected student's live name/avatar.
    """

    def _roster_snapshot(self, label):
        snap = self.snapshot(label)
        visible = self._visible(snap["nodes"])
        state, reasons, signals = ctx.classify_live_page(
            visible, None, snap["path"], self.window_rect
        )
        roster = self._live_roster_evidence(signals)
        verified = state == "CLASS_ROSTER" or roster["verified"]
        return snap, visible, state, reasons, signals, roster, verified

    def wait_for_roster(self, timeout=10.0):
        deadline = time.monotonic() + float(timeout)
        last = None
        attempt = 0
        while time.monotonic() < deadline:
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")
            attempt += 1
            data = self._roster_snapshot(
                "wait_roster_{:02d}".format(attempt)
            )
            last = data
            if data[-1]:
                return data
            time.sleep(0.35)
        if last:
            _snap, _visible, state, reasons, _signals, _roster, _verified = last
            raise RuntimeError(
                "class roster was not visually verified (last state: {}; {})".format(
                    state, "; ".join(reasons)
                )
            )
        raise RuntimeError("class roster was not visually verified")

    def _scroll_roster_top(self):
        for _ in range(5):
            self._scroll(10, settle=0.10)

    @staticmethod
    def _status_name(item):
        name = _norm(item.get("name"))
        if "not rateing" in name:
            return STATUS_NOT_RATING
        if name == "rated":
            return STATUS_RATED
        if name == "posted":
            return STATUS_POSTED
        return None

    def _student_candidates(self, visible, student):
        wanted = _norm(student)
        candidates = []
        for node in visible:
            name = _norm(node.get("name"))
            rect = node.get("rect")
            if not rect:
                continue
            if name != wanted:
                continue
            if wkcommon.rect_intersection_area(rect, self.client_rect) < 20:
                continue
            candidates.append(node)
        return candidates

    def _match_student(self, visible, signals, student):
        candidates = self._student_candidates(visible, student)
        evidence = (signals or {}).get("roster_visual_support", []) or []

        status_items = []
        for item in evidence:
            status = self._status_name(item)
            rect = item.get("rect")
            if status and rect:
                status_items.append((status, item))

        matches = []
        for node in candidates:
            rect = node["rect"]
            name_bottom = rect["top"] + rect["height"]
            nearby = []
            for status, item in status_items:
                srect = item["rect"]
                vertical = srect["top"] - name_bottom
                if -8 <= vertical <= 95 and _distance_x(rect, srect) <= 72:
                    nearby.append(
                        (
                            abs(vertical - 28) + _distance_x(rect, srect),
                            status,
                            item,
                        )
                    )
            nearby.sort(key=lambda row: row[0])
            if nearby:
                distance, status, item = nearby[0]
                matches.append((distance, node, status, item))

        if not matches:
            if len(candidates) == 1:
                return {
                    "node": candidates[0],
                    "status": STATUS_UNKNOWN,
                    "status_item": None,
                }
            return None

        matches.sort(key=lambda row: row[0])
        _distance, node, status, item = matches[0]
        return {"node": node, "status": status, "status_item": item}

    def find_student(self, student, max_scrolls=12):
        self._scroll_roster_top()

        for attempt in range(max_scrolls + 1):
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")

            (
                snap, visible, state, reasons, signals, roster, verified
            ) = self._roster_snapshot(
                "find_{}_{}".format(
                    re.sub(r"[^A-Za-z0-9]+", "_", student)[:24], attempt
                )
            )
            if not verified:
                raise RuntimeError(
                    "not on a visually verified class roster while looking for {!r}".format(
                        student
                    )
                )

            match = self._match_student(visible, signals, student)
            if match:
                match["snapshot"] = snap
                match["roster_evidence"] = roster
                return match

            if attempt < max_scrolls:
                self._scroll(-3, settle=0.22)

        return None

    def _post_all_rects(self, signals):
        return [
            item.get("rect")
            for item in ((signals or {}).get("roster_visual_support", []) or [])
            if item.get("kind") == "post-all" and item.get("rect")
        ]

    def _safe_student_points(self, node, signals):
        rect = node.get("rect")
        if not rect:
            return []

        x, name_y = _centre(rect)
        # First try the student's displayed name. If that text itself is not
        # clickable, the second point is the avatar directly above it.
        points = [(x, name_y), (x, rect["top"] - 42)]
        safe = []
        bottom_nav_top = (
            self.client_rect["top"] + self.client_rect["height"] - 85
        )

        for px, py in points:
            if not (
                self.client_rect["left"] + 20 <= px
                < self.client_rect["left"] + self.client_rect["width"] - 20
                and self.client_rect["top"] + 180 <= py < bottom_nav_top
            ):
                continue

            forbidden = False
            for post_rect in self._post_all_rects(signals):
                if (
                    post_rect["left"] - 35 <= px
                    <= post_rect["left"] + post_rect["width"] + 35
                    and post_rect["top"] - 35 <= py
                    <= post_rect["top"] + post_rect["height"] + 35
                ):
                    forbidden = True
                    break
            if not forbidden:
                safe.append((int(px), int(py)))
        return safe

    def _assessment_visible(self, snap):
        for node in self._visible(snap["nodes"]):
            name = _norm(node.get("name"))
            if (
                "in-class assessment" in name
                or "课堂评价" in (node.get("name") or "")
            ):
                return True
        return False

    def open_student(self, student, match):
        if match["status"] == STATUS_POSTED:
            raise RuntimeError(
                "{} is already Posted; refusing to change anything".format(student)
            )
        if match["status"] == STATUS_RATED:
            return {"opened": False, "skip": True, "reason": "already Rated"}
        if match["status"] != STATUS_NOT_RATING:
            raise RuntimeError(
                "{} status could not be verified as 'Not rateing'".format(student)
            )

        snap = match["snapshot"]
        visible = self._visible(snap["nodes"])
        _state, _reasons, signals = ctx.classify_live_page(
            visible, None, snap["path"], self.window_rect
        )
        points = self._safe_student_points(match["node"], signals)
        if not points:
            raise RuntimeError(
                "{} has no safe student-card click point".format(student)
            )

        for index, point in enumerate(points):
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")

            self.mouse.click(button="left", coords=point)
            deadline = time.monotonic() + 3.0
            attempt = 0
            while time.monotonic() < deadline:
                time.sleep(0.30)
                attempt += 1
                after = self.snapshot(
                    "opened_{}_{}_{}".format(
                        re.sub(r"[^A-Za-z0-9]+", "_", student)[:20],
                        index,
                        attempt,
                    )
                )
                if self._assessment_visible(after):
                    return {
                        "opened": True,
                        "skip": False,
                        "point": list(point),
                        "snapshot": after["path"],
                    }

                visible_after = self._visible(after["nodes"])
                state, _reasons, signals_after = ctx.classify_live_page(
                    visible_after, None, after["path"], self.window_rect
                )
                roster_after = self._live_roster_evidence(signals_after)
                if state != "CLASS_ROSTER" and not roster_after["verified"]:
                    # Page definitely changed; the rating engine can discover
                    # the assessment categories by scrolling.
                    return {
                        "opened": True,
                        "skip": False,
                        "point": list(point),
                        "snapshot": after["path"],
                    }

            # Still on roster: try the second safe point (avatar) once.

        raise RuntimeError(
            "{} did not open after two safe student-card attempts".format(student)
        )


def scores_for_categories(row, categories):
    scores = {}
    for category in categories:
        column = COLUMN_FOR_CATEGORY[category]
        value = row.get(column)
        if value is None:
            raise RuntimeError(
                "{} has no {} score in ratings.csv, but this lesson displays {}".format(
                    row["Student"], column, category
                )
            )
        scores[category] = int(value)
    return scores


def save_report(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    wkcommon.enable_utf8_stdout()
    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report_path = os.path.join(
        wkcommon.REPORTS, "class_run_{}.json".format(stamp)
    )

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "post_all_clicked": False,
        "selected_students": [],
        "completed": [],
        "skipped": [],
        "errors": [],
    }

    try:
        rows = load_ratings()
        selected = choose_students(rows)
        if not selected:
            print("")
            print("Cancelled. Nothing was clicked.")
            return 0

        payload["selected_students"] = [row["Student"] for row in selected]
        save_report(report_path, payload)

        print("")
        print("Selected {} student(s) for this run:".format(len(selected)))
        for row in selected:
            print("  - {}".format(row["Student"]))
        print("")
        print("Only students visually marked 'Not rateing' will be opened.")
        print("Rated students are skipped. Posted students are NEVER changed.")
        print("Post All is NEVER clicked.")
        print("Press ESC or F10 at any time to stop.")
        print("")

        nav = RosterNavigator()
        nav.wait_for_roster(timeout=4.0)

        for position, row in enumerate(selected, 1):
            if abort_pressed():
                raise RuntimeError("STOP pressed (ESC/F10)")

            student = row["Student"]
            print("[{}/{}] {}...".format(position, len(selected), student))

            nav = RosterNavigator()
            nav.wait_for_roster(timeout=8.0)
            match = nav.find_student(student)

            if not match:
                note = "{}: not found on the visible class roster".format(student)
                print("  SKIP - {}".format(note))
                payload["skipped"].append(note)
                save_report(report_path, payload)
                continue

            status = match["status"]
            if status == STATUS_POSTED:
                note = "{}: already Posted".format(student)
                print("  SKIP - {} (parents may already have received it)".format(note))
                payload["skipped"].append(note)
                save_report(report_path, payload)
                continue
            if status == STATUS_RATED:
                note = "{}: already Rated".format(student)
                print("  SKIP - {}".format(note))
                payload["skipped"].append(note)
                save_report(report_path, payload)
                continue
            if status != STATUS_NOT_RATING:
                note = "{}: roster status could not be verified".format(student)
                print("  SKIP - {}".format(note))
                payload["skipped"].append(note)
                save_report(report_path, payload)
                continue

            opened = nav.open_student(student, match)
            if opened.get("skip"):
                note = "{}: {}".format(student, opened.get("reason", "skipped"))
                payload["skipped"].append(note)
                save_report(report_path, payload)
                continue

            rater = HumanLikeRatingSession()
            categories = rater.discover_categories()
            scores = scores_for_categories(row, categories)

            print("  abilities: {}".format(", ".join(categories)))
            print(
                "  scores   : {}".format(
                    ",".join(str(scores[category]) for category in categories)
                )
            )

            results, _final_snap = rater.fill_discovered(categories, scores)
            submit_result = rater.submit_verified_student(categories, results)

            if not submit_result.get("accepted"):
                raise RuntimeError(
                    "{}: Submit was clicked but WOWKIDS success was not verified".format(
                        student
                    )
                )

            # We may verify Submit from the success toast before the roster has
            # fully rendered. Never open another student until the live roster
            # itself is visually proven.
            nav_after = RosterNavigator()
            nav_after.wait_for_roster(timeout=12.0)

            payload["completed"].append(
                {
                    "student": student,
                    "categories": categories,
                    "scores": scores,
                    "submit_verification": submit_result.get("verification"),
                }
            )
            save_report(report_path, payload)
            print("  DONE - Submitted. Post All untouched.")

        print("")
        print("CLASS RUN FINISHED")
        print("  submitted : {}".format(len(payload["completed"])))
        print("  skipped   : {}".format(len(payload["skipped"])))
        print("  Post All  : NEVER CLICKED")
        print("REPORT: {}".format(report_path))
        return 0

    except Exception as exc:
        payload["errors"].append(str(exc))
        save_report(report_path, payload)
        print("")
        print("STOPPED SAFELY: {}".format(exc))
        print("Post All clicked: NO")
        print("REPORT: {}".format(report_path))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
