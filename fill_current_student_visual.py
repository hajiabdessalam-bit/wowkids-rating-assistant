from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon
from rating_engine import WowkidsRatingSession


def parse_scores(text):
    values = [part.strip() for part in text.split(",")]
    if len(values) != 5:
        raise argparse.ArgumentTypeError("scores must contain exactly five comma-separated values")
    try:
        scores = [int(value) for value in values]
    except ValueError:
        raise argparse.ArgumentTypeError("scores must be integers")
    if any(score < 1 or score > 5 for score in scores):
        raise argparse.ArgumentTypeError("every score must be in 1..5")
    return scores


def main():
    parser = argparse.ArgumentParser(
        description="Fill the five ratings on the currently open fresh WOWKIDS form; never Submit."
    )
    parser.add_argument(
        "--scores",
        required=True,
        type=parse_scores,
        help="Making,ProblemSolving,Theory,Creative,Interpersonal, e.g. 4,4,4,3,5",
    )
    args = parser.parse_args()

    os.makedirs(wkcommon.REPORTS, exist_ok=True)
    stamp = wkcommon.timestamp()
    report = os.path.join(wkcommon.REPORTS, "fill_current_visual_{}.json".format(stamp))

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scores": args.scores,
        "submitted": False,
        "submit_code_present": False,
        "results": [],
    }

    try:
        session = WowkidsRatingSession()
        payload["results"] = session.fill_fresh_form(args.scores, collapse_each=True)
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

    print("SUCCESS: all five ratings were selected and visually verified.")
    print("Submit clicked: NO")
    print("REPORT: {}".format(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
