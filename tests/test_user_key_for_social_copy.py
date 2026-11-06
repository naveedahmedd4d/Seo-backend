"""The social copy generators must use the user's saved key, not the env var.

A user who saves a Groq/Gemini/OpenRouter key in Settings got
"Set GROQ_API_KEY in the backend .env file" -- the generators guarded on the
process-wide env var while the call they then made honoured the per-user
credentials. The guard rejected a request the call could have served.
"""

import pytest

from app.services import openrouter_service as svc
from app.services.llm_settings_service import LlmCredentials

ITEMS = [{"title": "T", "link": "l", "source": "s", "snippet": "sn", "date": "d"}]


@pytest.fixture
def no_env_key(monkeypatch):
    """Simulate production: nothing in .env, everything in the DB."""
    monkeypatch.setattr(svc.settings, "groq_api_key", "", raising=False)
    return monkeypatch


@pytest.fixture
def user_creds():
    return LlmCredentials(provider="groq", api_key="gsk-user-saved", model="openai/gpt-oss-20b", source="user")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn,kwargs",
    [
        ("generate_news_headline", {}),
        ("generate_social_post_description", {"headline": "H", "query": "q"}),
        ("generate_social_post_story_description", {"headline": "H", "query": "q"}),
        ("generate_social_post_hashtags", {"headline": "H", "query": "q", "count": 5}),
    ],
)
async def test_generator_uses_saved_key_when_env_is_empty(
    fn, kwargs, no_env_key, user_creds, monkeypatch
):
    seen = {}

    async def fake_generate_content(prompt, **kw):
        seen["called"] = True
        return "Line one\nLine two"

    monkeypatch.setattr(svc, "generate_content", fake_generate_content)
    token = svc.set_llm_context(user_creds)
    try:
        await getattr(svc, fn)(ITEMS, **kwargs)
    finally:
        svc.reset_llm_context(token)
    assert seen.get("called"), f"{fn} refused a request its own call could serve"


@pytest.mark.asyncio
async def test_still_errors_when_there_is_no_key_anywhere(no_env_key, monkeypatch):
    """No saved key and no env key is a real failure -- keep raising."""
    async def fake_generate_content(prompt, **kw):
        raise ValueError("Add your Groq API key in Settings")

    monkeypatch.setattr(svc, "generate_content", fake_generate_content)
    with pytest.raises(ValueError):
        await svc.generate_news_headline(ITEMS)
