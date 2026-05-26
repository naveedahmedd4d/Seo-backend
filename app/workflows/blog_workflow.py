from typing import TypedDict, Annotated, Optional
from langgraph.graph import StateGraph, END
import operator


class BlogState(TypedDict):
    """State for blog generation workflow"""
    topic: str
    keywords: list
    outline: str
    content: str
    images: list
    formatted_content: str
    wordpress_post_id: Optional[int]
    status: str
    error: str


def research_keywords_node(state: BlogState) -> BlogState:
    """Research keywords for the blog topic"""
    return {
        **state,
        "status": "researching_keywords"
    }


def generate_outline_node(state: BlogState) -> BlogState:
    """Generate blog outline"""
    return {
        **state,
        "status": "generating_outline"
    }


def generate_content_node(state: BlogState) -> BlogState:
    """Generate blog content"""
    return {
        **state,
        "status": "generating_content"
    }


def fetch_images_node(state: BlogState) -> BlogState:
    """Fetch relevant images"""
    return {
        **state,
        "status": "fetching_images"
    }


def format_post_node(state: BlogState) -> BlogState:
    """Format the blog post"""
    return {
        **state,
        "status": "formatting"
    }


def publish_to_wordpress_node(state: BlogState) -> BlogState:
    """Publish to WordPress"""
    return {
        **state,
        "status": "publishing"
    }


def create_blog_workflow():
    """Create the blog generation workflow graph"""
    workflow = StateGraph(BlogState)
    
    workflow.add_node("research", research_keywords_node)
    workflow.add_node("outline", generate_outline_node)
    workflow.add_node("content", generate_content_node)
    workflow.add_node("images", fetch_images_node)
    workflow.add_node("format", format_post_node)
    workflow.add_node("publish", publish_to_wordpress_node)
    
    workflow.set_entry_point("research")
    workflow.add_edge("research", "outline")
    workflow.add_edge("outline", "content")
    workflow.add_edge("content", "images")
    workflow.add_edge("images", "format")
    workflow.add_edge("format", "publish")
    workflow.add_edge("publish", END)
    
    return workflow.compile()

