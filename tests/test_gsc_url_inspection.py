"""Unit tests for GSC URL Inspection helpers."""

import pytest

from app.services.gsc_url_inspection_service import (
    is_not_indexed,
    parse_inspection_response,
)


def test_parse_inspection_response_extracts_fields():
    payload = {
        "inspectionResult": {
            "indexStatusResult": {
                "verdict": "FAIL",
                "coverageState": "Crawled - currently not indexed",
                "indexingState": "INDEXING_ALLOWED",
                "robotsTxtState": "ALLOWED",
                "pageFetchState": "SUCCESSFUL",
                "googleCanonical": "https://example.com/post/",
                "userCanonical": "https://example.com/post/",
                "lastCrawlTime": "2026-04-01T12:00:00Z",
            }
        }
    }
    result = parse_inspection_response(payload)
    assert result["verdict"] == "FAIL"
    assert result["coverage_state"] == "Crawled - currently not indexed"
    assert result["indexing_state"] == "INDEXING_ALLOWED"
    assert result["last_crawl_time"] is not None


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"verdict": "FAIL"}, True),
        ({"verdict": "PASS", "coverage_state": "Submitted and indexed"}, False),
        (
            {"verdict": "PASS", "coverage_state": "Crawled - currently not indexed"},
            True,
        ),
        (
            {"verdict": "NEUTRAL", "coverage_state": "URL is not on Google"},
            True,
        ),
        (
            {"verdict": "PASS", "indexing_state": "BLOCKED_BY_META_TAG"},
            True,
        ),
    ],
)
def test_is_not_indexed(result, expected):
    assert is_not_indexed(result) is expected
