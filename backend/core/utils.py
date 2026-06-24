"""通用工具：计时装饰器、JSON 提取、文本切分。"""
from __future__ import annotations

import functools
import json
import re
import time
from typing import Any, Callable, List, TypeVar

from .logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def timing(func: Callable[..., T]) -> Callable[..., T]:
    """记录 ``func`` 的实际耗时，不改变其行为。"""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            logger.info("%s executed in %.3fs", func.__qualname__, time.perf_counter() - start)

    return wrapper


def extract_json(text: str) -> Any:
    """尽力从 ``text`` 中提取第一个 JSON 对象/数组。

    会处理 LLM 偶尔输出的 markdown 代码围栏及前后多余文字。
    若无法恢复出有效 JSON 则返回 ``None``。
    """
    if not text:
        return None

    cleaned = text.strip()
    # 去除 ```json ... ``` 代码围栏。
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


def truncate(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 80) -> List[str]:
    """使用 LangChain 的递归切分器将 ``text`` 切分为带重叠的片段。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
    )
    return [doc.page_content for doc in splitter.create_documents([text])]


def sanitize_relation_type(rel: str, fallback: str = "RELATES_TO") -> str:
    """把自由格式的关系标签清洗为合法的 Neo4j 关系类型。

    关系类型会被直接插入 Cypher（无法参数化），因此只能包含
    ``[A-Za-z0-9_]``。
    """
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", rel or "").strip("_").upper()
    return cleaned[:48] or fallback
