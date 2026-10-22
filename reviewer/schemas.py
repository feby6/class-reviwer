"""Data shapes passed to and from Gemini (used as its structured-output schemas).

Timestamps are "MM:SS" strings; minutes may go above 59 (e.g. "75:30"). Field descriptions are sent to Gemini as part
of the schema, so they double as instructions: keep them precise.

Step A fills `Observations` (facts only). Step B fills `Judgement` (one answer per checklist item). Every fact Step B
judges must exist in `Observations`: when a checklist question changes, check that Step A still collects its facts.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------- Step A: observations (facts only, no grading) ----------


class Span(BaseModel):
    start: str
    end: str
    note: str = ""


class FaceSpan(BaseModel):
    start: str
    end: str
    state: Literal["visible", "partial", "not_visible"]
    note: str = ""


class LightSpan(BaseModel):
    start: str
    end: str
    level: Literal["good", "dim", "dark"]


class Background(BaseModel):
    type: Literal["virtual", "physical", "none", "unclear"]
    has_8x8_branding: bool = Field(description="The 8x8 / 'Eight Times Eight' logo is on the tutor's video, as a "
                                               "virtual background or as an overlay on a real room")
    description: str
    glitch_spans: list[Span]


class Evidence(BaseModel):
    present: bool
    ts: Optional[str] = None
    quote: str = Field("", description="Original words, plus English translation in brackets if not English")


class RangeEvidence(BaseModel):
    present: bool
    start: Optional[str] = None
    end: Optional[str] = None
    evidence: str = ""


class Quote(BaseModel):
    ts: str
    speaker: str
    quote: str = Field(description="Original words, plus English translation in brackets if not English")


class Meta(BaseModel):
    languages_spoken: list[str]
    tutor_identification: str = Field(description="Who the tutor is and how you identified them")
    layout_notes: str
    student_count_estimate: Optional[int] = None


class Visual(BaseModel):
    camera_off_spans: list[Span] = Field(description="Times the tutor's tile shows only a name/placeholder")
    face_visibility: list[FaceSpan]
    lighting: list[LightSpan]
    background: Background
    screen_share_spans: list[Span]
    media_played: list[Span] = Field(description="Topic-related videos or media played (not a board/puzzle site)")
    setup_warnings: list[Quote] = Field(description="Times a child or staff member tells the tutor about a setup "
                                                    "problem (e.g. 'we can't see you', 'your video is off')")


class NewJoiner(BaseModel):
    name: str
    ts: str
    invited_to_introduce: bool = Field(description="The tutor asked the child to introduce themselves to the class "
                                                   "and gave them time (not just 'are you new?' / name / age)")
    quote: str = ""


class Opening(BaseModel):
    greeting: Evidence
    rapport_building: RangeEvidence = Field(description="Personal chat with the kids before teaching (their day, "
                                                        "week, school) or telling them what today is about")
    went_straight_to_content: bool
    went_straight_to_content_ts: Optional[str] = None
    new_joiners: list[NewJoiner] = Field(description="Children new to the class; empty if none")


class Question(BaseModel):
    ts: str
    student_name: str = Field(description="Name used, or 'unknown'")
    question: str


class NameMention(BaseModel):
    name: str
    first_ts: str
    count: int


class Engagement(BaseModel):
    individual_questions: list[Question]
    students_addressed_by_name: list[NameMention]
    praise_moments: list[Quote] = Field(description="Every moment the tutor praises or encourages a child "
                                                    "('very good', 'well done', or the same in any language)")


class Closing(BaseModel):
    conclusion: Evidence = Field(description="Wrap-up near the end: what was done today / what's next, and goodbye")
    homework: Evidence = Field(description="Homework assigned, or children told to do their homework")


class Observations(BaseModel):
    meta: Meta
    visual: Visual
    opening: Opening
    engagement: Engagement
    closing: Closing
    key_quotes: list[Quote] = Field(description="At most ~20 other notable quotes, not a transcript")


# ---------- Step B: one factual answer per checklist item (category and feedback are made in scoring.py) ----------

Category = Literal["poor", "average", "good", "excellent"]


class ChecklistResult(BaseModel):
    item_id: str
    # evidence comes before status so the model looks at the facts before it decides
    evidence: str = Field(description="What was seen (visual items) or what happened (audio items)")
    quote: str = Field(description="Audio items: exact words spoken (original + English translation if not "
                                   "English). Visual items: empty.")
    timestamps: list[str] = Field(description="MM:SS times of the evidence")
    reason: str = Field(description="One line: why this status, in terms of the item's definition")
    status: Literal["pass", "fail", "unclear", "n_a"]


class Judgement(BaseModel):
    checklist: list[ChecklistResult]
