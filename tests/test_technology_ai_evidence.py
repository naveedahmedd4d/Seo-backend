import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.technology_ai_content import (
    TECHNOLOGY_AI_CATEGORY, collect_evidence, evidence_context, draft_issues,
    generate_article, safe_source_url,
    render_source_markers, reviewed_claim_issues,
)
from app.services.blog_consistency import sanitize_blog_body
from app.services.technology_ai_publication import reader_content, clean_article_fields
from app.services.research_context import format_research_context


URL = "https://example.org/research"
BODY = "## Choose a workflow\n\nMeasure baseline latency and compare errors. [Study](https://example.org/research)."
META = {"title": "Choosing a Workflow", "meta_description": "Compare a baseline with measured outcomes.",
        "faqs": [], "related_queries": []}


def config(**changes):
    return SimpleNamespace(**{
        "target_word_count": 100, "min_words": 10, "max_tokens": 1000,
        "temperature": 0.4, "custom_style_notes": "", "faq_count": 3,
        "related_query_count": 2, **changes,
    })


def research():
    return {"answer": "UNVERIFIED generated summary with 99% adoption", "results": [
        {"title": "Study", "url": URL, "published_date": "2026-01-02",
         "content": "Navigation and cookie preferences.\n\nWorkflow latency evaluation requires baseline measurements."}
    ]}


def test_evidence_keeps_provenance_and_excludes_generated_answer():
    sources = collect_evidence(research(), "workflow latency")
    context = evidence_context(sources)
    assert URL in context
    assert "2026-01-02" in context
    assert "Workflow latency evaluation" in context
    assert "UNVERIFIED" not in context
    assert sources[0]["retrieved_at"]
    assert collect_evidence(research(), "workflow", sources) == sources


def test_legacy_formatter_is_unchanged():
    context = format_research_context(research())
    assert "UNVERIFIED" in context
    assert URL not in context


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://user:secret@example.org/", "https://example.org/a\nother", "not-a-url"])
def test_invalid_evidence_urls_are_rejected(url):
    assert safe_source_url(url) == ""
    assert collect_evidence({"results": [{"url": url, "content": "Evidence"}]}, "AI") == []


def test_sources_and_context_are_bounded_and_malformed_results_ignored():
    sources = collect_evidence({"results": [None] + [
        {"url": f"https://example.org/{i}", "content": "AI workflows. " * 1500}
        for i in range(20)
    ]}, "AI workflows")
    assert 0 < len(sources) <= 8
    assert len(evidence_context(sources)) <= 18000
    assert collect_evidence({"results": "invalid"}, "AI") == []


@pytest.mark.parametrize("category", [None, "Sports", "News & Politics", "Celebrities & Public Figures", "Finance & Economy"])
def test_other_categories_still_strip_external_links(category):
    assert URL not in sanitize_blog_body(BODY, category=category)


def test_technology_citations_and_toc_are_removed_from_public_content():
    body = "## Table of Contents\n\n- [Section](#section)\n\n" + BODY
    cleaned = sanitize_blog_body(body, category=TECHNOLOGY_AI_CATEGORY)
    assert URL not in cleaned
    assert "Study" not in cleaned
    assert "Table of Contents" not in cleaned
    assert sanitize_blog_body(cleaned, category=TECHNOLOGY_AI_CATEGORY) == cleaned


@pytest.mark.asyncio
async def test_pipeline_reviews_body_then_generates_metadata():
    context = evidence_context(collect_evidence(research(), "workflow"))
    generator = AsyncMock(side_effect=["Reader: CTO; compare workflows", BODY,
        json.dumps({"body_content": BODY, "issues": [], "claims": [
            {"claim": "Measure baseline", "source_url": URL, "status": "supported",
             "evidence_quote": "Workflow latency evaluation requires baseline measurements."}]}), json.dumps(META)])
    result = await generate_article("workflow", "CTO", [], context, config(), generator, AsyncMock())
    assert result["quality_review"]["status"] == "ready_for_review"
    assert result["quality_review"]["review_completed"] is True
    assert result["quality_review"]["body_fingerprint"]
    assert BODY in generator.await_args_list[-1].args[0]
    assert "CTO" in generator.await_args_list[1].args[0]
    assert URL not in result["body_content"]
    assert URL in result["quality_review"]["evidence_draft"]
    assert result["quality_review"]["claims"][0]["source_url"] == URL


@pytest.mark.asyncio
@pytest.mark.parametrize("review", ["not json", json.dumps({"body_content": "Short", "issues": [], "claims": []}),
    json.dumps({"body_content": BODY, "issues": [], "claims": [{"claim": "invalid"}]})])
