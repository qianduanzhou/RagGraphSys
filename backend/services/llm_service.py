"""LLM 服务 —— 唯一与大模型交互的地方。

通过 LangChain 的 :class:`langchain_openai.ChatOpenAI` 初始化，指向任意
OpenAI 兼容接口（``base_url`` / ``api_key`` 取自 ``.env``）。所有对话生成、
流式输出、关键词/图谱抽取、反思均封装于此；业务与图代码只调用本服务的方法，
不直接接触 LangChain 对象。
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.config import Settings
from core.logger import get_logger
from core.utils import extract_json, timing

logger = get_logger(__name__)


def _to_lc_messages(messages: List[Dict[str, str]]):
    """把 OpenAI 风格的 ``{role, content}`` 消息列表转成 LangChain 消息对象。"""
    result = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        else:
            result.append(HumanMessage(content=content))
    return result


class LLMService:
    """基于 LangChain ``ChatOpenAI`` 的 LLM 高层封装。"""

    def __init__(self, settings: Settings, llm: Optional[BaseChatModel] = None):
        self.settings = settings
        if llm is not None:
            # 测试或自定义实现可注入
            self.llm = llm
        else:
            self.llm = ChatOpenAI(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                timeout=settings.llm_request_timeout,
            )
            logger.info(
                "LLM 初始化完成：model=%s，base_url=%s",
                settings.llm_model,
                settings.llm_base_url,
            )

    def _model_with(self, temperature: Optional[float], max_tokens: Optional[int]):
        """按单次调用需要临时绑定 temperature/max_tokens（不改原对象）。"""
        kwargs: Dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return self.llm.bind(**kwargs) if kwargs else self.llm

    @timing
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **extra: Any,
    ) -> str:
        """非流式对话，返回助手消息文本。"""
        model = self._model_with(temperature, max_tokens)
        resp = model.invoke(_to_lc_messages(messages))
        return resp.content

    @timing
    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[str]:
        """流式对话，逐 token（delta）yield，供 SSE 端点转发。"""
        model = self._model_with(temperature, max_tokens)
        for chunk in model.stream(_to_lc_messages(messages)):
            content = chunk.content
            if not content:
                continue
            if isinstance(content, str):
                yield content
            else:
                # 多模态 / 分块 content（list of dict），取其中的文本部分
                yield "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )

    # ------------------------------------------------------------------ #
    # 基于 chat() 的高层抽取 / 反思方法
    # ------------------------------------------------------------------ #
    @timing
    def extract_keywords(self, query: str, max_entities: int = 5) -> List[str]:
        """从用户问题中抽取用于 Neo4j 检索的关键实体。"""
        system = (
            "你是一个实体抽取器。从用户问题中抽取用于知识图谱检索的关键实体"
            "（专有名词、人名、产品名、技术名词等）。"
            "只输出一个 JSON 字符串数组，例如 [\"实体A\",\"实体B\"]，最多 "
            f"{max_entities} 个，不要输出任何额外文字。"
        )
        try:
            raw = self.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": query}],
                temperature=0.0,
                max_tokens=128,
            )
            parsed = extract_json(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()][:max_entities]
        except Exception as exc:  # noqa: BLE001 - 抽取是尽力而为
            logger.warning("关键词抽取失败：%s", exc)
        return []

    @timing
    def extract_graph(self, text: str, max_triples: int = 12) -> List[Dict[str, str]]:
        """从文本中抽取 (head, rel, tail) 三元组，供 Neo4j 入库。"""
        system = (
            "你是一个知识图谱抽取器。从给定文本中抽取实体与关系三元组。"
            "只输出 JSON 对象，格式为："
            '{"entities":["..."], "triples":[{"head":"头实体","rel":"关系","tail":"尾实体"}]}。'
            "关系用简短的词组表示（中文或英文均可，不要包含特殊字符）。不要输出额外文字。"
        )
        try:
            raw = self.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": text[:4000]}],
                temperature=0.1,
                max_tokens=1024,
            )
            parsed = extract_json(raw) or {}
            triples = parsed.get("triples", []) if isinstance(parsed, dict) else []
            clean = [
                {
                    "head": str(t["head"]).strip(),
                    "rel": str(t.get("rel", "relates_to")).strip() or "relates_to",
                    "tail": str(t["tail"]).strip(),
                }
                for t in triples
                if isinstance(t, dict) and t.get("head") and t.get("tail")
            ]
            return clean[:max_triples]
        except Exception as exc:  # noqa: BLE001 - 尽力而为
            logger.warning("图谱抽取失败：%s", exc)
            return []

    @timing
    def reflect(self, question: str, answer: str, context: str) -> Dict[str, Any]:
        """审核答案：是否通过 + 可选改进建议。"""
        system = (
            "你是答案审核员。判断回答是否：1) 准确且切题地回答了问题；"
            "2) 若参考资料与问题相关，回答应充分利用资料且不编造；若参考资料与问题无关，"
            "回答基于通用知识即可，不应判为不通过。"
            '只输出 JSON：{"pass": true/false, "feedback": "若不通过，给出具体改进建议；若通过，留空"}。'
            "不要输出额外文字。"
        )
        user = (
            f"问题：{question}\n\n参考资料：\n{context or '（无参考资料）'}\n\n回答：\n{answer}"
        )
        try:
            raw = self.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.0,
                max_tokens=256,
            )
            parsed = extract_json(raw) or {}
            # 解析不确定时默认通过，避免无限循环
            return {
                "pass": bool(parsed.get("pass", True)),
                "feedback": str(parsed.get("feedback", "") or ""),
            }
        except Exception as exc:  # noqa: BLE001 - 反思不能让整张图崩掉
            logger.warning("反思失败，按通过处理：%s", exc)
            return {"pass": True, "feedback": ""}
