from __future__ import annotations

import csv
import json
import os
import time
from contextlib import contextmanager


HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(HERE, "reports")
JSONL_PATH = os.path.join(REPORTS_DIR, "performance.jsonl")
CSV_PATH = os.path.join(REPORTS_DIR, "performance.csv")


class StudentPerformance:
    """Lightweight per-student timing recorder.

    This is intentionally observational only. It does not click, scroll,
    submit, or change any automation behavior.
    """

    def __init__(
        self,
        *,
        job_id,
        student,
        class_name="",
        class_time="",
        target_date="",
    ):
        self.job_id = str(job_id or "")
        self.student = str(student or "")
        self.class_name = str(class_name or "")
        self.class_time = str(class_time or "")
        self.target_date = str(target_date or "")
        self.started_at_wall = time.strftime("%Y-%m-%d %H:%M:%S")
        self.started = time.perf_counter()
        self.phases = {}
        self.notes = {}

    @contextmanager
    def phase(self, name):
        key = str(name)
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = max(0.0, time.perf_counter() - started)
            self.phases[key] = round(self.phases.get(key, 0.0) + elapsed, 3)

    def add(self, name, seconds):
        key = str(name)
        self.phases[key] = round(
            self.phases.get(key, 0.0) + max(0.0, float(seconds)),
            3,
        )

    def note(self, key, value):
        self.notes[str(key)] = value

    def summary(self, status="completed"):
        total = max(0.0, time.perf_counter() - self.started)
        return {
            "jobId": self.job_id,
            "student": self.student,
            "className": self.class_name,
            "classTime": self.class_time,
            "targetDate": self.target_date,
            "startedAt": self.started_at_wall,
            "status": str(status),
            "totalSeconds": round(total, 3),
            "phases": dict(self.phases),
            "notes": dict(self.notes),
        }

    def finish(self, status="completed"):
        os.makedirs(REPORTS_DIR, exist_ok=True)
        data = self.summary(status=status)

        try:
            with open(JSONL_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception:
            pass

        fields = [
            "startedAt",
            "jobId",
            "targetDate",
            "classTime",
            "className",
            "student",
            "status",
            "totalSeconds",
            "rosterReady",
            "studentLookup",
            "openStudent",
            "assessmentLoad",
            "categoryDiscovery",
            "ratingActions",
            "submit",
            "returnToRoster",
            "categoryCount",
        ]
        row = {
            "startedAt": data["startedAt"],
            "jobId": data["jobId"],
            "targetDate": data["targetDate"],
            "classTime": data["classTime"],
            "className": data["className"],
            "student": data["student"],
            "status": data["status"],
            "totalSeconds": data["totalSeconds"],
            "rosterReady": self.phases.get("roster_ready", 0.0),
            "studentLookup": self.phases.get("student_lookup", 0.0),
            "openStudent": self.phases.get("open_student", 0.0),
            "assessmentLoad": self.phases.get("assessment_load", 0.0),
            "categoryDiscovery": self.phases.get("category_discovery", 0.0),
            "ratingActions": self.phases.get("rating_actions", 0.0),
            "submit": self.phases.get("submit", 0.0),
            "returnToRoster": self.phases.get("return_to_roster", 0.0),
            "categoryCount": self.notes.get("categoryCount", ""),
        }

        try:
            new_file = not os.path.exists(CSV_PATH)
            with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                if new_file:
                    writer.writeheader()
                writer.writerow(row)
        except Exception:
            pass

        return data
