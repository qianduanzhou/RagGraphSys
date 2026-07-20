"""FastAPI 应用入口。

本地运行：``python main.py``（或 ``uvicorn main:app --reload``）
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import time

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
from services.auth_service import AuthService
from services.conversation_service import ConversationService
from services.embedding_service import EmbeddingService
from services.llm_service import LLMService
from services.source_file_store import SourceFileStore
from services.web_search_service import WebSearchService

settings = get_settings()
logger = get_logger(__name__)


RETRY_ATTEMPTS = 30
RETRY_INTERVAL = 2.0


def _retry_ready(name: str, fn) -> None:
    """对依赖预热做退避重试，最多 RETRY_ATTEMPTS 次，每次间隔 RETRY_INTERVAL 秒。

    Qdrant/Neo4j 容器启动后可能需要数秒（尤其 Neo4j）才真正可连接；
    只试一次会在它们尚未就绪时放弃，导致 Qdrant 集合永远不创建、健康检查长期离线。
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            fn()
            if attempt > 1:
                logger.info("%s ready after %d retry attempt(s)", name, attempt - 1)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s not ready yet (attempt %d/%d): %s",
                name, attempt, RETRY_ATTEMPTS, exc,
            )
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_INTERVAL)
    logger.warning("%s still unavailable after %d attempts; degraded mode.", name, RETRY_ATTEMPTS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """构造各服务单例并挂载到 ``app.state``。"""
    logger.info("Initialising services...")

    auth = AuthService(settings.auth_db_path)
    conversations = ConversationService(settings.conversations_db_path)
    source_files = SourceFileStore(settings.source_files_dir)
    llm = LLMService(settings)
    embedding = EmbeddingService(settings)
    qdrant = QdrantStore(settings, embedding)
    neo4j = Neo4jStore(settings)
    rag = RagService(qdrant=qdrant, neo4j=neo4j, llm=llm, settings=settings)

    # 依赖预热：退避重试至 Qdrant/Neo4j 就绪（见 _retry_ready），
    # 避免容器刚启动、服务尚未可连接时只试一次就放弃。
    _retry_ready("Qdrant", qdrant.ensure_collection)
    _retry_ready("Neo4j", neo4j.verify)

    app.state.auth = auth
    app.state.settings = settings
    app.state.conversations = conversations
    app.state.source_files = source_files
    app.state.llm = llm
    app.state.embedding = embedding
    app.state.qdrant = qdrant
    app.state.neo4j = neo4j
    app.state.rag = rag
    app.state.graph = build_graph(llm, rag, settings)

    web = WebSearchService(settings)
    app.state.web = web
    app.state.multi_agent_graph = build_multi_agent_graph(llm, rag, web, settings, source_files=source_files)

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
