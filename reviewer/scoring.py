"""Turn Step B's per-item answers into the final result. Pure Python, no AI: the same answers always give the same
category and the same feedback text.

Flow, called by pipeline.py:
  ground()   -> make every answer evidence-backed (an answer without proof becomes "unclear")
  majority() -> combine the 3 Step B votes per item; agreement between votes = confidence
  decide()   -> category, issue points, quality flag, warnings and the feedback text

All numbers (points per tier, bands, low-confidence limit) and the feedback phrases live in checklist.yaml.
"""
from collections import Counter
from pathlib import Path

import yaml

CONFIG = yaml.safe_load((Path(__file__).parent / "checklist.yaml").read_text())
ORDER = ["excellent", "good", "average", "poor"]  # best -> worst


def ts_seconds(ts: str) -> float | None:
    """'MM:SS' or 'H:MM:SS' -> seconds; None if unparseable."""
    try:
        parts = [float(p) for p in ts.strip().split(":")]
    except (ValueError, AttributeError):
        return None
    if len(parts) not in (2, 3) or any(p < 0 for p in parts):
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def ground(items: list[dict], duration_s: float, config: dict = CONFIG) -> list[dict]:
    """Return exactly one answer per checklist item, in checklist order:
    - items the model left out become "unclear";
    - unparseable timestamps, and timestamps past the end of the video, are dropped;
    - a pass or fail without proof becomes "unclear". Proof = a timestamp plus:
        pass on an audio item: a quote (it claims something was said, so the words must be shown);
        anything else, incl. a fail on an audio item: a description or quote (something that was never said, like
        homework that wasn't given, can't be quoted, so the moment it should have happened is described instead);
    - a bonus item can only be pass or n_a, so it never counts against the tutor."""
    got = {i["item_id"]: i for i in items}
    out = []
    for spec in config["items"]:
        i = dict(got.get(spec["id"]) or {"item_id": spec["id"], "status": "unclear", "evidence": "", "quote": "",
                                           "timestamps": [], "reason": "not returned by the model"})
        i["timestamps"] = [t for t in i.get("timestamps", [])
                           if (s := ts_seconds(t)) is not None and s <= duration_s + 1]
        needs_quote = spec["kind"] == "audio" and i["status"] == "pass"
        proof = (i.get("quote") if needs_quote else (i.get("evidence") or i.get("quote"))) or ""
        if i["status"] in ("pass", "fail") and not (i["timestamps"] and proof.strip()):
            i["grounding_note"] = f"{i['status']} downgraded: no timestamp or {'quote' if needs_quote else 'evidence'}"
            i["status"] = "unclear"
        if spec["tier"] == "bonus" and i["status"] in ("fail", "unclear"):
            i["status"] = "n_a"  # bonus never counts against the tutor
        out.append(i)
    return out


def _cite(i: dict) -> str:
    """' (MM:SS, MM:SS: quote or description)' for the feedback text; at most two timestamps."""
    ts = ", ".join(i["timestamps"][:2])
    proof = (i.get("quote") or i.get("evidence") or "").strip()
    return f" ({ts}: {proof})" if ts or proof else ""


def decide(items: list[dict], config: dict = CONFIG) -> dict:
    """Grounded answers -> category, issue points, flags, warnings and the fixed-template feedback.
    - Any hard item that fails: Poor + quality_issue flag, whatever the points.
    - Otherwise issue points (core fail, minor fail, unclear) are compared with the bands.
    - 3+ unclear items: their points are not added and the result is marked low confidence.
    - Points exactly on a band cut: marked "borderline" with the better neighbouring category."""
    specs = {s["id"]: s for s in config["items"]}
    pts = config["points"]
    tier = {i["item_id"]: specs[i["item_id"]]["tier"] for i in items}

    hard_fails = [i for i in items if tier[i["item_id"]] == "hard" and i["status"] == "fail"]
    hard_unclear = [i for i in items if tier[i["item_id"]] == "hard" and i["status"] == "unclear"]
    unclear = [i for i in items if tier[i["item_id"]] != "bonus" and i["status"] == "unclear"]
    low_confidence = len(unclear) >= config["low_confidence_unclear"]

    points = sum(pts[f"{tier[i['item_id']]}_fail"] for i in items
                 if i["status"] == "fail" and tier[i["item_id"]] in ("core", "minor"))
    if not low_confidence:
        points += pts["unclear"] * sum(tier[i["item_id"]] in ("core", "minor") for i in unclear)

    bands = config["bands"]
    borderline = None
    if hard_fails:
        category = "poor"
    else:
        category = next((c for c in ("excellent", "good", "average") if points < bands[c]), "poor")
        # exactly on a cut -> one step from the better neighbour
        if category != "excellent" and points == bands[ORDER[ORDER.index(category) - 1]]:
            borderline = ORDER[ORDER.index(category) - 1]

    warnings = [f"{i['item_id']} could not be confirmed: {i.get('reason') or i.get('evidence', '')}"
                for i in hard_unclear]
    if low_confidence:
        warnings.append("low confidence, partially reviewable: could not check "
                        + ", ".join(i["item_id"] for i in unclear))

    good = [specs[i["item_id"]]["good"] + _cite(i) for i in items if i["status"] == "pass"]
    needs = [specs[i["item_id"]]["improve"] + _cite(i) for i in items if i["status"] == "fail"]
    text = "\n".join(
        [f"Category: {category.capitalize()}" + (f" (borderline {borderline.capitalize()})" if borderline else "")]
        + (["Quality issue: " + "; ".join(f"{i['item_id']}{_cite(i)}" for i in hard_fails)] if hard_fails else [])
        + ["Good:"] + [f"- {g}" for g in good]
        + ["Needs work:"] + [f"- {n}" for n in needs]
        + (["Warnings:"] + [f"- {w}" for w in warnings] if warnings else [])
    )
    return {
        "category": category,
        "points": points,
        "borderline": borderline,
        "quality_issue": bool(hard_fails),
        "quality_issue_reasons": [f"{i['item_id']}{_cite(i)}" for i in hard_fails],
        "low_confidence": low_confidence,
        "unchecked_items": [i["item_id"] for i in unclear] if low_confidence else [],
        "warnings": warnings,
        "feedback": {"good": good, "needs_work": needs, "warnings": warnings},
        "feedback_text": text,
    }


def majority(votes: list[list[dict]]) -> tuple[list[dict], float]:
    """Grounded item lists from N Step B runs -> (majority items, overall confidence).
    No strict majority (tie) -> unclear. Item confidence = share of votes agreeing with its final status."""
    n = len(votes)
    merged = []
    for k in range(len(votes[0])):
        counts = Counter(v[k]["status"] for v in votes)
        top, c = counts.most_common(1)[0]
        status = top if c > n / 2 else "unclear"
        pick = next((v[k] for v in votes if v[k]["status"] == status), votes[0][k])
        merged.append({**pick, "status": status, "votes": dict(counts),
                       "confidence": round(counts.get(status, 0) / n, 3)})
    return merged, round(sum(i["confidence"] for i in merged) / len(merged), 3)
