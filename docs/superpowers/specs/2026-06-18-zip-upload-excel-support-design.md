# Zip 上传解析 + Excel/CSV 支持 设计

- **日期**: 2026-06-18
- **状态**: 已确认，待实现
- **范围**: backend (Python / FastAPI) + frontend (React / TypeScript)

---

## 1. 背景与动机

当前知识库上传入口（前端 `Sidebar.tsx` + 后端 `/api/ingest/files`）依赖浏览器的
`webkitdirectory` 实现"上传整个文件夹"。该方案存在缺陷：

- 跨浏览器表现不一致，部分环境不触发文件夹选择；
- 会把目录里的系统文件 / 二进制文件一并带入，需要前端逐个过滤；
- 目录结构在多选模式下丢失，重名文件互相覆盖。

解析层 `services/file_parser.py` 支持 文本 / 代码 / 配置类、PDF、Word(.docx)，但：

- 不支持 **zip 容器**（用户希望用 zip 打包上传替代文件夹）；
- 不支持 **Excel**（企业知识库最常见的格式之一）；
- `.csv` 当前仅作纯文本 UTF-8 解码，未保留行列结构。

本设计目标：

1. 用 **zip 打包上传**替代文件夹上传——跨浏览器一致、保留目录结构、不混入系统文件；
2. 新增 **Excel(.xlsx / .xls)** 解析为表格文本；
3. **CSV** 从纯文本解码升级为 markdown 表格结构化解析。

## 2. 目标与非目标

**目标**

- 上传单个 `.zip`，后端解包、按成员逐个解析入库；成员 `source = zip 内相对路径`。
- 支持 `.xlsx` / `.xls` 解析为表格文本（多 sheet 合并为一个 source）。
- CSV 渲染为 markdown 表格，保留表头与行列结构。
- 前端移除"上传整个文件夹"按钮，改为 zip 上传入口；主拖拽 / 多选入口 accept 扩展。
- 现有 `parse_upload` 签名不变，向后兼容。

**非目标**

- 不支持旧版 `.doc`（OLE 二进制 Word）。
- 不做图片 / 扫描件 OCR。
- 不改 LangGraph 图谱逻辑、向量切片逻辑、Neo4j / Qdrant 存储逻辑。

## 3. 现状摘要

| 文件 | 现状 |
|---|---|
| `backend/services/file_parser.py` | `parse_upload(name, raw)->str`，按 ext 分发：TEXT/PDF/Word，PDF/Word 用 lazy import。`ALLOWED_EXTS = TEXT_EXTS \| PDF_EXTS \| WORD_EXTS`，`.csv` 在 TEXT_EXTS 内（纯解码）。 |
| `backend/api.py` | `/api/ingest/file`（单文件）、`/api/ingest/files`（批量，`files: List[UploadFile]` 或服务器 `folder_path`），白名单过滤 ALLOWED_EXTS，非白名单跳过。 |
| `frontend/src/components/Sidebar.tsx` | 拖拽 / 多选上传 + 独立"上传整个文件夹"按钮（webkitdirectory），`filterAllowed` 前端过滤。 |
| `frontend/src/api/client.ts` | `ingestFiles(files)` 用 FormData 逐个 append `files`。 |
| `requirements.txt` | 文档解析仅 `pypdf`、`python-docx`，无 Excel 库。 |

## 4. 架构方案：分离容器与叶子

- **`services/file_parser.py`**：单一职责——单文件 → 文本。新增 CSV / Excel 分支，签名不变。
- **`services/archive.py`**（新建）：zip 容器 → 成员列表，负责解包、递归、zip-bomb 防护、source 命名。
- **`api.py`**：编排——上传 zip 先经 archive 展开成成员，再逐个 `parse_upload` + 入库。

职责清晰、可独立测试、`parse_upload` 向后兼容。

## 5. 详细设计

### 5.1 `services/file_parser.py`（叶子解析层）

签名不变：`parse_upload(filename: str, raw: bytes) -> str`。

扩展名分组调整：

```python
TEXT_EXTS  = { ... }              # 从中移除 .csv
CSV_EXTS   = {".csv"}             # 新增：结构化解析
PDF_EXTS   = {".pdf"}
WORD_EXTS  = {".docx"}
EXCEL_EXTS = {".xlsx", ".xls"}    # 新增

ALLOWED_EXTS = TEXT_EXTS | CSV_EXTS | PDF_EXTS | WORD_EXTS | EXCEL_EXTS
# 注：zip 不在此集合——它是容器，单独处理
```

`parse_upload` 分发新增：

- `ext in CSV_EXTS` → `_parse_csv(raw)`
- `ext in EXCEL_EXTS` → `_parse_excel(raw)`
- 不支持类型的错误信息更新为：`支持：文本/代码、CSV、PDF、Word(.docx)、Excel(.xlsx/.xls)`

#### `_parse_csv(raw) -> str`

