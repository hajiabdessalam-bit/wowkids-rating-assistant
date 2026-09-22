from __future__ import annotations

import ctypes
import datetime as dt
import hashlib
import base64
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
from humanlike_engine import (
    HumanLikeRatingSession,
    abort_pressed,
    assessment_payload_ready,
)
from home_navigator import WowkidsHomeNavigator
from performance_log import StudentPerformance


STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE,
    "WOWKIDSRatingAssistant",
)
CONFIG_PATH = os.path.join(STATE_DIR, "device_config.json")
LEGACY_CONFIG_PATH = os.path.join(HERE, "device_config.json")
STATUS_PATH = os.path.join(STATE_DIR, "agent_status.json")
UPDATE_STATE_PATH = os.path.join(STATE_DIR, "poll_update_state.json")
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
MUTEX_NAME = "Local\\WOWKIDSRatingAssistantCloudAgentV4"


def _source_version():
    digest = hashlib.sha256()
    for name in ('wowkids_cloud_agent.py', 'home_navigator.py', 'class_controller.py',
                 'humanlike_engine.py', 'rating_engine.py', 'validate_context.py',
                 'visual_score_rows.py', 'wkcommon.py', 'performance_log.py'):
        with open(os.path.join(HERE, name), 'rb') as source:
            digest.update(source.read())
    return digest.hexdigest()[:16]


LOADED_VERSION = _source_version()


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


