"""RAG 编排服务。

整合 Qdrant 向量库、Neo4j 图谱库与 LLM，提供：
  * 文档导入（分块写入 Qdrant，抽取三元组写入 Neo4j），
  * 供 LangGraph 节点使用的混合检索。
"""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.config import Settings
from core.logger import get_logger
from core.utils import is_aggregate_query, split_text
from rag.neo4j_store import Neo4jStore
from rag.qdrant_store import QdrantStore
from services.llm_service import LLMService

logger = get_logger(__name__)


def _sample_graph_chunks(chunks: List[str], max_chunks: int = 8) -> List[str]:
    """Pick representative chunks for graph extraction.

    Only extracting triples from the beginning of a long document misses
    entities in later pages/sheets.  Sampling head / middle / tail keeps cost
    bounded while improving graph coverage.
    """

    if len(chunks) <= max_chunks:
        return list(chunks)

    candidates = [
        0,
        1,
        2,
        len(chunks) // 4,
        len(chunks) // 2,
        (len(chunks) * 3) // 4,
        len(chunks) - 2,
        len(chunks) - 1,
    ]
    indices: List[int] = []
    for idx in candidates:
        idx = max(0, min(len(chunks) - 1, idx))
        if idx not in indices:
            indices.append(idx)
        if len(indices) >= max_chunks:
            break
    return [chunks[i] for i in sorted(indices)]


def merge_results(
    qdrant_hits: List[Dict[str, Any]],
    neo4j_hits: List[Dict[str, Any]],
    score_threshold: float = 0.0,
) -> tuple[str, List[Dict[str, Any]]]:
    """将向量检索与图谱检索的结果融合为一个上下文字符串和带标签的来源列表。

    由 ``GraphNodes.merge``（非流式 LangGraph 流水线）与
    ``RagService.build_context``（流式流水线）共用，保证格式化逻辑集中在一处。

    ``score_threshold``（cosine 相似度，默认 0.0 即不过滤）会丢弃低于阈值的
    向量命中，避免无关结果污染上下文；Neo4j 关系为关键词精确命中、不带分数，不参与过滤。

    ``score is None`` 视为「无相似度、整文档拉取」的分片（见
    :meth:`RagService.resolve_vector_hits`），无条件放行阈值过滤。
    """
    # 相关度过滤：仅保留达到阈值的向量命中；score=None（整文档拉取）放行
    qdrant_hits = [
        h for h in qdrant_hits
        if h.get("score") is None or float(h.get("score", 0.0)) >= score_threshold
    ]

    parts: List[str] = []
    sources: List[Dict[str, Any]] = []

    if qdrant_hits:
        parts.append("【向量检索结果 / Qdrant】")
        for i, hit in enumerate(qdrant_hits, 1):
            score = hit.get("score")
            score_str = "n/a" if score is None else f"{float(score):.3f}"
            parts.append(f"[V{i}] (score={score_str}, src={hit.get('source')}) {hit['text']}")
            sources.append(
                {
                    "type": "qdrant",
                    "content": hit["text"],
                    "score": hit.get("score"),
                    "source": hit.get("source"),
                }
            )

    if neo4j_hits:
        parts.append("\n【知识图谱关系 / Neo4j】")
        for hit in neo4j_hits:
            line = f"{hit['head']} -[{hit['rel']}]-> {hit['tail']}"
            parts.append(line)
            sources.append({"type": "neo4j", "content": line})

    return "\n".join(parts).strip(), sources


