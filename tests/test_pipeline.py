"""pipeline.review(): unreadable uploads, and a fully cached run that needs no API key or network."""
import json
import shutil

import pytest
from conftest import IDS, items, make_obs

from reviewer import pipeline, step_a_observe, step_b_judge
from reviewer.gemini_client import DEFAULT_MODEL, SEED
from reviewer.pipeline import cache_key, review

needs_ffprobe = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")


@needs_ffprobe
def test_unreadable_upload_is_unreviewable_not_a_crash(tmp_path):
    fake = tmp_path / "notes.mp4"
    fake.write_text("this is not a video")
    r = review(fake, tmp_path / "run")
    assert r["category"] == "unreviewable" and r["problems"] == ["not a readable video file"]


def _cached_run(tmp_path, monkeypatch, obs, step_b_answers=None):
    """A run folder whose precheck and Step A are already cached (and Step B too, if answers are given).
    Creating a Gemini client fails the test, proving no API call is made."""
    video = tmp_path / "class.mp4"
    video.write_bytes(b"x")
    run = tmp_path / "run"
    run.mkdir()
    st = video.stat()
    (run / pipeline.PRECHECK).write_text(json.dumps({
        "duration_s": 3600, "has_video": True, "has_audio": True, "black_s": 0, "silence_s": 0, "problems": [],
        "source": {"size": st.st_size, "mtime_ns": st.st_mtime_ns}}))
    (run / pipeline.OBSERVATIONS).write_text(obs.model_dump_json())
    (run / pipeline.OBSERVATIONS_KEY).write_text(cache_key(*step_a_observe.cache_parts(DEFAULT_MODEL)))
    if step_b_answers is not None:
        prompt = step_b_judge.build_prompt(obs)
        (run / pipeline.STEP_B_CACHE).mkdir()
        for k in range(3):
            name = cache_key(*step_b_judge.cache_parts(DEFAULT_MODEL, SEED + k, prompt))
            (run / pipeline.STEP_B_CACHE / f"{name}.json").write_text(json.dumps({"checklist": step_b_answers}))

    def no_client():
        raise AssertionError("this run must not create a Gemini client")
    monkeypatch.setattr(pipeline, "client", no_client)
    return video, run


def test_cached_run_needs_no_api(tmp_path, monkeypatch):
    video, run = _cached_run(tmp_path, monkeypatch, make_obs(), items(tone="fail"))
    r = review(video, run)
    assert r["category"] == "good" and r["points"] == 2 and [i["item_id"] for i in r["checklist"]] == IDS
    assert json.loads((run / pipeline.USAGE).read_text())["judge_2"] == {"cached": True}


def test_non_class_video_is_unreviewable_and_skips_step_b(tmp_path, monkeypatch):
    obs = make_obs(meta={"is_online_class": False, "not_class_reason": "a cooking vlog with one presenter"})
    video, run = _cached_run(tmp_path, monkeypatch, obs)   # no Step B cache: a Step B call would fail the test
    r = review(video, run)
    assert r["category"] == "unreviewable"
    assert r["problems"] == ["not an online class recording: a cooking vlog with one presenter"]
    assert not (run / pipeline.STEP_B_CACHE).exists()
