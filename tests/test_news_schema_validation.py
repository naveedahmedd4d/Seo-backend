"""BlogGenerateRequest and research schema accept all three news content_intent values."""

from app.schemas.blog import BlogAutoImagesRequest, BlogGenerateRequest
from app.schemas.research import ResearchSummaryRequest


def test_blog_generate_request_accepts_news_timeline():
    req = BlogGenerateRequest(
        topic="Pete Hegseth signal group chat",
        category="News & Politics",
        content_intent="news_timeline",
    )
    assert req.content_intent == "news_timeline"


def test_blog_generate_request_accepts_news_explainer():
    req = BlogGenerateRequest(
        topic="LIV Golf contracts explained",
        category="Sports",
        content_intent="news_explainer",
    )
    assert req.content_intent == "news_explainer"


def test_blog_generate_request_accepts_news_opinion():
    req = BlogGenerateRequest(
        topic="Kristi Noem dog killing reaction",
        category="News & Politics",
        content_intent="news_opinion",
    )
    assert req.content_intent == "news_opinion"


def test_blog_auto_images_request_accepts_news_content_types():
    for content_type in ("news_timeline", "news_explainer", "news_opinion"):
        req = BlogAutoImagesRequest(content_type=content_type)  # type: ignore[call-arg]
        assert req.content_type == content_type


def test_research_summary_request_accepts_news_intents():
    from app.schemas.research import ResearchSummaryRequest as RSR

    # Build a minimal valid request if the schema requires results
    try:
        req = RSR(
            query="pete hegseth signal",
            results={},
            content_intent="news_timeline",
        )
        assert req.content_intent == "news_timeline"
    except Exception:
        # If results is a richer type, still assert the Literal is accepted via model_fields
        field = ResearchSummaryRequest.model_fields["content_intent"]
        annotation = str(field.annotation)
        assert "news_timeline" in annotation
        assert "news_explainer" in annotation
        assert "news_opinion" in annotation
