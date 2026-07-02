"""RAG 编排服务。

整合 Qdrant 向量库、Neo4j 图谱库与 LLM，提供：
  * 文档导入（分块写入 Qdrant，抽取三元组写入 Neo4j），
  * 供 LangGraph 节点使用的混合检索。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from core.config import Settings
from core.logger import get_logger
from core.utils import split_text
from rag.neo4j_store import Neo4jStore
from rag.qdrant_store import QdrantStore
from services.llm_service import LLMService

logger = get_logger(__name__)


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
    """
    # 相关度过滤：仅保留达到阈值的向量命中
    qdrant_hits = [h for h in qdrant_hits if float(h.get("score", 0.0)) >= score_threshold]

    parts: List[str] = []
    sources: List[Dict[str, Any]] = []

    if qdrant_hits:
        parts.append("【向量检索结果 / Qdrant】")
        for i, hit in enumerate(qdrant_hits, 1):
            parts.append(f"[V{i}] (score={hit.get('score', 0):.3f}, src={hit.get('source')}) {hit['text']}")
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
    def _source_key(source: str, owner: str | None = None) -> str:
        return f"{owner}::{source}" if owner else source

    # ------------------------------------------------------------------ #
    # 导入
    # ------------------------------------------------------------------ #
    def ingest_text(
        self,
        text: str,
        source: str = "manual",
        owner: str | None = None,
    ) -> Dict[str, int]:
        """对文本分块后写入 Qdrant，并抽取三元组写入 Neo4j。"""
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
            metadatas.append(meta)
        upserted = self.qdrant.upsert(chunks, metadatas)

        # 从文档开头部分抽取图谱（控制成本）。传入 source 便于按文档删除。
        triples = self.llm.extract_graph("\n\n".join(chunks[:6]))
        merged = self.neo4j.add_knowledge(
            [(t["head"], t["rel"], t["tail"]) for t in triples],
            source=self._source_key(source, owner),
        )

        logger.info("Ingested '%s': %d chunks, %d triples", source, upserted, merged)
        return {"chunks": upserted, "triples": merged}

    def delete_document(self, source: str, owner: str | None = None) -> Dict[str, Any]:
        """删除某来源文档：清除其在 Qdrant 的全部分片与 Neo4j 的图谱关系。

        Neo4j 清理依赖关系上的来源标记（见 :meth:`add_knowledge`），历史数据可能
        无法精确清理，但 Qdrant 分片一定会删除——问答检索不再命中该文档。
        """
        chunks = self.qdrant.delete_by_source(source, owner=owner)
        relations = self.neo4j.delete_by_source(self._source_key(source, owner))
        logger.info("Deleted document '%s': %d chunks, %d relations", source, chunks, relations)
        return {"source": source, "chunks": chunks, "relations": relations}

    def delete_documents(self, sources: List[str], owner: str | None = None) -> Dict[str, Any]:
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
                stats = self.delete_document(source, owner=owner)
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

    def ingest_file(
        self,
        path: str | Path,
        encoding: str = "utf-8",
        owner: str | None = None,
    ) -> Dict[str, int]:
        file_path = Path(path)
        text = file_path.read_text(encoding=encoding)
        return self.ingest_text(text, source=file_path.name, owner=owner)

    # ------------------------------------------------------------------ #
    # 检索（混合）
    # ------------------------------------------------------------------ #
    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        owner: str | None = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """独立执行向量检索与图谱检索，并同时返回两者结果。"""
        limit = top_k or self.settings.qdrant_top_k

        vector_hits: List[Dict[str, Any]] = []
        graph_hits: List[Dict[str, Any]] = []
        try:
            vector_hits = self.qdrant.search(query, top_k=limit, owner=owner)
        except Exception as exc:  # noqa: BLE001 - retrieval must degrade gracefully
            logger.exception("Qdrant retrieval failed: %s", exc)

        try:
            keywords = self.llm.extract_keywords(query) or [query[:32]]
            graph_hits = self.neo4j.search(keywords, limit=limit, owner=owner)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Neo4j retrieval failed: %s", exc)

        return {"qdrant": vector_hits, "neo4j": graph_hits}

    def build_context(
        self,
        query: str,
        top_k: int | None = None,
        owner: str | None = None,
    ) -> Dict[str, Any]:
        """执行混合检索，随后合并为上下文字符串和来源列表。

        对应 LangGraph 中 router->qdrant/neo4j->merge 的路径，使流式接口
        在开始流式生成前可以复用相同的检索逻辑。
        """
        retrieved = self.retrieve(query, top_k=top_k, owner=owner)
        context, sources = merge_results(
            retrieved["qdrant"],
            retrieved["neo4j"],
            score_threshold=self.settings.qdrant_score_threshold,
        )
        used_rag = bool(sources)
        logger.info("build_context: %d sources, used_rag=%s", len(sources), used_rag)
        return {"context": context, "sources": sources, "used_rag": used_rag}
