from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wkcommon
import validate_context as ctx
from class_controller import (
    RosterNavigator,
    STATUS_NOT_RATING,
    STATUS_POSTED,
    STATUS_RATED,
)
from humanlike_engine import HumanLikeRatingSession, abort_pressed


CONFIG_PATH = os.path.join(HERE, "device_config.json")
STATUS_PATH = os.path.join(HERE, "agent_status.json")
LOG_DIR = os.path.join(HERE, "logs")
DEFAULT_BASE_URL = "https://feedback-assistant-alpha.vercel.app"
POLL_IDLE_SECONDS = 15
POLL_WAITING_SECONDS = 3

JOB_SCORE_FOR_CATEGORY = {
    "Making Skills": "making",
    "Problem Solving": "problemSolving",
    "Theory & Application": "theory",
    "Creative Thinking": "creative",
    "Interpersonal Skills": "interpersonal",
}

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\WOWKIDSRatingAssistantCloudAgent"


class ApiError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            "This PC is not paired yet. Run PAIR_WINDOWS_AGENT.bat once."
        )
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    token = str(data.get("deviceToken") or "").strip()
    base_url = str(data.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")
    if not token.startswith("wk_"):
        raise RuntimeError(
            "device_config.json does not contain a valid pairing token. "
            "Run PAIR_WINDOWS_AGENT.bat again."
        )
    return {"deviceToken": token, "baseUrl": base_url}


class CloudApi:
    def __init__(self, config):
        self.base_url = config["baseUrl"]
        self.token = config["deviceToken"]

    def request(self, method="GET", body=None, timeout=10):
        url = self.base_url + "/api/wowkids-device"
        headers = {
            "Accept": "application/json",
            "x-wowkids-device-token": self.token,
            "User-Agent": "WOWKIDS-Rating-Assistant/1.0",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                payload = json.loads(raw) if raw else {}
                return payload
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {"error": raw}
            raise ApiError(
                payload.get("error") or "HTTP {}".format(exc.code),
                status=exc.code,
                payload=payload,
            )
        except urllib.error.URLError as exc:
            raise ApiError("network unavailable: {}".format(exc.reason))

    def poll(self):
        return self.request("GET")

    def post(self, action, job_id, **fields):
        body = {"action": action, "jobId": job_id}
        body.update(fields)
        return self.request("POST", body=body)

    def claim(self, job_id):
        return self.post("claim", job_id)

    def progress(self, job_id, progress):
        return self.post("progress", job_id, progress=progress)

    def complete(self, job_id, progress):
        return self.post("complete", job_id, progress=progress)

    def fail(self, job_id, progress):
        return self.post("fail", job_id, progress=progress)


def _named_mutex_or_exit():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def _normal_text(value):
    return " ".join(str(value or "").strip().split())


def _normal_time(value):
    text = _normal_text(value)
    for char in ("–", "—", "−", "~", "～"):
        text = text.replace(char, "-")
    return re.sub(r"\s+", "", text)


def _same_rect(a, b, tolerance=2):
    if not a or not b:
        return False
    return all(
        abs(int(a.get(key, 0)) - int(b.get(key, 0))) <= tolerance
        for key in ("left", "top", "width", "height")
    )


def _live_roster_document_nodes(snap, signals, client_rect):
    """Return the UIA subtree owning the visually proven Post All button.

    Chromium can retain stale PageFrames. We use the screenshot-corroborated
    Post All rectangle to identify the live Document, then class/date/time
    matching uses only nodes from that same Document.
    """
    evidence = (signals or {}).get("roster_visual_support", []) or []
    post = next(
        (
            item
            for item in evidence
            if item.get("kind") == "post-all" and item.get("rect")
        ),
        None,
    )
    if not post:
        return []

    nodes = snap["nodes"]
    parents = ctx.build_parent_map(nodes)
    candidate_indexes = []
    for index, node in enumerate(nodes):
        if (
            _normal_text(node.get("name")) == _normal_text(post.get("name"))
            and _same_rect(node.get("rect"), post.get("rect"))
        ):
            candidate_indexes.append(index)

    document_indexes = []
    for index in candidate_indexes:
        doc = ctx.document_index_of(index, nodes, parents)
        if doc is not None and doc not in document_indexes:
            document_indexes.append(doc)

    if len(document_indexes) != 1:
        return []

    doc_index = document_indexes[0]
    depth = nodes[doc_index].get("depth", 0)
    subtree = []
    for node in nodes[doc_index + 1 :]:
        if node.get("depth", 0) <= depth:
            break
        ok, _reason = wkcommon.node_is_visibly_present(node, client_rect)
        if ok:
            subtree.append(node)
    return subtree


def _roster_identity(nav, label="cloud_match"):
    (
        snap,
        _visible,
        state,
        reasons,
        signals,
        roster,
        verified,
    ) = nav._roster_snapshot(label)

    if not verified:
        return {
            "verifiedRoster": False,
            "state": state,
            "reasons": reasons,
            "texts": [],
            "dates": [],
            "times": [],
        }

    live_nodes = _live_roster_document_nodes(
        snap, signals, nav.client_rect
    )
    if not live_nodes:
        return {
            "verifiedRoster": True,
            "state": state,
            "reasons": reasons,
            "texts": [],
            "dates": [],
            "times": [],
            "documentVerified": False,
        }

    top_limit = nav.client_rect["top"] + min(230, nav.client_rect["height"] // 3)
    texts = []
    for node in live_nodes:
        rect = node.get("rect")
        name = _normal_text(node.get("name"))
        if not rect or not name:
            continue
        if rect["top"] >= top_limit:
            continue
        if name not in texts:
            texts.append(name)

    joined = " | ".join(texts)
    dates = sorted(set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", joined)))
    times = sorted(
        set(
            _normal_time(match)
            for match in re.findall(
                r"\b\d{1,2}:\d{2}\s*[-–—~～]\s*\d{1,2}:\d{2}\b",
                joined,
            )
        )
    )
    return {
        "verifiedRoster": True,
        "documentVerified": True,
        "state": state,
        "reasons": reasons,
        "texts": texts,
        "dates": dates,
        "times": times,
        "snapshot": snap["path"],
        "rosterEvidence": roster,
    }


def _matches_job(identity, job):
    if not identity.get("verifiedRoster") or not identity.get("documentVerified"):
        return False, "roster visible but live header document is not unambiguous"

    target_date = str(job.get("target_date") or "").strip()
    target_time = _normal_time(job.get("class_time") or "")
    target_name = _normal_text(job.get("wowkids_class_name") or "")

    texts = identity.get("texts", [])
    joined = " | ".join(texts)
    dates = identity.get("dates", [])
    times = identity.get("times", [])

    if len(dates) > 1:
        return False, "multiple conflicting class dates are visible"
    if len(times) > 1:
        return False, "multiple conflicting class times are visible"
    if target_date and dates != [target_date]:
        return False, "waiting for class date {}".format(target_date)
    if target_time and times != [target_time]:
        return False, "waiting for class time {}".format(job.get("class_time") or "")
    if target_name and target_name.casefold() not in joined.casefold():
        return False, "waiting for class {}".format(target_name)

    return True, "class name/date/time matched"


def _job_progress(stage, completed=None, skipped=None, errors=None, **extra):
    payload = {
        "stage": stage,
        "completed": list(completed or []),
        "skipped": list(skipped or []),
        "errors": list(errors or []),
        "updatedAt": _now(),
    }
    payload.update(extra)
    return payload


def _job_student_name(item):
    return str(item.get("name") or item.get("cn") or "").strip()


def _find_job_student(nav, item):
    names = []
    for value in (item.get("name"), item.get("cn")):
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)

    for name in names:
        match = nav.find_student(name)
        if match:
            return name, match
    return None, None


def _scores_for_categories(item, categories):
    raw = item.get("scores") or {}
    result = {}
    for category in categories:
        key = JOB_SCORE_FOR_CATEGORY.get(category)
        if not key:
            raise RuntimeError(
                "unsupported assessment category {!r}".format(category)
            )
        value = raw.get(key)
        if not isinstance(value, int) or not 1 <= value <= 5:
            raise RuntimeError(
                "{} has no valid score for {}".format(
                    _job_student_name(item), category
                )
            )
        result[category] = value
    return result


def _save_status(**fields):
    data = {"updatedAt": _now()}
    data.update(fields)
    try:
        _write_json(STATUS_PATH, data)
    except Exception:
        pass


def _send_progress(api, job_id, progress):
    try:
        api.progress(job_id, progress)
        return True
    except ApiError as exc:
        if exc.status in (403, 404, 409):
            raise RuntimeError(
                "cloud job is no longer active: {}".format(exc)
            )
        return False


def wait_for_matching_roster(api, job):
    job_id = job["id"]
    completed = list((job.get("progress") or {}).get("completed") or [])
    skipped = list((job.get("progress") or {}).get("skipped") or [])
    errors = list((job.get("progress") or {}).get("errors") or [])
    last_cloud_update = 0.0
    last_reason = ""

    while True:
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        reason = "waiting for WOWKIDS"
        identity = None
        try:
            nav = RosterNavigator()
            identity = _roster_identity(nav)
            matched, reason = _matches_job(identity, job)
            if matched:
                progress = _job_progress(
                    "class_matched",
                    completed,
                    skipped,
                    errors,
                    classMatch={
                        "date": job.get("target_date"),
                        "time": job.get("class_time"),
                        "name": job.get("wowkids_class_name"),
                    },
                )
                _send_progress(api, job_id, progress)
                _save_status(
                    state="class_matched",
                    jobId=job_id,
                    message=reason,
                )
                return nav, identity
        except Exception as exc:
            reason = "waiting for WOWKIDS: {}".format(exc)

        now = time.monotonic()
        if now - last_cloud_update >= 10.0 or reason != last_reason:
            progress = _job_progress(
                "waiting_for_class",
                completed,
                skipped,
                errors,
                waitingReason=reason,
                target={
                    "date": job.get("target_date"),
                    "time": job.get("class_time"),
                    "name": job.get("wowkids_class_name"),
                },
            )
            _send_progress(api, job_id, progress)
            _save_status(
                state="waiting_for_class",
                jobId=job_id,
                message=reason,
            )
            last_cloud_update = now
            last_reason = reason

        time.sleep(POLL_WAITING_SECONDS)


def process_job(api, job):
    job_id = job["id"]
    if job.get("status") == "queued":
        response = api.claim(job_id)
        job = response.get("job") or job

    payload = job.get("payload") or {}
    students = list(payload.get("students") or [])
    if not students:
        raise RuntimeError("queued job contains no students")

    previous_progress = job.get("progress") or {}
    completed = list(previous_progress.get("completed") or [])
    skipped = list(previous_progress.get("skipped") or [])
    errors = list(previous_progress.get("errors") or [])
    completed_ids = {
        str(item.get("studentId") or "")
        for item in completed
        if isinstance(item, dict)
    }

    wait_for_matching_roster(api, job)
    class_categories = None

    for index, item in enumerate(students, 1):
        if abort_pressed():
            raise RuntimeError("STOP pressed (ESC/F10)")

        student_id = str(item.get("studentId") or "")
        display_name = _job_student_name(item)
        if student_id in completed_ids:
            continue

        progress = _job_progress(
            "rating",
            completed,
            skipped,
            errors,
            currentStudent=display_name,
            currentIndex=index,
            totalStudents=len(students),
        )
        _send_progress(api, job_id, progress)
        _save_status(
            state="rating",
            jobId=job_id,
            message="{} ({}/{})".format(
                display_name, index, len(students)
            ),
        )

        nav = RosterNavigator()
        nav.wait_for_roster(timeout=12.0)
        matched, reason = _matches_job(_roster_identity(nav), job)
        if not matched:
            # Do not rate a student if the user navigated away to another class.
            nav, _identity = wait_for_matching_roster(api, job)

        matched_name, match = _find_job_student(nav, item)
        if not match:
            skipped_item = {
                "studentId": student_id,
                "name": display_name,
                "reason": "not found on the matched WOWKIDS roster",
            }
            skipped.append(skipped_item)
            _send_progress(
                api,
                job_id,
                _job_progress(
                    "rating",
                    completed,
                    skipped,
                    errors,
                    currentStudent=None,
                    currentIndex=index,
                    totalStudents=len(students),
                ),
            )
            continue

        status = match.get("status")
        if status == STATUS_POSTED:
            skipped.append(
                {
                    "studentId": student_id,
                    "name": display_name,
                    "reason": "already Posted",
                }
            )
            continue
        if status == STATUS_RATED:
            skipped.append(
                {
                    "studentId": student_id,
                    "name": display_name,
                    "reason": "already Rated",
                }
            )
            continue
        if status != STATUS_NOT_RATING:
            skipped.append(
                {
                    "studentId": student_id,
                    "name": display_name,
                    "reason": "roster status was not safely verified",
                }
            )
            continue

        opened = nav.open_student(matched_name, match)
        if not opened.get("opened"):
            skipped.append(
                {
                    "studentId": student_id,
                    "name": display_name,
                    "reason": opened.get("reason") or "student did not open",
                }
            )
            continue

        rater = HumanLikeRatingSession()
        if class_categories is None:
            class_categories = list(rater.discover_categories())

        scores = _scores_for_categories(item, class_categories)
        results, _final = rater.fill_discovered(
            class_categories, scores
        )
        submit = rater.submit_verified_student(
            class_categories, results
        )
        if not submit.get("accepted"):
            raise RuntimeError(
                "{}: Submit was clicked but success was not verified".format(
                    display_name
                )
            )

        nav_after = RosterNavigator()
        nav_after.wait_for_roster(timeout=15.0)
        still_matches, why = _matches_job(
            _roster_identity(nav_after), job
        )
        if not still_matches:
            raise RuntimeError(
                "{}: returned to a roster but class identity no longer matched ({})".format(
                    display_name, why
                )
            )

        completed_item = {
            "studentId": student_id,
            "name": display_name,
            "categories": list(class_categories),
            "scores": scores,
            "submitVerification": submit.get("verification"),
            "completedAt": _now(),
        }
        completed.append(completed_item)
        completed_ids.add(student_id)

        _send_progress(
            api,
            job_id,
            _job_progress(
                "rating",
                completed,
                skipped,
                errors,
                currentStudent=None,
                currentIndex=index,
                totalStudents=len(students),
                classCategories=list(class_categories),
            ),
        )

    final_progress = _job_progress(
        "completed",
        completed,
        skipped,
        errors,
        totalStudents=len(students),
        classCategories=list(class_categories or []),
        postAllClicked=False,
    )
    api.complete(job_id, final_progress)
    _save_status(
        state="completed",
        jobId=job_id,
        message="{} submitted, {} skipped".format(
            len(completed), len(skipped)
        ),
    )


def run_forever():
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()
    os.makedirs(LOG_DIR, exist_ok=True)

    config = _read_config()
    api = CloudApi(config)
    mutex = _named_mutex_or_exit()
    if mutex is None:
        return 0

    _save_status(
        state="starting",
        message="Connecting to Feedback Assistant",
    )

    try:
        while True:
            # The background listener must not die just because the user presses
            # Escape in an unrelated application. ESC/F10 is honored only once
            # an actual WOWKIDS job is active.
            try:
                response = api.poll()
                job = response.get("job")
                device = response.get("device") or {}
                if not job:
                    _save_status(
                        state="idle",
                        device=device.get("name"),
                        message="Waiting for queued ratings",
                    )
                    time.sleep(POLL_IDLE_SECONDS)
                    continue

                _save_status(
                    state="job_found",
                    jobId=job.get("id"),
                    message="Queued class: {} {}".format(
                        job.get("target_date") or "",
                        job.get("class_time") or "",
                    ).strip(),
                )

                try:
                    process_job(api, job)
                except Exception as exc:
                    message = str(exc)
                    trace = traceback.format_exc()
                    try:
                        progress = (job.get("progress") or {}).copy()
                        progress["stage"] = "failed"
                        progress["errors"] = list(
                            progress.get("errors") or []
                        ) + [message]
                        progress["postAllClicked"] = False
                        progress["updatedAt"] = _now()
                        api.fail(job["id"], progress)
                    except Exception:
                        pass
                    _save_status(
                        state="failed",
                        jobId=job.get("id"),
                        message=message,
                        traceback=trace[-4000:],
                    )
                    time.sleep(5)

            except ApiError as exc:
                _save_status(
                    state="offline",
                    message=str(exc),
                )
                time.sleep(POLL_IDLE_SECONDS)
            except Exception as exc:
                _save_status(
                    state="error",
                    message=str(exc),
                )
                time.sleep(POLL_IDLE_SECONDS)
    finally:
        try:
            ctypes.windll.kernel32.CloseHandle(mutex)
        except Exception:
            pass


def main():
    try:
        return run_forever()
    except Exception as exc:
        _save_status(state="fatal", message=str(exc))
        try:
            print("WOWKIDS cloud agent stopped: {}".format(exc))
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
