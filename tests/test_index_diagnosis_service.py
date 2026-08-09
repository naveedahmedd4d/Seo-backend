"""Unit tests for index diagnosis service."""

from app.services.index_diagnosis_service import (
    _extract_json,
    _fallback_diagnosis,
    _normalize_diagnosis,
)


def test_extract_json_from_codeblock():
    raw = '```json\n{"root_cause": "thin_content", "confidence": 0.8}\n```'
    parsed = _extract_json(raw)
    assert parsed is not None
    assert parsed["root_cause"] == "thin_content"


def test_fallback_diagnosis_noindex():
    result = _fallback_diagnosis(
        {"coverage_state": "Crawled - currently not indexed"},
        {"has_noindex": True, "word_count": 500, "status_code": 200},
    )
    assert result["root_cause"] == "noindex"


def test_normalize_diagnosis_unknown_root_cause():
    result = _normalize_diagnosis({
        "root_cause": "something_else",
        "confidence": 2.5,
        "explanation": "Test",
        "recommended_fixes": [],
    })
    assert result["root_cause"] == "unknown"
    assert result["confidence"] == 1.0
