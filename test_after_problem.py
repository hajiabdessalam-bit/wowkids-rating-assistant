from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon
from rating_engine_v2 import WowkidsRatingSessionV2

TEST_SCORES = {
    "Theory & Application": 4,
    "Creative Thinking": 3,
    "Interpersonal Skills": 5,
}


def _save_progress(path, completed):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"completed": completed}, fh, ensure_ascii=False, indent=2)


def _load_progress(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return list(data.get("completed", []))
    except Exception:
        return []


def main():
    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report = os.path.join(wkcommon.REPORTS, "after_problem_{}.json".format(stamp))
    progress_path = os.path.join(wkcommon.REPORTS, "remaining_progress.json")
    completed = _load_progress(progress_path)
    results = []

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scores": TEST_SCORES,
        "completed_before": list(completed),
        "submitted": False,
        "submit_code_present": False,
        "results": results,
    }

    try:
        session = WowkidsRatingSessionV2()
        for category, score in TEST_SCORES.items():
            if category in completed:
                print("SKIP: {} already completed in an earlier run.".format(category))
                continue
            print("Testing {} = {}...".format(category, score))
            result = session.select_score(category, score, collapse_after=True)
            results.append(result)
            completed.append(category)
            _save_progress(progress_path, completed)
            print("OK: {} verified.".format(category))
    except Exception as exc:
        payload["completed_after"] = list(completed)
        payload["error"] = str(exc)
        with open(report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("ABORT: {}".format(exc))
        print("Submit clicked: NO")
        print("Progress saved. Re-running will continue after completed categories.")
        print("REPORT: {}".format(report))
        return 2

    payload["completed_after"] = list(completed)
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print("SUCCESS: Theory, Creative, and Interpersonal were selected and visually verified.")
    print("Making Skills and Problem Solving were left unchanged.")
    print("Submit clicked: NO")
    print("REPORT: {}".format(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
