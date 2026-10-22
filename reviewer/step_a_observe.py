"""Step A: watch the class video and record timestamped facts. No grading happens here.

Gemini receives the shrunk video (1 frame every 20 s + the full audio track) and fills `schemas.Observations`.
The "What to look for" list below mirrors the checklist in checklist.yaml: every question Step B asks must be
answerable from these facts. When you change a checklist question, update this list and the schema with it.
"""
import logging
import time
from pathlib import Path

import httpx
from google.genai import types

from .gemini_client import generate
from .schemas import Observations

log = logging.getLogger(__name__)

FPS = 0.05  # frames Gemini samples per second of video (1 every 20 s); audio is always processed in full
MEDIA_RESOLUTION = types.MediaResolution.MEDIA_RESOLUTION_LOW  # ~66 tokens per frame
PROCESSING_TIMEOUT_S = 10 * 60  # give up if Gemini hasn't finished processing the upload by then

PROMPT = """You are watching a recording of an online class for children, led by one adult tutor.

1. First identify the tutor (the adult leading the class: most speaking time, usually the host tile).
   The screen layout may change (gallery grid, speaker view, screen share); always track the tutor's own camera tile.
   During screen share it is often a small thumbnail in a corner.
2. Record only observable facts. Every fact needs a timestamp in MM:SS (minutes can go above 59, e.g. 75:30) and,
   for speech, the exact words.
3. Speech may be in any language or a mix. Quote the original words followed by an English translation in brackets
   when it is not English.
4. If you cannot tell, use "unclear" / present=false with a note. Do not guess. Do NOT grade or give opinions.

What to look for:
- Background: is the 8x8 / "Eight Times Eight" branding on the tutor's video (virtual background or a logo overlay on
  a real room)? What does it show? Any glitches?
- Tutor's camera: times it shows only a name/placeholder tile; whether the face is visible; lighting on the face.
  A layout that hides the tutor's tile is not camera off.
- Setup problems: times a child or staff member tells the tutor about a setup problem (e.g. "we can't see you").
- Screen sharing, and any topic-related videos or media played (sharing a board or puzzle site is not media).
- Opening, before teaching starts: greeting, personal chat with the kids (their day, week, school), telling them what
  today is about, or going straight into content or revision of the last class.
- New joiners: any child new to the class, and whether the tutor invited them to introduce themselves to the class
  and gave them time (not just asking "are you new?", their name or age).
- Engagement: questions asked to individual children (with the child's name if said), which children were addressed
  by name and how often.
- Praise: every moment the tutor praises or encourages a child, with the exact words.
- Closing: a wrap-up near the end (what was done today / what's next, goodbye), and homework (assigned, or children
  told to do their homework).
- Up to 20 other key quotes. Do not write a full transcript."""


def cache_parts(model: str) -> tuple:
    """Everything that changes Step A's answer. The pipeline hashes this to decide if cached observations are valid."""
    return (PROMPT, Observations.model_json_schema(), model, FPS, MEDIA_RESOLUTION.value)


def _wait_until_active(c, f):
    """Poll until Gemini has processed the uploaded video, or fail after PROCESSING_TIMEOUT_S."""
    deadline = time.monotonic() + PROCESSING_TIMEOUT_S
    while f.state.name == "PROCESSING":
        if time.monotonic() > deadline:
            raise RuntimeError(f"Gemini did not finish processing the upload within {PROCESSING_TIMEOUT_S}s")
        time.sleep(5)
        try:
            f = c.files.get(name=f.name)
        except httpx.TransportError:  # a dropped status poll; the file is still processing
            continue
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini could not process the video: {f.state.name}")
    return f


def observe(c, model: str, video: Path) -> tuple[Observations, dict]:
    """Upload the shrunk video, get the observations, and always delete the upload from Google afterwards."""
    log.info("Step A: uploading %s", video.name)
    f = c.files.upload(file=str(video))
    try:
        f = _wait_until_active(c, f)
        log.info("Step A: Gemini is watching the video")
        part = types.Part(
            file_data=types.FileData(file_uri=f.uri, mime_type=f.mime_type),
            video_metadata=types.VideoMetadata(fps=FPS),
        )
        return generate(c, model, [part, PROMPT], Observations, MEDIA_RESOLUTION)
    finally:
        try:  # children's video must not stay on Google's side (it would otherwise expire after 48 h)
            c.files.delete(name=f.name)
        except Exception as e:
            log.warning("Step A: could not delete uploaded file %s: %s", f.name, e)
