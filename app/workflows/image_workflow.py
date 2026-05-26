from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
import operator


class ImageState(TypedDict):
    """State for image fetching workflow"""
    keyword: str
    images: list
    filtered_images: list
    status: str
    error: str


def search_images_node(state: ImageState) -> ImageState:
    """Search images from SerpAPI"""
    return {
        **state,
        "status": "searching"
    }


def filter_results_node(state: ImageState) -> ImageState:
    """Filter image results"""
    return {
        **state,
        "status": "filtering"
    }


def store_urls_node(state: ImageState) -> ImageState:
    """Store image URLs"""
    return {
        **state,
        "status": "completed"
    }


def create_image_workflow():
    """Create the image fetching workflow graph"""
    workflow = StateGraph(ImageState)
    
    workflow.add_node("search", search_images_node)
    workflow.add_node("filter", filter_results_node)
    workflow.add_node("store", store_urls_node)
    
    workflow.set_entry_point("search")
    workflow.add_edge("search", "filter")
    workflow.add_edge("filter", "store")
    workflow.add_edge("store", END)
    
    return workflow.compile()

