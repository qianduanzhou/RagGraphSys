"""文件解析层：把上传的任意支持类型文件统一解析为纯文本。

按扩展名分发：
  * 文本 / 代码 / 配置类（.txt .md .py .js .ts .java .html .css …）—— 直接 UTF-8 解码；
  * PDF（.pdf）—— 用 pypdf 提取每页文本；
  * Word（.docx）—— 用 python-docx 提取段落与表格文本。

PDF / Word 的解析库采用 **lazy import**：未安装时文本类文件仍可正常工作，
仅在真正遇到 PDF / Word 时才提示安装对应库，避免强依赖。
"""
from __future__ import annotations

import io
from typing import Set

from core.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------- #
# 纯文本 / 代码 / 配置类 —— 直接 UTF-8 解码即可
# ---------------------------------------------------------------------- #
TEXT_EXTS: Set[str] = {
    # 文档 / 数据（.csv 已移至 CSV_EXTS 做结构化解析）
    ".txt", ".md", ".markdown", ".json", ".log", ".rst", ".org",
    # 前端 / Web
    ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss", ".sass",
    ".less", ".vue", ".svelte",
    # 通用编程语言
    ".py", ".java", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".cs", ".go",
    ".rs", ".kt", ".kts", ".scala", ".swift", ".rb", ".php", ".pl", ".lua",
    ".dart", ".r", ".m", ".mm",
    # 脚本 / Shell
    ".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1",
    # 数据 / 配置
    ".sql", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".properties", ".env", ".gradle", ".gemspec",
}

PDF_EXTS: Set[str] = {".pdf"}
WORD_EXTS: Set[str] = {".docx"}  # 旧版 .doc（二进制 OLE）暂不支持，需系统级工具
CSV_EXTS: Set[str] = {".csv"}     # 结构化解析为 markdown 表格
EXCEL_EXTS: Set[str] = {".xlsx", ".xls"}

# 上传白名单（API 层与前端共用同一套语义）。
# 注：zip 是容器，不在此集合——由 services/archive.py 单独展开。
ALLOWED_EXTS: Set[str] = TEXT_EXTS | CSV_EXTS | PDF_EXTS | WORD_EXTS | EXCEL_EXTS

# 表格防爆上限（CSV / Excel 共用）
TABLE_MAX_COLS = 50
TABLE_MAX_ROWS = 5000


def _ext(filename: str) -> str:
    name = (filename or "").lower()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def parse_upload(filename: str, raw: bytes) -> str:
    """根据扩展名把文件字节解析为纯文本。

    无法识别的扩展名、解析异常或提取不到任何文本时抛 :class:`ValueError`，
    由调用方（API 层）转成合适的 HTTP 错误。
    """
    ext = _ext(filename)
    if ext in TEXT_EXTS:
        text = raw.decode("utf-8", errors="ignore")
    elif ext in CSV_EXTS:
        text = _parse_csv(raw)
    elif ext in PDF_EXTS:
        text = _parse_pdf(raw)
    elif ext in WORD_EXTS:
        text = _parse_docx(raw)
    elif ext in EXCEL_EXTS:
        text = _parse_excel(ext, raw)
    else:
        raise ValueError(
            f"unsupported file type '{ext or '(无扩展名)'}'；"
            f"支持：文本/代码、CSV、PDF、Word(.docx)、Excel(.xlsx/.xls)"
        )

    text = (text or "").strip()
    if not text:
        raise ValueError(
            f"未能从 {filename} 提取出任何文本（可能是扫描件 / 图片 / 空文件）"
        )
    return text


