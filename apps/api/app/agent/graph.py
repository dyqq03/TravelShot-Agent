from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from app.agent.formatter import final_formatter_node
from app.agent.goal_parser import goal_parser_node
from app.agent.planner import planner_node
from app.agent.react_executor import react_executor_node
from app.agent.state import AgentState


class SequentialGraph:
    def __init__(self, nodes: list[Callable[[AgentState], AgentState]]) -> None:
        self.nodes = nodes

    def invoke(self, state: AgentState) -> AgentState:
        current = dict(state)
        for node in self.nodes:
            current.update(node(current))
        return current


@lru_cache(maxsize=1)
def build_agent_graph():
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return SequentialGraph([goal_parser_node, planner_node, react_executor_node, final_formatter_node])

    graph = StateGraph(AgentState)
    graph.add_node("goal_parser", goal_parser_node)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", react_executor_node)
    graph.add_node("final_formatter", final_formatter_node)
    graph.add_edge(START, "goal_parser")
    graph.add_edge("goal_parser", "planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "final_formatter")
    graph.add_edge("final_formatter", END)
    return graph.compile()


def build_phase1_graph():
    # Compatibility shim for older Phase 1 imports.
    return build_agent_graph()


def run_agent_graph(
    plan_id: str,
    user_input: str,
    parsed_goal: dict | None = None,
    candidate_spots: list[dict] | None = None,
    warnings: list[str] | None = None,
    llm_used: bool = False,
    candidate_spots_source: str | None = None,
    reference_images: list[str] | None = None,
    llm_call_count: int = 0,
) -> AgentState:
    graph = build_agent_graph()
    initial_state: AgentState = {
        "plan_id": plan_id,
        "user_input": user_input,
        "warnings": warnings or [],
        "agent_steps": [],
        "llm_used": llm_used,
        "reference_images": reference_images or [],
        "llm_call_count": llm_call_count,
    }
    if parsed_goal is not None:
        initial_state["parsed_goal"] = parsed_goal
    if candidate_spots is not None:
        initial_state["candidate_spots"] = candidate_spots
    if candidate_spots_source is not None:
        initial_state["candidate_spots_source"] = candidate_spots_source
    return graph.invoke(initial_state)


def run_phase1_graph(*args, **kwargs) -> AgentState:
    # Compatibility shim for older Phase 1 imports.
    return run_agent_graph(*args, **kwargs)
