"""Review jobs: one folder per upload, a small status file, a background worker, and the rules for uploads.

Both front doors use this module: the HTTP API (app/api.py) and the Streamlit frontend when it runs on its own
(e.g. Streamlit Community Cloud, which can only run one process). Keeping the rules here means they are the same in
both modes.

Each upload gets its own run folder `RUNS_DIR/<job id>/` holding the uploaded video (`source.<ext>`), the pipeline's
cached results and report.json (see reviewer/pipeline.py), and `job.json`:

    {"id", "filename", "status", "step", "error", "category", "created_at", "updated_at"}

status: queued -> running -> done | unreviewable | failed

Only one review is in progress at a time: a new upload is refused while another is uploading, queued or running
(Gemini credits and rate limits are the bottleneck, not the server). Status files are written atomically (temp file
+ rename), so readers never see a half-written file while a job updates it.
"""
import json
import logging
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from reviewer.pipeline import REPORT, review

log = logging.getLogger(__name__)

RUNS_DIR = Path(os.environ.get("RUNS_DIR", "runs"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "1000")) * 1024 * 1024
ALLOWED_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
JOB_FILE = "job.json"
FINISHED = {"done", "unreviewable", "failed"}
CHUNK = 1024 * 1024  # copy uploads to disk 1 MB at a time

_executor = ThreadPoolExecutor(max_workers=1)  # one review at a time (see create)
_lock = threading.Lock()  # serialises read-modify-write of job.json files and the one-at-a-time check


class ReviewError(Exception):
    """A request that can't be done, with an HTTP-style status code (the API returns it as is)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- status files ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_dir(job_id: str) -> Path:
    return RUNS_DIR / job_id


def _write(job: dict) -> None:
    path = job_dir(job["id"]) / JOB_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2))
    tmp.replace(path)  # atomic on the same filesystem


def get(job_id: str) -> dict | None:
    """The job's status, or None if it doesn't exist or the id isn't a valid job id."""
    if not is_valid_id(job_id):
        return None
    try:
        return json.loads((job_dir(job_id) / JOB_FILE).read_text())
    except (OSError, ValueError):
        return None


def update(job_id: str, **fields) -> dict:
    with _lock:
        job = get(job_id) or {}
        job.update(fields, updated_at=_now())
        _write(job)
        return job


def report(job_id: str) -> dict | None:
    try:
        return json.loads((job_dir(job_id) / REPORT).read_text())
    except (OSError, ValueError):
        return None


def list_jobs() -> list[dict]:
    """All jobs, newest first."""
    if not RUNS_DIR.exists():
        return []
    jobs = [j for d in RUNS_DIR.iterdir() if d.is_dir() and (j := get(d.name))]
    return sorted(jobs, key=lambda j: j.get("created_at", ""), reverse=True)


def is_valid_id(job_id: str) -> bool:
    """Job ids are 32 hex characters; checking this keeps user input from naming paths outside RUNS_DIR."""
    return len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id)


def active_job() -> dict | None:
    """The review currently in progress (queued or running), if any."""
    return next((j for j in list_jobs() if j["status"] not in FINISHED), None)


def source_video(job_id: str) -> Path | None:
    """The uploaded video in a job's folder (saved as source.<ext>)."""
    return next(job_dir(job_id).glob("source.*"), None)


# ---------- worker ----------

def _run(job_id: str, video: Path) -> None:
    """Worker body: run the pipeline and record the outcome. Never raises (errors are stored in the job)."""
    update(job_id, status="running", step="starting")
    try:
        result = review(video, job_dir(job_id), on_step=lambda step: update(job_id, step=step))
    except Exception as e:  # anything from ffmpeg, Gemini or our code: keep the server alive, show the reason
        log.exception("job %s failed", job_id)
        update(job_id, status="failed", step="failed", error=f"{type(e).__name__}: {e}")
        return
    status = "unreviewable" if result["category"] == "unreviewable" else "done"
    update(job_id, status=status, step=status, category=result["category"],
           error="; ".join(result.get("problems", [])) or None)


def mark_interrupted() -> int:
    """At startup: jobs left queued/running by a previous process will never finish, so mark them failed.
    Retrying such a job is cheap because its folder's caches are kept. Returns how many were marked."""
    count = 0
    for job in list_jobs():
        if job["status"] not in FINISHED:
            update(job["id"], status="failed", step="failed", error="interrupted by a restart; retry it")
            count += 1
    return count


# ---------- operations used by the API and the frontend ----------

def create(filename: str, stream: BinaryIO) -> dict:
    """Validate an upload, save it into a new run folder, and queue its review. Returns the job.
    Raises ReviewError: 415 wrong type, 409 another review in progress, 413 too large, 400 empty.
    The user's file name is only stored for display; it is never used to build a path."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ReviewError(415, f"unsupported file type {suffix or '(none)'}; use one of {sorted(ALLOWED_SUFFIXES)}")
    with _lock:  # the one-at-a-time check and the folder creation must happen together
        if (current := active_job()) is not None:
            raise ReviewError(409, f"another review is still in progress ({current['filename']}); "
                                   "upload again when it has finished")
        job_id = uuid.uuid4().hex
        job_dir(job_id).mkdir(parents=True)
        job = {"id": job_id, "filename": filename, "status": "queued", "step": "uploading", "error": None,
               "category": None, "created_at": _now(), "updated_at": _now()}
        _write(job)

    dest = job_dir(job_id) / f"source{suffix}"
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ReviewError(413, f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                out.write(chunk)
        if size == 0:
            raise ReviewError(400, "empty file")
    except Exception as e:  # rejected or broken upload: keep a failed record, free the slot for the next upload
        dest.unlink(missing_ok=True)
        update(job_id, status="failed", step="failed",
               error=e.message if isinstance(e, ReviewError) else f"upload failed: {e}")
        raise
    job = update(job_id, step="waiting in queue")
    _executor.submit(_run, job_id, dest)
    log.info("queued review %s for %s (%.1f MB)", job_id, filename, size / 1e6)
    return job


def get_with_report(job_id: str) -> dict:
    """The job plus its report once finished. Raises ReviewError 404 if unknown."""
    job = get(job_id)
    if job is None:
        raise ReviewError(404, "review not found")
    return {**job, "report": report(job_id) if job["status"] in ("done", "unreviewable") else None}


def retry(job_id: str) -> dict:
    """Re-run a failed job in its own folder (finished steps are reused from the cache)."""
    job = get(job_id)
    if job is None:
        raise ReviewError(404, "review not found")
    if job["status"] != "failed":
        raise ReviewError(409, f"only failed reviews can be retried (this one is {job['status']})")
    video = source_video(job_id)
    if video is None:
        raise ReviewError(409, "the uploaded video is no longer available; upload it again")
    with _lock:
        if (current := active_job()) is not None:
            raise ReviewError(409, f"another review is still in progress ({current['filename']})")
        job = {**job, "status": "queued", "step": "waiting in queue", "error": None, "updated_at": _now()}
        _write(job)
    _executor.submit(_run, job_id, video)
    return job


def delete(job_id: str) -> None:
    """Delete a finished job's folder: the uploaded video, cached results and the report."""
    job = get(job_id)
    if job is None:
        raise ReviewError(404, "review not found")
    if job["status"] not in FINISHED:  # the worker is still writing to the folder
        raise ReviewError(409, f"this review is still {job['status']}; delete it once it has finished")
    with _lock:
        shutil.rmtree(job_dir(job_id), ignore_errors=True)
    log.info("deleted review %s (%s)", job_id, job["filename"])
