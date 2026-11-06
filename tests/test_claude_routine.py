"""Claude Code routine helpers."""

from app.services.claude_routine_service import bearer_matches, is_valid_fire_url, validate_fire_url
import pytest


def test_bearer_matches_constant_time():
    assert bearer_matches("Bearer abc123", "abc123")
    assert bearer_matches("bearer abc123", "abc123")
    assert not bearer_matches("Bearer wrong", "abc123")
    assert not bearer_matches(None, "abc123")


def test_validate_fire_url():
    ok = "https://api.anthropic.com/v1/claude_code/routines/trig_abc/fire"
    assert validate_fire_url(ok) == ok
    assert is_valid_fire_url(ok)
    with pytest.raises(ValueError):
        validate_fire_url("http://api.anthropic.com/v1/claude_code/routines/trig_abc/fire")
    with pytest.raises(ValueError):
        validate_fire_url("https://evil.example/v1/claude_code/routines/trig_abc/fire")
    with pytest.raises(ValueError):
        validate_fire_url("https://api.anthropic.com/v1/messages")