async def test_bad_review_cannot_silently_pass(review):
    generator = AsyncMock(side_effect=["Plan", BODY, review, json.dumps(META)])
    result = await generate_article("workflow", None, [], URL, config(), generator, AsyncMock())
    assert result["body_content"] == reader_content(BODY)
    assert result["quality_review"]["status"] == "needs_revision"
    assert result["quality_review"]["review_completed"] is False


@pytest.mark.asyncio
async def test_unverified_claim_and_invented_citation_are_flagged():
    generator = AsyncMock(side_effect=["Plan", BODY, json.dumps({
        "body_content": BODY, "issues": [], "claims": [
            {"claim": "ROI 5.8x", "source_url": "https://invented.org/", "status": "supported"},
            {"claim": "Safety improves 25%", "source_url": "", "status": "unverified"},
        ]}), "invalid repair", json.dumps(META)])
    result = await generate_article("workflow", None, [], URL, config(), generator, AsyncMock())
    assert result["quality_review"]["status"] == "needs_revision"
    assert any("ROI 5.8x" in w for w in result["quality_review"]["warnings"])
    assert any("Safety improves" in w for w in result["quality_review"]["warnings"])


@pytest.mark.asyncio
async def test_no_research_and_metadata_failure_remain_visible():
    generator = AsyncMock(side_effect=[RuntimeError("planner unavailable"), BODY,
        RuntimeError("review unavailable"), "invalid JSON"])
    result = await generate_article("workflow", None, [], None, config(), generator, AsyncMock())
    assert result["faqs"] == []
    assert result["meta_description"] == ""
    assert result["quality_review"]["status"] == "needs_revision"
    assert any("No traceable" in w for w in result["quality_review"]["warnings"])
    assert any("Metadata" in w for w in result["quality_review"]["warnings"])


def test_deterministic_review_flags_unknown_links_and_short_body():
    issues = draft_issues(BODY.replace(URL, "https://unknown.org"), URL, 1000)
    assert any("outside" in issue for issue in issues)
    assert any("minimum" in issue for issue in issues)


@pytest.mark.asyncio
async def test_zero_faq_setting_is_respected():
    generator = AsyncMock(side_effect=["Plan", BODY,
        json.dumps({"body_content": BODY, "issues": [], "claims": []}),
        json.dumps({**META, "faqs": [{"question": "Why?", "answer": "Because."}]})])
    result = await generate_article("workflow", None, [], URL, config(faq_count=0), generator, AsyncMock())
    assert result["faqs"] == []


def test_api_response_hides_technology_citations_without_mutating_private_evidence():
    from app.api.blog import _state_data_for_api
    state = {"category": TECHNOLOGY_AI_CATEGORY, "body_content": BODY}
    assert URL not in _state_data_for_api(state)["body_content"]
    assert state["body_content"] == BODY
    assert URL not in _state_data_for_api({**state, "category": "Sports"})["body_content"]


@pytest.mark.asyncio
async def test_save_removes_citations_and_invalidates_old_review():
    from app.api.blog import update_blog_workflow
    from app.models.workflow_state import WorkflowStatus
    from app.schemas.blog import BlogUpdateRequest
    workflow = MagicMock()
    workflow.status = WorkflowStatus.COMPLETED
    workflow.state_data = {"category": TECHNOLOGY_AI_CATEGORY, "research_context": URL,
                           "quality_review": {"status": "ready_for_review", "claims": ["stale"]}}
    workflow.id = 1
    workflow.error_message = None
    workflow.created_at = workflow.updated_at = __import__("datetime").datetime.now()
    request = BlogUpdateRequest(title="Edited", meta_description="Description", body_content=BODY,
                                faqs=[], related_queries=[])
    with patch("app.api.blog.get_workflow_state", return_value=workflow), patch("app.api.blog.flag_modified"):
        await update_blog_workflow(1, request, SimpleNamespace(id=1), MagicMock())
    assert URL not in workflow.state_data["body_content"]
    assert not any("add supporting links" in warning for warning in workflow.state_data["quality_review"]["warnings"])
    assert workflow.state_data["quality_review"]["status"] == "needs_review"
    assert workflow.state_data["quality_review"]["claims"] == []


@pytest.mark.asyncio
async def test_research_assessment_failure_is_not_sufficient_for_technology():
    from app.services.openrouter_service import assess_research_sufficiency
    with patch("app.services.openrouter_service.generate_blog_seo_content", new=AsyncMock(return_value="invalid")):
        enough, queries = await assess_research_sufficiency("AI automation", URL, TECHNOLOGY_AI_CATEGORY)
        assert enough is False and queries
        assert await assess_research_sufficiency("Sport", URL, "Sports") == (True, [])


