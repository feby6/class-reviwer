"""Step B: answer each checklist question from the Step A observations. Text only, no video.

Gemini gets the checklist questions (checklist.yaml) and the observations, and returns one factual answer per item:
pass / fail / unclear / n_a, with timestamps, a quote or description, and a one-line reason. It does not grade the
class or write feedback: the category and the feedback text are made in scoring.py, so they are deterministic.
"""
import yaml

from .gemini_client import SEED, generate
from .schemas import Judgement, Observations
from .scoring import CONFIG

PROMPT = """You check one recorded online class for children against a checklist, using ONLY the timestamped
observations below (they were extracted earlier from the recording).

Answer each checklist item on its own, as a narrow factual question, following its definition exactly.
For every item return:
- status: pass / fail / unclear / n_a (n_a only where the item's definition allows it)
- timestamps (MM:SS) of the evidence
- visual items: `evidence` = description of what is seen; `quote` = ""
  audio items:  `evidence` = what happened; `quote` = the exact words spoken (original + English translation in
  brackets if not English)
- reason: one line explaining the status in terms of the definition

Pass and fail both need specific evidence from the observations. If the evidence is missing, thin or ambiguous,
answer unclear. Do not give the benefit of the doubt.
For a fail because something did not happen, cite the moment where it should have happened (e.g. when teaching
starts, or the end of the class) with what was said or seen there.

## Checklist
{checklist}

## Observations
{observations}"""


def build_prompt(obs: Observations) -> str:
    """The full Step B prompt. Only item ids, kinds and questions go in: tiers, points, bands and feedback phrases
    do not, so changing them never invalidates cached Step B answers."""
    items = [{"id": s["id"], "kind": s["kind"], "question": s["check"].strip()} for s in CONFIG["items"]]
    return PROMPT.format(
        checklist=yaml.safe_dump(items, sort_keys=False, width=120),
        observations=obs.model_dump_json(indent=1),
    )


def cache_parts(model: str, seed: int, prompt: str) -> tuple:
    """Everything that changes a Step B answer. The pipeline hashes this to name the cached answer file."""
    return (model, seed, prompt, Judgement.model_json_schema(), "temperature=0")


def judge(c, model: str, prompt: str, seed: int = SEED) -> tuple[Judgement, dict]:
    return generate(c, model, [prompt], Judgement, seed=seed)
