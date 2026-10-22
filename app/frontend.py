"""Streamlit frontend for the class reviewer.

Two ways to run it:
- with the backend (`uv run python -m app`): API_URL is set and every action goes through the HTTP API (app/api.py);
- on its own (`uv run streamlit run app/frontend.py`, and on Streamlit Community Cloud, which runs a single process):
  API_URL is not set, so reviews run inside this process using the same job code (app/jobs.py).

Settings come from environment variables or, on Streamlit Cloud, from the app's Secrets (copied into the environment
below): GEMINI_API_KEY (required for reviews), APP_PASSWORD (asks for a password before the app can be used),
GEMINI_MODEL, MAX_UPLOAD_MB, RUNS_DIR. Upload size limit for the browser: .streamlit/config.toml.
"""
import hmac
import json
import os
import sys
import time
from pathlib import Path

# Streamlit puts app/ on the import path, not the repo root; the reviewer and app packages live in the root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import streamlit as st  # noqa: E402

st.set_page_config(page_title="Class Reviewer", page_icon="🎓", layout="wide")


def _load_secrets_into_env() -> None:
    """Copy Streamlit secrets (Streamlit Cloud's settings) into the environment, where the reviewer reads its
    settings. Existing environment variables win. Must run before app.jobs is imported (it reads env at import)."""
    try:
        secrets = dict(st.secrets)
    except Exception:  # no secrets configured (normal when running locally)
        return
    for key, value in secrets.items():
        if isinstance(value, (str, int, float)) and key not in os.environ:
            os.environ[key] = str(value)


_load_secrets_into_env()

from app import jobs  # noqa: E402
from reviewer.pipeline import configure_logging  # noqa: E402
from reviewer.scoring import CONFIG  # noqa: E402

API_URL = os.environ.get("API_URL", "").rstrip("/")  # set: use the backend; empty: run reviews in this process
POLL_SECONDS = 3
VIDEO_TYPES = sorted(s.lstrip(".") for s in jobs.ALLOWED_SUFFIXES)
CATEGORY_STYLE = {"excellent": ("🟢", "Excellent"), "good": ("🔵", "Good"),
                  "average": ("🟠", "Average"), "poor": ("🔴", "Poor")}
SPECS = {s["id"]: s for s in CONFIG["items"]}  # checklist items: tier and feedback phrases


# ---------- access ----------

def require_password() -> None:
    """If APP_PASSWORD is set, ask for it once per browser session. Stops the page until it's right."""
    expected = os.environ.get("APP_PASSWORD")
    if not expected or st.session_state.get("authenticated"):
        return
    st.title("🎓 Class Reviewer")
    with st.form("login"):
        given = st.text_input("Password", type="password")
        if st.form_submit_button("Enter"):
            if hmac.compare_digest(given.encode(), expected.encode()):  # constant-time comparison
                st.session_state.authenticated = True
                st.rerun()
            st.error("Wrong password")
    st.stop()


# ---------- backend: HTTP API or in-process jobs, same five operations ----------

@st.cache_resource
def _start_local_worker() -> bool:
    """In-process mode, once per server process: set up logging and mark jobs cut off by a restart as failed."""
    configure_logging()
    jobs.mark_interrupted()
    return True


def _http(method: str, path: str, **kw):
    r = httpx.request(method, f"{API_URL}{path}", timeout=kw.pop("timeout", 30), **kw)
    if r.status_code >= 400:
        is_json = r.headers.get("content-type", "").startswith("application/json")
        raise jobs.ReviewError(r.status_code, r.json().get("detail", r.text) if is_json else r.text)
    return r.json() if r.content else None


def backend(operation: str, *args):
    """Run one operation: "list", "get", "create", "retry" or "delete". Shows errors and stops the page on failure."""
    try:
        if API_URL:
            if operation == "list":
                return _http("GET", "/reviews")
            if operation == "get":
                return _http("GET", f"/reviews/{args[0]}")
            if operation == "create":
                name, file = args
                return _http("POST", "/reviews", files={"file": (name, file, "application/octet-stream")},
                             timeout=600)
            if operation == "retry":
                return _http("POST", f"/reviews/{args[0]}/retry")
            if operation == "delete":
                return _http("DELETE", f"/reviews/{args[0]}")
        else:
            _start_local_worker()
            return {"list": jobs.list_jobs, "get": jobs.get_with_report, "create": jobs.create,
                    "retry": jobs.retry, "delete": jobs.delete}[operation](*args)
    except jobs.ReviewError as e:
        st.error(e.message)
    except httpx.HTTPError as e:
        st.error(f"Can't reach the backend at {API_URL} ({e.__class__.__name__}). Is it running?")
    st.stop()


# ---------- rendering ----------

