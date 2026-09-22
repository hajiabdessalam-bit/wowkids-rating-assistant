from __future__ import annotations

import csv
import os
import statistics
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "reports", "performance.csv")
PHASES = [
    ("rosterReady", "roster ready"),
    ("studentLookup", "student lookup"),
    ("openStudent", "open student"),
    ("assessmentLoad", "assessment load"),
    ("categoryDiscovery", "category discovery"),
    ("ratingActions", "rating actions"),
    ("submit", "submit"),
    ("returnToRoster", "return to roster"),
]


def _number(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    limit = 12
    if argv:
        try:
            limit = max(1, int(argv[0]))
        except Exception:
            pass

    if not os.path.exists(CSV_PATH):
        print("No performance data yet.")
        print("Run at least one successful queued rating job first.")
        return 0

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    completed = [row for row in rows if row.get("status") == "completed"]
    if not completed:
        print("No completed student timing rows yet.")
        return 0

    chosen = completed[-limit:]
    totals = [_number(row.get("totalSeconds")) for row in chosen]

    print("")
    print("WOWKIDS PERFORMANCE - LAST {} COMPLETED STUDENT(S)".format(len(chosen)))
    print("=" * 64)
    for index, row in enumerate(chosen, 1):
        print(
            "{:>2}. {:<22} {:>6.2f}s   {} {}".format(
                index,
                (row.get("student") or "")[:22],
                _number(row.get("totalSeconds")),
                row.get("targetDate") or "",
                row.get("classTime") or "",
            )
        )

    print("")
    print("Average total : {:.2f}s/student".format(statistics.mean(totals)))
    print("Fastest       : {:.2f}s".format(min(totals)))
    print("Slowest       : {:.2f}s".format(max(totals)))

    print("")
    print("Average phase time:")
    for key, label in PHASES:
        values = [_number(row.get(key)) for row in chosen]
        if any(value > 0 for value in values):
            print("  {:<20} {:>6.2f}s".format(label, statistics.mean(values)))

    print("")
    print("Full CSV:")
    print("  {}".format(CSV_PATH))
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
