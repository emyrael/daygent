"""LangGraph retrieve/generate flow used as a domain lineage fixture."""

from langgraph.graph import StateGraph


def retrieve(state: dict) -> dict:
    """LangGraph retrieve handler."""
    return state


def generate(state: dict) -> dict:
    """LangGraph generate handler."""
    return state


def unused_router(state: dict) -> str:
    """Local helper that is not a graph node action."""
    return "generate"


graph = StateGraph(dict)
graph.add_node("retrieve", retrieve)
graph.add_node("generate", generate)
graph.add_edge("retrieve", "generate")
