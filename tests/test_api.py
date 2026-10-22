"""app/api.py + app/jobs.py with a fake pipeline: uploads, validation, background jobs, retry. No Gemini, no ffmpeg."""
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import api, jobs
from reviewer.pipeline import REPORT


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "RUNS_DIR", tmp_path)
    calls = {"fail_next": False}

    def fake_review(video, run_dir, on_step=None, **_):
        on_step("precheck")
        if calls["fail_next"]:
            calls["fail_next"] = False
            raise RuntimeError("Gemini failed twice")
        report = {"video": video.name, "category": "good", "points": 2, "feedback_text": "Category: Good"}
        (run_dir / REPORT).write_text(json.dumps(report))
        return report

    monkeypatch.setattr(jobs, "review", fake_review)
    with TestClient(api.app) as c:
        c.calls = calls
        yield c


def wait_finished(c, job_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"/reviews/{job_id}").json()
        if job["status"] in jobs.FINISHED:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def queued_job(filename="x.mp4") -> dict:
    """A job stuck in "queued" (never handed to the worker), to simulate a review in progress."""
    job = {"id": uuid.uuid4().hex, "filename": filename, "status": "queued", "step": "waiting in queue",
           "error": None, "category": None, "created_at": "2026-01-01T00:00:00+00:00", "updated_at": ""}
    jobs.job_dir(job["id"]).mkdir(parents=True)
    jobs._write(job)
    return job


def upload(c, name="class.mp4", data=b"fake video bytes"):
    return c.post("/reviews", files={"file": (name, data, "video/mp4")})


def test_upload_runs_in_background_and_returns_report(client):
    r = upload(client)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job = wait_finished(client, r.json()["id"])
    assert job["status"] == "done" and job["category"] == "good" and job["report"]["points"] == 2
    assert job["filename"] == "class.mp4"
    assert [j["id"] for j in client.get("/reviews").json()] == [job["id"]]


def test_same_file_name_gets_separate_folders(client):
    a = wait_finished(client, upload(client).json()["id"])["id"]   # one review at a time: let the first finish
    b = upload(client).json()["id"]
    assert a != b and (jobs.RUNS_DIR / a).is_dir() and (jobs.RUNS_DIR / b).is_dir()


def test_rejects_wrong_type_and_empty_file(client):
    assert upload(client, name="notes.pdf").status_code == 415
    assert upload(client, data=b"").status_code == 400


def test_unknown_or_unsafe_ids_are_404(client):
    assert client.get("/reviews/" + "0" * 32).status_code == 404
    assert client.get("/reviews/..%2F..%2Fetc").status_code == 404


def test_failed_job_shows_error_and_can_be_retried(client):
    client.calls["fail_next"] = True
    job = wait_finished(client, upload(client).json()["id"])
    assert job["status"] == "failed" and "Gemini failed twice" in job["error"]
    r = client.post(f"/reviews/{job['id']}/retry")
    assert r.status_code == 202
    assert wait_finished(client, job["id"])["status"] == "done"
    assert client.post(f"/reviews/{job['id']}/retry").status_code == 409   # done jobs can't be retried


def test_unfinished_jobs_are_marked_failed_on_restart(client):
    job = queued_job()
    assert jobs.mark_interrupted() == 1
    assert jobs.get(job["id"])["status"] == "failed"


def test_delete_removes_video_and_results(client):
    job = wait_finished(client, upload(client).json()["id"])
    folder = jobs.RUNS_DIR / job["id"]
    assert (folder / "source.mp4").exists()
    assert client.delete(f"/reviews/{job['id']}").status_code == 204
    assert not folder.exists()
    assert client.get(f"/reviews/{job['id']}").status_code == 404
    assert client.get("/reviews").json() == []


def test_cannot_delete_unfinished_or_unknown_review(client):
    job = queued_job()
    assert client.delete(f"/reviews/{job['id']}").status_code == 409
    assert (jobs.RUNS_DIR / job["id"]).exists()
    assert client.delete("/reviews/" + "f" * 32).status_code == 404


def test_only_one_review_at_a_time(client):
    job = queued_job("first.mp4")
    r = upload(client, name="second.mp4")
    assert r.status_code == 409 and "first.mp4" in r.json()["detail"]
    jobs.update(job["id"], status="done")
    assert upload(client, name="second.mp4").status_code == 202
