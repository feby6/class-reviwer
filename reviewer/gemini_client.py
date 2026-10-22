"""Gemini access shared by Step A and Step B.

One function, `generate()`, sends a request and returns the answer parsed into a pydantic model, plus token usage.
Requests use structured JSON output (Gemini fills the given schema), temperature 0 and a fixed seed, so repeated runs
are as close to identical as the API allows.
"""
import os

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

# Explicit model id, never a "-latest" alias. Google lists no dated snapshot for 3.8 Flash, so every call logs the
# model_version Gemini actually served (see usage.json) to spot silent model changes.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
SEED = 7
# A stuck request fails after 10 minutes instead of hanging the run. Step A on a 90-minute class takes about 3.
TIMEOUT_MS = 10 * 60 * 1000


def client() -> genai.Client:
    """Gemini client. Reads the API key from the GEMINI_API_KEY environment variable."""
    return genai.Client(http_options=types.HttpOptions(timeout=TIMEOUT_MS))


def generate(c: genai.Client, model: str, contents: list, schema: type[BaseModel],
             media_resolution: types.MediaResolution | None = None, seed: int = SEED) -> tuple[BaseModel, dict]:
    """Call Gemini and parse the answer into `schema`. Tries twice:
    - after a server error or timeout, the same request is sent again;
    - after a malformed answer, the seed is changed, because the identical request would likely repeat it.
    Returns (parsed answer, usage dict). Raises RuntimeError if both attempts fail."""
    last_err = None
    for attempt in range(2):
        retry_with_new_seed = attempt > 0 and isinstance(last_err, ValueError)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
            seed=seed + 1000 if retry_with_new_seed else seed,
            media_resolution=media_resolution,
        )
        try:
            resp = c.models.generate_content(model=model, contents=contents, config=config)
        except (errors.ServerError, httpx.TimeoutException) as e:  # transient: retry the same request
            last_err = e
            continue
        usage = {
            "model": model,
            "model_version": resp.model_version,
            "input_tokens": resp.usage_metadata.prompt_token_count,
            "output_tokens": resp.usage_metadata.candidates_token_count,
        }
        try:
            return schema.model_validate_json(resp.text), usage
        except ValueError as e:  # truncated or off-schema JSON (pydantic's ValidationError is a ValueError)
            last_err = e
    raise RuntimeError(f"Gemini failed twice for {schema.__name__}: {last_err}")
