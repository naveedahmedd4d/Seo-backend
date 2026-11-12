"""Claude LLM relay helpers."""

import pytest

from app.services.claude_llm_service import (
    DEFAULT_BASE_URL,
    _extract_text,
    _job_done,
    _job_failed,
    _safe_job_id,
    normalize_base_url,
    normalize_timeout_ms,
)


def test_normalize_base_url_strips_path():
    assert normalize_base_url("https://claude-llm.vercel.app/api/v1/run") == DEFAULT_BASE_URL
    assert normalize_base_url("https://claude-llm.vercel.app/") == DEFAULT_BASE_URL
    assert normalize_base_url(None) == DEFAULT_BASE_URL


def test_normalize_base_url_rejects_ssrf_targets():
    with pytest.raises(ValueError):
        normalize_base_url("http://claude-llm.vercel.app")
    with pytest.raises(ValueError):
        normalize_base_url("https://localhost")
    with pytest.raises(ValueError):
        normalize_base_url("https://127.0.0.1")
    with pytest.raises(ValueError):
        normalize_base_url("https://169.254.169.254")
    with pytest.raises(ValueError):
        normalize_base_url("https://10.0.0.1")
    with pytest.raises(ValueError):
        normalize_base_url("not-a-url")


def test_normalize_timeout_ms():
    assert normalize_timeout_ms(None) == 120_000
    assert normalize_timeout_ms(60_000) == 60_000
    with pytest.raises(ValueError):
        normalize_timeout_ms(100)
    with pytest.raises(ValueError):
        normalize_timeout_ms(1_000_000)


def test_extract_text_from_greeting_result():
    assert _extract_text({"greeting": "Hello, hope you are having a wonderful day!"}) == (
        "Hello, hope you are having a wonderful day!"
    )
    assert _extract_text("plain") == "plain"
    assert _extract_text({"text": "ok"}) == "ok"
    # Do not treat top-level error-like shapes as content.
    assert _extract_text({"message": "Rate limited"}) is None


def test_job_done_and_failed():
    assert _job_done({"result": {"greeting": "hi"}, "error": None})
    assert _job_done({"status": "DONE"})
    assert _job_done({"status": "SUCCESS", "result": {"text": "x"}})
    assert not _job_done({"status": "RUNNING", "job_id": "x"})
    assert not _job_done({"status": "RUNNING", "result": {"partial": True}})
    assert _job_failed({"error": "boom"}) == "boom"
    assert _job_failed({"status": "FAILED"}) == "Claude LLM job failed"


def test_safe_job_id():
    assert _safe_job_id("cmwdazbpl0002l8084y5mkk6f") == "cmwdazbpl0002l8084y5mkk6f"
    assert _safe_job_id("../secret") is None
    assert _safe_job_id(123) is None
