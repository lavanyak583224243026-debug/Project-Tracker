"""Gemini API Service using google-genai SDK and CrewAI LLM configuration."""
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv

# Base project path
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

logger = logging.getLogger("gemini_service")

DEFAULT_MODEL = "gemini-3.5-flash"
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"]


def get_api_key() -> str:
    """Return GEMINI_API_KEY from environment or .env, without exposing it."""
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key in ("your_gemini_api_key_here", "YOUR_GEMINI_API_KEY_HERE", ""):
        return ""
    return key


def get_model_name() -> str:
    """Return configured model name (e.g. gemini-3.5-flash)."""
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def get_genai_client():
    """Create and return google-genai Client."""
    from google import genai

    key = get_api_key()
    if not key:
        raise ValueError("GEMINI_API_KEY is not configured or is a placeholder.")
    return genai.Client(api_key=key)


def check_gemini_availability() -> Tuple[bool, str]:
    """Check whether Gemini is configured without pinging the API or leaking credentials."""
    key = get_api_key()
    if not key:
        return False, "GEMINI_API_KEY is missing or configured with a placeholder."

    try:
        from google import genai
        _ = genai.Client(api_key=key)
        model = get_model_name()
        return True, f"Gemini API Connected ({model})"
    except Exception as ex:
        err_msg = str(ex).replace(key, "[REDACTED_API_KEY]")
        return False, f"Gemini initialization error: {err_msg[:60]}"


def clean_json_response(raw_text: str) -> str:
    """Strip markdown codeblocks and whitespace to extract pure JSON."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def generate_gemini_content(
    prompt: str,
    system_instruction: Optional[str] = None,
) -> str:
    """Call Google Gemini API using google-genai SDK with automatic model rotation on 429."""
    key = get_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing. Please configure it in .env.")

    from google import genai
    from google.genai import errors

    client = genai.Client(api_key=key)
    primary_model = get_model_name()
    candidate_models = [primary_model] + [m for m in FALLBACK_MODELS if m != primary_model]

    config = {}
    if system_instruction:
        config["system_instruction"] = system_instruction

    last_error = None
    for model in candidate_models:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config if config else None,
            )
            if response and response.text:
                return response.text
        except errors.ClientError as ce:
            err_str = str(ce)
            last_error = ce
            if "401" in err_str or "UNAUTHENTICATED" in err_str:
                raise RuntimeError("Gemini Authentication Error (401): Check your GEMINI_API_KEY.")
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "404" in err_str:
                logger.warning(f"Model {model} limit/unavailable. Rotating to next model...")
                continue
            raise RuntimeError(f"Gemini Client Error: {ce.code} {ce.message}")
        except errors.ServerError as se:
            last_error = se
            continue
        except Exception as ex:
            last_error = ex
            continue

    if last_error:
        raise RuntimeError(f"Gemini Error across models: {str(last_error)}")
    raise RuntimeError("Gemini returned an empty response.")


def get_crewai_llm():
    """Configure and return CrewAI LLM instance backed by Google Gemini."""
    from crewai import LLM

    key = get_api_key()
    model = get_model_name()

    # In CrewAI, Google Gemini models use the 'gemini/' prefix with LiteLLM
    # LiteLLM accepts api_key and GEMINI_API_KEY in environment
    if key:
        os.environ["GEMINI_API_KEY"] = key

    # Format model string for CrewAI / LiteLLM (e.g. gemini/gemini-3.8-flash or gemini/gemini-1.5-flash)
    llm_model_name = f"gemini/{model}" if not model.startswith("gemini/") else model
    return LLM(
        model=llm_model_name,
        api_key=key or "missing_key",
        temperature=0.2,
    )
