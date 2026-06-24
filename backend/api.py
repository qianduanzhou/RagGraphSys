"""HTTP API 层。

接口：
    POST /api/chat          -> 运行混合 RAG 图
    POST /api/ingest        -> 导入原始文本
    POST /api/ingest/file   -> 导入上传的 .txt/.md 文件
    GET  /api/health        -> 存活检查及依赖状态
    GET  /api/stats         -> 集合 / 图谱数量统计
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.logger import get_logger
from services.archive import extract_zip
from services.file_parser import ALLOWED_EXTS, parse_upload

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: Optional[List[ChatMessage]] = Field(default_factory=list)
    mode: Literal["rag", "multi"] = "rag"


class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]
    used_rag: bool
    iterations: int


class IngestTextRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = "manual"


class IngestResponse(BaseModel):
    status: str
    chunks: int
    triples: int


class FileIngestResult(BaseModel):
    name: str
    chunks: int = 0
    triples: int = 0
    ok: bool = True
    error: Optional[str] = None


class BatchIngestResponse(BaseModel):
    status: str
    chunks: int
    triples: int
    succeeded: int
    failed: int
    files: List[FileIngestResult]


class DeleteDocRequest(BaseModel):
    source: str = Field(..., min_length=1)


class DeleteDocResponse(BaseModel):
    source: str
    chunks: int
    relations: int


class BatchDeleteItem(BaseModel):
    source: str
    chunks: int = 0
    relations: int = 0
    ok: bool = True
    error: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    sources: List[str] = Field(..., min_length=1)


class BatchDeleteResponse(BaseModel):
    status: str
    deleted: int
    failed: int
    results: List[BatchDeleteItem]


# ---------------------------------------------------------------------- #
# 辅助函数
# ---------------------------------------------------------------------- #
def _state(request: Request):
    """从 app.state 中取出预先构建好的单例。"""
    graph = getattr(request.app.state, "graph", None)
    rag = getattr(request.app.state, "rag", None)
    if graph is None or rag is None:
        raise HTTPException(status_code=503, detail="application not initialised")
    return graph, rag


def _select_graph(request: Request, mode: str):
    """按模式选择编译图。multi_agent_graph 缺失时返回 503（正常不触发，图始终构建）。"""
    if mode == "multi":
        graph = getattr(request.app.state, "multi_agent_graph", None)
        if graph is None:
            raise HTTPException(status_code=503, detail="多智能体模式不可用")
        return graph
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="application not initialised")
    return graph


def _history_to_dicts(history: Optional[List[ChatMessage]]) -> List[Dict[str, str]]:
    if not history:
        return []
    return [{"role": m.role, "content": m.content} for m in history]


# ---------------------------------------------------------------------- #
# 接口
# ---------------------------------------------------------------------- #
@router.get("/health")
def health(request: Request) -> Dict[str, Any]:
    rag = getattr(request.app.state, "rag", None)
    web = getattr(request.app.state, "web", None)
    web_ok = bool(web.available) if web is not None else False
    qdrant_ok = neo4j_ok = False
    counts: Dict[str, Any] = {}
    if rag is not None:
        try:
            counts["qdrant_points"] = rag.qdrant.count()
            qdrant_ok = counts["qdrant_points"] >= 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: qdrant check failed: %s", exc)
        try:
            counts["neo4j_entities"] = rag.neo4j.count_entities()
            neo4j_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: neo4j check failed: %s", exc)
    return {
        "status": "ok" if (qdrant_ok and neo4j_ok) else "degraded",
        "qdrant": qdrant_ok,
        "neo4j": neo4j_ok,
        "web_search": web_ok,
        "counts": counts,
    }


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    graph = _select_graph(request, payload.mode)
    logger.info("/chat mode=%s question=%s", payload.mode, payload.message[:120])
    try:
        result = graph.invoke(
            {
                "question": payload.message,
                "history": _history_to_dicts(payload.history),
                "iterations": 0,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("graph invocation failed")
        raise HTTPException(status_code=500, detail=f"graph failed: {exc}") from exc

    return ChatResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        used_rag=result.get("used_rag", False),
        iterations=result.get("iterations", 0),
    )


# ---------------------------------------------------------------------- #
# 流式对话（SSE）—— 运行真实的编译后 LangGraph。
#
# graph.astream(stream_mode=["updates","custom"]) 同时消费两种模式：
#   * updates：每个节点完成事件以 `node` 帧转发（router -> qdrant -> neo4j ->
#     merge -> llm），向客户端实时展示流水线进度；
#   * custom：llm_node 内通过 langgraph 的 StreamWriter 写出的字符增量，以
#     `delta` 帧转发，无需手搓线程桥接。
# 该路径跳过反思（route_after_llm -> END）；非流式 /chat 保留完整循环。
# ---------------------------------------------------------------------- #
def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _summarize_update(node: str, update: Any) -> dict:
    """将节点的状态更新投影为一个精简、便于客户端消费的负载。"""
    if not isinstance(update, dict):
        return {}
    if node == "router_node":
        return {"needs_rag": update.get("needs_rag"), "used_rag": update.get("used_rag")}
    if node == "qdrant_node":
        return {"hits": len(update.get("qdrant_results") or [])}
    if node == "neo4j_node":
        return {"hits": len(update.get("neo4j_results") or [])}
    if node == "merge_node":
        # 暴露真实来源，便于 UI 在生成前渲染徽章
        return {"sources": update.get("sources", []), "used_rag": update.get("used_rag")}
    if node == "llm_node":
        return {"iterations": update.get("iterations")}
    if node == "dispatch_node":
        return {}
    if node == "rag_agent_node":
        sources = update.get("rag_agent_sources", []) or []
        return {
            "answer": update.get("rag_agent_answer", ""),
            "sources": sources,
            "hits": len(sources),
            "used_rag": update.get("used_rag"),
        }
    if node == "web_agent_node":
        sources = update.get("web_sources", []) or []
        return {
            "answer": update.get("web_agent_answer", ""),
            "sources": sources,
            "hits": len(sources),
            "used_web": update.get("used_web"),
        }
    if node == "integration_node":
        return {"iterations": update.get("iterations")}
    return {}


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """SSE 流：`node` 帧（updates 模式的流水线进度）与 `delta`（custom 模式的字符增量）交替输出。"""
    graph = _select_graph(request, payload.mode)

    initial = {
        "question": payload.message,
        "history": _history_to_dicts(payload.history),
        "iterations": 0,
        "streaming": True,
    }

    async def event_stream():
        try:
            async for mode, data in graph.astream(initial, stream_mode=["updates", "custom"]):
                if mode == "updates":
                    for node, update in data.items():
                        yield _sse(
                            {"type": "node", "node": node, "update": _summarize_update(node, update)}
                        )
                elif mode == "custom":
                    # 节点通过 StreamWriter 写入的负载，形如 {"type": "delta", "text": ...}
                    yield _sse(data)
            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream graph failed: %s", exc)
            yield _sse({"type": "error", "message": f"graph failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 nginx/代理缓冲
            "Connection": "keep-alive",
        },
    )


@router.post("/ingest", response_model=IngestResponse)
def ingest_text(payload: IngestTextRequest, request: Request) -> IngestResponse:
    _, rag = _state(request)
    try:
        stats = rag.ingest_text(payload.text, source=payload.source)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest failed")
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc
    return IngestResponse(status="ok", chunks=stats["chunks"], triples=stats["triples"])


@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(file: UploadFile = File(...), request: Request = None) -> IngestResponse:  # type: ignore[assignment]
    _, rag = _state(request)
    name = file.filename or "upload"
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type '{suffix}'; allowed: 文本/代码、PDF、Word(.docx)",
        )

    raw = await file.read()
    try:
        text = parse_upload(name, raw)
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        stats = rag.ingest_text(text, source=name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("file ingest failed")
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc
    return IngestResponse(status="ok", chunks=stats["chunks"], triples=stats["triples"])


@router.get("/stats")
def stats(request: Request) -> Dict[str, Any]:
    _, rag = _state(request)
    return {
        "qdrant_points": rag.qdrant.count(),
        "neo4j_entities": rag.neo4j.count_entities(),
    }


# ---------------------------------------------------------------------- #
# 文档管理
# ---------------------------------------------------------------------- #
class DocInfo(BaseModel):
    name: str
    chunks: int
    triples: int
    at: int  # timestamp


@router.get("/docs", response_model=List[DocInfo])
def list_docs(request: Request) -> List[DocInfo]:
    """返回已入库的所有文档列表（从 Qdrant payload 中按 source 聚合）。

    以 Qdrant 为单一事实来源：已入库的文档即使刷新界面、重启后端也仍可长期看到。
    每个文档聚合其分片数与最大入库时间戳；三元组无法按文档拆分，置 0（全局
    数量见 ``/api/health``）。
    """
    _, rag = _state(request)
    try:
        all_points = rag.qdrant.scan_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_docs scan failed: %s", exc)
        return []

    docs: Dict[str, DocInfo] = {}
    for point in all_points:
        payload = (point.get("payload") if isinstance(point, dict) else None) or {}
        source = payload.get("source") or "unknown"
        created_at = int(payload.get("created_at") or 0)
        info = docs.setdefault(source, DocInfo(name=source, chunks=0, triples=0, at=created_at))
        info.chunks += 1
        if created_at > info.at:
            info.at = created_at

    # 时间戳缺失（历史数据）或为 0 时退回按名称倒序，保证顺序稳定。
    return sorted(docs.values(), key=lambda d: (d.at, d.name), reverse=True)


@router.post("/docs/delete", response_model=DeleteDocResponse)
def delete_doc(payload: DeleteDocRequest, request: Request) -> DeleteDocResponse:
    """删除指定来源的文档：清除其 Qdrant 分片与 Neo4j 图谱关系。

    用请求体而非路径参数传递 ``source``，因为文件名可能含 ``.`` / 空格 / 中文等。
    """
    _, rag = _state(request)
    try:
        stats = rag.delete_document(payload.source)
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete doc failed for %s", payload.source)
        raise HTTPException(status_code=500, detail=f"delete failed: {exc}") from exc
    return DeleteDocResponse(
        source=stats["source"],
        chunks=stats["chunks"],
        relations=stats["relations"],
    )


@router.post("/docs/delete/batch", response_model=BatchDeleteResponse)
def delete_docs_batch(payload: BatchDeleteRequest, request: Request) -> BatchDeleteResponse:
    """批量删除多个文档：单次请求、逐项容错（单项失败不中断整批）。

    复刻 ``/ingest/files`` 的批量范式：返回每个文档的 ok/failed 明细 + 聚合计数，
    便于前端展示「已删除 N 个，失败 M 个」并定位失败文档。
    """
    _, rag = _state(request)
    stats = rag.delete_documents(payload.sources)
    return BatchDeleteResponse(**stats)


@router.post("/ingest/files", response_model=BatchIngestResponse)
async def ingest_files(
    files: List[UploadFile] = File(default_factory=list),
    folder_path: Optional[str] = None,
    request: Request = None,
) -> BatchIngestResponse:
    """批量上传多个文件或从服务器文件夹路径批量导入。

    - ``files``：浏览器多选 / 整个文件夹上传的多个文件（前端用 FormData 逐个 append）。
    - ``folder_path``：可选的服务器本地目录（递归读取白名单内文件），便于命令行批量导入。
    返回每个文件的成功 / 失败明细，便于前端展示。
    """
    _, rag = _state(request)

    # (display_name, raw_bytes) —— 上传文件与文件夹读取结果汇入同一队列，
    # 统一交给 parse_upload 按扩展名解析（文本/代码、CSV、PDF、Word、Excel）。
    # zip 容器会先经 extract_zip 展开为成员（source = zip 内相对路径）。
    file_sources: List[tuple[str, bytes]] = []
    zip_failures: List[FileIngestResult] = []  # 损坏 / 超限的 zip，单独记失败

    # 1) 服务器文件夹路径（可选，递归读取白名单内文件）
    if folder_path:
        fp = Path(folder_path)
        if not fp.is_dir():
            raise HTTPException(status_code=400, detail=f"folder not found: {folder_path}")
        for fpath in sorted(fp.rglob("*")):
            if fpath.is_file() and fpath.suffix.lower() in ALLOWED_EXTS:
                try:
                    file_sources.append((fpath.name, fpath.read_bytes()))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skip unreadable file %s: %s", fpath, exc)

    # 2) 浏览器上传的文件（zip 先展开为成员，source 取 zip 内相对路径）
    for file in files:
        name = file.filename or "upload"
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        try:
            raw = await file.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cannot read uploaded file %s: %s", name, exc)
            continue

        if suffix == ".zip":
            try:
                members = extract_zip(name, raw)
            except ValueError as exc:  # noqa: BLE001
                zip_failures.append(FileIngestResult(name=name, ok=False, error=str(exc)))
                continue
            if not members:
                zip_failures.append(
                    FileIngestResult(name=name, ok=False, error="zip 内无可入库文件")
                )
            file_sources.extend(members)
        elif suffix in ALLOWED_EXTS:
            file_sources.append((name, raw))
        else:
            # 非白名单类型：直接跳过（多选会混入二进制 / 系统文件）
            continue

    if not file_sources and not zip_failures:
        raise HTTPException(
            status_code=400,
            detail="no ingestible files provided (allowed: 文本/代码、CSV、PDF、Word(.docx)、Excel(.xlsx/.xls)、zip)",
        )

    # 解析 + 逐个入库，收集每文件结果（zip 解压失败项预先并入）
    results: List[FileIngestResult] = list(zip_failures)
    total_chunks = total_triples = 0
    for fname, raw in file_sources:
        try:
            text = parse_upload(fname, raw)
            stats = rag.ingest_text(text, source=fname)
            total_chunks += stats["chunks"]
            total_triples += stats["triples"]
            results.append(FileIngestResult(name=fname, chunks=stats["chunks"], triples=stats["triples"], ok=True))
        except Exception as exc:  # noqa: BLE001
            logger.exception("batch ingest failed for %s", fname)
            results.append(FileIngestResult(name=fname, ok=False, error=str(exc)))

    succeeded = sum(1 for r in results if r.ok)
    failed = len(results) - succeeded
    logger.info(
        "Batch ingest: %d sources, %d ok, %d failed, %d chunks, %d triples",
        len(results), succeeded, failed, total_chunks, total_triples,
    )
    return BatchIngestResponse(
        status="ok" if failed == 0 else "partial",
        chunks=total_chunks,
        triples=total_triples,
        succeeded=succeeded,
        failed=failed,
        files=results,
    )
