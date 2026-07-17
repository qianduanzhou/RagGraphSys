"""应用配置。

通过 ``python-dotenv`` 从 ``.env`` 文件加载所有配置，并使用
pydantic-settings 进行校验。业务代码必须通过 :func:`get_settings`
读取配置，不要直接访问 ``os.environ``。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/core/config.py -> backend/ 是服务的项目根目录。
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# 显式加载 .env（pydantic-settings 也会读取，这里属于双保险，
# 同时满足由 python-dotenv 完成加载的要求）。
load_dotenv(BASE_DIR / ".env")


class Settings(BaseSettings):
    """从环境变量 / .env 文件加载的强类型配置。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- LLM / Embedding（OpenAI 兼容） ----
    # 通过任意 OpenAI 兼容接口接入（智谱 / OpenAI / DeepSeek / 本地 vLLM 等）。
    # LLM 与 Embedding 可分别接入不同厂商：各自配置对应的 base_url 与 api_key。
    llm_api_key: str = ""
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    llm_model: str = "glm-5.2"
    default_headers: str = ""
    # Embedding 允许接入与 LLM 不同的厂商：base_url / api_key 各自独立。
    # embedding_api_key 留空时回退到 llm_api_key（与 LLM 同厂商时无需重复填写）；
    # embedding_base_url 默认与 llm_base_url 同源，换厂商时改成 embedding 服务地址。
    embedding_api_key: str = ""
    embedding_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    embedding_model: str = "embedding-3"
    embedding_dimension: int = 2048
    llm_request_timeout: int = 60
    llm_temperature: float = 0.6
    llm_max_tokens: int = 2048

    # ---- Qdrant ----
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "rag_documents"
    qdrant_top_k: int = 5
    # 向量检索结果的相关度过滤阈值（cosine 相似度，0~1）。
    # merge 阶段会丢弃低于此分的结果，避免无关命中污染上下文。
    # 闲聊等无关查询因此自然不命中、context 为空，由 LLM 当通用对话处理。
    qdrant_score_threshold: float = 0.35

    # ---- Neo4j ----
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    # ---- 联网搜索（Tavily） ----
    # 多智能体模式下「联网智能体」使用。留空则联网搜索不可用，web_agent 自动降级。
    tavily_api_key: str = ""
    tavily_max_results: int = 5

    # ---- RAG 流水线 ----
    chunk_size: int = 500
    chunk_overlap: int = 80
    max_reflection_iterations: int = 2  # 最大生成尝试次数
    # 聚合型查询（「列出所有岗位」等整文档穷举）专用：
    # 触发时按命中文档拉取全部分片（不受 top_k 限制），并放宽输出 token 上限，
    # 避免 top-k 语义检索只回出部分条目、长列表被截断。
    rag_aggregate_max_chunks: int = 60  # 整文档拉取的分片上限，控制上下文体量
    llm_max_tokens_aggregate: int = 4096  # 聚合型回答的输出上限

    # ---- 应用 ----
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:4173"
    log_level: str = "INFO"
    auth_db_path: str = str(BASE_DIR / "data" / "users.json")
    conversations_db_path: str = str(BASE_DIR / "data" / "conversations.json")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def default_headers_dict(self) -> dict[str, str] | None:
        raw = (self.default_headers or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("DEFAULT_HEADERS must be a JSON object string") from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed.items()
        ):
            raise ValueError("DEFAULT_HEADERS must be a JSON object with string keys and values")
        return parsed


@lru_cache
def get_settings() -> Settings:
    """返回缓存的 :class:`Settings` 单例。"""
    return Settings()
