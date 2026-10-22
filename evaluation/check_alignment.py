"""Offline check that Step B (judgement) is consistent with Step A (observations). No API calls, free to run.

    uv run python -m evaluation.check_alignment      # every run folder in runs/ that has a report

1. Grounding: every timestamp and quote Step B cites must appear in that video's observations.
2. Agreement: for items with a plain fact in the observations, Step B's status must match it.
Exit code 1 if any problem is found, so it can run in CI after an evaluation.
"""
import json
import re
import sys
from pathlib import Path

from reviewer.pipeline import OBSERVATIONS, REPORT
from reviewer.schemas import Observations
from reviewer.scoring import ts_seconds

RUNS = Path("runs")


def norm(s: str) -> str:
    """Lowercase, letters and digits only, single spaces: for loose text matching."""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def quote_found(quote: str, obs_text: str) -> bool:
    """Each piece of the quote (pieces joined with '...' are separate) must have a 6-word run, or be whole if
    shorter, in the observations. Bracketed translations are ignored."""
    for piece in re.split(r"\.\.\.|…", re.sub(r"\[.*?\]|\(.*?\)", "", quote)):
        words = norm(piece).split()
        runs = [" ".join(words[i:i + 6]) for i in range(max(1, len(words) - 5))]
        if words and not any(r in obs_text for r in runs):
            return False
    return True


def ts_found(ts: str, obs_ts: list[float]) -> bool:
    """A cited timestamp is grounded if it is within a minute of any timestamp in the observations."""
    s = ts_seconds(ts)
    return s is not None and any(abs(s - o) <= 60 for o in obs_ts)


def expected(o: Observations) -> dict[str, set[str]]:
    """Statuses Step B may give, judged only from plain facts in the observations. Items without a plain fact
    (intro quality, camera_on, lighting, repeated issues) are judgement calls and are not checked here."""
    exp = {
        "background": {"pass"} if o.visual.background.has_8x8_branding else {"fail", "unclear"},
        "homework": {"pass"} if o.closing.homework.present else {"fail", "unclear"},
        "media": {"pass"} if o.visual.media_played else {"n_a"},
    }
    joiners = o.opening.new_joiners
    if not joiners:
        exp["new_joiner_intros"] = {"n_a"}
    elif not all(j.invited_to_introduce for j in joiners):
        exp["new_joiner_intros"] = {"fail", "unclear"}
    if not o.engagement.praise_moments:
        exp["tone"] = {"fail", "unclear"}
    if not o.closing.conclusion.present:
        exp["conclusion"] = {"fail", "unclear"}
    if not o.opening.rapport_building.present and o.opening.went_straight_to_content:
        exp["intro_rapport"] = {"fail"}
    if not o.engagement.individual_questions:
        exp["individual_questions"] = {"fail", "unclear"}
    return exp


def check(run: Path) -> tuple[list[str], int, int]:
    """(problems, citations checked, plain facts checked) for one run folder."""
    o = Observations.model_validate_json((run / OBSERVATIONS).read_text())
    obs_json = o.model_dump_json()
    obs_text = norm(obs_json)
    obs_ts = [s for t in re.findall(r"\b\d{1,3}:\d{2}(?::\d{2})?\b", obs_json) if (s := ts_seconds(t)) is not None]
    items = {i["item_id"]: i for i in json.loads((run / REPORT).read_text())["checklist"]}

    problems, cited = [], 0
    for iid, i in items.items():
        for t in i.get("timestamps", []):
            cited += 1
            if not ts_found(t, obs_ts):
                problems.append(f"{iid}: timestamp {t} not near anything in the observations")
        if i.get("quote"):
            cited += 1
            if not quote_found(i["quote"], obs_text):
                problems.append(f"{iid}: quote not in observations: {i['quote'][:80]!r}")
    exp = expected(o)
    for iid, allowed in exp.items():
        if items[iid]["status"] not in allowed:
            problems.append(f"{iid}: Step B says {items[iid]['status']}, observations imply {'/'.join(sorted(allowed))}")
    return problems, cited, len(exp)


def main() -> None:
    runs = []
    for d in sorted(p for p in RUNS.iterdir() if p.is_dir()):
        report = json.loads((d / REPORT).read_text()) if (d / REPORT).exists() else {}
        if "checklist" in report and (d / OBSERVATIONS).exists():
            runs.append(d)
    total_bad = total_cited = total_facts = checked = 0
    for d in runs:
        try:
            problems, cited, facts = check(d)
        except ValueError as e:  # observations or report from an older format: re-run the evaluation first
            print(f"SKIP {d.name}: {e.__class__.__name__} (re-run this video)")
            continue
        checked += 1
        total_bad += len(problems)
        total_cited += cited
        total_facts += facts
        print(f"{'OK  ' if not problems else 'WARN'} {d.name.split()[0]:5} citations={cited} facts checked={facts}")
        for p in problems:
            print(f"       - {p}")
    print(f"\n{checked} of {len(runs)} runs checked, {total_cited} citations and {total_facts} plain facts checked, {total_bad} problems")
    sys.exit(1 if total_bad else 0)


if __name__ == "__main__":
    main()
