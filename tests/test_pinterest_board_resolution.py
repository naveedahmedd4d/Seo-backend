"""Tests for Pinterest board resolution during publish."""

from app.services.pinterest_service import _board_id_from_metadata


def test_board_id_from_platform_options():
    assert _board_id_from_metadata({}, {"board_id": "123"}) == "123"


def test_board_id_from_default_metadata():
    assert _board_id_from_metadata({"default_board_id": "456"}, {}) == "456"


def test_board_id_coerces_numeric_default():
    assert _board_id_from_metadata({"default_board_id": 789}, {}) == "789"


def test_board_id_falls_back_to_first_cached_board():
    meta = {"boards": [{"id": "b1", "name": "One"}, {"id": "b2", "name": "Two"}]}
    assert _board_id_from_metadata(meta, {}) == "b1"


def test_board_id_returns_none_when_missing():
    assert _board_id_from_metadata({}, {}) is None
