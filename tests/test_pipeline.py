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


def test_cached_run_needs_no_api(tmp_path, monkeypatch):
    video = tmp_path / "class.mp4"
    video.write_bytes(b"x")
    run = tmp_path / "run"
    run.mkdir()
    st = video.stat()
    (run / pipeline.PRECHECK).write_text(json.dumps({
        "duration_s": 3600, "has_video": True, "has_audio": True, "black_s": 0, "silence_s": 0, "problems": [],
        "source": {"size": st.st_size, "mtime_ns": st.st_mtime_ns}}))
    obs = make_obs()
    (run / pipeline.OBSERVATIONS).write_text(obs.model_dump_json())
    (run / pipeline.OBSERVATIONS_KEY).write_text(cache_key(*step_a_observe.cache_parts(DEFAULT_MODEL)))
    prompt = step_b_judge.build_prompt(obs)
    (run / pipeline.STEP_B_CACHE).mkdir()
    for k in range(3):
        name = cache_key(*step_b_judge.cache_parts(DEFAULT_MODEL, SEED + k, prompt))
        (run / pipeline.STEP_B_CACHE / f"{name}.json").write_text(json.dumps({"checklist": items(tone="fail")}))

    def no_client():
        raise AssertionError("a cached run must not create a Gemini client")
    monkeypatch.setattr(pipeline, "client", no_client)

    r = review(video, run)
    assert r["category"] == "good" and r["points"] == 2 and [i["item_id"] for i in r["checklist"]] == IDS
    assert json.loads((run / pipeline.USAGE).read_text())["judge_2"] == {"cached": True}
