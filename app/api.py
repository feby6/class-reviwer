"""HTTP API for class reviews (FastAPI). A thin layer over app/jobs.py, which holds the rules.

    uv run uvicorn app.api:app --port 8000          # docs at http://localhost:8000/docs
    (or `uv run python -m app` to start it together with the frontend)

Endpoints:
    POST   /reviews              upload a recording (multipart field "file"); returns the job, review runs in background.
                                 Only one review at a time: 409 while another is uploading, queued or running.
    GET    /reviews              all jobs, newest first
    GET    /reviews/{id}         one job's status and current step, plus the report once finished
    POST   /reviews/{id}/retry   re-run a failed job in its own folder (cached steps are reused, so it is cheap)
    DELETE /reviews/{id}         delete a finished job: its uploaded video, cached results and report
    GET    /health               liveness check

The Gemini key stays on the server (GEMINI_API_KEY). There is no authentication: run it on a trusted network or put
it behind a proxy that adds auth.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse

from reviewer.pipeline import configure_logging

from . import jobs

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    if n := jobs.mark_interrupted():
        log.warning("marked %d unfinished job(s) from a previous run as failed", n)
    yield


app = FastAPI(title="Class Reviewer", lifespan=lifespan)


@app.exception_handler(jobs.ReviewError)
def review_error(_: Request, e: jobs.ReviewError) -> JSONResponse:
    return JSONResponse({"detail": e.message}, status_code=e.status)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/reviews", status_code=202)
def create_review(file: UploadFile) -> dict:
    # sync endpoint: FastAPI runs it in a worker thread, so copying the (spooled) upload to disk doesn't block others
    return jobs.create(file.filename, file.file)


@app.get("/reviews")
def list_reviews() -> list[dict]:
    return jobs.list_jobs()


@app.get("/reviews/{job_id}")
def get_review(job_id: str) -> dict:
    return jobs.get_with_report(job_id)


@app.post("/reviews/{job_id}/retry", status_code=202)
def retry_review(job_id: str) -> dict:
    return jobs.retry(job_id)


@app.delete("/reviews/{job_id}", status_code=204)
def delete_review(job_id: str) -> None:
    jobs.delete(job_id)
