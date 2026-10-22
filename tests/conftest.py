"""Shared test helpers. All tests run offline: no Gemini calls (ffprobe is only needed by test_pipeline.py)."""
from reviewer.schemas import Observations
from reviewer.scoring import CONFIG, decide, ground

SPECS = {s["id"]: s for s in CONFIG["items"]}
IDS = list(SPECS)


def items(**status):
    """One Step B answer per checklist item: all 'pass' with full evidence, except the ids given as status=..."""
    return [{"item_id": i, "status": status.get(i, "pass"), "evidence": f"{i} seen", "timestamps": ["01:00"],
             "quote": f"{i} said" if SPECS[i]["kind"] == "audio" else "", "reason": "r"} for i in IDS]


def score(duration=3600, **status):
    """Ground and decide a synthetic set of answers."""
    return decide(ground(items(**status), duration))


def make_obs(**overrides) -> Observations:
    """Minimal valid Step A observations; pass section dicts to override, e.g. closing={...}."""
    ev = {"present": False, "ts": None, "quote": ""}
    data = {
        "meta": {"languages_spoken": [], "tutor_identification": "", "layout_notes": ""},
        "visual": {"camera_off_spans": [], "face_visibility": [], "lighting": [],
                   "background": {"type": "unclear", "has_8x8_branding": False, "description": "",
                                  "glitch_spans": []},
                   "screen_share_spans": [], "media_played": [], "setup_warnings": []},
        "opening": {"greeting": ev, "rapport_building": {"present": False}, "went_straight_to_content": False,
                    "new_joiners": []},
        "engagement": {"individual_questions": [], "students_addressed_by_name": [], "praise_moments": []},
        "closing": {"conclusion": ev, "homework": ev},
        "key_quotes": [],
    }
    data.update(overrides)
    return Observations.model_validate(data)
