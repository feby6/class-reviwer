"""evaluation/: label mapping, metrics and the Step A <-> Step B alignment check."""
from conftest import make_obs

from evaluation.check_alignment import expected, norm, quote_found
from evaluation.evaluate import confusion, map_label, within_one


def test_map_label():
    assert map_label("weak") == ("poor", False)
    assert map_label("Known Quality Issues") == ("poor", True)
    assert map_label("quality_issue") == ("poor", True)
    assert map_label("Good") == ("good", False)


def test_within_one_and_confusion():
    assert within_one("poor", "average") and not within_one("poor", "good")
    m = confusion([{"expected": "poor", "category": "average"}])
    assert m.splitlines()[4].split() == ["poor", "0", "0", "1", "0"]


def test_alignment_quote_check():
    obs = norm("Tutor: Hello Gautham. Later: Oh very good, very nice! And: how was your day today")
    assert quote_found("Hello Gautham. ... Oh very good, very nice. … How was your day?", obs)
    assert quote_found("Bahut accha [very good]", norm("bahut accha"))
    assert not quote_found("Please do puzzles one to five for homework", obs)


def test_alignment_expected_statuses_from_plain_facts():
    e = expected(make_obs())
    assert e["background"] == {"fail", "unclear"} and e["new_joiner_intros"] == {"n_a"}
    assert e["tone"] == {"fail", "unclear"} and e["media"] == {"n_a"}
    ev_yes = {"present": True, "ts": "40:00", "quote": "do your homework"}
    e = expected(make_obs(closing={"conclusion": ev_yes, "homework": ev_yes}))
    assert e["homework"] == {"pass"} and "conclusion" not in e
