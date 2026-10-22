"""Command line: review one recording.

    uv run python -m reviewer.cli path/to/class.mp4              # results in runs/<video name>/
    uv run python -m reviewer.cli path/to/class.mp4 --force      # ignore all caches
    uv run python -m reviewer.cli path/to/class.mp4 --votes 1    # one Step B run instead of 3

Prints the feedback text; the full result is in runs/<video name>/report.json.
Exit code 2 means the file could not be reviewed (e.g. no audio, too short, not a video).
"""
import argparse
import json
import sys
from pathlib import Path

from .gemini_client import DEFAULT_MODEL
from .pipeline import configure_logging, review


def main() -> None:
    ap = argparse.ArgumentParser(description="Review a class recording.")
    ap.add_argument("video", type=Path)
    ap.add_argument("--runs", type=Path, default=Path("runs"), help="parent folder for run folders (default: runs)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model for both steps")
    ap.add_argument("--force", action="store_true", help="ignore all caches and re-run every step")
    ap.add_argument("--votes", type=int, default=3, help="Step B runs; majority per item, agreement = confidence")
    a = ap.parse_args()
    if not a.video.is_file():
        sys.exit(f"not a file: {a.video}")
    if a.votes < 1:
        sys.exit("--votes must be >= 1")
    configure_logging()

    # shortcut: one folder per file name, fine for one person on the command line. The app uses one folder per upload.
    report = review(a.video, a.runs / a.video.stem, a.model, a.force, a.votes)
    print(report.get("feedback_text") or json.dumps(report, indent=2, ensure_ascii=False))
    if report["category"] == "unreviewable":
        sys.exit(2)


if __name__ == "__main__":
    main()
