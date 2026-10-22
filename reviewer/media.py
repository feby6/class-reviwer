"""Local video handling with ffmpeg/ffprobe, before any paid API call.

- `precheck()`: can this file be reviewed at all? (readable, has video and audio, long enough, not mostly black or
  silent). A file that fails costs nothing: the pipeline stops and reports "unreviewable".
- `shrink()`: re-encode to a small file for upload (480p, 2 fps, mono audio).

Requires the ffmpeg and ffprobe binaries on PATH (`brew install ffmpeg`).
"""
import json
import re
import subprocess
from pathlib import Path

MIN_DURATION_S = 5 * 60   # shorter than this is not a full class
MAX_BLACK_RATIO = 0.8     # more than 80% black screen -> nothing to review
MAX_SILENCE_RATIO = 0.8   # more than 80% silence -> nothing to review


class UnreadableVideo(Exception):
    """ffprobe/ffmpeg could not read the file (not a video, or corrupted)."""


def probe(path: Path) -> dict:
    """Duration and which streams exist. Raises UnreadableVideo if ffprobe can't read the file."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout
        data = json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        raise UnreadableVideo(str(e)) from e
    stream_types = {s["codec_type"] for s in data.get("streams", [])}
    return {
        "duration_s": float(data.get("format", {}).get("duration", 0) or 0),
        "has_video": "video" in stream_types,
        "has_audio": "audio" in stream_types,
    }


def parse_detect(stderr: str) -> tuple[float, float]:
    """Total black and silent seconds from ffmpeg's blackdetect / silencedetect log lines."""
    black = sum(float(x) for x in re.findall(r"black_duration:\s*([\d.]+)", stderr))
    silence = sum(float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", stderr))
    return black, silence


def detect_black_and_silence(path: Path) -> tuple[float, float]:
    """(black seconds, silent seconds). Looks at 1 frame every 10 s, which keeps a 90-minute file to about a minute."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-vf", "fps=1/10,blackdetect=d=0:pix_th=0.10", "-af", "silencedetect=n=-50dB:d=5",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise UnreadableVideo(r.stderr[-500:])
    return parse_detect(r.stderr)


def verdict(info: dict, black_s: float, silence_s: float) -> list[str]:
    """Reasons the file can't be reviewed. An empty list means it can."""
    problems = []
    if not info["has_video"]:
        problems.append("no video stream")
    if not info["has_audio"]:
        problems.append("no audio stream")
    d = info["duration_s"]
    if d < MIN_DURATION_S:
        problems.append(f"too short ({d:.0f}s)")
    elif black_s / d > MAX_BLACK_RATIO:
        problems.append(f"mostly black video ({black_s / d:.0%})")
    elif silence_s / d > MAX_SILENCE_RATIO:
        problems.append(f"mostly silent audio ({silence_s / d:.0%})")
    return problems


def precheck(path: Path) -> dict:
    """Free check of the uploaded file. `problems` is empty when the video can be reviewed."""
    try:
        info = probe(path)
        black_s, silence_s = (detect_black_and_silence(path) if info["has_video"] and info["has_audio"]
                              else (0.0, 0.0))
    except UnreadableVideo:
        return {"duration_s": 0.0, "has_video": False, "has_audio": False, "black_s": 0.0, "silence_s": 0.0,
                "problems": ["not a readable video file"]}
    return {**info, "black_s": black_s, "silence_s": silence_s, "problems": verdict(info, black_s, silence_s)}


def shrink(src: Path, dst: Path) -> Path:
    """Re-encode to 480p, 2 fps, mono audio (a 200 MB recording becomes ~35 MB). Gemini only samples 1 frame every
    20 s, so nothing it uses is lost. Writes to a temp file first so a failed encode never leaves a half file at dst."""
    if dst.exists():
        return dst
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
             "-vf", "scale=-2:480", "-r", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
             "-ac", "1", "-c:a", "aac", "-b:a", "48k", str(tmp)],
            check=True,
        )
    except subprocess.CalledProcessError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.rename(dst)
    return dst
