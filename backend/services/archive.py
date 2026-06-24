"""zip 容器解包层：把上传的 .zip 展开为 [(member_source, member_bytes), ...]。

与 services.file_parser 的叶子解析分工：
  * archive 负责 "拆包 + source 命名 + 防护"；
  * file_parser 负责把每个成员字节解析为文本。

防护：
  * 递归解压内嵌 zip（最大深度 ZIP_MAX_DEPTH）；
  * 累计解压字节 / 成员总数上限（防 zip bomb）；
  * 跳过目录条目、非白名单类型、空文件；
  * 防御性过滤绝对路径 / ".." 成员名（内存处理不写盘，仍防异常输入）。
"""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple

from core.logger import get_logger
from services.file_parser import ALLOWED_EXTS

logger = get_logger(__name__)

ZIP_MAX_DEPTH = 5
ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024   # 200 MB
ZIP_MAX_MEMBERS = 2000


def _ext(name: str) -> str:
    low = name.lower()
    if "." not in low:
        return ""
    return "." + low.rsplit(".", 1)[-1]


def _norm_source(name: str) -> str | None:
    """规范化成员名为 zip 内相对路径（正斜杠）；异常成员返回 None（跳过）。"""
    n = name.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    # 绝对路径（Unix / 或 Windows 盘符 x:/）或 ".." 穿越 —— 防御性拒绝
    if n.startswith("/"):
        return None
    if len(n) >= 3 and n[1:3] == ":/":
        return None
    if any(part == ".." for part in n.split("/")):
        return None
    return n


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """还原 zip 条目名的正确编码。

    Python ``zipfile`` 对未置 UTF-8 flag（general purpose bit 11）的条目，
    按 zip 规范用 CP437 解码文件名。但中文 Windows 上的压缩工具（资源管理器、
    好压、360 等）常用 GBK/GB18030 编码文件名却不设该 flag，导致 GBK 字节被
    CP437 错解成 box-drawing 乱码（如「基础语法」→「╗∙┤í╙∩╖¿」），随后作为
    source 存入知识库，前端文档列表即显示乱码。

    处理：flag 已置 → filename 已按 UTF-8 正确解码，直接用；否则把 CP437 串
    还原为原始字节，依次用 GB18030 / UTF-8 重解；都失败则回退原 filename（不丢数据）。
    """
    if info.flag_bits & 0x800:
        return info.filename
    name = info.filename
    try:
        raw = name.encode("cp437")
    except (UnicodeEncodeError, LookupError):
        return name
    for enc in ("gb18030", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def extract_zip(filename: str, raw: bytes) -> List[Tuple[str, bytes]]:
    """解压 zip，返回 [(member_source, member_bytes), ...]。

    member_source 为 zip 内相对路径（正斜杠），如 ``docs/readme.md``；
    递归展开内嵌 zip；损坏或超出防护上限时抛 :class:`ValueError`。
    """
    members: List[Tuple[str, bytes]] = []
    state = {"total": 0, "count": 0}
    _extract_into(raw, prefix="", depth=1, members=members, state=state)
    return members


def _extract_into(raw: bytes, prefix: str, depth: int,
                  members: List[Tuple[str, bytes]], state: dict) -> None:
    """递归解压。prefix 为外层累积的 source 前缀（含尾 ``/`` 或空）。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"zip 解压失败（文件损坏）：{exc}") from exc

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            src = _norm_source(_decode_zip_name(info))
            if not src:
                continue
            ext = _ext(src)

            # 内嵌 zip：递归展开
            if ext == ".zip":
                if depth >= ZIP_MAX_DEPTH:
                    logger.warning("skip nested zip beyond max depth: %s", prefix + src)
                    continue
                try:
                    inner_raw = zf.read(info)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("cannot read nested zip %s: %s", src, exc)
                    continue
                _extract_into(inner_raw, prefix + src + "/", depth + 1, members, state)
                continue

            if ext not in ALLOWED_EXTS:
                continue

            try:
                data = zf.read(info)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cannot read zip member %s: %s", src, exc)
                continue
            if not data:
                continue

            state["total"] += len(data)
            state["count"] += 1
            if state["total"] > ZIP_MAX_TOTAL_BYTES:
                raise ValueError(
                    f"zip 解压超限：累计 {state['total']} 字节超过上限 {ZIP_MAX_TOTAL_BYTES}"
                )
            if state["count"] > ZIP_MAX_MEMBERS:
                raise ValueError(
                    f"zip 解压超限：成员数 {state['count']} 超过上限 {ZIP_MAX_MEMBERS}"
                )
            members.append((prefix + src, data))
