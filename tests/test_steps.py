"""Step A / Step B cache keys and the shared Gemini call (with a fake client, no network)."""
import copy
from types import SimpleNamespace

from conftest import make_obs

from reviewer import step_a_observe, step_b_judge
from reviewer.gemini_client import generate
from reviewer.pipeline import cache_key
from reviewer.schemas import Judgement
from reviewer.scoring import CONFIG


def test_step_b_cache_key_ignores_scoring_but_not_questions():
    obs, saved = make_obs(), copy.deepcopy(CONFIG)
    key = lambda seed=7: cache_key(*step_b_judge.cache_parts("m", seed, step_b_judge.build_prompt(obs)))
    base = key()
    try:
        CONFIG["bands"]["excellent"] = 3
        CONFIG["points"]["core_fail"] = 5
        CONFIG["items"][0]["good"] = "changed phrase"
        CONFIG["items"][0]["tier"] = "core"
        assert key() == base                     # scoring/feedback changes keep cached answers
        CONFIG["items"][0]["check"] = "changed question"
        assert key() != base                     # a changed question asks again
        assert key(seed=8) != key()
    finally:
        CONFIG.clear()
        CONFIG.update(saved)


def test_step_a_cache_key_tracks_its_settings():
    base = cache_key(*step_a_observe.cache_parts("m"))
    assert cache_key(*step_a_observe.cache_parts("other-model")) != base
    assert step_a_observe.MEDIA_RESOLUTION.value in step_a_observe.cache_parts("m")


def test_gemini_retry_changes_seed_after_bad_json():
    seeds, texts = [], iter(["{not json", '{"checklist": []}'])

    def fake_generate_content(model, contents, config):
        seeds.append(config.seed)
        return SimpleNamespace(text=next(texts), model_version="v",
                               usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1))

    c = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content))
    out, usage = generate(c, "m", ["p"], Judgement, seed=7)
    assert out.checklist == [] and seeds == [7, 1007] and usage["model_version"] == "v"


def test_step_a_always_deletes_upload_even_when_processing_fails():
    deleted = []
    failed = SimpleNamespace(name="files/1", state=SimpleNamespace(name="FAILED"))
    c = SimpleNamespace(files=SimpleNamespace(upload=lambda file: failed, delete=lambda name: deleted.append(name)))
    try:
        step_a_observe.observe(c, "m", __import__("pathlib").Path("x.mp4"))
    except RuntimeError as e:
        assert "could not process" in str(e)
    assert deleted == ["files/1"]
