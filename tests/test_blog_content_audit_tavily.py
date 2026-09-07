"""Tests for Tavily gap matrix helpers."""

from app.services.blog_content_audit_tavily import (
    _topic_coverage,
    count_missing_gaps,
    top_missing_topics,
)


def test_topic_coverage_missing():
    assert _topic_coverage("early life and education", "career overview", "career awards") == "missing"


def test_topic_coverage_covered():
    text = "early life and education background in detail"
    assert _topic_coverage("early life and education", text, text) == "covered"


def test_count_missing_gaps():
    matrix = [
        {"topic": "A", "status": "missing"},
        {"topic": "B", "status": "covered"},
        {"topic": "C", "status": "missing"},
    ]
    assert count_missing_gaps(matrix) == 2
    assert top_missing_topics(matrix) == ["A", "C"]
