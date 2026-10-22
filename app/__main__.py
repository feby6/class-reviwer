"""Start the whole app with one command:

    uv run python -m app            # frontend: http://localhost:8501   backend/API: http://localhost:8000

Runs the backend (uvicorn, app/api.py) and the frontend (Streamlit, app/frontend.py) as two child processes:
the backend starts first and must answer /health before the frontend starts. If either process stops, the other is
stopped too; Ctrl+C (or SIGTERM) stops both. Ports: API_PORT (default 8000) and UI_PORT (default 8501).
Both listen on 127.0.0.1 only: there is no login, so the app is not exposed to the network.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

API_PORT = int(os.environ.get("API_PORT", "8000"))
UI_PORT = int(os.environ.get("UI_PORT", "8501"))
STARTUP_TIMEOUT_S = 30
ROOT = Path(__file__).resolve().parent.parent


def _wait_for_backend(proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"backend exited during startup (code {proc.returncode}); is port {API_PORT} already in use?")
        try:
            if httpx.get(f"http://127.0.0.1:{API_PORT}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    proc.terminate()
    sys.exit(f"backend did not answer on port {API_PORT} within {STARTUP_TIMEOUT_S}s")


def _stop(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


class _Shutdown(Exception):
    pass


def _on_signal(*_):
    raise _Shutdown


def main() -> None:
    # Handle Ctrl+C and SIGTERM explicitly: a launcher started in the background inherits "ignore SIGINT"
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _on_signal)
    if not os.environ.get("GEMINI_API_KEY"):
        print("warning: GEMINI_API_KEY is not set; uploads will fail until it is", file=sys.stderr)

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(API_PORT)], cwd=ROOT)
    procs = [backend]
    try:
        _wait_for_backend(backend)
        frontend = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app/frontend.py", "--server.port", str(UI_PORT),
             "--server.address", "127.0.0.1", "--server.headless", "true"],
            cwd=ROOT, env={**os.environ, "API_URL": f"http://127.0.0.1:{API_PORT}"})
        procs.append(frontend)
        print(f"\n  Class Reviewer running:  http://localhost:{UI_PORT}   (API: http://localhost:{API_PORT}/docs)\n"
              "  Press Ctrl+C to stop.\n", flush=True)
        while all(p.poll() is None for p in procs):  # supervise: if one stops, stop the other
            time.sleep(1)
        dead = next(p for p in procs if p.poll() is not None)
        print(f"{'backend' if dead is backend else 'frontend'} stopped (code {dead.returncode}); shutting down",
              file=sys.stderr)
    except _Shutdown:
        print("stopping…", file=sys.stderr)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):  # a second Ctrl+C must not interrupt the cleanup
            signal.signal(sig, signal.SIG_IGN)
        _stop(procs)


if __name__ == "__main__":
    main()
