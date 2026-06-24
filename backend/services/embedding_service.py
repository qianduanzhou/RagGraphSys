"""Embedding 服务 —— 唯一与向量化模型交互的地方。

通过 LangChain 的 :class:`langchain_openai.OpenAIEmbeddings` 初始化，
直接以参数指向任意 OpenAI 兼容接口（与 ``ChatOpenAI`` 同源）。
业务 / 检索代码只调用本服务的 ``embed`` / ``embed_batch``，不直接接触 LangChain 对象。
"""
from __future__ import annotations

from typing import List, Optional

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from core.config import Settings
from core.logger import get_logger
from core.utils import timing

logger = get_logger(__name__)


class EmbeddingService:
    """基于 LangChain ``OpenAIEmbeddings`` 的 embedding 高层封装。"""

    def __init__(self, settings: Settings, embeddings: Optional[Embeddings] = None):
        self.settings = settings
        if embeddings is not None:
            # 测试或自定义实现可注入
            self.embeddings = embeddings
            return

        # 直接以参数形式把 OpenAI 兼容端点传给 OpenAIEmbeddings
        # （与 ChatOpenAI 同源），避免向进程级 os.environ 写入凭据。
        self.embeddings = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        logger.info(
            "Embedding 初始化完成：model=%s，base_url=%s",
            settings.embedding_model,
            settings.llm_base_url,
        )

    @timing
    def embed(self, text: str) -> List[float]:
        """单条文本 -> 向量（embed_query 语义）。"""
        return self.embeddings.embed_query(text)

    @timing
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """多条文本 -> 向量列表（顺序保持）。"""
        if not texts:
            return []
        return self.embeddings.embed_documents(list(texts))

    @property
    def dimension(self) -> int:
        return self.settings.embedding_dimension
