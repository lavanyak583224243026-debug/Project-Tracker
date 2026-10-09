"""Optional live Gemini API smoke test.

Skipped automatically unless a valid live GEMINI_API_KEY is configured.
"""
import os
import pytest
from services.gemini_service import check_gemini_availability, generate_gemini_content, get_api_key

HAS_KEY = bool(get_api_key())
RUN_LIVE = os.getenv("RUN_LIVE_TESTS", "").lower() in ("1", "true", "yes")


@pytest.mark.skipif(not (HAS_KEY and RUN_LIVE), reason="Optional live Gemini API test. Enable with RUN_LIVE_TESTS=1.")
def test_live_gemini_smoke():
    """Verify live connectivity to configured Gemini model."""
    is_available, status_msg = check_gemini_availability()
    if not is_available and "429" in status_msg:
        pytest.skip(f"Live Gemini quota exceeded: {status_msg}")
    assert is_available is True, f"Live Gemini check failed: {status_msg}"

    response = generate_gemini_content("Respond with the exact word: 'READY'")
    assert "READY" in response.upper()
