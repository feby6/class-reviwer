"""scoring.py: grounding, majority vote, category rules and the feedback template."""
from conftest import IDS, SPECS, items, score

from reviewer.scoring import CONFIG, ground, majority, ts_seconds


def test_config_is_well_formed():
    assert {s["tier"] for s in CONFIG["items"]} <= {"hard", "core", "minor", "bonus"}
    assert {s["kind"] for s in CONFIG["items"]} <= {"visual", "audio"}
    assert all(s["good"] for s in CONFIG["items"])
    assert len(IDS) == len(set(IDS))


def test_ts_seconds():
    assert ts_seconds("01:30") == 90 and ts_seconds("1:02:03") == 3723 and ts_seconds("75:30") == 4530
    assert ts_seconds("abc") is None and ts_seconds("12") is None


def test_all_pass_is_excellent_zero_points():
    r = score()
    assert (r["category"], r["points"], r["quality_issue"], r["borderline"]) == ("excellent", 0, False, None)


def test_hard_fail_is_poor_with_reason():
    r = score(face_visible="fail")
    assert r["category"] == "poor" and r["quality_issue"]
    assert r["quality_issue_reasons"][0].startswith("face_visible (01:00: face_visible seen)")
    assert "Quality issue:" in r["feedback_text"]


def test_repeated_issue_in_one_class_is_a_quality_issue():
    r = score(no_repeated_issue="fail")
    assert r["category"] == "poor" and r["quality_issue_reasons"][0].startswith("no_repeated_issue")


def test_hard_unclear_is_warning_only():
    r = score(background="unclear")
    assert not r["quality_issue"] and r["category"] == "excellent" and r["points"] == 0
    assert r["warnings"][0].startswith("background could not be confirmed")


def test_points_and_bands():
    assert score(homework="fail")["points"] == 1                                         # minor
    assert score(tone="fail")["category"] == "good"                                      # 2
    assert score(tone="fail", homework="fail")["category"] == "good"                     # 3
    assert score(tone="fail", intro_rapport="fail")["category"] == "average"             # 4
    assert score(tone="fail", intro_rapport="fail", camera_on="fail")["category"] == "poor"  # 6
    assert score(tone="unclear")["points"] == 0.5


def test_borderline_only_exactly_on_a_cut():
    assert score(tone="fail")["borderline"] == "excellent"                       # 2
    assert score(tone="fail", homework="fail")["borderline"] is None             # 3
    assert score(tone="fail", intro_rapport="fail")["borderline"] == "good"      # 4
    r = score(tone="fail", intro_rapport="fail", camera_on="fail")               # 6
    assert (r["category"], r["borderline"]) == ("poor", "average")
    assert score(face_visible="fail", tone="fail")["borderline"] is None         # hard fail: no borderline


def test_three_unclear_is_low_confidence_and_adds_no_points():
    r = score(tone="unclear", homework="unclear", conclusion="unclear", camera_on="fail")
    assert r["low_confidence"] and r["points"] == 2 and r["category"] == "good"
    assert set(r["unchecked_items"]) == {"tone", "homework", "conclusion"}
    assert any("partially reviewable" in w for w in r["warnings"])
    assert not score(tone="unclear", homework="unclear")["low_confidence"]


def test_na_and_bonus_never_add_points():
    assert score(new_joiner_intros="n_a")["points"] == 0
    for s in ("pass", "n_a", "fail", "unclear"):
        r = score(media=s)
        assert r["points"] == 0 and r["category"] == "excellent"
    assert {i["item_id"]: i["status"] for i in ground(items(media="fail"), 3600)}["media"] == "n_a"


def test_grounding_downgrades_pass_and_fail_without_proof():
    raw = items(homework="fail", conclusion="fail")
    raw[IDS.index("intro_rapport")]["quote"] = ""    # audio pass without a quote
    raw[IDS.index("background")]["evidence"] = ""    # visual pass without a description
    raw[IDS.index("tone")]["timestamps"] = []        # pass without a timestamp
    raw[IDS.index("conclusion")].update(quote="", evidence="")  # fail with nothing at all
    g = {i["item_id"]: i for i in ground(raw, 3600)}
    assert {g[k]["status"] for k in ("intro_rapport", "background", "tone", "conclusion")} == {"unclear"}
    assert "no timestamp or quote" in g["intro_rapport"]["grounding_note"]


def test_audio_fail_for_something_never_said_needs_no_quote():
    raw = items(homework="fail")
    raw[IDS.index("homework")].update(quote="", evidence="Class ends with 'bye everyone'; no homework mentioned")
    g = {i["item_id"]: i for i in ground(raw, 3600)}
    assert g["homework"]["status"] == "fail" and "grounding_note" not in g["homework"]


def test_timestamps_beyond_duration_dropped():
    raw = items()
    raw[IDS.index("conclusion")]["timestamps"] = ["99:00", "xx", "02:00"]
    raw[IDS.index("tone")]["timestamps"] = ["99:00"]
    g = {i["item_id"]: i for i in ground(raw, duration_s=600)}
    assert g["conclusion"]["timestamps"] == ["02:00"] and g["conclusion"]["status"] == "pass"
    assert g["tone"]["status"] == "unclear"


def test_missing_items_become_unclear():
    g = ground([], 600)
    assert [i["item_id"] for i in g] == IDS
    assert {i["status"] for i in g if SPECS[i["item_id"]]["tier"] != "bonus"} == {"unclear"}


def test_feedback_is_deterministic_template():
    a, b = score(homework="fail"), score(homework="fail")
    assert a["feedback_text"] == b["feedback_text"]
    assert "- Give homework (01:00: homework said)" in a["feedback_text"]
    assert "- Branded 8x8 background in use (01:00: background seen)" in a["feedback_text"]


def test_majority_vote_confidence_and_tie():
    merged, _ = majority([ground(items(tone="fail"), 3600), ground(items(tone="fail"), 3600),
                          ground(items(homework="fail"), 3600)])
    m = {i["item_id"]: i for i in merged}
    assert m["tone"]["status"] == "fail" and m["tone"]["confidence"] == round(2 / 3, 3)
    assert m["homework"]["status"] == "pass"
    merged, _ = majority([ground(items(tone="pass"), 3600), ground(items(tone="fail"), 3600),
                          ground(items(tone="unclear"), 3600)])
    assert {i["item_id"]: i["status"] for i in merged}["tone"] == "unclear"
