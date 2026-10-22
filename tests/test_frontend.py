"""app/frontend.py in standalone mode (no API_URL, as on Streamlit Cloud), rendered with Streamlit's test runner.
No backend, no Gemini: jobs are written straight into a temporary runs folder."""
import json
import os
import uuid
from pathlib import Path

import pytest
from conftest import items
from streamlit.testing.v1 import AppTest

from app import jobs
from reviewer.scoring import decide, ground, majority

FRONTEND = str(Path(__file__).resolve().parent.parent / "app" / "frontend.py")


@pytest.fixture(autouse=True)
def standalone(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "RUNS_DIR", tmp_path)
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    yield
    os.environ.pop("APP_PASSWORD", None)  # may have been copied in from test secrets


def finished_job() -> dict:
    g = ground(items(homework="fail"), 2700)
    merged, conf = majority([g, g, g])
    report = {"video": "c.mp4", "duration_s": 2700, **decide(merged), "confidence": conf, "votes": 3,
              "checklist": merged}
    job = {"id": uuid.uuid4().hex, "filename": "class one.mp4", "status": "done", "step": "done", "error": None,
           "category": report["category"], "created_at": "2026-01-01T00:00:00+00:00", "updated_at": ""}
    jobs.job_dir(job["id"]).mkdir(parents=True)
    jobs._write(job)
    (jobs.job_dir(job["id"]) / "report.json").write_text(json.dumps(report))
    return job


def run(**session) -> AppTest:
    at = AppTest.from_file(FRONTEND, default_timeout=30)
    for k, v in session.items():
        at.session_state[k] = v
    return at.run()


def test_finished_review_renders_reasons_timestamps_and_quotes():
    job = finished_job()
    at = run(job_id=job["id"])
    assert not at.exception
    assert [m.value for m in at.metric][0] == "🟢 Excellent"   # homework fail = 1 point
    needs = next(m.value for m in at.markdown if "Give homework" in m.value)
    assert "🕒 01:00" in needs and '💬 _"homework said"_' in needs
    assert [b.label for b in at.sidebar.button] == ["🟢 class one.mp4"]


def test_password_gate(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    at = run()
    assert not at.sidebar.button and at.text_input  # nothing but the password form
    at.text_input[0].input("wrong")
    at.button[0].click().run()
    assert at.error and at.error[0].value == "Wrong password"
    at.text_input[0].input("s3cret")
    at.button[0].click().run()
    assert at.session_state["authenticated"] and not at.exception


def test_password_can_come_from_streamlit_secrets():
    at = AppTest.from_file(FRONTEND, default_timeout=30)
    at.secrets["APP_PASSWORD"] = "from-secrets"
    at.run()
    assert at.text_input and not at.sidebar.button  # secret copied into the environment -> gate shown