def _read_update_state():
    try:
        with open(UPDATE_STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _apply_agent_update(payload):
    """Apply one small source file delivered inside the normal poll response."""
    if not isinstance(payload, dict):
        return False

    update_id = str(payload.get("id") or "").strip()
    path = str(payload.get("path") or "").strip().replace("\\", "/")
    content_b64 = str(payload.get("contentB64") or "")
    expected = str(payload.get("sha256") or "").strip().lower()
    index = int(payload.get("index") or 0)
    total = int(payload.get("total") or 0)

    if not update_id or not path or not content_b64 or total < 1:
        return False
    if path.startswith("../") or "/../" in path or path.startswith("/"):
        raise RuntimeError("unsafe poll-update path")

    raw = base64.b64decode(content_b64.encode("ascii"))
    actual = hashlib.sha256(raw).hexdigest()
    if expected and actual != expected:
        raise RuntimeError(
            "poll-update checksum mismatch for {}".format(path)
        )

    target = os.path.join(HERE, *path.split("/"))
    os.makedirs(os.path.dirname(target) or HERE, exist_ok=True)

    same = False
    try:
        with open(target, "rb") as fh:
            same = fh.read() == raw
    except Exception:
        pass

    if not same:
        backup_dir = os.path.join(
            HERE,
            "_poll_update_backups",
            time.strftime("%Y%m%d_%H%M%S"),
        )
        if os.path.isfile(target):
            backup_path = os.path.join(
                backup_dir,
                *path.split("/"),
            )
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            try:
                import shutil
                shutil.copy2(target, backup_path)
            except Exception:
                pass

        tmp = target + ".poll-update.tmp"
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, target)

    state = _read_update_state()
    state.update({
        "updateId": update_id,
        "nextIndex": index + 1,
        "lastPath": path,
        "updatedAt": _now(),
    })
    if index + 1 >= total:
        state["installedId"] = update_id
        state["nextIndex"] = total
    _write_json(UPDATE_STATE_PATH, state)
    return True


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
        update_state = _read_update_state()
        headers = {
            "Accept": "application/json",
            "x-wowkids-device-token": self.token,
            "User-Agent": "WOWKIDS-Rating-Assistant/2.0",
            "x-wowkids-installed-update-id": str(
                update_state.get("installedId") or ""
            ),
            "x-wowkids-update-id": str(
                update_state.get("updateId") or ""
            ),
            "x-wowkids-update-next": str(
                int(update_state.get("nextIndex") or 0)
            ),
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

def _available_scores(item):
    raw = item.get("scores") or {}
    result = {}
    for category, key in JOB_SCORE_FOR_CATEGORY.items():
        value = raw.get(key)
        if isinstance(value, int) and 1 <= value <= 5:
            result[category] = value
    return result


def _current_rating_resume_match(jobs):
    """Resume safely from an assessment page at ANY vertical position.

    A stopped rating can leave the form midway down, where the student/date
    header is no longer visible and the normal page classifier may be UNKNOWN.
    Normalize the assessment to the top first, then identify the live student,
    date and class time before choosing a queued job.

    Scrolling an unrelated HOME/roster page to its top is harmless and lets
    the normal selector continue if this is not an assessment.
    """
    try:
        rater = HumanLikeRatingSession(raise_window=False)

        # The key recovery behavior: always normalize the current mini-app view
        # before trying to identify it. This exposes the student card and the
        # lesson/date/time line shown at the top of every assessment.
        rater._scroll_to_top()
        snap = rater.snapshot("resume_probe_top")
        visible = rater._visible(snap["nodes"])

        state, _reasons, _signals = ctx.classify_live_page(
            visible,
            None,
            snap["path"],
            rater.window_rect,
        )

        # Mid-rating pages can still classify UNKNOWN because the ability
        # headings are below the fold. The assessment payload verifier is the
        # stronger signal at the normalized top, so do not require
        # state == RATING_FORM here.
        page_texts = [
            _normal_text(node.get("name"))
            for node in visible
            if _normal_text(node.get("name"))
        ]
        joined = " | ".join(page_texts)
        has_assessment_marker = (
            "课堂评价" in joined
            or "in-class assessment" in joined.casefold()
        )
        if state != "RATING_FORM" and not has_assessment_marker:
            return None
    except Exception:
        return None

    dates = sorted(set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", joined)))
    times = sorted(set(
        _normal_time(match)
        for match in re.findall(
            r"\b\d{1,2}:\d{2}\s*[-–—~～]\s*\d{1,2}:\d{2}\b",
            joined,
        )
    ))

    matches = []
    for job in jobs:
        if job.get("status") not in ("queued", "running"):
            continue

        target_date = str(job.get("target_date") or "").strip()
        target_time = _normal_time(job.get("class_time") or "")

        # The screenshot the user showed exposes both values at the top of the
        # lesson card. Use them to disambiguate same-name students/classes.
        if target_date:
            if len(dates) != 1 or dates[0] != target_date:
                continue
        if target_time:
            if len(times) != 1 or times[0] != target_time:
                continue

        progress = job.get("progress") or {}
        completed_ids = {
            str(item.get("studentId") or "")
            for item in progress.get("completed") or []
            if isinstance(item, dict)
        }

        payload = job.get("payload") or {}
        for item in payload.get("students") or []:
            student_id = str(item.get("studentId") or "")
            if student_id and student_id in completed_ids:
                continue

            names = []
            for value in (item.get("name"), item.get("cn")):
                name = str(value or "").strip()
                if name and name not in names:
                    names.append(name)

            for name in names:
                ready, _reason = assessment_payload_ready(
                    snap,
                    rater.window_rect,
                    rater.client_rect,
                    student=name,
                )
                if ready:
                    matches.append((job, item, name))
                    break

    # Never guess. Resume only when top-of-form identity gives one unique
    # queued student/job combination.
    unique = []
    seen = set()
    for job, item, name in matches:
        key = (str(job.get("id")), str(item.get("studentId") or ""), name)
        if key not in seen:
            seen.add(key)
            unique.append((job, item, name))

    if len(unique) != 1:
        return None

    job, item, name = unique[0]
    resumed = dict(job)
    resumed["_resumeStudentId"] = str(item.get("studentId") or "")
    resumed["_resumeStudentName"] = name
    resumed["_resumeMatchedDate"] = dates[0] if len(dates) == 1 else ""
    resumed["_resumeMatchedTime"] = times[0] if len(times) == 1 else ""
    return resumed



def _save_status(**fields):
    data = {"updatedAt": _now(), "pid": os.getpid(), "codeVersion": LOADED_VERSION}
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
    navigation_attempted = False

    while True:
        if abort_pressed():
            raise RuntimeError("STOP pressed (F10)")

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

        # New behavior learned from the short 2026-09-22 human demo:
        # if WOWKIDS is on Home/calendar (or the queued date popup), navigate
        # date -> class time -> View comments -> roster automatically.
        if not navigation_attempted:
            try:
                passive = WowkidsHomeNavigator(raise_window=False)
                snap, visible, state, _reasons, _signals = passive.live_snapshot(
                    "cloud_home_probe"
                )
                target_date = passive._popup_has_date(
                    visible,
                    dt.datetime.strptime(
                        str(job.get("target_date")), "%Y-%m-%d"
                    ).date(),
                )
                month = passive._calendar_month(visible)
                navigable = state == "HOME" or month is not None or target_date

                if navigable:
                    navigation_attempted = True
                    progress = _job_progress(
                        "navigating_to_class",
                        completed,
                        skipped,
                        errors,
                        target={
                            "date": job.get("target_date"),
                            "time": job.get("class_time"),
                            "name": job.get("wowkids_class_name"),
                        },
                    )
                    _send_progress(api, job_id, progress)
                    _save_status(
                        state="navigating_to_class",
                        jobId=job_id,
                        message="Opening {} {}".format(
                            job.get("target_date") or "",
                            job.get("class_time") or "",
                        ).strip(),
                    )

                    active = WowkidsHomeNavigator(raise_window=False)
                    active.navigate_to_roster(job)

                    nav = RosterNavigator(raise_window=False)
                    identity = _roster_identity(nav, label="after_home_navigation")
                    matched, reason = _matches_job(identity, job)
                    if not matched:
                        raise RuntimeError(
                            "navigation reached a roster, but it did not match "
                            "the queued class ({})".format(reason)
                        )

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
                        message="Home -> queued class -> roster verified",
                    )
                    return nav, identity

            except Exception as exc:
                if navigation_attempted:
                    # Once we have begun clicking the calendar, do not loop and
                    # make a second blind attempt. Fail closed with a useful
                    # error instead.
                    raise RuntimeError(
                        "automatic Home-to-roster navigation stopped safely: {}".format(
                            exc
                        )
                    )
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
    resume_hint = {
        "_resumeStudentId": job.get("_resumeStudentId"),
        "_resumeStudentName": job.get("_resumeStudentName"),
    }
    if job.get("status") == "queued":
        response = api.claim(job_id)
        job = response.get("job") or job
        # Claiming returns the server copy, so restore the local-only resume
        # hint discovered from the live assessment page.
        for key, value in resume_hint.items():
            if value:
                job[key] = value

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

    resume_student_id = str(job.get("_resumeStudentId") or "")
    resume_student_name = str(job.get("_resumeStudentName") or "").strip()

    if not resume_student_id:
        wait_for_matching_roster(api, job)

    known_categories = previous_progress.get("classCategories") or []
    class_categories = list(known_categories) if known_categories else None

    ordered_students = list(students)
    if resume_student_id:
        resume_items = [
            item for item in students
            if str(item.get("studentId") or "") == resume_student_id
        ]
        if resume_items:
            ordered_students = resume_items + [
                item for item in students
                if str(item.get("studentId") or "") != resume_student_id
            ]

    resume_consumed = False

    for index, item in enumerate(ordered_students, 1):
        if abort_pressed():
            raise RuntimeError("STOP pressed (F10)")

        student_id = str(item.get("studentId") or "")
        display_name = _job_student_name(item)
        if student_id in completed_ids:
            continue

        perf = StudentPerformance(
            job_id=job_id,
            student=display_name,
            class_name=job.get("class_name") or "",
            class_time=job.get("class_time") or "",
            target_date=job.get("target_date") or "",
        )
        perf.note("codeVersion", LOADED_VERSION)

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

        is_resume_current = (
            bool(resume_student_id)
            and not resume_consumed
            and student_id == resume_student_id
        )

        if is_resume_current:
            matched_name = resume_student_name or display_name
            resume_consumed = True
            perf.note("resumedFromOpenAssessment", True)
            _send_progress(
                api,
                job_id,
                _job_progress(
                    "resuming_current_student",
                    completed,
                    skipped,
                    errors,
                    currentStudent=display_name,
                    currentIndex=index,
                    totalStudents=len(ordered_students),
                ),
            )
        else:
            with perf.phase("roster_ready"):
                nav = RosterNavigator(raise_window=False)
                nav.wait_for_roster(timeout=12.0)
                matched, reason = _matches_job(_roster_identity(nav), job)
                if not matched:
                    # Do not rate a student if the user navigated away to another class.
                    nav, _identity = wait_for_matching_roster(api, job)

            with perf.phase("student_lookup"):
                matched_name, match = _find_job_student(nav, item)
            if not match:
                skipped_item = {
                    "studentId": student_id,
                    "name": display_name,
                    "reason": "not found on the matched WOWKIDS roster",
                }
                skipped.append(skipped_item)
                perf.finish("skipped_not_found")
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
                        totalStudents=len(ordered_students),
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
                perf.finish("skipped_posted")
                continue
            if status == STATUS_RATED:
                skipped.append(
                    {
                        "studentId": student_id,
                        "name": display_name,
                        "reason": "already Rated",
                    }
                )
                perf.finish("skipped_rated")
                continue
            if status != STATUS_NOT_RATING:
                skipped.append(
                    {
                        "studentId": student_id,
                        "name": display_name,
                        "reason": "roster status was not safely verified",
                    }
                )
                perf.finish("skipped_unknown_status")
                continue

            with perf.phase("open_student"):
                opened = nav.open_student(matched_name, match)
            if not opened.get("opened"):
                skipped.append(
                    {
                        "studentId": student_id,
                        "name": display_name,
                        "reason": opened.get("reason") or "student did not open",
                    }
                )
                perf.finish("skipped_not_opened")
                continue

        rater = HumanLikeRatingSession(raise_window=False)
        with perf.phase("assessment_load"):
            rater.wait_for_assessment_ready(student=matched_name)

        if class_categories is None:
            available_scores = _available_scores(item)
            with perf.phase("rating_actions"):
                discovered, results, _final = rater.discover_and_fill(
                    available_scores
                )
            class_categories = list(discovered)
            scores = {
                category: available_scores[category]
                for category in class_categories
            }
            perf.note("firstStudentDiscoveryIntegrated", True)
        else:
            scores = _scores_for_categories(item, class_categories)
            with perf.phase("rating_actions"):
                results, _final = rater.fill_discovered(
                    class_categories, scores
                )
        perf.note("categoryCount", len(class_categories or []))
        with perf.phase("submit"):
            submit = rater.submit_verified_student(
                class_categories, results
            )
        if not submit.get("accepted"):
            raise RuntimeError(
                "{}: Submit was clicked but success was not verified".format(
                    display_name
                )
            )

        with perf.phase("return_to_roster"):
            nav_after = RosterNavigator(raise_window=False)
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

        perf_summary = perf.finish("completed")
        completed_item = {
            "studentId": student_id,
            "name": display_name,
            "categories": list(class_categories),
            "scores": scores,
            "submitVerification": submit.get("verification"),
            "completedAt": _now(),
            "performance": perf_summary,
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
    """Choose a pending job from the page currently visible in WOWKIDS.

    Priority:
      1. Resume a job already in the middle of rating.
      2. If a roster is open, choose the job matching that roster.
      3. If Home/calendar or a date popup is open, choose the most recently
         queued/waiting job and navigate to it automatically.

    This prevents an old waiting class from blocking the class the coach just
    queued and opened WOWKIDS to handle.
    """
    jobs = list(response.get("jobs") or [])
    if not jobs and response.get("job"):
        jobs = [response["job"]]
    if not jobs:
        return None, "no pending jobs"

    resumed = _current_rating_resume_match(jobs)
    if resumed:
        return resumed, "resuming the student assessment already open"

    for job in jobs:
        if job.get("status") != "running":
            continue
        stage = (job.get("progress") or {}).get("stage")
        if stage not in (
            "waiting_for_class",
            "queued",
            "navigating_to_class",
            "class_matched",
        ):
            return job, "resuming active rating job"

    # First prefer an already-open exact roster.
    roster_was_open = False
    try:
        nav = RosterNavigator(raise_window=False)
        identity = _roster_identity(nav, label="cloud_pick_job")
        roster_was_open = bool(identity.get("verifiedRoster"))
        for job in jobs:
            matched, reason = _matches_job(identity, job)
            if matched:
                return job, reason
    except Exception:
        identity = None
        roster_was_open = False

    # Batch mode: after one class finishes the screen is still on that class's
    # roster. If no pending job belongs to this roster, safely use the bottom
    # Home tab, then let the existing Home -> date -> class navigator handle
    # the next queued class. This enables several queued classes to run in one
    # unattended session.
    if roster_was_open:
        candidates = [
            job for job in jobs
            if job.get("status") in ("queued", "running")
        ]
        if candidates:
            try:
                home = WowkidsHomeNavigator(raise_window=False)
                home.go_home_from_roster()
                running_waiting = [
                    job for job in candidates
                    if job.get("status") == "running"
                ]
                pool = running_waiting or candidates
                pool.sort(
                    key=lambda job: str(job.get("created_at") or ""),
                    reverse=True,
                )
                return pool[0], "completed roster left; opening next queued class"
            except Exception as exc:
                return None, "next class is queued but Home transition stopped safely: {}".format(exc)

    # If the user has only opened WOWKIDS Home, that is now enough. Use the
    # latest pending intent rather than allowing an old waiting job to block it.
    try:
        home = WowkidsHomeNavigator(raise_window=False)
        _snap, visible, state, _reasons, _signals = home.live_snapshot(
            "cloud_pick_home"
        )
        month = home._calendar_month(visible)
        any_popup_date = any(
            re.search(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日",
                      _normal_text(node.get("name")))
            for node in visible
        )
        if state == "HOME" or month is not None or any_popup_date:
            candidates = [
                job for job in jobs
                if job.get("status") in ("queued", "running")
            ]
            candidates.sort(
                key=lambda job: str(job.get("created_at") or ""),
                reverse=True,
            )
            if candidates:
                job = candidates[0]
                return job, "WOWKIDS Home detected; opening latest queued class"
    except Exception as exc:
        return None, "waiting for WOWKIDS Home or matching roster: {}".format(exc)

    return None, "WOWKIDS is open, but no pending class matches the current page"

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
                # Exit only between jobs. The existing watchdog relaunches us
                # with fresh imports; never interrupt a student mid-rating.
                if _source_version() != LOADED_VERSION:
                    _save_status(state='restarting', message='Loading updated agent code')
                    return 75
                response = api.poll()

                # Updates ride inside the same polling channel that already
                # works reliably on this PC.
                if _apply_agent_update(response.get("agentUpdate")):
                    _save_status(
                        state="updating",
                        message="Applying Windows agent update",
                    )
                    if _source_version() != LOADED_VERSION:
                        return 75

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
                        # Preserve progress already reported by successful
                        # students in this same batch. The older failure path
                        # reused the stale job object from before processing
                        # and could erase completed students when student 3
                        # failed.
                        base_progress = (job.get("progress") or {}).copy()
                        try:
                            fresh = api.poll()
                            fresh_jobs = list(fresh.get("jobs") or [])
                            if not fresh_jobs and fresh.get("job"):
                                fresh_jobs = [fresh["job"]]
                            current = next(
                                (
                                    item for item in fresh_jobs
                                    if item.get("id") == job.get("id")
                                ),
                                None,
                            )
                            if current:
                                base_progress = (
                                    current.get("progress") or base_progress
                                ).copy()
                        except Exception:
                            pass

                        progress = base_progress
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
