from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
import operator


class ResearchState(TypedDict):
    """State for research workflow"""
    query: str
    results: dict
    insights: list
    status: str
    error: str


def query_trivly_node(state: ResearchState) -> ResearchState:
    """Query Tavily API"""
    return {
        **state,
        "status": "querying"
    }


def analyze_results_node(state: ResearchState) -> ResearchState:
    """Analyze research results"""
    return {
        **state,
        "status": "analyzing"
    }


def generate_insights_node(state: ResearchState) -> ResearchState:
    """Generate insights from research"""
    return {
        **state,
        "status": "generating_insights"
    }


def store_node(state: ResearchState) -> ResearchState:
    """Store research results"""
    return {
        **state,
        "status": "completed"
    }


def create_research_workflow():
    """Create the research workflow graph"""
    workflow = StateGraph(ResearchState)
    
    workflow.add_node("query", query_trivly_node)
    workflow.add_node("analyze", analyze_results_node)
    workflow.add_node("generate_insights", generate_insights_node)
    workflow.add_node("store", store_node)
    
    workflow.set_entry_point("query")
    workflow.add_edge("query", "analyze")
    workflow.add_edge("analyze", "generate_insights")
    workflow.add_edge("generate_insights", "store")
    workflow.add_edge("store", END)
    
    return workflow.compile()

