from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, TypeVar

from google import genai
from google.genai import types

from config import (
    GEMINI_FLASH,
    GEMINI_FLASH_LITE,
    GEMINI_PRO,
    get_optional,
    get_secret,
    validate_env,
)
from google.oauth2 import service_account
from core.schemas import MemoryStore
from core.state import init_run_dir, load_memory, make_run_id

logger = logging.getLogger(__name__)
T = TypeVar("T")


class _GeminiModel:
    """Thin wrapper so call sites use model.generate_content(prompt) regardless of SDK."""

    def __init__(self, client: genai.Client, model_name: str, config: Any = None) -> None:
        self._client     = client
        self._model_name = model_name
        self._config     = config

    def generate_content(self, prompt: str) -> Any:
        kwargs: dict[str, Any] = {
            "model":    self._model_name,
            "contents": prompt,
        }
        if self._config is not None:
            kwargs["config"] = self._config
        result = self._client.models.generate_content(**kwargs)
        logger.info("API_CALL gemini %s", self._model_name)
        return result

    def with_grounding(self) -> "_GeminiModel":
        """Return a copy of this model with Google Search grounding enabled."""
        grounded_config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
        return _GeminiModel(self._client, self._model_name, config=grounded_config)


class ErrorClass(Enum):
    TRANSIENT = "transient"
    RATE_MIN  = "rate_min"
    RATE_DAY  = "rate_day"
    OVERLOAD  = "overload"
    FATAL     = "fatal"


@dataclass(slots=True)
class RetryPolicy:
    max_attempts: int   = 3
    base_delay_s: float = 2.0
    max_delay_s:  float = 60.0


@dataclass
class RunContext:
    run_id:      str
    run_dirs:    dict[str, Path]
    memory:      MemoryStore
    model_pro:   _GeminiModel
    model_flash: _GeminiModel
    model_lite:  _GeminiModel
    keys:        dict[str, Any]          = field(default_factory=dict)
    token_log:   list[dict[str, Any]]    = field(default_factory=list)


def classify_error(exc: Exception) -> ErrorClass:
    msg = str(exc).lower()
    if any(x in msg for x in ("quota", "rate", "429", "resource_exhausted")):
        return ErrorClass.RATE_MIN if ("per_minute" in msg or "per minute" in msg) else ErrorClass.RATE_DAY
    if any(x in msg for x in ("503", "overload", "unavailable")):
        return ErrorClass.OVERLOAD
    if any(x in msg for x in ("timeout", "deadline", "connection")):
        return ErrorClass.TRANSIENT
    if any(x in msg for x in ("invalid", "permission", "auth", "401", "403")):
        return ErrorClass.FATAL
    return ErrorClass.TRANSIENT


def with_retry(
    call: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    label: str = "",
) -> T:
    delay = policy.base_delay_s
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return call()
        except Exception as exc:
            cls = classify_error(exc)
            if cls == ErrorClass.FATAL:
                raise
            if cls == ErrorClass.RATE_DAY:
                raise RuntimeError(f"Daily rate limit reached on {label}") from exc
            if attempt == policy.max_attempts:
                raise
            wait = 60.0 if cls == ErrorClass.RATE_MIN else min(delay, policy.max_delay_s)
            logger.warning(
                "%s attempt %d/%d [%s] — waiting %.0fs: %s",
                label, attempt, policy.max_attempts, cls.value, wait, exc,
            )
            time.sleep(wait)
            delay *= 2
    raise RuntimeError(f"Unreachable — {label}")  # pragma: no cover


def bootstrap() -> RunContext:
    """Validate env, init Gemini client (Vertex AI via service account), return RunContext."""
    validate_env()

    project     = get_secret("GOOGLE_CLOUD_PROJECT")
    creds_path  = get_secret("GOOGLE_APPLICATION_CREDENTIALS")
    credentials = service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client = genai.Client(vertexai=True, project=project, location="us-central1",
                          credentials=credentials)

    run_id   = make_run_id()
    run_dirs = init_run_dir(run_id)
    memory   = load_memory()

    reddit_id     = get_optional("REDDIT_CLIENT_ID")
    reddit_secret = get_optional("REDDIT_CLIENT_SECRET")
    reddit_agent  = get_optional("REDDIT_USER_AGENT")

    keys: dict[str, Any] = {
        "pexels":    get_secret("PEXELS_API_KEY"),
        "pixabay":   get_secret("PIXABAY_API_KEY"),
        "freesound": get_secret("FREESOUND_CLIENT_SECRET"),
        "jamendo":   get_secret("JAMENDO_API_KEY"),
        "coverr":    get_optional("COVERR_API_KEY"),
        "reddit":    {
            "client_id":     reddit_id,
            "client_secret": reddit_secret,
            "user_agent":    reddit_agent,
        } if reddit_id else None,
    }

    return RunContext(
        run_id      = run_id,
        run_dirs    = run_dirs,
        memory      = memory,
        model_pro   = _GeminiModel(client, GEMINI_PRO),
        model_flash = _GeminiModel(client, GEMINI_FLASH),
        model_lite  = _GeminiModel(client, GEMINI_FLASH_LITE),
        keys        = keys,
    )
