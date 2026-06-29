# 文件解析与归档：任意文件怎么变成纯文本（file_parser.py + archive.py）

> 上传一个 PDF、Word、Excel、zip，后端怎么把它们的文字抽出来喂给知识库？这一篇讲 [services/file_parser.py](../backend/services/file_parser.py)（按类型解析成文本）和 [services/archive.py](../backend/services/archive.py)（拆 zip 压缩包）。两个文件分工配合。

## 它在整条链路里的位置

```
上传文件
   ├─ 是 .zip? ─► archive.extract_zip（拆包） ─► [(member名, bytes)]
   ▼
file_parser.parse_upload(name, raw)   按扩展名分发
   ├─ 文本/代码类 ─► UTF-8 直接解码
   ├─ .csv / .xlsx / .xls ─► 渲染成 markdown 表格
   ├─ .pdf ─► pypdf 提取
   ├─ .docx ─► python-docx 提取
   ▼
 纯文本 ─► rag.ingest_text（切块、向量化、图谱，见12篇）
```

---

## 一、file_parser：按扩展名分发

入口函数就是按后缀分发：

```python
def parse_upload(filename, raw):
    ext = _ext(filename)            # ".pdf" / ".docx" / ...
    if ext in TEXT_EXTS:   text = raw.decode("utf-8", errors="ignore")
    elif ext in CSV_EXTS:  text = _parse_csv(raw)
    elif ext in PDF_EXTS:  text = _parse_pdf(raw)
    elif ext in WORD_EXTS: text = _parse_docx(raw)
    elif ext in EXCEL_EXTS: text = _parse_excel(ext, raw)
    else: raise ValueError(f"unsupported file type '{ext}' ...")

    text = (text or "").strip()
    if not text:
        raise ValueError(f"未能从 {filename} 提取出任何文本")
    return text
```

### 白名单：ALLOWED_EXTS

项目维护几组扩展名集合，合并成总白名单 `ALLOWED_EXTS`（API 层和前端共用语义）：

- `TEXT_EXTS`：`.txt .md .json .py .java .ts .html .css ...`（几十种代码/配置）—— 直接 UTF-8 解码。
- `CSV_EXTS`：`.csv`
- `PDF_EXTS`：`.pdf`
- `WORD_EXTS`：`.docx`
- `EXCEL_EXTS`：`.xlsx .xls`

不在白名单的类型直接 `raise ValueError`，API 层转成 415 错误。**注意 `.zip` 不在白名单**——它是「容器」，交给 archive 单独展开。

### 解析失败的处理

两种失败都抛 `ValueError`（由 API 层转 HTTP 400）：

1. 类型不支持。
2. 解析出来是空文本（比如扫描件 PDF、纯图片）。

---

## 二、lazy import：按需导入解析库

PDF/Word/Excel 解析依赖 pypdf、python-docx、openpyxl、xlrd 等库。本项目用 **lazy import**——不在文件顶部 import，而是在真正用到时才导入：

```python
def _parse_pdf(raw):
    try:
        from pypdf import PdfReader       # 只在解析 PDF 时才导入
    except ImportError as exc:
        raise ValueError("解析 PDF 需要安装 pypdf：pip install pypdf") from exc
    ...
```

好处：

- 没装 PDF/Excel 库时，**文本类文件照样能用**，不会因为缺一个可选依赖整个服务起不来。
- 只有真正遇到对应格式才提示装库。

---

## 三、表格类：渲染成 markdown 表格

CSV 和 Excel 都被渲染成 markdown 表格（保留表头和行列结构），便于向量化后检索：

```python
def _render_table(rows):
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]   # 短行补齐
    lines = ["| " + " | ".join(norm[0]) + " |"]           # 表头
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")  # 分隔行
    for r in norm[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
```

- 短行右侧补空单元格，保证表格规整。
- 多 sheet 的 Excel 每个 sheet 渲染成一个表，前缀 `## Sheet: 名字`。
- **防爆上限**：`TABLE_MAX_ROWS=5000`、`TABLE_MAX_COLS=50`，超大表自动截断，避免把向量库撑爆。

### Excel 新旧格式分别处理

- `.xlsx` → openpyxl（`load_workbook`，`read_only=True` 省内存）。
- `.xls`（老格式）→ xlrd。还做了数字处理：xlrd 把整数当 float（如 `88.0`），`_xls_cell_str` 把它显示成 `'88'`。

