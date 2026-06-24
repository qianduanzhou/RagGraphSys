"""构建编译后的多智能体图（RAG + 联网 + 整合）。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from core.config import Settings
from core.logger import get_logger
from multiagent.nodes import MultiAgentNodes, MultiAgentState, route_after_dispatch
from rag.rag_service import RagService
from services.llm_service import LLMService
from services.web_search_service import WebSearchService

logger = get_logger(__name__)


def build_multi_agent_graph(
    llm: LLMService,
    rag: RagService,
    web: WebSearchService,
    settings: Settings,
) -> CompiledStateGraph:
    """将多智能体节点与边连接为编译后的 StateGraph。"""
    nodes = MultiAgentNodes(llm=llm, rag=rag, web=web, settings=settings)

    graph = StateGraph(MultiAgentState)

    graph.add_node("dispatch_node", nodes.dispatch)
    graph.add_node("rag_agent_node", nodes.rag_agent)
    graph.add_node("web_agent_node", nodes.web_agent)
    graph.add_node("integration_node", nodes.integration)

    # START -> dispatch --(fan-out)--> [rag_agent, web_agent] --> integration -> END
    graph.add_edge(START, "dispatch_node")
    graph.add_conditional_edges("dispatch_node", route_after_dispatch)
    graph.add_edge("rag_agent_node", "integration_node")
    graph.add_edge("web_agent_node", "integration_node")
    graph.add_edge("integration_node", END)

    compiled = graph.compile()
    logger.info("MultiAgent graph compiled: dispatch->(rag,web)->integration")
    return compiled
