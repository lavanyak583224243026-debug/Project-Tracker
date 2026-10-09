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

DEFAULT_MODEL = "gemini-3.8-flash"


def get_api_key() -> str:
    """Return GEMINI_API_KEY from environment or .env, without exposing it."""
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key in ("your_gemini_api_key_here", "YOUR_GEMINI_API_KEY_HERE", ""):
        return ""
    return key


def get_model_name() -> str:
    """Return configured model name (e.g. gemini-3.8-flash)."""
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def get_genai_client():
    """Create and return google-genai Client."""
    from google import genai

    key = get_api_key()
    if not key:
        raise ValueError("GEMINI_API_KEY is not configured or is a placeholder.")
    return genai.Client(api_key=key)


def check_gemini_availability() -> Tuple[bool, str]:
    """Check whether Gemini is reachable and configured without leaking credentials."""
    key = get_api_key()
    if not key:
        return False, "GEMINI_API_KEY is missing or configured with a placeholder."

    import time
    from google import genai

    model = get_model_name()

    for attempt in range(2):
        try:
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=model,
                contents="ping",
            )
            if response and response.text:
                return True, f"Gemini API available ({model})"
            return False, "Gemini API returned an empty response."
        except Exception as ex:
            if attempt == 0 and ("503" in str(ex) or "UNAVAILABLE" in str(ex)):
                time.sleep(1.5)
                continue
            err_msg = str(ex)
            if "401" in err_msg or "UNAUTHENTICATED" in err_msg:
                return False, "Gemini Authentication failed (Invalid API key)."
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                return False, "Gemini Rate limit / Quota exceeded (HTTP 429)."
            if "503" in err_msg or "UNAVAILABLE" in err_msg:
                return False, "Gemini Service temporarily unavailable (HTTP 503)."
            if "404" in err_msg or "NOT_FOUND" in err_msg:
                return False, f"Gemini Model '{get_model_name()}' not found or unsupported."
            return False, f"Gemini API connection error: {type(ex).__name__}"


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
    """Call Google Gemini API using google-genai SDK with comprehensive error handling."""
    key = get_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing. Please configure it in .env.")

    from google import genai
    from google.genai import errors

    client = genai.Client(api_key=key)
    model = get_model_name()

    config = {}
    if system_instruction:
        config["system_instruction"] = system_instruction

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config if config else None,
        )
        if not response or not response.text:
            raise RuntimeError("Gemini returned an empty response.")
        return response.text
    except errors.ClientError as ce:
        err_str = str(ce)
        if "401" in err_str or "UNAUTHENTICATED" in err_str:
            raise RuntimeError("Gemini Authentication Error (401): Check your GEMINI_API_KEY.")
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            raise RuntimeError("Gemini Quota Exceeded (429): Rate limit reached. Try again later.")
        if "404" in err_str:
            raise RuntimeError(f"Gemini Model Not Found (404): '{model}' is not available.")
        raise RuntimeError(f"Gemini Client Error: {ce.code} {ce.message}")
    except errors.ServerError as se:
        raise RuntimeError(f"Gemini Server Error (503): Service temporarily unavailable. {se.message}")
    except Exception as ex:
        raise RuntimeError(f"Gemini Error: {type(ex).__name__} - {str(ex)}")


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