@pytest.mark.asyncio
@pytest.mark.parametrize("has_key,research_error", [(False, False), (True, True), (True, False)])
async def test_workflow_persists_sources_and_exposes_research_failures(has_key, research_error):
    from app.api.blog import run_blog_workflow
    from app.models.workflow_state import WorkflowStatus
    workflow = MagicMock(state_data={})
    batch = {**META, "body_content": BODY, "quality_review": {
        "status": "ready_for_review", "warnings": [], "review_completed": True}}
    with (
        patch("app.api.blog.get_workflow_state", return_value=workflow),
        patch("app.api.blog.resolve_llm_credentials", return_value=SimpleNamespace()),
        patch("app.api.blog.set_llm_context", return_value=object()),
        patch("app.api.blog.reset_llm_context"),
        patch("app.services.llm_settings_service.resolve_api_key_optional", return_value="test-key" if has_key else None),
        patch("app.api.blog.research_keywords", new=AsyncMock(side_effect=RuntimeError("unavailable")) if research_error else AsyncMock(return_value=research())),
        patch("app.api.blog.assess_research_sufficiency", new=AsyncMock(return_value=(True, []))),
        patch("app.api.blog._resolve_workflow_blog_config", return_value=None),
        patch("app.api.blog.generate_single_blog_batch", new=AsyncMock(return_value=batch)),
    ):
        await run_blog_workflow(MagicMock(), 1, "AI workflow", None, [], TECHNOLOGY_AI_CATEGORY, 1, True)
    assert workflow.status == WorkflowStatus.COMPLETED
    assert URL not in workflow.state_data["body_content"]
    if has_key and not research_error:
        assert workflow.state_data["research_sources"][0]["url"] == URL
        assert URL in workflow.state_data["research_context"]
    else:
        assert workflow.state_data["quality_review"]["status"] == "needs_revision"
        assert workflow.state_data["quality_review"]["warnings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("html", [False, True])
@pytest.mark.parametrize("category", [TECHNOLOGY_AI_CATEGORY, "Sports"])
async def test_publish_removes_technology_citations_and_keeps_category_specific_cta(html, category):
    from app.api.blog import publish_blog
    from app.models.workflow_state import WorkflowStatus
    from app.schemas.blog import BlogPublishRequest
    workflow = MagicMock(status=WorkflowStatus.COMPLETED, state_data={
        "category": category, "body_content": BODY, "title": "Workflow",
        "faqs": [{"question": "Why?", "answer": BODY}],
        "related_queries": [{"query": "How?", "answer": BODY}]})
    site = SimpleNamespace(url="https://publisher.org", id=1)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    publisher = AsyncMock(return_value={"success": True, "post_id": 2, "link": "https://publisher.org/post", "message": "Published"})
    request = BlogPublishRequest(workflow_id=1, wordpress_site_id=1, source_category=category,
        body_content_html=f'<p>Source <a href="{URL}">Study</a></p>' if html else None)
    with (
        patch("app.api.blog.get_workflow_state", return_value=workflow),
        patch("app.api.blog.get_wordpress_site", return_value=site),
        patch("app.api.blog._resolve_publish_category_ids", new=AsyncMock(return_value=[])),
        patch("app.api.blog.publish_to_wordpress", new=publisher),
    ):
        await publish_blog(request, SimpleNamespace(id=1), db)
    sent = publisher.await_args.args[2]
    if category == TECHNOLOGY_AI_CATEGORY:
        assert URL not in sent
        assert "Study" not in sent
        assert "Frequently Asked Questions" in sent
        assert "Related Search Queries" in sent
        assert "For more insights" not in sent
    else:
        assert "For more insights" in sent
        if not html:
            assert URL not in sent.split("## Frequently Asked Questions")[0]


def test_wordpress_markdown_conversion_excludes_citations():
    from app.services.wordpress_service import _markdown_to_html
    html = _markdown_to_html(sanitize_blog_body(BODY, category=TECHNOLOGY_AI_CATEGORY))
    assert URL not in html


@pytest.mark.asyncio
async def test_metadata_cannot_silently_introduce_new_statistics():
    generator = AsyncMock(side_effect=["Plan", BODY,
        json.dumps({"body_content": BODY, "issues": [], "claims": []}),
        json.dumps({**META, "faqs": [{"question": "ROI?", "answer": "Returns are 580%."}]})])
    result = await generate_article("workflow", None, [], URL, config(), generator, AsyncMock())
    assert result["quality_review"]["status"] == "needs_revision"
    assert any("numerical claims" in w for w in result["quality_review"]["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["Sports", "Finance & Economy", "News & Politics", "Lifestyle & Misc"])
async def test_other_categories_keep_two_call_batch_generation(category):
    from app.services.openrouter_service import generate_single_blog_batch
    generator = AsyncMock(side_effect=[json.dumps(META), "Legacy body"])
    with patch("app.services.openrouter_service.generate_blog_seo_content", new=generator), \
         patch("app.services.openrouter_service._blog_delay", new=AsyncMock()):
        result = await generate_single_blog_batch("Topic", None, [], category, "Research")
    assert generator.await_count == 2
    assert result["body_content"] == "Legacy body"
    assert "quality_review" not in result


def test_source_markers_resolve_only_against_known_evidence():
    context = evidence_context(collect_evidence(research(), "workflow"))
    result = render_source_markers("A finding [S1]. Unknown [S9]. Existing [S1](https://example.org/a).", context)
    assert f"[Study]({URL})" in result
    assert "[S9]" in result
    assert "[S1](https://example.org/a)" in result
    assert any("unknown source" in warning for warning in draft_issues(result, context))


def test_real_url_does_not_validate_invented_quote_or_lost_survey_scope():
    context = evidence_context(collect_evidence({"results": [{"url": URL, "title": "Survey",
        "content": "In 2025, 88 percent of survey respondents reported AI use."}]}, "AI survey"))
    claim = {"claim": "88% use AI", "source_url": URL, "status": "supported", "evidence_quote": "Invented supporting text"}
    assert reviewed_claim_issues([claim], BODY, context)
    claim["evidence_quote"] = "In 2025, 88 percent of survey respondents reported AI use."
    assert any("scope" in warning for warning in reviewed_claim_issues([claim], f"88% of enterprises use AI [Survey]({URL}).", context))


@pytest.mark.asyncio
async def test_one_targeted_repair_can_resolve_missing_citations():
    context = evidence_context(collect_evidence(research(), "workflow"))
    uncited = BODY.replace(f"[Study]({URL})", "Study")
    generator = AsyncMock(side_effect=["Plan", uncited,
        json.dumps({"body_content": uncited, "issues": [], "claims": []}),
        json.dumps({"body_content": BODY, "issues": [], "claims": []}), json.dumps(META)])
    result = await generate_article("workflow", None, [], context, config(), generator, AsyncMock())
    assert generator.await_count == 5
    assert result["body_content"] == reader_content(BODY)
    assert result["quality_review"]["status"] == "ready_for_review"


def test_editorial_gate_flags_unfinished_artifact_and_generic_opening():
    body = "In the realm of business, AI matters.\n\n## Decision aid\n\nCreate a flowchart to compare options."
    issues = draft_issues(body, None)
    assert any("still an instruction" in warning for warning in issues)
    assert any("scene-setting" in warning for warning in issues)


@pytest.mark.parametrize("citation", [
    "[6](https://competitor.com/post)", "[S1]", "[6]", "[2, 6]",
    "[Competitor](//competitor.com/post)",
    '<a href="https://competitor.com/post">Competitor</a>',
    "<a href='https://competitor.com/post'>Competitor</a>",
    "<https://competitor.com/post>",
    "[Competitor][ref]\n\n[ref]: https://competitor.com/post",
])
def test_public_copy_removes_citation_formats(citation):
    cleaned = reader_content(f"## Plan\n\nMeasure errors {citation}.")
    assert "competitor" not in cleaned.lower()
    assert "[6]" not in cleaned and "[S1]" not in cleaned
    assert "Measure errors" in cleaned
    assert reader_content(cleaned) == cleaned


def test_public_copy_preserves_media_local_links_and_code_indentation():
    text = ('[Guide](/guides/ai) [Section](#plan)\n\n'
            '![Diagram](https://cdn.example.com/chart.png)\n'
            '<img src="https://cdn.example.com/chart.png" alt="Diagram">\n'
            '```python\n    run()\n```')
    assert reader_content(text) == text
    own = '<a href="https://publisher.org/guide">Guide</a>'
    assert reader_content(own, "https://publisher.org") == own
    assert reader_content('[Competitor](https://publisher.org.evil/)', "https://publisher.org") == ""


def test_public_sections_cleaned_without_exposing_or_mutating_private_sources():
    from app.api.blog import _state_data_for_api
    state = {"category": TECHNOLOGY_AI_CATEGORY, "body_content": BODY,
             "faqs": [{"question": "Why?", "answer": BODY}],
             "related_queries": [{"query": "How?", "answer": BODY}],
             "research_sources": [{"url": URL}],
             "quality_review": {"evidence_draft": BODY}}
    response = _state_data_for_api(state)
    assert URL not in response["faqs"][0]["answer"]
    assert URL not in response["related_queries"][0]["answer"]
    assert response["research_sources"] == state["research_sources"]
    assert response["quality_review"]["evidence_draft"] == BODY
    assert state["faqs"][0]["answer"] == BODY
    assert reader_content("## Plan\n\nDo this.\n\n## Sources\n\nCompetitor\n\n## Next steps\n\nStart small.") == "## Plan\n\nDo this.\n\n## Next steps\n\nStart small."
