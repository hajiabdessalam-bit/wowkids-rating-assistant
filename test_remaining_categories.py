from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon
from rating_engine import WowkidsRatingSession, CATEGORY_ORDER

# Development-only dummy scores for the current test student.
# Making Skills was already verified separately as score 4.
TEST_SCORES = {
    "Problem Solving": 4,
    "Theory & Application": 4,
    "Creative Thinking": 3,
    "Interpersonal Skills": 5,
}


def main():
    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report = os.path.join(wkcommon.REPORTS, "remaining_categories_{}.json".format(stamp))
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "test_scores": TEST_SCORES,
        "submitted": False,
        "submit_code_present": False,
        "results": [],
    }

    try:
        session = WowkidsRatingSession()
        ok, details = session.verify_rating_form()
        if not ok:
            raise RuntimeError("not a visually verified rating form: {}".format(details["state"]))

        # Put every accordion into a known closed state first. This does not
        # change any score already selected in Making Skills.
        for category in CATEGORY_ORDER:
            session.ensure_collapsed(category)

        for category in CATEGORY_ORDER[1:]:
            payload["results"].append(
                session.select_score(category, TEST_SCORES[category], collapse_after=True)
            )
    except Exception as exc:
        payload["error"] = str(exc)
        with open(report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("ABORT: {}".format(exc))
        print("Submit clicked: NO")
        print("REPORT: {}".format(report))
        return 2

    with open(report, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print("SUCCESS: remaining four categories were selected and visually verified.")
    print("Making Skills was left unchanged.")
    print("Submit clicked: NO")
    print("REPORT: {}".format(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
