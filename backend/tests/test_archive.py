"""services.archive 的测试：zip 容器解包。

全部用 zipfile 在内存中生成样本，不落盘。
"""
import io
import struct
import zipfile
import zlib

import pytest

from services.archive import extract_zip


def _make_zip(members: dict[str, bytes]) -> bytes:
    """members: {成员名: 内容} -> zip 字节。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ------------------------------------------------------------------ #
# 基础解压 + source 命名
# ------------------------------------------------------------------ #
def test_extract_zip_returns_members_with_relative_sources():
    raw = _make_zip({
        "readme.md": b"# hello",
        "docs/note.txt": b"note body",
        "data.csv": b"a,b\n1,2\n",
    })
    members = dict(extract_zip("bundle.zip", raw))
    assert set(members.keys()) == {"readme.md", "docs/note.txt", "data.csv"}
    assert members["docs/note.txt"] == b"note body"


def test_extract_zip_normalizes_leading_dot_slash():
    raw = _make_zip({"./a.txt": b"x"})
    members = dict(extract_zip("p.zip", raw))
    assert "a.txt" in members


# ------------------------------------------------------------------ #
# 过滤：非白名单类型 / 目录条目 / 空文件
# ------------------------------------------------------------------ #
def test_extract_zip_skips_disallowed_dirs_and_empty():
    raw = _make_zip({
        "keep.md": b"ok",
        "image.png": b"\x89PNG fake",      # 非白名单
        "emptydir/": b"",                   # 目录条目
        "sub/notes.txt": b"nested ok",
        "blank.py": b"",                    # 0 字节
    })
    members = dict(extract_zip("mix.zip", raw))
    assert set(members.keys()) == {"keep.md", "sub/notes.txt"}


# ------------------------------------------------------------------ #
# 递归内嵌 zip
# ------------------------------------------------------------------ #
def test_extract_zip_recurses_nested_zip():
    inner = _make_zip({"inner.txt": b"deep"})
    raw = _make_zip({"nested.zip": inner})
    members = dict(extract_zip("outer.zip", raw))
    assert "nested.zip/inner.txt" in members
    assert members["nested.zip/inner.txt"] == b"deep"


# ------------------------------------------------------------------ #
# zip-bomb 防护：字节上限 / 成员数上限
# ------------------------------------------------------------------ #
def test_extract_zip_guard_total_bytes(monkeypatch):
    import services.archive as arch
    monkeypatch.setattr(arch, "ZIP_MAX_TOTAL_BYTES", 1000)
    raw = _make_zip({"big.txt": b"A" * 10000})   # 解压后远超 1000
    with pytest.raises(ValueError, match="超限"):
        extract_zip("bomb.zip", raw)


def test_extract_zip_guard_member_count(monkeypatch):
    import services.archive as arch
    monkeypatch.setattr(arch, "ZIP_MAX_MEMBERS", 3)
    raw = _make_zip({f"f{i}.txt": b"x" for i in range(5)})
    with pytest.raises(ValueError, match="超限"):
        extract_zip("many.zip", raw)


# ------------------------------------------------------------------ #
# 损坏 zip
# ------------------------------------------------------------------ #
def test_extract_zip_corrupted_raises():
    with pytest.raises(ValueError, match="损坏"):
        extract_zip("bad.zip", b"this is not a zip file at all")


# ------------------------------------------------------------------ #
# 中文文件名编码还原：中文 Windows 工具打出的 zip 常用 GBK 编码文件名、
# 却不设 UTF-8 flag，导致 zipfile 按 CP437 错解成 box-drawing 乱码。
# ------------------------------------------------------------------ #
def _make_gbk_zip(members: list[tuple[str, bytes]]) -> bytes:
    """手工构造中文 Windows 风格 zip：文件名按 GBK 写入、不设 UTF-8 flag。

    不能用 ``zipfile.writestr``——它对非 ASCII 文件名会强制设 UTF-8 flag，
    无法模拟「资源管理器/好压」这类用 GBK 却不设 flag 的工具。故按 zip 二进制
    格式（STORE 存储）直接拼字节，文件名字段写 GBK 原始字节、flag 字段置 0。
    """
    local_blob = bytearray()
    central_parts: list[bytes] = []
    for name, data in members:
        name_bytes = name.encode("gbk")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        local_offset = len(local_blob)
        # local file header（30B 固定 + name + data），flag=0 不设 UTF-8
        local_blob += struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50, 20, 0, 0, 0, 0,
            crc, len(data), len(data),
            len(name_bytes), 0,
        )
        local_blob += name_bytes + data
        # central directory record（46B 固定 + name），flag=0 不设 UTF-8
        central_parts.append(struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50, 20, 20, 0, 0, 0, 0,
            crc, len(data), len(data),
            len(name_bytes), 0, 0, 0, 0, 0,
            local_offset,
        ) + name_bytes)
    central_blob = b"".join(central_parts)
    central_start = len(local_blob)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50, 0, 0, len(members), len(members),
        len(central_blob), central_start, 0,
    )
    return bytes(local_blob) + central_blob + eocd


def test_decode_zip_name_restores_gbk_without_utf8_flag():
    """GBK 文件名、未设 UTF-8 flag（被 zipfile 按 CP437 错解为乱码），
    _decode_zip_name 应还原为正确中文。"""
    from services.archive import _decode_zip_name

    gbk_bytes = "基础语法/字典.md".encode("gbk")
    mojibake = gbk_bytes.decode("cp437")  # 模拟 zipfile 读取时的 CP437 默认解码
    info = zipfile.ZipInfo(filename=mojibake)
    info.flag_bits = 0  # 未设 UTF-8 flag
    assert _decode_zip_name(info) == "基础语法/字典.md"


def test_decode_zip_name_keeps_utf8_flagged_as_is():
    """UTF-8 flag 已置 → filename 已正确，原样返回，不做二次解码。"""
    from services.archive import _decode_zip_name

    info = zipfile.ZipInfo(filename="已正确.md")
    info.flag_bits = 0x800
    assert _decode_zip_name(info) == "已正确.md"


def test_extract_zip_decodes_gbk_chinese_filenames():
    """端到端：模拟中文 Windows 工具产出的 zip（GBK 文件名、未设 UTF-8 flag），
    extract_zip 应解出正确中文 source，而非 box-drawing 乱码。"""
    raw = _make_gbk_zip([
        ("knowledge/Python/基础语法/字典.md", b"# dict\ndict content"),
        ("readme.txt", b"plain ascii"),
    ])
    members = dict(extract_zip("cn.zip", raw))
    assert "knowledge/Python/基础语法/字典.md" in members
    assert members["knowledge/Python/基础语法/字典.md"] == b"# dict\ndict content"
    assert "readme.txt" in members
