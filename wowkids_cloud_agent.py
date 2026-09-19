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


STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
CONFIG_PATH = os.path.join(STATE_DIR, "device_config.json")
LEGACY_CONFIG_PATH = os.path.join(HERE, "device_config.json")
STATUS_PATH = os.path.join(STATE_DIR, "agent_status.json")
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
MUTEX_NAME = "Local\\WOWKIDSRatingAssistantCloudAgentV2"


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
    os.makedirs(STATE_DIR, exist_ok=True)

    # Migrate the original repo-local pairing file once. The permanent copy
    # lives under LOCALAPPDATA so git pulls, repo cleanups and app updates
    # cannot make the PC "forget" its device token.
    if not os.path.exists(CONFIG_PATH) and os.path.exists(LEGACY_CONFIG_PATH):
        try:
            with open(LEGACY_CONFIG_PATH, encoding="utf-8") as fh:
                legacy = json.load(fh)
            _write_json(CONFIG_PATH, legacy)
        except Exception:
            pass

    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            "This PC has never been paired. Run PAIR_WINDOWS_AGENT.bat once."
        )
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    token = str(data.get("deviceToken") or "").strip()
    base_url = str(data.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/")
    if not token.startswith("wk_"):
        raise RuntimeError(
            "The saved pairing is invalid. Use PAIR_WINDOWS_AGENT.bat only "
            "if this PC was intentionally reset."
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
    """Pick the Chromium Document that best matches screenshot-proven roster UI.

    WeChat can keep stale PageFrames visible to UI Automation.  A single live
    Post All rectangle can therefore map to more than one Document.  Instead
    of failing just because duplicates exist, score every candidate Document
    against ALL screenshot-corroborated roster badges (Post All, Rated,
    Not rateing, Gallery, etc.).  A stale document normally disagrees on some
    names/statuses even when geometry is similar.

    If the best candidates are still tied, we only accept the tie when their
    visible header identity (date/time text) agrees.  Otherwise we fail closed.
    """
    evidence = (signals or {}).get("roster_visual_support", []) or []
    evidence = [
        item for item in evidence
        if item.get("rect") and item.get("name")
    ]
    posts = [item for item in evidence if item.get("kind") == "post-all"]
    if not posts:
        return []

    nodes = snap["nodes"]
    parents = ctx.build_parent_map(nodes)

    def rect_close(a, b, tolerance=3):
        if not a or not b:
            return False
        return all(
            abs(int(a.get(key, 0)) - int(b.get(key, 0))) <= tolerance
            for key in ("left", "top", "width", "height")
        )

    def norm(value):
        return _normal_text(value).casefold()

    candidate_docs = []
    for index, node in enumerate(nodes):
        if not node.get("rect"):
            continue
        if not any(
            norm(node.get("name")) == norm(post.get("name"))
            and rect_close(node.get("rect"), post.get("rect"))
            for post in posts
        ):
            continue
        doc = ctx.document_index_of(index, nodes, parents)
        if doc is not None and doc not in candidate_docs:
            candidate_docs.append(doc)

    scored = []
    for doc_index in candidate_docs:
        depth = nodes[doc_index].get("depth", 0)
        subtree = []
        for node in nodes[doc_index + 1 :]:
            if node.get("depth", 0) <= depth:
                break
            ok, _reason = wkcommon.node_is_visibly_present(node, client_rect)
            if ok:
                subtree.append(node)

        matched = []
        for item in evidence:
            wanted_name = norm(item.get("name"))
            wanted_rect = item.get("rect")
            if any(
                norm(node.get("name")) == wanted_name
                and rect_close(node.get("rect"), wanted_rect)
                for node in subtree
            ):
                matched.append(item)

        post_count = sum(1 for item in matched if item.get("kind") == "post-all")
        badge_count = sum(
            1 for item in matched
            if item.get("kind") in ("posted", "purple-badge", "grey-badge")
        )
        if post_count < 1 or badge_count < 2:
            continue

        top_limit = client_rect["top"] + min(230, client_rect["height"] // 3)
        header = []
        for node in subtree:
            rect = node.get("rect")
            name = _normal_text(node.get("name"))
            if rect and name and rect["top"] < top_limit:
                header.append(name)
        header_text = " | ".join(header)
        dates = tuple(sorted(set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", header_text))))
        times = tuple(sorted(set(
            _normal_time(match)
            for match in re.findall(
                r"\b\d{1,2}:\d{2}\s*[-–—~～]\s*\d{1,2}:\d{2}\b",
                header_text,
            )
        )))
        scored.append({
            "doc": doc_index,
            "subtree": subtree,
            "score": len(matched),
            "badge_count": badge_count,
            "dates": dates,
            "times": times,
        })

    if not scored:
        return []

    scored.sort(
        key=lambda item: (item["score"], item["badge_count"], item["doc"]),
        reverse=True,
    )
    best_score = scored[0]["score"]
    best_badges = scored[0]["badge_count"]
    tied = [
        item for item in scored
        if item["score"] == best_score and item["badge_count"] == best_badges
    ]
    if len(tied) == 1:
        return tied[0]["subtree"]

    signatures = {(item["dates"], item["times"]) for item in tied}
    if len(signatures) == 1:
        # Equivalent duplicate accessibility frames. Prefer the newest frame.
        tied.sort(key=lambda item: item["doc"], reverse=True)
        return tied[0]["subtree"]

    return []

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
            nav = RosterNavigator(raise_window=False)
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


def _select_runnable_job(response):
    """Choose the job whose class is actually open, not merely the oldest job.

    A coach can queue several classes. Older jobs may legitimately stay in
    waiting_for_class for hours. They must not monopolize the agent and block
    a newer class that is currently open in WOWKIDS.
    """
    jobs = list(response.get("jobs") or [])
    if not jobs and response.get("job"):
        jobs = [response["job"]]
    if not jobs:
        return None, "no pending jobs"

    # If a job had already started rating students before a restart, resume it
    # before considering anything else.
    for job in jobs:
        if job.get("status") != "running":
            continue
        stage = (job.get("progress") or {}).get("stage")
        if stage not in ("waiting_for_class", "queued"):
            return job, "resuming active rating job"

    # All remaining jobs are queued or merely waiting for their class. Observe
    # the current roster once and select whichever job matches its identity.
    try:
        nav = RosterNavigator(raise_window=False)
        identity = _roster_identity(nav, label="cloud_pick_job")
    except Exception as exc:
        return None, "waiting for a WOWKIDS roster: {}".format(exc)

    reasons = []
    for job in jobs:
        matched, reason = _matches_job(identity, job)
        if matched:
            return job, reason
        reasons.append(
            "{} {}: {}".format(
                job.get("target_date") or "",
                job.get("class_time") or "",
                reason,
            ).strip()
        )

    return None, "open roster does not match pending jobs ({})".format(
        " | ".join(reasons[:4])
    )


def run_forever():
    wkcommon.enable_utf8_stdout()
    wkcommon.bootstrap_libs()
    wkcommon.set_dpi_awareness()
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

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
                device = response.get("device") or {}
                jobs = list(response.get("jobs") or [])
                if not jobs and response.get("job"):
                    jobs = [response["job"]]

                if not jobs:
                    _save_status(
                        state="idle",
                        device=device.get("name"),
                        message="Waiting for queued ratings",
                    )
                    time.sleep(POLL_IDLE_SECONDS)
                    continue

                job, selection_reason = _select_runnable_job(response)
                if not job:
                    _save_status(
                        state="waiting_for_matching_class",
                        device=device.get("name"),
                        pendingJobs=len(jobs),
                        message=selection_reason,
                    )
                    time.sleep(POLL_WAITING_SECONDS)
                    continue

                _save_status(
                    state="job_found",
                    jobId=job.get("id"),
                    message="{} — {} {}".format(
                        selection_reason,
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
