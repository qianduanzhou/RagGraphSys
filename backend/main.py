"""FastAPI 应用入口。

本地运行：``python main.py``（或 ``uvicorn main:app --reload``）
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import router as api_router
from core.config import get_settings
from core.logger import get_logger
from graph import build_graph
from multiagent import build_multi_agent_graph
from rag.neo4j_store import Neo4jStore
from rag.qdrant_store import QdrantStore
from rag.rag_service import RagService
from services.embedding_service import EmbeddingService
from services.llm_service import LLMService
from services.web_search_service import WebSearchService

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """构造各服务单例并挂载到 ``app.state``。"""
    logger.info("Initialising services...")

    llm = LLMService(settings)
    embedding = EmbeddingService(settings)
    qdrant = QdrantStore(settings, embedding)
    neo4j = Neo4jStore(settings)
    rag = RagService(qdrant=qdrant, neo4j=neo4j, llm=llm, settings=settings)

    # 尽力预热依赖：缺失的存储会优雅降级，而不会阻塞启动。
    try:
        qdrant.ensure_collection()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Qdrant unavailable at startup: %s", exc)
    try:
        neo4j.verify()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j unavailable at startup: %s", exc)

    app.state.llm = llm
    app.state.embedding = embedding
    app.state.qdrant = qdrant
    app.state.neo4j = neo4j
    app.state.rag = rag
    app.state.graph = build_graph(llm, rag, settings)

    web = WebSearchService(settings)
    app.state.web = web
    app.state.multi_agent_graph = build_multi_agent_graph(llm, rag, web, settings)

    logger.info("Application ready: http://%s:%d", settings.app_host, settings.app_port)
    yield

    try:
        neo4j.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j close failed: %s", exc)
    logger.info("Application stopped.")


app = FastAPI(
    title="Hybrid Graph + Vector RAG AI System",
    version="1.0.0",
    description="LangGraph-orchestrated hybrid RAG (Qdrant + Neo4j) powered by an OpenAI-compatible LLM.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {"service": "hybrid-rag-graph", "docs": "/docs", "health": "/api/health"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )
