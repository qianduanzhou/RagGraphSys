"""多智能体 LangGraph：状态、节点实现与并行-汇合编排。

图拓扑::

    START -> dispatch_node --(fan-out)--> [rag_agent_node, web_agent_node]
                                                |                |
                                                +-> integration_node -> END

- rag_agent_node / web_agent_node 并行执行，各产出一份回答 + 来源；
- integration_node 汇合两者，综合后流式输出最终答案。
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.config import get_stream_writer

from core.config import Settings
from core.logger import get_logger
from core.utils import truncate
from rag.rag_service import RagService
from services.llm_service import LLMService
from services.web_search_service import WebSearchService

logger = get_logger(__name__)


class MultiAgentState(TypedDict, total=False):
    """在多智能体图中流转的可变状态。``total=False`` 允许部分字段更新（与 nodes.GraphState 同构）。"""

    question: str
    history: List[Dict[str, str]]
    rag_agent_answer: str
    rag_agent_sources: List[Dict[str, Any]]
    web_agent_answer: str
    web_sources: List[Dict[str, Any]]
    used_rag: bool
    used_web: bool
    answer: str
    iterations: int
    streaming: bool


class MultiAgentNodes:
    """持有服务依赖，对外暴露多智能体图的节点可调用对象。"""

    def __init__(self, llm: LLMService, rag: RagService, web: WebSearchService, settings: Settings):
        self.llm = llm
        self.rag = rag
        self.web = web
        self.settings = settings

    # ------------------------------------------------------------------ #
    # dispatch_node — 启动多智能体（透传 + 记日志）
    # ------------------------------------------------------------------ #
    def dispatch(self, state: MultiAgentState) -> Dict[str, Any]:
        logger.info("multi_agent dispatch | question=%s", truncate(state["question"]))
        return {}

    # ------------------------------------------------------------------ #
    # rag_agent_node — 检索知识库并生成回答
    # ------------------------------------------------------------------ #
    def rag_agent(self, state: MultiAgentState) -> Dict[str, Any]:
        question = state["question"]
        try:
            retrieved = self.rag.build_context(question)
            context = retrieved.get("context", "") or ""
            sources = retrieved.get("sources", []) or []
            used_rag = bool(retrieved.get("used_rag", False))
        except Exception as exc:  # noqa: BLE001
            logger.exception("rag_agent retrieval failed: %s", exc)
            context, sources, used_rag = "", [], False

        system = (
            "你是知识库检索助手。仅根据下方「知识库资料」回答用户问题。"
            "若资料无关或不足以回答，明确回复「知识库中无相关内容」，不要编造。"
            "回答简洁、准确。"
        )
        system += f"\n\n知识库资料：\n{context}" if context else "\n\n（知识库中无相关资料）"

        try:
            answer = self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ])
        except Exception as exc:  # noqa: BLE001
            logger.exception("rag_agent generation failed: %s", exc)
            answer = "（知识库检索失败）"

        logger.info("rag_agent: used_rag=%s, sources=%d", used_rag, len(sources))
        return {"rag_agent_answer": answer, "rag_agent_sources": sources, "used_rag": used_rag}

    # ------------------------------------------------------------------ #
    # web_agent_node — 联网搜索并生成回答
    # ------------------------------------------------------------------ #
    def web_agent(self, state: MultiAgentState) -> Dict[str, Any]:
        question = state["question"]
        try:
            results = self.web.search(question)
        except Exception as exc:  # noqa: BLE001
            logger.exception("web_agent search failed: %s", exc)
            results = []

        used_web = bool(results)
        sources = [
            {
                "type": "web",
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "score": r.get("score"),
            }
            for r in results
        ]

        system = (
            "你是联网搜索助手。根据下方「搜索结果」回答用户问题。"
            "结果来自网络、未必准确；若与问题无关，回复「联网未找到相关结果」。"
            "用 Markdown 链接 [标题](url) 标注来源。回答简洁、准确。"
        )
        if results:
            ctx = "\n\n".join(
                f"[{i + 1}] {r.get('title', '')} ({r.get('url', '')})\n{r.get('content', '')}"
                for i, r in enumerate(results)
            )
            system += f"\n\n搜索结果：\n{ctx}"
        else:
            system += "\n\n（联网搜索无结果）"

        try:
            answer = self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ])
        except Exception as exc:  # noqa: BLE001
            logger.exception("web_agent generation failed: %s", exc)
            answer = "（联网搜索不可用）"

        logger.info("web_agent: used_web=%s, sources=%d", used_web, len(sources))
        return {"web_agent_answer": answer, "web_sources": sources, "used_web": used_web}

    # ------------------------------------------------------------------ #
    # integration_node — 综合两份回答，流式输出最终答案
    # ------------------------------------------------------------------ #
    def integration(self, state: MultiAgentState) -> Dict[str, Any]:
        question = state["question"]
        history = state.get("history", []) or []
        rag_answer = state.get("rag_agent_answer", "") or ""
        web_answer = state.get("web_agent_answer", "") or ""
        iterations = state.get("iterations", 0)

        system = (
            "你是整合助手。综合下方「知识库回答」与「联网回答」，给用户一个最终答案。\n"
            "规则：\n"
            "- 涉及用户上传文档的内容以「知识库回答」为准；最新/外部/通用信息以「联网回答」为准；\n"
            "- 知识库的文档内容用 Markdown 引用块（每行以 > 开头）标注；\n"
            "- 联网内容用 [标题](url) 链接标注来源；\n"
            "- 若某一方明确表示无相关内容（如「知识库中无相关内容」「联网未找到相关结果」），"
            "则以另一方为主，不要重复该说明；\n"
            "- 不要赘述两个来源的过程，直接给出整合后的答案。\n"
            "回答简洁、准确、有条理。"
        )
        system += f"\n\n知识库回答：\n{rag_answer or '（空）'}"
        system += f"\n\n联网回答：\n{web_answer or '（空）'}"

        messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": question}]

        try:
            if state.get("streaming"):
                writer = get_stream_writer()
                buffer: List[str] = []
                for token in self.llm.chat_stream(messages):
                    buffer.append(token)
                    writer({"type": "delta", "text": token})
                answer = "".join(buffer)
            else:
                answer = self.llm.chat(messages)
        except Exception as exc:  # noqa: BLE001
            logger.exception("integration generation failed: %s", exc)
            answer = f"抱歉，整合回答时出错：{exc}"

        logger.info("integration: produced answer (%d chars)", len(answer))
        return {"answer": answer, "iterations": iterations + 1}


# ---------------------------------------------------------------------- #
# 条件路由：dispatch 后并行扇出到两个 agent
# ---------------------------------------------------------------------- #
def route_after_dispatch(state: MultiAgentState) -> List[str]:
    """dispatch 后扇出到 RAG 与联网两个 agent（并行）。"""
    return ["rag_agent_node", "web_agent_node"]
