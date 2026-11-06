"""Live vendor catalog: /api/settings/llm/models is sourced from the vendors."""

import time

import pytest

from app.services import llm_catalog_service as cat


@pytest.fixture(autouse=True)
def _clear_cache():
    cat.clear_cache()
    yield
    cat.clear_cache()


def test_parses_openai_shaped_list():
    body = {"data": [{"id": "model-a"}, {"id": "model-b"}]}
    assert cat.parse_models("groq", body) == ["model-a", "model-b"]


def test_parses_gemini_shaped_list():
    body = {
        "models": [
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]
    }
    # embedding-only models cannot generate a blog, so they are filtered out
    assert cat.parse_models("gemini", body) == ["gemini-3.8-flash"]


def test_parses_cohere_shaped_list():
    body = {"models": [{"name": "command-r-plus-08-2024", "endpoints": ["chat"]},
                       {"name": "embed-v4", "endpoints": ["embed"]}]}
    assert cat.parse_models("cohere", body) == ["command-r-plus-08-2024"]


def test_parses_cloudflare_shaped_list():
    body = {"result": [
        {"name": "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "task": {"name": "Text Generation"}},
        {"name": "@cf/baai/bge-base-en-v1.5", "task": {"name": "Text Embeddings"}},
    ]}
    assert cat.parse_models("cloudflare", body) == ["@cf/meta/llama-3.3-70b-instruct-fp8-fast"]


def test_unknown_shape_yields_empty_not_crash():
    assert cat.parse_models("groq", {"unexpected": True}) == []
    assert cat.parse_models("groq", None) == []


def test_cache_hit_avoids_refetch():
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return ["m1"]

    a = cat.cached("nvidia", fake, ttl=60)
    b = cat.cached("nvidia", fake, ttl=60)
    assert a == b == ["m1"]
    assert calls["n"] == 1, "second call must come from cache"


def test_cache_expires():
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return [f"m{calls['n']}"]

    cat.cached("nvidia", fake, ttl=0)
    time.sleep(0.01)
    cat.cached("nvidia", fake, ttl=0)
    assert calls["n"] == 2
