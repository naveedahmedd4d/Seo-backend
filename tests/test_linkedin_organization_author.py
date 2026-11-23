"""LinkedIn Company Page author resolution helpers."""

from app.services.linkedin_service import (
    organization_id_from_urn,
    organization_urn_from_id,
    resolve_linkedin_author_urn,
)


def test_organization_urn_helpers():
    assert organization_urn_from_id("5515715") == "urn:li:organization:5515715"
    assert organization_urn_from_id("urn:li:organization:5515715") == "urn:li:organization:5515715"
    assert organization_id_from_urn("urn:li:organization:5515715") == "5515715"


def test_resolve_author_prefers_default_organization():
    metadata = {
        "person_urn": "urn:li:person:abc",
        "post_as": "organization",
        "default_organization_id": "5515715",
    }
    assert resolve_linkedin_author_urn(metadata) == "urn:li:organization:5515715"


def test_resolve_author_person_override():
    metadata = {
        "person_urn": "urn:li:person:abc",
        "post_as": "organization",
        "default_organization_id": "5515715",
    }
    assert (
        resolve_linkedin_author_urn(metadata, organization_id="person") == "urn:li:person:abc"
    )


def test_resolve_author_falls_back_to_person():
    metadata = {"person_urn": "urn:li:person:abc", "post_as": "person"}
    assert resolve_linkedin_author_urn(metadata) == "urn:li:person:abc"