---

## 四、archive：拆 zip 压缩包

上传 `.zip` 时，API 层先调 `extract_zip(name, raw)` 把它拆成成员文件，再逐个 `parse_upload`：

```python
def extract_zip(filename, raw):
    members = []                       # [(member_source, member_bytes), ...]
    _extract_into(raw, prefix="", depth=1, members=members, state=state)
    return members
```

### member_source = zip 内相对路径

关键设计：展开后的每个成员，`source` 取 **zip 内相对路径**（如 `docs/readme.md`），而不是 zip 文件名本身。这样不同成员在知识库里是独立文档，删除时也能精确到成员级。

### 四重防护

zip 是「容器」，最容易出安全问题（zip bomb、路径穿越）。本项目做了四层防护：

1. **递归深度**：内嵌 zip 递归展开，`ZIP_MAX_DEPTH=5` 封顶，避免无限递归。
2. **解压总量**：累计字节超 `ZIP_MAX_TOTAL_BYTES=200MB` 报错，防 zip bomb（小压缩包解出天文数字）。
3. **成员数量**：超 `ZIP_MAX_MEMBERS=2000` 报错。
4. **路径穿越**：`_norm_source` 拒绝绝对路径（`/xx`、`C:/xx`）和含 `..` 的成员名；跳过目录条目、非白名单、空文件。

### 中文文件名乱码修复（真实坑）

Windows 压缩工具（资源管理器、好压、360）常用 GBK 编码文件名，却不设 zip 规范里的 UTF-8 标志位。Python `zipfile` 因此按 CP437 错解，文件名变乱码，进而作为 `source` 存进知识库、前端显示乱码。`_decode_zip_name` 修复它：

```python
def _decode_zip_name(info):
    if info.flag_bits & 0x800:         # 已置 UTF-8 flag，直接用
        return info.filename
    raw = info.filename.encode("cp437")  # 还原成原始字节
    for enc in ("gb18030", "utf-8"):     # 依次用中文/UTF-8 重解
        try: return raw.decode(enc)
        except UnicodeDecodeError: continue
    return info.filename                # 都失败回退原值，不丢数据
```

---

## 五、批量导入：ingest_files 的分工

[api.py](../backend/api.py) 的 `/ingest/files` 把两件事汇到一起：

```python
file_sources = []                  # (display_name, raw_bytes) 统一队列
for file in files:
    if suffix == ".zip":           # zip 先展开成成员
        members = extract_zip(name, raw)
        file_sources.extend(members)
    elif suffix in ALLOWED_EXTS:   # 普通文件直接进队列
        file_sources.append((name, raw))

for fname, raw in file_sources:    # 统一解析 + 入库，逐个收集 ok/failed
    text = parse_upload(fname, raw)
    stats = rag.ingest_text(text, source=fname)
```

要点：

- **zip 与普通文件汇入同一队列**，统一交给 `parse_upload`，逻辑只写一份。
- **逐项容错**：单个文件解析/入库失败不影响整批，每个文件返回独立的 ok/failed（见 [13 篇](13-api-FastAPI框架详解.md)）。

---

## 修改建议

- **加新格式**（如 `.pptx`）：在 `file_parser` 加一组 `EXTS` + 一个 `_parse_xxx` 函数（lazy import），并入 `ALLOWED_EXTS` 和 `parse_upload` 的分发。
- **解析库未装**：保持 lazy import + 友好提示，别改成顶部强 import（会让缺依赖时整个服务起不来）。
- **zip 防护参数**别轻易放宽（`MAX_TOTAL_BYTES` 等），那是防 zip bomb 的底线。

---

## 学习检查

1. `parse_upload` 是怎么决定用哪种解析方式的？为什么 `.zip` 不在它的分发里？
2. 什么是 lazy import？它相比在文件顶部 import 有什么好处？
3. zip 解出来后，每个成员的 `source` 是什么？为什么不用 zip 文件名本身？
4. archive.py 做了哪几重安全防护？分别防什么？
5. CSV/Excel 被渲染成什么格式？为什么这么做（而不是直接存原始字节）？
6. 中文文件名在 zip 里为什么会乱码？`_decode_zip_name` 怎么救？
