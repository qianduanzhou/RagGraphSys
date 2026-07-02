"""LangGraph 状态、节点实现与路由函数。

Graph topology（图拓扑）::

    START -> router_node
                   |-- needs_rag=True  --> [qdrant_node, neo4j_node] --> merge_node -> llm_node
                   |-- needs_rag=False ----------------------------------------------------> llm_node
                                                                                              |
                                                                                              v
                                                                reflection_node --(pass)--> END
                                                                                   --(fail)--> llm_node (loop)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END
from langgraph.config import get_stream_writer

from core.config import Settings
from core.logger import get_logger
from core.utils import truncate
from rag.rag_service import RagService, merge_results
from services.llm_service import LLMService

logger = get_logger(__name__)


class GraphState(TypedDict, total=False):
    """在图中流转的可变状态。``total=False`` 允许部分字段更新。"""
    question: str
    history: List[Dict[str, str]]
    needs_rag: bool
    used_rag: bool
    qdrant_results: List[Dict[str, Any]]
    neo4j_results: List[Dict[str, Any]]
    context: str
    sources: List[Dict[str, Any]]
    answer: str
    reflection_passed: bool
    reflection_feedback: str
    iterations: int
    streaming: bool
    owner: str


class GraphNodes:
    """持有服务依赖，对外暴露已绑定的节点可调用对象。"""

    def __init__(self, llm: LLMService, rag: RagService, settings: Settings):
        self.llm = llm
        self.rag = rag
        self.settings = settings
        self.max_iterations = settings.max_reflection_iterations

    # ------------------------------------------------------------------ #
    # router_node — 检索前置节点（始终检索）
    # ------------------------------------------------------------------ #
    def router(self, state: GraphState) -> Dict[str, Any]:
        """始终判定需要检索。

        设计变更：原实现用 LLM 预判「是否检索」，但对基于上传文档的具体提问会误判为
        DIRECT 从而跳过检索（用户上传文档后提问却得到「未提供参考资料」）。现改为始终检索，
        是否真正采用检索结果由 ``merge_node`` 的相关度阈值决定：命中相关片段则作为参考资料，
        闲聊等无关查询自然被阈值过滤、context 为空，由 LLM 当通用对话处理。
        附带收益：省去一次 LLM 调用（原 router 每次提问需 5~12s 判断且不稳定）。
        """
        question = state["question"]
        needs_rag = True
        logger.info("router_node: needs_rag=%s (always retrieve) | question=%s", needs_rag, truncate(question))
        return {"needs_rag": needs_rag, "used_rag": needs_rag}

    # ------------------------------------------------------------------ #
    # qdrant_node — 语义向量检索
    # ------------------------------------------------------------------ #
    def qdrant(self, state: GraphState) -> Dict[str, Any]:
        try:
            results = self.rag.qdrant.search(
                state["question"],
                self.settings.qdrant_top_k,
                owner=state.get("owner"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("qdrant_node failed: %s", exc)
            results = []
        logger.info("qdrant_node: %d hits", len(results))
        return {"qdrant_results": results}

    # ------------------------------------------------------------------ #
    # neo4j_node — 知识图谱检索
    # ------------------------------------------------------------------ #
    def neo4j(self, state: GraphState) -> Dict[str, Any]:
        try:
            keywords = self.llm.extract_keywords(state["question"]) or [state["question"][:32]]
            results = self.rag.neo4j.search(
                keywords,
                limit=self.settings.qdrant_top_k,
                owner=state.get("owner"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("neo4j_node failed: %s", exc)
            results = []
        logger.info("neo4j_node: %d relations", len(results))
        return {"neo4j_results": results}

    # ------------------------------------------------------------------ #
    # merge_node — 融合向量与图谱上下文，并标注来源
    # ------------------------------------------------------------------ #
    def merge(self, state: GraphState) -> Dict[str, Any]:
        vector_hits = state.get("qdrant_results", []) or []
        graph_hits = state.get("neo4j_results", []) or []

        context, sources = merge_results(
            vector_hits,
            graph_hits,
            score_threshold=self.settings.qdrant_score_threshold,
        )
        logger.info(
            "merge_node: %d sources (qdrant_raw=%d, neo4j=%d, threshold=%.2f)",
            len(sources), len(vector_hits), len(graph_hits), self.settings.qdrant_score_threshold,
        )
        return {"context": context, "sources": sources, "used_rag": bool(sources)}

    # ------------------------------------------------------------------ #
    # llm_node — 生成回答
    # ------------------------------------------------------------------ #
    def llm_generate(self, state: GraphState) -> Dict[str, Any]:
        question = state["question"]
        history = state.get("history", []) or []
        context = state.get("context", "") or ""
        iterations = state.get("iterations", 0)
        feedback = state.get("reflection_feedback", "") if iterations > 0 else ""
        used_rag = state.get("used_rag", False)

        if context:
            # 有检索结果：优先基于资料回答；但资料与问题无关时不拒绝，
            # 而是直接用自身知识回答（满足「非文档内容也照常回答」的需求）。
            # 同时用 Markdown 引用块把「文档内容」与「模型补充」区分开。
            system = (
                "你是一个智能知识问答助手。下方「参考资料」来自已上传的知识库。\n"
                "为了让用户区分「文档内容」与「你自身的补充」，请用 Markdown 结构组织回答：\n"
                "- **来自参考资料的内容**（原文或紧贴资料的事实陈述）放进 Markdown 引用块，"
                "即每一行以 `> ` 开头；\n"
                "- **你自身的补充**（解释、类比、扩展、通用知识）用普通段落，不要加引用块；\n"
                "- 若参考资料与问题相关，优先基于其作答；若无关或不足以回答，可不用引用块，"
                "直接用普通段落运用自身知识回答，不要回复「无法回答」「未提供相关资料」之类。\n\n"
                "示例（注意引用块与普通段落的区分）：\n"
                "> 消息结构采用 JSON 格式，含 header 与 body 两部分。\n\n"
                "这与 gRPC 的设计类似，便于跨语言解析。\n\n"
                "回答简洁、准确、有条理。"
            )
            system += f"\n\n参考资料：\n{context}"
        else:
            # 无检索结果：直接调用 LLM 通用知识回答，不做限制（全部为模型内容，无需引用块）
            system = (
                "你是一个智能助手。请直接回答用户问题，无需引用任何参考资料。"
                "回答简洁、准确、有条理。"
            )

        if feedback:
            system += f"\n\n上一版回答的审核意见：{feedback}。请据此改进你的回答。"

        messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": question}]

        try:
            if state.get("streaming"):
                # 按字符流式生成；通过 langgraph 的 StreamWriter 把每个 token 作为
                # custom 事件转发（由 astream(stream_mode="custom") 消费），
                # 同时累积完整回答供下游状态使用。
                writer = get_stream_writer()
                buffer: List[str] = []
                for token in self.llm.chat_stream(messages):
                    buffer.append(token)
                    writer({"type": "delta", "text": token})
                answer = "".join(buffer)
            else:
                answer = self.llm.chat(messages)
        except Exception as exc:  # noqa: BLE001
            logger.exception("llm_node generation failed: %s", exc)
            answer = f"抱歉，生成回答时出错：{exc}"

        logger.info("llm_node: iteration=%d produced answer (%d chars)", iterations + 1, len(answer))
        return {"answer": answer, "iterations": iterations + 1}

    # ------------------------------------------------------------------ #
    # reflection_node — 评估 / 改进回答
    # ------------------------------------------------------------------ #
    def reflection(self, state: GraphState) -> Dict[str, Any]:
        iterations = state.get("iterations", 0)
        # 达到尝试次数上限后停止反思。
        if iterations >= self.max_iterations:
            logger.info("reflection_node: iteration cap reached (%d), passing", iterations)
            return {"reflection_passed": True}

        verdict = self.llm.reflect(state["question"], state.get("answer", ""), state.get("context", ""))
        logger.info(
            "reflection_node: pass=%s feedback=%s",
            verdict["pass"],
            truncate(verdict.get("feedback", ""), 120),
        )
        return {
            "reflection_passed": verdict["pass"],
            "reflection_feedback": verdict.get("feedback", ""),
        }


# ---------------------------------------------------------------------- #
# 条件路由函数
# ---------------------------------------------------------------------- #
def route_after_router(state: GraphState) -> List[str] | str:
    """需要 RAG 时扇出到两个检索节点，否则直接进入 LLM。"""
    if state.get("needs_rag", True):
        return ["qdrant_node", "neo4j_node"]
    return "llm_node"


def make_route_after_reflection(max_iterations: int):
    def _route(state: GraphState) -> str:
        if state.get("reflection_passed", True):
            return END
        if state.get("iterations", 0) >= max_iterations:
            return END
        return "llm_node"

    return _route


def route_after_llm(state: GraphState) -> str:
    """流式路径跳过反思直接结束；非流式路径进入反思。"""
    if state.get("streaming"):
        return END
    return "reflection_node"
