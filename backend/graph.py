"""构建编译后的 LangGraph 混合检索图。"""
from __future__ import annotations

from typing import Optional

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from core.config import Settings
from core.logger import get_logger
from nodes import (
    GraphNodes,
    GraphState,
    make_route_after_reflection,
    route_after_llm,
    route_after_router,
)
from rag.rag_service import RagService
from services.llm_service import LLMService

logger = get_logger(__name__)


def build_graph(
    llm: LLMService,
    rag: RagService,
    settings: Settings,
) -> CompiledStateGraph:
    """将节点和边连接为一个编译后的 :class:`StateGraph`。"""
    nodes = GraphNodes(llm=llm, rag=rag, settings=settings)

    graph = StateGraph(GraphState)

    graph.add_node("router_node", nodes.router)
    graph.add_node("qdrant_node", nodes.qdrant)
    graph.add_node("neo4j_node", nodes.neo4j)
    graph.add_node("merge_node", nodes.merge)
    graph.add_node("llm_node", nodes.llm_generate)
    graph.add_node("reflection_node", nodes.reflection)

    # START -> router
    graph.add_edge(START, "router_node")

    # router --(needs_rag)--> [qdrant, neo4j]   （并行扇出）
    # router --(direct)-----> llm_node
    graph.add_conditional_edges("router_node", route_after_router)

    # 两个检索节点在 merge 汇合，随后生成回答
    graph.add_edge("qdrant_node", "merge_node")
    graph.add_edge("neo4j_node", "merge_node")
    graph.add_edge("merge_node", "llm_node")

    # 生成 -> （流式：END | 非流式：反思 -> END | 重新生成）
    graph.add_conditional_edges("llm_node", route_after_llm)
    graph.add_conditional_edges("reflection_node", make_route_after_reflection(settings.max_reflection_iterations))

    compiled = graph.compile()
    logger.info("LangGraph compiled: router->(qdrant,neo4j)->merge->llm->reflection")
    return compiled


def run_graph(
    compiled: CompiledStateGraph,
    question: str,
    history: Optional[list] = None,
) -> dict:
    """便捷运行入口，返回最终状态字典。"""
    initial_state = {
        "question": question,
        "history": history or [],
        "iterations": 0,
    }
    result = compiled.invoke(initial_state)
    return dict(result)
