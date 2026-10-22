"""Compare the pipeline's categories with human labels.

    uv run python -m evaluation.evaluate category_test/               # one subfolder per human label
    uv run python -m evaluation.evaluate category_test/ --stability   # run every video twice without cache

Each video's results go to runs/<video name>/ (shared with the CLI, so cached steps are reused).
Dataset labels map to output labels: weak / quality_issue / known_quality_issues -> poor; quality_issue and
known_quality_issues also expect the quality_issue flag. Results are saved to runs/evaluation_results.json.
"""
import argparse
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from reviewer.pipeline import REPORT, review
from reviewer.scoring import ORDER

RUNS = Path("runs")
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
QUALITY_LABELS = {"quality_issue", "quality_issues", "known_quality_issue", "known_quality_issues"}


def map_label(raw: str) -> tuple[str, bool]:
    """Dataset folder name -> (expected output category, quality_issue flag expected)."""
    k = re.sub(r"[\s\-]+", "_", raw.strip().lower())
    if k in QUALITY_LABELS:
        return "poor", True
    if k in {"weak", "poor"}:
        return "poor", False
    if k in {"average", "good", "excellent"}:
        return k, False
    raise ValueError(f"unknown label: {raw!r}")


def load_rows(root: Path) -> list[dict]:
    """Every video in root/<label>/ as {"video": path, "label": folder name}."""
    return [{"video": str(v), "label": d.name}
            for d in sorted(p for p in root.iterdir() if p.is_dir())
            for v in sorted(d.iterdir()) if v.suffix.lower() in VIDEO_EXT]


def run_dir(video: str) -> Path:
    return RUNS / Path(video).stem


def within_one(a: str, b: str) -> bool:
    return abs(ORDER.index(a) - ORDER.index(b)) <= 1


def confusion(results: list[dict]) -> str:
    """Text confusion matrix: rows = human label, columns = predicted category."""
    lines = ["human \\ predicted".ljust(18) + "".join(c.rjust(10) for c in ORDER)]
    for h in ORDER:
        lines.append(h.ljust(18) + "".join(
            str(sum(r["expected"] == h and r["category"] == p for r in results)).rjust(10) for p in ORDER))
    return "\n".join(lines)


def _review(video: str, **kw) -> dict | Exception:
    try:
        return review(Path(video), run_dir(video), **kw)
    except Exception as e:  # one failing video shouldn't stop the whole evaluation
        return e


def evaluate(rows: list[dict], votes: int, workers: int) -> None:
    with ThreadPoolExecutor(workers) as pool:
        reports = list(pool.map(lambda r: _review(r["video"], votes=votes), rows))

    results = []
    print(f"{'video':6} {'human':9} {'points':>6}  {'predicted':10} {'flags':28} failed items")
    for row, rep in zip(rows, reports):
        name = Path(row["video"]).stem.split()[0]
        if isinstance(rep, Exception):
            print(f"{name:6} ERROR {rep}")
            continue
        if rep["category"] == "unreviewable":
            print(f"{name:6} SKIP unreviewable {rep['problems']}")
            continue
        expected, exp_flag = map_label(row["label"])
        flags = [f for f, on in [("QUALITY", rep["quality_issue"]),
                                 (f"borderline {rep['borderline']}", rep["borderline"]),
                                 ("low-confidence", rep["low_confidence"])] if on]
        if rep["quality_issue"] != exp_flag:
            flags.append(f"flag expected {exp_flag}")
        failed = [i["item_id"] for i in rep["checklist"] if i["status"] == "fail"]
        unclear = [i["item_id"] for i in rep["checklist"] if i["status"] == "unclear"]
        mark = "OK " if rep["category"] == expected else ("~  " if within_one(rep["category"], expected) else "XX ")
        print(f"{name:6} {expected:9} {rep['points']:>6}  {mark}{rep['category']:7} {', '.join(flags):28} "
              f"{', '.join(failed) or '-'}" + (f"  | unclear: {', '.join(unclear)}" if unclear else "")
              + f"  | conf {rep['confidence']}")
        results.append({"video": row["video"], "label": row["label"], "expected": expected,
                        "expected_flag": exp_flag, "category": rep["category"], "points": rep["points"],
                        "quality_issue": rep["quality_issue"], "borderline": rep["borderline"],
                        "low_confidence": rep["low_confidence"], "failed": failed, "unclear": unclear,
                        "confidence": rep["confidence"]})

    n = len(results)
    if not n:
        return
    print("\n" + confusion(results))
    print(f"\nexact: {sum(r['category'] == r['expected'] for r in results)}/{n}   "
          f"within one category: {sum(within_one(r['category'], r['expected']) for r in results)}/{n}   "
          f"quality flag: {sum(r['quality_issue'] == r['expected_flag'] for r in results)}/{n}")
    (RUNS / "evaluation_results.json").write_text(json.dumps(results, indent=2))


def stability(rows: list[dict], votes: int, workers: int) -> None:
    """Run each video twice with every cache ignored; compare the category and each item's status."""
    videos = [r["video"] for r in rows]
    with ThreadPoolExecutor(workers) as pool:
        first = list(pool.map(lambda v: _review(v, force=True, votes=votes), videos))
        for v, r in zip(videos, first):
            if isinstance(r, dict) and "checklist" in r:  # keep run 1 for inspection; run 2 overwrites report.json
                shutil.copy(run_dir(v) / REPORT, run_dir(v) / "report_stability_run1.json")
        second = list(pool.map(lambda v: _review(v, force=True, votes=votes), videos))

    cat_same = n = item_same = item_total = 0
    for v, r1, r2 in zip(videos, first, second):
        name = Path(v).name
        if isinstance(r1, Exception) or isinstance(r2, Exception):
            print(f"ERR  {name}: {r1 if isinstance(r1, Exception) else r2}")
            continue
        if "checklist" not in r1:
            print(f"SKIP {name}: unreviewable")
            continue
        s1 = {i["item_id"]: i["status"] for i in r1["checklist"]}
        s2 = {i["item_id"]: i["status"] for i in r2["checklist"]}
        diff = [k for k in s1 if s1[k] != s2.get(k)]
        same = r1["category"] == r2["category"]
        n += 1
        cat_same += same
        item_same += len(s1) - len(diff)
        item_total += len(s1)
        print(f"{'SAME' if same else 'DIFF'} {name}: {r1['category']} ({r1['points']}) vs "
              f"{r2['category']} ({r2['points']}) items changed={diff}")
    if n:
        print(f"\ncategory agreement: {cat_same}/{n}   item agreement: {item_same}/{item_total} "
              f"({item_same / item_total:.0%})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="folder with one subfolder per human label")
    ap.add_argument("--votes", type=int, default=3)
    ap.add_argument("--stability", action="store_true")
    ap.add_argument("--workers", type=int, default=4, help="videos processed in parallel")
    a = ap.parse_args()
    rows = load_rows(a.src)
    for r in rows:
        map_label(r["label"])  # fail fast on an unknown label folder
    if a.stability:
        stability(rows, a.votes, a.workers)
    else:
        evaluate(rows, a.votes, a.workers)


if __name__ == "__main__":
    main()
