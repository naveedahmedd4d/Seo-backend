from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage
import operator

class TrendingState(TypedDict):
    """State for trending keywords workflow"""
    keywords: Annotated[list, operator.add]
    status: str
    error: str


def fetch_keywords_node(state: TrendingState) -> TrendingState:
    """Fetch trending keywords from SerpAPI"""
    # This will be called by the service with database session
    return {
        **state,
        "status": "fetching"
    }


def process_data_node(state: TrendingState) -> TrendingState:
    """Process and validate keyword data"""
    return {
        **state,
        "status": "processing"
    }


def store_results_node(state: TrendingState) -> TrendingState:
    """Store results in database"""
    return {
        **state,
        "status": "completed"
    }


def create_trending_workflow():
    """Create the trending keywords workflow graph"""
    workflow = StateGraph(TrendingState)
    
    workflow.add_node("fetch", fetch_keywords_node)
    workflow.add_node("process", process_data_node)
    workflow.add_node("store", store_results_node)
    
    workflow.set_entry_point("fetch")
    workflow.add_edge("fetch", "process")
    workflow.add_edge("process", "store")
    workflow.add_edge("store", END)
    
    return workflow.compile()