- UTF-8 解码（`errors="ignore"`，与现有文本解码一致）→ `io.StringIO` → `csv.reader`；
- 渲染为 **markdown 表格**：第 1 行为表头，第 2 行为分隔行 `| --- | --- |`，其后为数据行；
- 防爆上限：`CSV_MAX_COLS = 50`（超长行截断并标注）、`CSV_MAX_ROWS = 5000`（超出行数尾部附 `… (已截断 N 行)`）；
- 全空文件 / 仅表头无数据 → 与现有逻辑一致，提取不到文本时由 `parse_upload` 抛 `ValueError`。

#### `_parse_excel(raw) -> str`

- **lazy import**（与 pdf / docx 一致）：
  - `.xlsx` → `openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)`
  - `.xls` → `xlrd.open_workbook(file_contents=raw)`
- 多 sheet 遍历，合并为**一个 source 的文本**，每个 sheet 前加标题 `## Sheet: {sheet名}`，sheet 间空行分隔；
- 单元格值转 `str`，行内以 ` | ` 分隔（与 docx 表格风格一致），跳过全空行；
- 防爆上限：`EXCEL_MAX_ROWS = 5000`、`EXCEL_MAX_COLS = 50`（每 sheet，超出截断标注）；
- 库缺失时抛可读 `ValueError`，解析异常 `logger.warning` 后抛 `ValueError`。

### 5.2 `services/archive.py`（新建，容器层）

```python
def extract_zip(filename: str, raw: bytes) -> list[tuple[str, bytes]]:
    """解压 zip，返回 [(member_source, member_bytes), ...]。

    - member_source = zip 内相对路径（正斜杠），如 "docs/readme.md"
    - 递归解压内嵌 zip（成员本身是 .zip 则继续展开）
    - zip-bomb 防护：累计解压字节 / 成员总数上限
    - 跳过目录条目、非白名单类型、空文件
    - 防御性过滤绝对路径 / ".." 成员名
    """
```

常量：

```python
ZIP_MAX_DEPTH       = 5
ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024   # 200 MB
ZIP_MAX_MEMBERS     = 2000
```

行为细则：

- **source 命名**：用 `zipfile` 成员的压缩内路径，统一为正斜杠，去掉前导 `./`。
- **递归**：成员扩展名为 `.zip` 时递归 `extract_zip`，成员 source 拼接为
  `{外层相对路径}/{内层相对路径}`（如 `nested.zip/inner.txt`）；达到 `ZIP_MAX_DEPTH` 不再展开，该成员按普通文件处理（但 .zip 不在白名单，会被跳过——即深层嵌套 zip 内容不解析，仅作防护）。
- **白名单过滤**：成员按 `ALLOWED_EXTS` 过滤，非白名单 / 目录条目 / 0 字节文件**静默跳过**。
- **zip-bomb 防护**：累计已解压字节数超过 `ZIP_MAX_TOTAL_BYTES`、或成员总数超过 `ZIP_MAX_MEMBERS` 时抛 `ValueError("zip 解压超限：…")`。
- **路径防御**：过滤绝对路径成员名（开头 `/` 或含盘符）与含 `..` 的成员名（虽内存处理不写盘，仍防御异常输入）。

### 5.3 `backend/api.py`

`/api/ingest/files` 上传文件循环改为（注意：原代码中 `results` 列表在收集完
`file_sources` 之后才初始化，因此 zip 解压失败项先收进 `zip_failures`，最后并入）：

```python
file_sources: list[tuple[str, bytes]] = []
zip_failures: list[FileIngestResult] = []

for file in files:
    name = file.filename or "upload"
    ext  = _ext(name)
    try:
        raw = await file.read()
    except Exception as exc:
        logger.warning("cannot read uploaded file %s: %s", name, exc)
        continue
    if ext == ".zip":
        try:
            members = extract_zip(name, raw)          # [(source, bytes), ...]
        except ValueError as exc:
            zip_failures.append(FileIngestResult(name=name, ok=False, error=str(exc)))
            continue
        file_sources.extend(members)
    elif ext in ALLOWED_EXTS:
        file_sources.append((name, raw))
    else:
        continue                                       # 非白名单跳过

# folder_path 部分不变；随后 results 初始化时并入 zip_failures：
#   results: list[FileIngestResult] = list(zip_failures)
```

- 后续逐个 `parse_upload + rag.ingest_text`、结果收集、统计的循环**完全不变**。
- `folder_path` 服务器目录导入逻辑**保留**（命令行批量场景）。
- `/api/ingest/file` 单文件接口**不处理 zip**（语义为单文件）；zip 统一走批量接口。
- zip 解压本身失败（如损坏 / 超限）记为一条 `FileIngestResult(ok=False)`，**不中断**其余文件（保持现有容错策略）。

### 5.4 前端

**`frontend/src/components/Sidebar.tsx`**

