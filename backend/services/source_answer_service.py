"""Direct source-file answering helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from services.file_parser import parse_upload
from services.source_file_store import SourceFileStore


@dataclass
class SourceContext:
    context: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    errors: list[str] = field(default_factory=list)


def build_source_context(
    store: SourceFileStore | None,
    owner: str | None,
    conversation_id: str | None,
    max_chars: int,
) -> SourceContext:
    """Parse saved raw source files into one bounded LLM context."""

    if store is None:
        return SourceContext(errors=["源文件存储服务不可用"])

    records = store.list(owner, conversation_id)
    if not records:
        return SourceContext(errors=["当前范围内没有已保存的源文件"])

    parts: list[str] = []
    sources: list[dict[str, Any]] = []
    used = 0
    truncated = False
    errors: list[str] = []

    for record in records:
        name = str(record.get("name") or "upload")
        try:
            raw = store.read(owner, conversation_id, name)
            text = parse_upload(name, raw)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            continue

        header = f"## 源文件：{name}\n"
        available = max_chars - used - len(header)
        if available <= 0:
            truncated = True
            break

        piece = text
        if len(piece) > available:
            piece = piece[:available]
            truncated = True

        parts.append(header + piece)
        used += len(header) + len(piece)
        sources.append(
            {
                "type": "source_file",
                "source": name,
                "content": piece[:800],
                "score": None,
            }
        )

        if truncated:
            break

    return SourceContext(
        context="\n\n".join(parts).strip(),
        sources=sources,
        truncated=truncated,
        errors=errors,
    )


def source_answer_messages(
    question: str,
    context: SourceContext,
    history: Iterable[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Build messages for direct source-file answering."""

    system = (
        "你是源文件解析问答助手。下方资料来自用户上传并保存的原始文件，"
        "已经在本轮问答时重新解析。请优先、严格根据这些源文件内容回答用户问题。\n"
        "如果用户要求“所有、全部、列出、统计、筛选”，必须逐条扫描源文件内容，"
        "不要只看到第一条命中就停止；能列全时列全。\n"
        "如果源文件内容不足以回答，明确说明缺少哪些信息，不要编造。"
    )
    if context.truncated:
        system += "\n注意：源文件解析内容因上下文长度限制已截断，长清单可能不完整。"
    if context.errors:
        system += "\n部分源文件解析失败：" + "；".join(context.errors[:5])
    system += f"\n\n源文件解析内容：\n{context.context or '（无可用源文件内容）'}"

    return [{"role": "system", "content": system}] + list(history or []) + [
        {"role": "user", "content": question}
    ]
