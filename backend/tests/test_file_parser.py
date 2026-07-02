"""services.file_parser 的测试。

文本 / 代码类用真实字节；PDF 用 monkeypatch 注入假 pypdf；
Word(.docx) 用真实 python-docx 做往返（未装则跳过）。
"""
import io
import types

import pytest

from services.file_parser import ALLOWED_EXTS, parse_upload


# ------------------------------------------------------------------ #
# 文本 / 代码 / 配置类
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("name", ["a.txt", "main.py", "App.tsx", "Main.java",
                                  "index.html", "style.css", "conf.json", "notes.md"])
def test_parse_text_code(name):
    assert parse_upload(name, "print('hello')".encode()) == "print('hello')"


def test_parse_text_strips_whitespace():
    assert parse_upload("a.py", b"   code here\n\n") == "code here"


def test_parse_code_handles_non_utf8_bytes():
    # 非 UTF-8 字节被 ignore，不抛错
    out = parse_upload("a.py", b"ok \xff\xfe end")
    assert "ok" in out and "end" in out


# ------------------------------------------------------------------ #
# PDF（monkeypatch pypdf，避免依赖真实 PDF 生成库）
# ------------------------------------------------------------------ #
def test_parse_pdf(monkeypatch):
    fake_page = types.SimpleNamespace(extract_text=lambda: "Page one text")
    fake_reader = lambda stream: types.SimpleNamespace(pages=[fake_page])
    fake_mod = types.ModuleType("pypdf")
    fake_mod.PdfReader = fake_reader
    monkeypatch.setitem(__import__("sys").modules, "pypdf", fake_mod)

    assert parse_upload("doc.pdf", b"%PDF- fake") == "Page one text"
    assert ".pdf" in ALLOWED_EXTS


def test_parse_pdf_expands_merged_table_notes(monkeypatch):
    note = "\u9002\u5e94\u9ad8\u7a7a\u4f5c\u4e1a\u3002"
    header = "\u5c0f\u8ba1 30"
    footer = "\u5c0f\u8ba1 80"
    text = (
        f"{header} "
        "62 G2026020701 Guangzhou power supply maintenance Guangdong 34 "
        f"{note} "
        "63 G2026020702 Shenzhen power supply maintenance Guangdong 10 "
        "64 G2026020703 Huizhou power supply maintenance Guangdong 4 "
        f"{footer}"
    )
    fake_page = types.SimpleNamespace(extract_text=lambda: text)
    fake_reader = lambda stream: types.SimpleNamespace(pages=[fake_page])
    fake_mod = types.ModuleType("pypdf")
    fake_mod.PdfReader = fake_reader
    monkeypatch.setitem(__import__("sys").modules, "pypdf", fake_mod)

    parsed = parse_upload("jobs.pdf", b"%PDF- fake")

    assert "\u3010PDF\u8868\u683c\u5408\u5e76\u5907\u6ce8\u5c55\u5f00\u3011" in parsed
    assert parsed.count(f"\u5907\u6ce8\uff1a{note}") == 3
    assert "G2026020702" in parsed
    assert "G2026020703" in parsed


def test_parse_pdf_missing_lib(monkeypatch):
    # 未装 pypdf 时给出可读错误
    import sys
    monkeypatch.setitem(sys.modules, "pypdf", None)
    with pytest.raises(ValueError, match="pypdf"):
        parse_upload("doc.pdf", b"%PDF-")


# ------------------------------------------------------------------ #
# Word(.docx) —— 真实往返
# ------------------------------------------------------------------ #
def test_parse_docx_roundtrip():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Hello from Word")
    buf = io.BytesIO()
    doc.save(buf)
    text = parse_upload("note.docx", buf.getvalue())
    assert "Hello from Word" in text
    assert ".docx" in ALLOWED_EXTS


# ------------------------------------------------------------------ #
# CSV —— 结构化解析为 markdown 表格（保留表头与行列结构）
# ------------------------------------------------------------------ #
def test_parse_csv_renders_markdown_table():
    raw = b"name,age\nAlice,30\nBob,25\n"
    text = parse_upload("data.csv", raw)
    assert text.splitlines()[0] == "| name | age |"
    assert "| --- | --- |" in text
    assert "| Alice | 30 |" in text
    assert "| Bob | 25 |" in text


def test_parse_csv_is_structured_not_raw_text():
    # csv 不应再是原始逗号串，应含表格分隔符
    assert "|" in parse_upload("data.csv", b"a,b\n1,2\n")


def test_csv_in_allowed_exts():
    assert ".csv" in ALLOWED_EXTS


# ------------------------------------------------------------------ #
# Excel(.xlsx) —— openpyxl 往返，渲染为每 sheet 一个 markdown 表格
# ------------------------------------------------------------------ #
def test_parse_xlsx_renders_sheet_as_table():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "销售"
    ws.append(["产品", "数量"])
    ws.append(["苹果", 10])
    ws.append(["香蕉", 5])
    buf = io.BytesIO()
    wb.save(buf)
    text = parse_upload("data.xlsx", buf.getvalue())
    assert "## Sheet: 销售" in text
    assert "| 产品 | 数量 |" in text
    assert "苹果" in text and "10" in text
    assert ".xlsx" in ALLOWED_EXTS


def test_parse_xlsx_multiple_sheets_merged():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "S1"
    ws1.append(["a"]); ws1.append(["1"])
    ws2 = wb.create_sheet("S2")
    ws2.append(["b"]); ws2.append(["2"])
    buf = io.BytesIO()
    wb.save(buf)
    text = parse_upload("multi.xlsx", buf.getvalue())
    assert "## Sheet: S1" in text
    assert "## Sheet: S2" in text


# ------------------------------------------------------------------ #
# Excel(.xls) —— xlwt 生成 + xlrd 解析往返
# ------------------------------------------------------------------ #
def test_parse_xls_renders_table():
    xlwt = pytest.importorskip("xlwt")
    pytest.importorskip("xlrd")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("数据")
    ws.write(0, 0, "姓名"); ws.write(0, 1, "分数")
    ws.write(1, 0, "张三"); ws.write(1, 1, 88)
    buf = io.BytesIO()
    wb.save(buf)
    text = parse_upload("old.xls", buf.getvalue())
    assert "## Sheet: 数据" in text
    assert "姓名" in text and "分数" in text
    assert "张三" in text and "88" in text
    assert ".xls" in ALLOWED_EXTS


# ------------------------------------------------------------------ #
# 错误路径
# ------------------------------------------------------------------ #
def test_parse_unsupported_type():
    with pytest.raises(ValueError, match="不支持的文件类型"):
        parse_upload("program.exe", b"MZ")


def test_parse_empty_raises():
    with pytest.raises(ValueError, match="提取"):
        parse_upload("empty.py", b"   \n\t  ")


def test_allowed_exts_covers_common_dev_files():
    for ext in [".py", ".js", ".ts", ".tsx", ".java", ".html", ".css", ".go",
                ".pdf", ".docx", ".sql", ".yaml"]:
        assert ext in ALLOWED_EXTS