- 移除：`folderInputRef`、"上传整个文件夹"按钮、`webkitdirectory` 的 `useEffect`。
- 新增："上传 zip 压缩包"按钮（隐藏 `<input type="file" accept=".zip">`，单选），替换原文件夹按钮的位置。
- `ALLOWED_EXT` 增加 `.zip`、`.xlsx`、`.xls`。
- 主拖拽 / 多选 `<input>` 的 `accept` 同步更新；`isAllowed` 放行 zip（拖入 zip 不被 `filterAllowed` 过滤掉）。
- 提示文案：`代码 · PDF · Word · Excel · Markdown · zip 压缩包`。

**`frontend/src/api/client.ts`**

- `ingestFiles` **无需改动**：zip 作为普通 `File` append，后端展开。

### 5.5 依赖 `backend/requirements.txt`

新增（版本实现时锁定并 `pip install` 验证）：

- `openpyxl`（读 `.xlsx`）
- `xlrd`（读 `.xls`）

zip 用 Python 内置 `zipfile`，CSV 用内置 `csv`，均无新依赖。

## 6. 接口契约

| 接口 | 签名 | 说明 |
|---|---|---|
| `parse_upload` | `(filename: str, raw: bytes) -> str` | 不变。新增 csv / excel 分支。无法识别 / 解析异常 / 空文本 → `ValueError`。 |
| `extract_zip` | `(filename: str, raw: bytes) -> list[tuple[str, bytes]]` | 新增。返回成员 `(source, raw)` 列表；损坏 / 超限 → `ValueError`。 |
| `POST /api/ingest/files` | 不变 | 内部支持 `.zip` 展开；响应模型 `BatchIngestResponse` 不变。 |

## 7. 错误处理与容错

- **单成员失败不中断**：zip 内某个文件解析失败 → 记 `FileIngestResult(ok=False, error=…)`，继续其余（沿用现有循环容错）。
- **zip 整体失败**：损坏 / 超限 → 该 zip 记一条失败结果，不影响同批次其他上传文件。
- **库缺失**：openpyxl / xlrd 未装时，遇到对应文件才提示安装，文本类文件不受影响（lazy import）。
- **大文件**：表格 / csv 行列上限 + zip 字节 / 成员上限双重防护。

## 8. 测试策略

**`backend/tests/test_file_parser.py`**（扩展）

- CSV 结构化解析：真实字节 → 含表头 markdown 表格断言。
- CSV 截断：超 `CSV_MAX_ROWS` / `CSV_MAX_COLS` 的样本。
- `.xlsx` 往返：openpyxl 生成（多 sheet + 表格），断言 `## Sheet:` 与 `| ` 分隔。
- `.xls`：`xlrd` 用 `pytest.importorskip`；样本读取断言（生成困难则用固定小样本或跳过细节）。
- 更新 `test_parse_unsupported_type` 的错误信息断言。

**`backend/tests/test_archive.py`**（新建，均用 `zipfile` 内存生成）

- 基础解压：含多类型成员 → 成员列表与 source 相对路径正确。
- source 规范化：前导 `./`、反斜杠路径 → 正斜杠、去前导。
- 递归内嵌 zip：成员 source 拼接 `nested.zip/inner.txt`。
- 非白名单成员被跳过；目录条目 / 0 字节文件被跳过。
- zip-bomb 防护：构造高压缩比或超多成员样本 → 抛 `ValueError`。
- 损坏 zip → 抛 `ValueError`。

**`backend/tests/test_api.py`**（扩展）

- `/api/ingest/files` 上传含一个 `.zip`（内含若干文本成员）→ mock `rag`，断言每个成员被入库、source 为相对路径。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| `.xls` 读取兼容性 | xlrd 2.0+ 专注读 `.xls`（移除的是 xlsx 支持），实现时 `pip install xlrd` 验证；个别异常文件按现有"单文件失败、其余继续"策略处理。 |
| zip bomb | 字节 / 成员 / 深度三重上限。 |
| 大表格 / 大 csv 性能 | 行列上限截断。 |
| source 重名冲突（不同 zip 内同相对路径） | 用户已选"相对路径"方案；同相对路径会并入同一 source（与现有同名文件行为一致），可在文档说明，不做特殊处理。 |

## 10. 实现顺序（粗略，供 writing-plans 细化）

1. 依赖：`requirements.txt` 加 openpyxl / xlrd 并安装。
2. `file_parser.py`：CSV / Excel 分支 + ext 分组调整 + 错误信息。
3. `archive.py`：`extract_zip` + 防护。
4. `api.py`：`/ingest/files` zip 展开编排。
5. 前端 `Sidebar.tsx`：按钮替换 + accept / 白名单 + 文案。
6. 测试：file_parser / archive / api 三处。
7. 手动联调：上传 zip（含 excel/csv/pdf/代码）、验证入库与文档列表。