def _point(item: dict, phrase: str) -> str:
    """One bullet: the item's fixed phrase from checklist.yaml ("good" or "improve"), the reason, when it happened,
    and the words spoken (speech items only)."""
    text = SPECS[item["item_id"]][phrase] or item["item_id"]
    line = f"- **{text}**: {item.get('reason', '')}"
    if item.get("timestamps"):
        line += f"  \n  🕒 {', '.join(item['timestamps'])}"
    if item.get("quote"):
        line += f"  \n  💬 _\"{item['quote']}\"_"
    return line


def show_report(job: dict) -> None:
    """Render a finished review."""
    report = job.get("report") or {}
    if job["status"] == "unreviewable":
        st.error("This recording can't be reviewed: " + "; ".join(report.get("problems", [])))
        return

    icon, label = CATEGORY_STYLE.get(report["category"], ("", report["category"]))
    cols = st.columns(3)
    cols[0].metric("Category", f"{icon} {label}")
    cols[1].metric("Issue points", report["points"], help="Core fail = 2, minor fail = 1, unclear = 0.5")
    cols[2].metric("Duration", f"{report.get('duration_s', 0) / 60:.0f} min")
    if report.get("borderline"):
        st.info(f"Borderline: one step from {report['borderline'].capitalize()}.")

    # Each point: "<fixed phrase>: <Step B's one-line reason>", then its timestamps and the words spoken.
    items = report["checklist"]
    hard_fails = [i for i in items if SPECS[i["item_id"]]["tier"] == "hard" and i["status"] == "fail"]
    if hard_fails:
        st.error("**Quality issue**\n\n" + "\n".join(_point(i, "improve") for i in hard_fails))
    for w in report.get("warnings", []):
        st.warning(w)

    good, needs = st.columns(2)
    with good:
        st.subheader("Good")
        st.markdown("\n".join(_point(i, "good") for i in items if i["status"] == "pass") or "—")
    with needs:
        st.subheader("Needs work")
        st.markdown("\n".join(_point(i, "improve") for i in items if i["status"] == "fail") or "Nothing flagged")

    st.download_button("Download report (JSON)", json.dumps(report, indent=2, ensure_ascii=False),
                       file_name=f"review_{job['id'][:8]}.json", mime="application/json")


def delete_button(job: dict) -> None:
    """Two-step delete (button opens a confirmation) for a finished review."""
    with st.popover("🗑️ Delete this review"):
        st.write("This permanently deletes the uploaded video and its review.")
        if st.button("Yes, delete it", type="primary", key=f"delete_{job['id']}"):
            backend("delete", job["id"])
            st.session_state.pop("job_id", None)
            st.toast(f"Deleted {job['filename']}")
            st.rerun()


# ---------- page ----------

require_password()

with st.sidebar:
    st.header("Past reviews")
    reviews = backend("list")
    for j in reviews:
        badge = CATEGORY_STYLE.get(j.get("category") or "", ("⏳" if j["status"] in ("queued", "running") else "⚠️",))[0]
        if st.button(f"{badge} {j['filename']}", key=j["id"], help=f"{j['status']} · {j['created_at']}",
                     width="stretch"):
            st.session_state.job_id = j["id"]
    if not os.environ.get("APP_PASSWORD"):
        st.caption("⚠️ No APP_PASSWORD set: anyone who can open this page can upload videos.")

st.title("🎓 Class Reviewer")
st.caption(f"Upload a class recording (up to {jobs.MAX_UPLOAD_BYTES // (1024 * 1024)} MB). It is checked, watched "
           "and judged against the checklist (about 5 minutes for a 1-hour class).")

# One review at a time (the job code enforces it too): block uploads while one is in progress.
active = next((j for j in reviews if j["status"] in ("queued", "running")), None)
if active:
    st.info(f"⏳ A review is in progress ({active['filename']}). You can upload the next recording when it finishes.")
uploaded = st.file_uploader("Class recording", type=VIDEO_TYPES, disabled=bool(active))
if uploaded and st.button("Review this recording", type="primary", disabled=bool(active)):
    with st.spinner("Saving upload…"):
        job = backend("create", uploaded.name, uploaded)  # streamed to disk in chunks, never copied whole
    st.session_state.job_id = job["id"]

job_id = st.session_state.get("job_id")
if job_id:
    job = backend("get", job_id)
    st.divider()
    st.subheader(job["filename"])
    if job["status"] in ("queued", "running"):
        st.info(f"⏳ {job['status'].capitalize()}: {job['step']}")
        time.sleep(POLL_SECONDS)
        st.rerun()  # poll until the job finishes
    elif job["status"] == "failed":
        st.error(f"Review failed: {job['error']}")
        if st.button("Retry (reuses finished steps)"):
            backend("retry", job_id)
            st.rerun()
        delete_button(job)
    else:
        show_report(job)
        delete_button(job)
