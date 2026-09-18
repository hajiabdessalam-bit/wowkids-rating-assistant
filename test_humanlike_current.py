from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon
from humanlike_engine import HumanLikeRatingSession


def _parse_scores(text, categories):
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) != len(categories):
        raise ValueError(
            "need exactly {} scores because this lesson has {} abilities".format(
                len(categories), len(categories)
            )
        )
    try:
        values = [int(part) for part in parts]
    except ValueError:
        raise ValueError("scores must be numbers from 1 to 5")
    if any(value < 1 or value > 5 for value in values):
        raise ValueError("every score must be from 1 to 5")
    return dict(zip(categories, values))


def main():
    wkcommon.enable_utf8_stdout()
    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report = os.path.join(
        wkcommon.REPORTS, "humanlike_current_{}.json".format(stamp)
    )

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "submitted": False,
        "submit_code_present": False,
        "results": [],
    }

    try:
        session = HumanLikeRatingSession()

        print("")
        print("Scanning this lesson first. This phase only scrolls; it does not rate.")
        categories = session.discover_categories()
        payload["discovered_categories"] = categories

        print("")
        print("Detected abilities on THIS lesson:")
        for index, category in enumerate(categories, 1):
            print("  {}. {}".format(index, category))

        print("")
        print("Enter one score (1-5) for each ability IN THE ORDER ABOVE.")
        print("Example for four abilities: 4,4,3,5")
        raw = input("Scores: ").strip()
        scores = _parse_scores(raw, categories)
        payload["scores"] = scores

        print("")
        print("Starting guarded fill. It will NOT click Submit.")
        print("Press ESC at any time to abort.")
        results, final_snap = session.fill_discovered(categories, scores)
        payload["results"] = results
        payload["final_screenshot"] = final_snap["path"]

        submit_rect, _ = session.find_submit_only()
        payload["submit_visible"] = bool(submit_rect)
        payload["submit_rect"] = submit_rect

    except Exception as exc:
        payload["error"] = str(exc)
        with open(report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("")
        print("ABORT: {}".format(exc))
        print("Submit clicked: NO")
        print("REPORT: {}".format(report))
        return 2

    with open(report, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print("")
    print("SUCCESS: every ability displayed by this lesson was filled and verified.")
    print("The accordions were intentionally left open, matching the human demo.")
    print("Submit clicked: NO")
    print("REPORT: {}".format(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