# ---------------------------------------------------------------------- #
# 二进制格式解析
# ---------------------------------------------------------------------- #
def _parse_pdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # noqa: BLE001
        raise ValueError("解析 PDF 需要安装 pypdf：pip install pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                pages.append(t)
        return "\n\n".join(pages)
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF parse failed: %s", exc)
        raise ValueError(f"PDF 解析失败：{exc}") from exc


def _parse_docx(raw: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # noqa: BLE001
        raise ValueError("解析 Word 需要安装 python-docx：pip install python-docx") from exc
    try:
        doc = Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        # 表格按行拼接，单元格以 | 分隔，保留结构信息
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DOCX parse failed: %s", exc)
        raise ValueError(f"Word 解析失败：{exc}") from exc


# ---------------------------------------------------------------------- #
# 表格类解析（CSV / Excel）—— 渲染为 markdown 表格，保留表头与行列结构
# ---------------------------------------------------------------------- #
def _render_table(rows: list[list[str]]) -> str:
    """把行列数据渲染为 markdown 表格；空则返回空串。

    列数以最大行为准，短行右侧补空单元格，保证表格规整。
    """
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for r in norm[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _parse_csv(raw: bytes) -> str:
    import csv

    text_in = raw.decode("utf-8", errors="ignore")
    reader = csv.reader(io.StringIO(text_in))
    rows: list[list[str]] = []
    truncated = False
    for i, row in enumerate(reader):
        if i >= TABLE_MAX_ROWS:
            truncated = True
            break
        if len(row) > TABLE_MAX_COLS:
            row = row[:TABLE_MAX_COLS]
        rows.append(row)

    out = _render_table(rows)
    if truncated:
        out += f"\n\n… (已截断，超出 {TABLE_MAX_ROWS} 行)"
    return out


# ---------------------------------------------------------------------- #
# Excel(.xlsx / .xls) —— 按扩展名分发，每 sheet 渲染为一个 markdown 表格
# ---------------------------------------------------------------------- #
def _parse_excel(ext: str, raw: bytes) -> str:
    """按扩展名分发：.xlsx → openpyxl；.xls → xlrd。"""
    if ext == ".xlsx":
        return _parse_xlsx(raw)
    return _parse_xls(raw)


def _iter_sheet_rows(ws) -> list[list[str]]:
    """从 openpyxl worksheet 读取行列，应用 TABLE_MAX_ROWS / TABLE_MAX_COLS 上限。"""
    rows: list[list[str]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= TABLE_MAX_ROWS:
            break
        cells: list[str] = []
        for j, v in enumerate(row):
            if j >= TABLE_MAX_COLS:
                break
            cells.append("" if v is None else str(v))
        rows.append(cells)
    return rows


def _parse_xlsx(raw: bytes) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # noqa: BLE001
        raise ValueError("解析 Excel(.xlsx) 需要安装 openpyxl：pip install openpyxl") from exc
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        parts: list[str] = []
        for ws in wb.worksheets:
            rows = [r for r in _iter_sheet_rows(ws) if any(c.strip() for c in r)]
            if not rows:
                continue
            parts.append(f"## Sheet: {ws.title}\n" + _render_table(rows))
        return "\n\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("XLSX parse failed: %s", exc)
        raise ValueError(f"Excel 解析失败：{exc}") from exc


def _xls_cell_str(v) -> str:
    """xlrd 的数字统一是 float；整数浮点（如 88.0）显示为 '88'。"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _parse_xls(raw: bytes) -> str:
    try:
        import xlrd
    except ImportError as exc:  # noqa: BLE001
        raise ValueError("解析 Excel(.xls) 需要安装 xlrd：pip install xlrd") from exc
    try:
        book = xlrd.open_workbook(file_contents=raw)
        parts: list[str] = []
        for idx in range(book.nsheets):
            sh = book.sheet_by_index(idx)
            rows: list[list[str]] = []
            for i in range(sh.nrows):
                if i >= TABLE_MAX_ROWS:
                    break
                cells: list[str] = []
                for j, v in enumerate(sh.row_values(i)):
                    if j >= TABLE_MAX_COLS:
                        break
                    cells.append("" if v is None or v == "" else _xls_cell_str(v))
                rows.append(cells)
            rows = [r for r in rows if any(c.strip() for c in r)]
            if not rows:
                continue
            parts.append(f"## Sheet: {sh.name}\n" + _render_table(rows))
        return "\n\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("XLS parse failed: %s", exc)
        raise ValueError(f"Excel 解析失败：{exc}") from exc
