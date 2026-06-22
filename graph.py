from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, START, END

# Import nodes from agent.py
from agent import discovery_node, process_lead_node, summary_node


class PipelineState(TypedDict, total=False):
    """The in-memory State shape representing the workflow's queue and processed logs."""
    leads_to_process: List[dict]
    current_lead: Optional[dict]
    processed_leads: List[dict]
    final_report: Optional[str]


def route_after_discovery(state: PipelineState) -> str:
    """Conditional router determining the next step after discovery.
    If there are active leads to process, goes to process_lead; otherwise summary.
    """
    queue = state.get("leads_to_process") or []
    return "process_lead" if queue else "summary"


def route_after_processing(state: PipelineState) -> str:
    """Conditional router determining the next step after processing a lead.
    If there are active leads remaining in the queue, loops back to process_lead; otherwise summary.
    """
    queue = state.get("leads_to_process") or []
    return "process_lead" if queue else "summary"


def create_agent_graph():
    """Declares the LangGraph workflow structure, wiring the Discovery, Loop,

    and Summary stages together.
    """
    # Initialize state graph
    workflow = StateGraph(PipelineState)
    
    # Add nodes
    workflow.add_node("discovery", discovery_node)
    workflow.add_node("process_lead", process_lead_node)
    workflow.add_node("summary", summary_node)
    
    # Connect START to discovery
    workflow.add_edge(START, "discovery")
    
    # Route discovery to either loop or summary
    workflow.add_conditional_edges(
        "discovery",
        route_after_discovery,
        {
            "process_lead": "process_lead",
            "summary": "summary"
        }
    )
    
    # Loop process_lead back to process next lead or summary
    workflow.add_conditional_edges(
        "process_lead",
        route_after_processing,
        {
            "process_lead": "process_lead",
            "summary": "summary"
        }
    )
    
    # Connect summary to END
    workflow.add_edge("summary", END)
    
    return workflow.compile()
