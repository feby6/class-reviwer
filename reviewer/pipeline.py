"""The full review of one class recording: `review(video, run_dir)`.

Steps, in order (see the README for the picture):
  1. precheck   free ffprobe/ffmpeg checks; an unreviewable file stops here at no cost
  2. shrink     re-encode to a small upload (480p, 2 fps, mono)
  3. Step A     Gemini watches the video -> timestamped facts            (step_a_observe.py)
  4. Step B     Gemini answers each checklist question, 3 votes          (step_b_judge.py)
  5. scoring    code grounds the answers, takes the majority, decides    (scoring.py)
  6. report     report.json + usage.json in the run folder

Every result is cached in the run folder, so re-running only pays for what changed:

  run_dir/
    precheck.json              reused while the source file's size + modified time are unchanged
    video_small.mp4            the shrunk upload, reused with the precheck
    step_a_observations.json   reused while step_a_observations.key matches (prompt, schema, model, fps, resolution)
    step_a_observations.key
    step_b_cache/<key>.json    one Step B answer per (model, seed, prompt, answer schema, temperature)
    report.json                the final result (always rewritten)
    usage.json                 tokens per call, or "cached"

Changing points, bands or feedback phrases in checklist.yaml never costs an API call. Each upload must get its own
run_dir (the app uses one folder per upload), otherwise two videos would overwrite each other's results.
"""
import hashlib
import json
import logging
import shutil
from pathlib import Path

from pydantic import ValidationError

from . import media, step_a_observe, step_b_judge
from .gemini_client import DEFAULT_MODEL, SEED, client
from .schemas import Judgement, Observations
from .scoring import decide, ground, majority

log = logging.getLogger(__name__)

PRECHECK = "precheck.json"
SMALL_VIDEO = "video_small.mp4"
OBSERVATIONS = "step_a_observations.json"
OBSERVATIONS_KEY = "step_a_observations.key"
STEP_B_CACHE = "step_b_cache"
REPORT = "report.json"
USAGE = "usage.json"


def configure_logging(level: int = logging.INFO) -> None:
    """Show only this app's steps (precheck, upload, watching, votes, done). The HTTP and Gemini SDK libraries
    log every request and status poll at INFO, so they are limited to warnings and errors."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cache_key(*parts) -> str:
    """Short stable hash of everything that affects a cached result."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _precheck(video: Path, run_dir: Path, force: bool) -> dict:
    """Run (or reuse) the free precheck. If the source video changed, drop everything derived from the old one."""
    st = video.stat()
    source = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    check = None if force else _read_json(run_dir / PRECHECK)
    if check and check.get("source") == source:
        return check
    if check is not None:
        log.info("source video changed: clearing cached results")
        for name in (SMALL_VIDEO, OBSERVATIONS, OBSERVATIONS_KEY):
            (run_dir / name).unlink(missing_ok=True)
        shutil.rmtree(run_dir / STEP_B_CACHE, ignore_errors=True)
    log.info("precheck: %s", video.name)
    check = {**media.precheck(video), "source": source}
    _write_json(run_dir / PRECHECK, check)
    return check


def _observations(video: Path, run_dir: Path, model: str, force: bool, get_client, usage: dict) -> Observations:
    """Step A, or its cached result when the cache key still matches."""
    key = cache_key(*step_a_observe.cache_parts(model))
    obs_file, key_file = run_dir / OBSERVATIONS, run_dir / OBSERVATIONS_KEY
    if not force and obs_file.exists() and key_file.exists() and key_file.read_text().strip() == key:
        try:
            usage["observe"] = {"cached": True}
            return Observations.model_validate_json(obs_file.read_text())
        except ValidationError:
            pass  # unreadable cache: redo Step A
    small = media.shrink(video, run_dir / SMALL_VIDEO)
    obs, usage["observe"] = step_a_observe.observe(get_client(), model, small)
    obs_file.write_text(obs.model_dump_json(indent=2))
    key_file.write_text(key)  # written last, so a crash never leaves a key pointing at missing observations
    return obs


def _judgements(obs: Observations, run_dir: Path, model: str, votes: int, force: bool, get_client,
                usage: dict) -> list[Judgement]:
    """Step B, `votes` times with seeds SEED, SEED+1, ...; each answer is cached separately."""
    prompt = step_b_judge.build_prompt(obs)
    cache_dir = run_dir / STEP_B_CACHE
    cache_dir.mkdir(exist_ok=True)
    runs = []
    for k in range(votes):
        seed = SEED + k
        cache_file = cache_dir / f"{cache_key(*step_b_judge.cache_parts(model, seed, prompt))}.json"
        cached = None if force else _read_json(cache_file)
        if cached is not None:
            try:
                runs.append(Judgement.model_validate(cached))
                usage[f"judge_{k}"] = {"cached": True}
                continue
            except ValidationError:
                pass  # saved with an older answer format: ask again
        log.info("Step B: vote %d of %d", k + 1, votes)
        j, usage[f"judge_{k}"] = step_b_judge.judge(get_client(), model, prompt, seed=seed)
        _write_json(cache_file, j.model_dump())
        runs.append(j)
    return runs


def review(video: Path, run_dir: Path, model: str = DEFAULT_MODEL, force: bool = False, votes: int = 3) -> dict:
    """Review one recording. Results go to `run_dir` (created if missing). Returns the report dict.
    `force=True` ignores every cache. An unreviewable file returns {"category": "unreviewable", "problems": [...]}."""
    run_dir.mkdir(parents=True, exist_ok=True)
    check = _precheck(video, run_dir, force)
    if check["problems"]:
        report = {"video": video.name, "category": "unreviewable", "problems": check["problems"]}
        _write_json(run_dir / REPORT, report)
        return report

    gemini = None

    def get_client():  # created only when a step isn't cached, so cached runs need no API key
        nonlocal gemini
        gemini = gemini or client()
        return gemini

    usage: dict = {}
    obs = _observations(video, run_dir, model, force, get_client, usage)
    runs = _judgements(obs, run_dir, model, votes, force, get_client, usage)

    grounded = [ground([i.model_dump() for i in j.checklist], check["duration_s"]) for j in runs]
    items, confidence = majority(grounded)
    report = {
        "video": video.name,
        "duration_s": check["duration_s"],
        **decide(items),
        "confidence": confidence,
        "votes": votes,
        "checklist": items,
        "model": model,
    }
    _write_json(run_dir / REPORT, report)
    _write_json(run_dir / USAGE, usage)
    log.info("done: %s -> %s", video.name, report["category"])
    return report