class RagService:
    def __init__(
        self,
        qdrant: QdrantStore,
        neo4j: Neo4jStore,
        llm: LLMService,
        settings: Settings,
    ):
        self.qdrant = qdrant
        self.neo4j = neo4j
        self.llm = llm
        self.settings = settings

    @staticmethod
    def _source_key(source: str, owner: str | None = None, conversation_id: str | None = None) -> str:
        # Neo4j 来源标记：owner::conversation_id::source（缺省维度省略）。
        # 旧的 owner::source 是其前缀，故 owner 维度过滤仍兼容；对话维度进一步收窄。
        parts: List[str] = []
        if owner:
            parts.append(owner)
        if conversation_id:
            parts.append(conversation_id)
        parts.append(source)
        return "::".join(parts)

    # ------------------------------------------------------------------ #
    # 导入
    # ------------------------------------------------------------------ #
    def ingest_text(
        self,
        text: str,
        source: str = "manual",
        owner: str | None = None,
        conversation_id: str | None = None,
        extract_graph: bool = True,
    ) -> Dict[str, Any]:
        """对文本分块后写入 Qdrant，并抽取三元组写入 Neo4j。

        ``conversation_id`` 非 None 时，分片与三元组都打上对话标记，检索/删除按对话隔离。
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("入库文本不能为空")

        chunks = split_text(text, self.settings.chunk_size, self.settings.chunk_overlap)
        # 记录入库时间戳，供 /docs 聚合展示与排序（刷新界面后仍可长期看到）。
        created_at = int(time.time())
        metadatas = []
        for i, chunk in enumerate(chunks):
            meta = {"source": source, "chunk_index": i, "char_len": len(chunk), "created_at": created_at}
            if owner:
                meta["owner"] = owner
            if conversation_id:
                meta["conversation_id"] = conversation_id
            metadatas.append(meta)
        upserted = self.qdrant.upsert(chunks, metadatas)
        if not extract_graph:
            graph_chunks = _sample_graph_chunks(chunks)
            logger.info(
                "Ingested vectors for '%s': %d chunks; graph extraction deferred (conv=%s)",
                source, upserted, conversation_id,
            )
            return {"chunks": upserted, "triples": 0, "graph_chunks": graph_chunks}

        # 从文档头/中/尾采样抽取图谱，避免长文档后半部分实体关系完全进不了 Neo4j。
        triples = self.llm.extract_graph("\n\n".join(_sample_graph_chunks(chunks)))
        merged = self.neo4j.add_knowledge(
            [(t["head"], t["rel"], t["tail"]) for t in triples],
            source=self._source_key(source, owner, conversation_id),
        )

        logger.info("Ingested '%s': %d chunks, %d triples (conv=%s)", source, upserted, merged, conversation_id)
        return {"chunks": upserted, "triples": merged}

    def extract_graph_for_chunks(
        self,
        chunks: List[str],
        source: str = "manual",
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> int:
        """Extract graph triples from already-ingested chunks and write them to Neo4j."""
        if not chunks:
            return 0
        triples = self.llm.extract_graph("\n\n".join(_sample_graph_chunks(chunks)))
        merged = self.neo4j.add_knowledge(
            [(t["head"], t["rel"], t["tail"]) for t in triples],
            source=self._source_key(source, owner, conversation_id),
        )
        logger.info("Extracted graph for '%s': %d triples (conv=%s)", source, merged, conversation_id)
        return merged

    def delete_document(
        self,
        source: str,
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> Dict[str, Any]:
        """删除某来源文档：清除其在 Qdrant 的全部分片与 Neo4j 的图谱关系。

        ``conversation_id`` 非 None 时仅在指定对话内删除（同名文件跨对话不串）。
        Neo4j 清理依赖关系上的来源标记（见 :meth:`add_knowledge`），历史数据可能
        无法精确清理，但 Qdrant 分片一定会删除——问答检索不再命中该文档。
        """
        chunks = self.qdrant.delete_by_source(source, owner=owner, conversation_id=conversation_id)
        relations = self.neo4j.delete_by_source(self._source_key(source, owner, conversation_id))
        logger.info("Deleted document '%s': %d chunks, %d relations", source, chunks, relations)
        return {"source": source, "chunks": chunks, "relations": relations}

    def delete_documents(
        self,
        sources: List[str],
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> Dict[str, Any]:
        """批量删除多个来源文档：逐个调用 :meth:`delete_document`，单项失败不中断整批。

        返回逐项明细 + 聚合计数（结构与批量导入 ``ingest_files`` 对齐），便于前端
        展示「已删除 N 个，失败 M 个」并定位失败文档。底层 ``delete_by_source``
        仅支持单 source，故在服务层循环；若日后批量规模变大、Qdrant exact count /
        Neo4j 全图清理成为瓶颈，再下沉到 store 层做聚合。
        """
        results: List[Dict[str, Any]] = []
        deleted = failed = 0
        for source in sources:
            try:
                stats = self.delete_document(source, owner=owner, conversation_id=conversation_id)
                results.append({
                    "source": source,
                    "chunks": stats["chunks"],
                    "relations": stats["relations"],
                    "ok": True,
                })
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("batch delete failed for '%s': %s", source, exc)
                results.append({"source": source, "ok": False, "error": str(exc)})
                failed += 1
        logger.info(
            "Batch deleted %d sources: %d ok, %d failed", len(sources), deleted, failed
        )
        return {
            "status": "ok" if failed == 0 else "partial",
            "deleted": deleted,
            "failed": failed,
            "results": results,
        }

    def delete_conversation(self, owner: str | None, conversation_id: str) -> Dict[str, Any]:
        """删除某对话的全部知识：Qdrant 分片 + Neo4j 图谱关系。返回各自清理计数。"""
        chunks = self.qdrant.delete_by_conversation(owner, conversation_id)
        relations = self.neo4j.delete_by_conversation(owner, conversation_id)
        logger.info("Deleted conversation: %d chunks, %d relations", chunks, relations)
        return {"chunks": chunks, "relations": relations}

    def ingest_file(
        self,
        path: str | Path,
        encoding: str = "utf-8",
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> Dict[str, int]:
        file_path = Path(path)
        text = file_path.read_text(encoding=encoding)
        return self.ingest_text(text, source=file_path.name, owner=owner, conversation_id=conversation_id)

    # ------------------------------------------------------------------ #
    # 检索（混合）
    # ------------------------------------------------------------------ #
    def resolve_vector_hits(
        self,
        question: str,
        vector_hits: List[Dict[str, Any]],
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """聚合型查询的整文档拉取入口（主图 ``merge_node`` 与 ``build_context`` 共用）。

        满足三重门槛时，用「主导命中文档」的 source 拉取其全部分片，替换原向量命中：
          1. 聚合关键词命中（:func:`is_aggregate_query`）；
          2. 相关度达标：命中里至少 1 条 ``score >= qdrant_score_threshold``
             （挡掉「所有…」式闲聊误触发）；
          3. 单一文档占主导：达标命中里同一 source 严格过半（``> len/2``），
             多文档场景下避免锁定到只蹭到零星命中的大文档。

        返回 ``(hits, aggregate_flag)``；任一门槛不满足则原样返回、flag=False。
        """
        if not is_aggregate_query(question or "") or not vector_hits:
            return vector_hits, False

        threshold = self.settings.qdrant_score_threshold
        high_scored = [
            h for h in vector_hits
            if h.get("score") is not None and float(h["score"]) >= threshold
        ]
        if not high_scored:
            return vector_hits, False

        source_counts = Counter(h.get("source") or "unknown" for h in high_scored)
        dominant_source, top_count = source_counts.most_common(1)[0]
        # 严格过半（> len/2），且至少 1 条
        min_dominant = max(1, len(high_scored) // 2 + 1)
        if top_count < min_dominant:
            return vector_hits, False

        full = self.qdrant.scroll_by_source(
            dominant_source,
            owner=owner,
            conversation_id=conversation_id,
            limit=self.settings.rag_aggregate_max_chunks,
        )
        if not full:
            return vector_hits, False

        logger.info(
            "aggregate retrieval: source=%s, %d chunks pulled (was %d hits)",
            dominant_source, len(full), len(vector_hits),
        )
        return full, True

    def prefer_single_conversation_document(
        self,
        vector_hits: List[Dict[str, Any]],
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """当前对话只有一个文档时，优先拉取该文档内容作为参考资料。

        用户在单文档对话里提问时，意图通常默认指向这个文档。即使语义分数低于阈值，
        也先把文档片段交给生成模型判断；若问题确实与资料无关，生成提示会允许它按
        通用问题回答。返回 ``(hits, preferred)``。
        """
        if not conversation_id:
            return vector_hits, False

        sources = self.qdrant.list_sources(owner=owner, conversation_id=conversation_id)
        if len(sources) != 1:
            return vector_hits, False

        source = sources[0]
        full = self.qdrant.scroll_by_source(
            source,
            owner=owner,
            conversation_id=conversation_id,
            limit=self.settings.rag_aggregate_max_chunks,
        )
        if not full:
            return vector_hits, False

        logger.info(
            "single-document retrieval: source=%s, %d chunks pulled (was %d hits)",
            source, len(full), len(vector_hits),
        )
        return full, True

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> Dict[str, Any]:
        """独立执行向量检索与图谱检索，并同时返回两者结果。

        对聚合型查询，向量命中会经 :meth:`resolve_vector_hits` 改写为整文档分片，
        并在返回值中带 ``aggregate`` 标志。``conversation_id`` 非 None 时按对话隔离检索。
        """
        limit = top_k or self.settings.qdrant_top_k

        vector_hits: List[Dict[str, Any]] = []
        graph_hits: List[Dict[str, Any]] = []
        try:
            vector_hits = self.qdrant.search(query, top_k=limit, owner=owner, conversation_id=conversation_id)
        except Exception as exc:  # noqa: BLE001 - retrieval must degrade gracefully
            logger.exception("Qdrant retrieval failed: %s", exc)

        # 聚合型查询：必要时改走整文档拉取
        vector_hits, aggregate = self.resolve_vector_hits(
            query, vector_hits, owner=owner, conversation_id=conversation_id
        )
        single_document = False
        if not aggregate:
            vector_hits, single_document = self.prefer_single_conversation_document(
                vector_hits, owner=owner, conversation_id=conversation_id
            )

        try:
            keywords = self.llm.extract_keywords(query) or [query[:32]]
            graph_hits = self.neo4j.search(keywords, limit=limit, owner=owner, conversation_id=conversation_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Neo4j retrieval failed: %s", exc)

        return {"qdrant": vector_hits, "neo4j": graph_hits, "aggregate": aggregate or single_document}

    def build_context(
        self,
        query: str,
        top_k: int | None = None,
        owner: str | None = None,
        conversation_id: str | None = None,
    ) -> Dict[str, Any]:
        """执行混合检索，随后合并为上下文字符串和来源列表。

        对应 LangGraph 中 router->qdrant/neo4j->merge 的路径，使流式接口
        在开始流式生成前可以复用相同的检索逻辑。返回值含 ``aggregate`` 标志，
        供调用方（如多智能体）据此放宽输出 token 上限。``conversation_id`` 非 None
        时按对话隔离检索。
        """
        retrieved = self.retrieve(query, top_k=top_k, owner=owner, conversation_id=conversation_id)
        context, sources = merge_results(
            retrieved["qdrant"],
            retrieved["neo4j"],
            score_threshold=self.settings.qdrant_score_threshold,
        )
        used_rag = bool(sources)
        aggregate = bool(retrieved.get("aggregate", False))
        logger.info("build_context: %d sources, used_rag=%s, aggregate=%s", len(sources), used_rag, aggregate)
        return {"context": context, "sources": sources, "used_rag": used_rag, "aggregate": aggregate}
