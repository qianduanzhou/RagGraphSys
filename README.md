# Hybrid Graph + Vector RAG 智能问答系统

一个可直接运行的、企业级**混合检索 RAG 系统**：以 **LangGraph** 状态机编排单路「路由 → 双路召回 → 复合 → 生成 → 反思」全流程，**Qdrant** 做向量语义检索，**Neo4j** 做知识图谱推理；大模型通过 **OpenAI 兼容接口**接入（默认 `glm-5.2` + `embedding-3`，可替换为任意 OpenAI 兼容模型）。另提供**多智能体问答模式**（RAG + 联网搜索 + 整合）。前端提供 ChatGPT 风格对话界面，支持 **SSE 流式逐字输出**与**实时流水线进度**，并内置**文档库管理**（多文件批量上传 / 列表 / 删除）。

```
用户问题 → LangGraph Router（路由判断）
              ├─ 需要 RAG ──→ Qdrant 语义召回 ─┐
              │             Neo4j 关系召回 ────┤
              │                                ├→ Merge（融合上下文 + 标注来源）
              └─ 无需 RAG ───────────────────→ │→ LLM 生成（glm-5.2）
                                                │
                                  非流式 ──→ Reflection（反思优化）
                                              ├ 通过 → 返回
                                              └ 不通过 → 重新生成（受次数限制）
                                  流式 ────→ 逐字返回（跳过反思，降低延迟）
```

**多智能体模式（`mode="multi"`）** 另走一张并行-汇合图：

```
dispatch ──(fan-out)──→ [rag_agent（知识库检索）, web_agent（Tavily 联网搜索）]  ← 并行
                                  │                    │
                                  └────→ integration（整合两份回答，流式输出最终答案）→ END
```

## 📚 学习路线（小白入门）

完全不懂 LangChain / LangGraph？项目根目录的 [docs/学习路线.md](docs/学习路线.md) 给了一份从零开始的 **8 步学习地图**（先过前置知识，再按「对话模型 → 向量化 → 文本切分 → LangGraph 状态与节点 → 构图 → 同步流式 → 多智能体」的顺序读），每步都配源码对照笔记。需要查某个 API 用在哪、第几步，看 [docs/langchain-langgraph-用法清单.md](docs/langchain-langgraph-用法清单.md)。

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | Python 3.10+ / FastAPI / Uvicorn |
| 流程编排 | LangGraph（StateGraph + 条件边 + 并行 fan-out + 反思循环） |
| AI 工具 | LangChain（递归文本切分） |
| 大模型 | 任意 OpenAI 兼容模型（默认 `glm-5.2`，`/chat/completions`，支持 SSE 流式） |
| 向量化 | 任意 OpenAI 兼容 Embedding（默认 `embedding-3`，`/embeddings`，cosine，dim=2048） |
| 向量库 | Qdrant |
| 图谱库 | Neo4j（Bolt 协议） |
| 联网搜索 | Tavily（多智能体模式的 web_agent） |
| 文档解析 | pypdf / python-docx / openpyxl / xlrd（PDF / Word / Excel，按需懒加载） |
| 前端 | React 18 + Vite + TypeScript（react-markdown + rehype-highlight 代码高亮 + lucide-react 图标） |

## 核心特性

- **混合检索**：Qdrant 向量召回 + Neo4j 图谱推理**双路并行**，互补语义与关系两类问题。
- **相关度过滤**：`QDRANT_SCORE_THRESHOLD`（cosine 相似度阈值）在 merge 阶段丢弃低分命中，闲聊等无关查询自然不命中、context 为空，由 LLM 当通用对话处理。
- **LangGraph 编排**：条件边、并行 fan-out、反思循环、节点级事件流，结构清晰可扩展。两套图（单路 RAG / 多智能体）共享同一套检索逻辑。
- **模型抽象**：`services/` 层是唯一直连大模型 API 的地方，业务/图代码不感知 HTTP 细节。
- **SSE 流式**：答案逐字生成 + 实时节点进度（路由 → 向量 → 图谱 → 复合 → 生成），多智能体模式下还有双 agent 原始回答面板。
- **优雅降级**：Qdrant / Neo4j / Tavily 任一不可用都不阻塞启动，检索自动降级为空。
- **多格式入库**：文本/代码、CSV、PDF、Word(.docx)、Excel(.xlsx/.xls)，并支持 **zip 批量解包**（含递归解压、防 zip bomb、防路径穿越）。
- **文档库管理**：批量上传多文件、文档列表、单条 / 批量删除（清除 Qdrant 分片与 Neo4j 关系）。
- **多智能体问答模式**：RAG 智能体 + 联网智能体（Tavily）并行检索，整合智能体综合后流式输出（前端可切换，默认 RAG）。
- **工程规范**：配置集中化（`.env`）、统一日志、统一 HTTP 客户端、完整错误处理。
- **自带测试**：**173 个 pytest 用例**（全 mock，无需 API Key、无需联网、无需启动 Qdrant/Neo4j，可直接在 CI 中运行），前端另有 vitest 单测。

## 项目结构

```
RagGraphSys/
├─ backend/                       # 后端服务
│  ├─ main.py                     # FastAPI 入口、CORS、lifespan、服务装配
│  ├─ api.py                      # REST 接口：chat / chat/stream / ingest / ingest/file(s) / docs / health / stats
│  ├─ nodes.py                    # GraphState + 6 个图节点（router/qdrant/neo4j/merge/llm/reflection）+ 路由函数
│  ├─ graph.py                    # 构建单路 RAG 的 StateGraph
│  ├─ core/                       # 公共模块：config / client / logger / utils
│  ├─ services/                   # 模型与工具层：llm_service / embedding_service / file_parser / archive(zip) / web_search
│  ├─ rag/                        # 检索层：qdrant_store / neo4j_store / rag_service
│  ├─ multiagent/                 # 多智能体图：graph.py + nodes.py（dispatch/rag_agent/web_agent/integration）
│  ├─ tests/                      # pytest 测试套件（173 个）
│  ├─ requirements.txt            # 运行依赖
│  ├─ requirements-dev.txt        # 开发/测试依赖
│  ├─ .env.example                # 环境变量示例
│  └─ pytest.ini / conftest.py    # 测试配置
├─ frontend/                      # 前端 React + Vite + TS
│  └─ src/
│     ├─ App.tsx                  # 顶层状态与流式编排
│     ├─ api/client.ts            # /api 调用 + SSE 解析
│     ├─ chat-history.ts          # 会话历史本地持久化
│     └─ components/              # Sidebar（文档库）/ ChatWindow / MessageBubble（Markdown+高亮）/ SourceBadge
├─ docker-compose.yml             # 数据层：Qdrant + Neo4j
└─ README.md
```

## 环境要求

- **Python** 3.10 及以上
- **Node.js** 18 及以上
- **Docker** 与 **Docker Compose**（用于一键启动 Qdrant / Neo4j）
- **LLM API Key**：任意 OpenAI 兼容服务商的 API Key（如智谱[开放平台](https://open.bigmodel.cn)、OpenAI、DeepSeek、本地 vLLM 等）

---

## 一、快速开始（本地开发）

### 步骤 1：启动数据层（Qdrant + Neo4j）

在项目根目录执行：

```bash
docker compose up -d
```

启动后会得到：
- Qdrant Dashboard：http://localhost:6333/dashboard
- Neo4j Browser：http://localhost:7474 （账号 `neo4j` / 密码见 `docker-compose.yml` 的 `NEO4J_AUTH`）

> ⚠️ **Neo4j 5.x 要求密码至少 8 位**。若 `NEO4J_AUTH=neo4j/123456` 启动报错或鉴权失败，请把 `docker-compose.yml` 的 `NEO4J_AUTH` 与 `backend/.env` 的 `NEO4J_PASSWORD` **同时**改成 8 位以上（如 `12345678`）。

### 步骤 2：配置并启动后端

```bash
cd backend

# 创建并激活虚拟环境
python -m venv .venv
# Windows（PowerShell）： .venv\Scripts\Activate.ps1
# Windows（Git Bash）：    .venv/Scripts/activate
# macOS / Linux：         source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env          # Windows CMD：copy .env.example .env
# 然后编辑 .env，填入你的 LLM_API_KEY

# 启动
python main.py                # 服务地址 http://localhost:8000 ，接口文档 http://localhost:8000/docs
```

### 步骤 3：启动前端

新开一个终端：

```bash
cd frontend
npm install
npm run dev                   # 开发服务器 http://localhost:5173
```

### 步骤 4：开始使用

1. 浏览器打开 http://localhost:5173
2. 在**左侧边栏**上传文件（`.txt` / `.md` / `.pdf` / `.docx` / `.xlsx` 等，支持多选与 `.zip` 批量）→ 系统自动切分写入 Qdrant，并抽取实体关系写入 Neo4j；文档库列表可查看与删除
3. 在**右侧对话框**提问 → 顶部「路由 → 向量 → 图谱 → 复合 → 生成」进度条实时点亮，答案逐字流出，并展示来源徽章（Qdrant 浅蓝 / Neo4j 翠绿）
4. 顶部可切换问答模式：**RAG**（默认，单路检索）或 **多智能体**（RAG + 联网并行整合）

---

## 二、配置说明（`.env`）

复制 `backend/.env.example` 为 `backend/.env` 后按需修改。关键字段：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | OpenAI 兼容 API Key，**必填** | — |
| `LLM_BASE_URL` | 模型接口地址 | `https://open.bigmodel.cn/api/paas/v4/` |
| `LLM_MODEL` | 对话模型 | `glm-5.2` |
| `EMBEDDING_MODEL` | 向量化模型 | `embedding-3` |
| `EMBEDDING_DIMENSION` | 向量维度 | `2048` |
| `LLM_REQUEST_TIMEOUT` | 单次 LLM 请求超时（秒） | `60` |
| `LLM_TEMPERATURE` | 生成温度 | `0.6` |
| `LLM_MAX_TOKENS` | 单次生成最大 token | `2048` |
| `QDRANT_URL` | Qdrant 地址 | `http://localhost:6333` |
| `QDRANT_COLLECTION` | 向量集合名 | `rag_documents` |
| `QDRANT_TOP_K` | 向量检索返回条数 | `5` |
| `QDRANT_SCORE_THRESHOLD` | 向量命中相关度阈值（cosine 相似度，0~1，低于此分丢弃） | `0.35` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 图谱连接信息 | `bolt://localhost:7687` / `neo4j` / `123456` |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 文档切分参数 | `500` / `80` |
| `MAX_REFLECTION_ITERATIONS` | 非流式路径最大生成尝试次数（含反思重试） | `2` |
| `TAVILY_API_KEY` | Tavily 联网搜索 API key（多智能体模式用；留空则联网自动降级） | （空） |
| `TAVILY_MAX_RESULTS` | 联网搜索返回条数上限 | `5` |
| `APP_HOST` / `APP_PORT` | 后端监听 | `0.0.0.0` / `8000` |
| `CORS_ORIGINS` | 允许的前端来源（逗号分隔） | `http://localhost:5173,http://localhost:4173` |
| `LOG_LEVEL` | 日志级别 | `INFO` |

---

## 三、API 接口

所有接口前缀 `/api`，交互式文档见 http://localhost:8000/docs 。

### 对话

| 方法 | 路径 | 入参 | 返回 |
|------|------|------|------|
| POST | `/api/chat` | `{message, history, mode?}` | `{answer, sources, used_rag, iterations}`（非流式，含反思） |
| POST | `/api/chat/stream` | `{message, history, mode?}` | **SSE 流式**：`node`（管线进度）+ `delta`（逐字）→ `done` / `error` |

> `/api/chat`、`/api/chat/stream` 请求体新增可选字段 `mode`：`"rag"`（默认，单路 RAG 管线）/ `"multi"`（多智能体模式：RAG 智能体 + 联网智能体并行 + 整合智能体）。

**SSE 帧格式**（`/api/chat/stream`）：每帧 `data: {"type": ..., ...}\n\n`
- `{"type":"node","node":"merge_node","update":{"sources":[...],"used_rag":true}}` — 节点完成事件
- `{"type":"delta","text":"你"}` — 逐字 token
- `{"type":"done"}` / `{"type":"error","message":"..."}`

**多智能体模式**（`mode="multi"`）下 `node` 帧会出现以下节点，且两个 agent 节点的 `update` 额外携带 `answer`（原始回答文本，供前端默认折叠的原始回答面板展示）：

| 节点名 | `update` 字段 |
|--------|---------------|
| `rag_agent_node` | `{answer, sources, hits, used_rag}` |
| `web_agent_node` | `{answer, sources:[{type:"web",title,url,...}], hits, used_web}` |
| `integration_node` | `{iterations}` |
| `dispatch_node` | `{}` |

### 入库

| 方法 | 路径 | 入参 | 返回 |
|------|------|------|------|
| POST | `/api/ingest` | `{text, source}` | `{status, chunks, triples}` |
| POST | `/api/ingest/file` | multipart `file`（单文件） | `{status, chunks, triples}` |
| POST | `/api/ingest/files` | multipart `files`（多文件，可选 `folder_path` 服务器目录） | 批量结果：`{status, chunks, triples, succeeded, failed, files:[{name,chunks,triples,ok,error}]}` |

**支持的文件类型**（`ingest/file`、`ingest/files` 共用同一套白名单）：

- **文本/代码/配置**：`.txt` `.md` `.json` `.log` `.rst`，以及 `.py .js .ts .tsx .jsx .java .go .rs .c .cpp .cs .sql .yaml .xml` 等数十种（直接 UTF-8 解码）
- **结构化**：`.csv`（解析为 markdown 表格）
- **PDF**：`.pdf`（pypdf 逐页提取）
- **Word**：`.docx`（python-docx 提取段落与表格）
- **Excel**：`.xlsx`（openpyxl）/ `.xls`（xlrd），每个 sheet 渲染为一个 markdown 表格
- **压缩包**：`.zip`（自动解包为成员，递归处理内嵌 zip；防 zip bomb：最大深度 5、累计 200MB、最多 2000 成员；防路径穿越）

> PDF/Word/Excel 解析库为**懒加载**：未安装时纯文本类文件仍可正常工作，仅在真正遇到对应类型时才提示安装相应库。

### 文档库管理

| 方法 | 路径 | 入参 | 返回 |
|------|------|------|------|
| GET | `/api/docs` | — | `[{name, chunks, triples, at}]` 已入库文档列表（按 source 聚合，时间倒序） |
| POST | `/api/docs/delete` | `{source}` | `{source, chunks, relations}` 清除该文档全部 Qdrant 分片与 Neo4j 关系 |
| POST | `/api/docs/delete/batch` | `{sources:[...]}` | 批量结果：`{status, deleted, failed, results:[{source,chunks,relations,ok,error}]}`（逐项容错，单项失败不中断整批） |

> `source` 用请求体而非路径参数传递，因为文件名可能含 `.` / 空格 / 中文等。

### 运维

| 方法 | 路径 | 入参 | 返回 |
|------|------|------|------|
| GET | `/api/health` | — | `{status, qdrant, neo4j, web_search, counts}` 存活与依赖检查 |
| GET | `/api/stats` | — | `{qdrant_points, neo4j_entities}` |

---

## 四、测试

后端自带**全 mock** 的 pytest 套件——无需 API Key、无需联网、无需启动 Qdrant/Neo4j，可直接在 CI 中运行。

```bash
cd backend
pip install -r requirements-dev.txt

pytest                                   # 运行全部（173 个）
pytest tests/test_graph.py               # 运行单个文件
pytest -k router                         # 按名称筛选
pytest --cov=. --cov-report=term-missing # 带覆盖率报告
```

覆盖 `core/`、`services/`、`rag/`、`nodes.py`、`graph.py`、`multiagent/`、`api.py`。
亮点：`test_graph.py` 验证流水线的节点拓扑序与 token 实时交错；`test_api.py` 通过 FastAPI `TestClient` 真打 `/api/chat/stream` SSE 端点；`test_multiagent_*` 覆盖多智能体并行-汇合流程。

前端测试：

```bash
cd frontend
npm run test        # vitest run
```

---

## 五、部署

### 部署架构

```
浏览器 ──→ Nginx（托管前端 dist + 反代 /api）
                  │
                  ▼
            FastAPI 后端（:8000）
                  ├─→ Qdrant（:6333）
                  ├─→ Neo4j（:7687）
                  └─→ LLM API（OpenAI 兼容）
```

### 5.1 数据层

生产环境直接用 `docker compose up -d` 启动 Qdrant + Neo4j（已配置持久化卷 `qdrant_data` / `neo4j_data`）。如需更高可用，可替换为云托管服务，只需修改 `.env` 中的连接地址与凭证。

### 5.2 后端

后端无需编译，安装依赖后直接运行。生产环境推荐多进程：

```bash
# Linux / macOS：gunicorn + uvicorn worker
pip install gunicorn
gunicorn main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000

# 或直接用 uvicorn（Windows 也适用）
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

> 说明：`gunicorn` 依赖 `fork`，**Windows 原生不支持**；Windows 上请用 `uvicorn --workers`，或把后端容器化（见 5.5）。

生产环境 `.env` 注意事项：
- `LLM_API_KEY` 通过环境变量/密钥管理注入，**不要提交到代码库**。
- `CORS_ORIGINS` 改为正式前端域名（若前后端同源经 Nginx 反代可不配置）。
- `NEO4J_PASSWORD` 使用强密码（≥ 8 位）。
- 建议启用 HTTPS（由 Nginx 终结 TLS）。

### 5.3 前端

```bash
cd frontend
npm install
npm run build          # 产物输出到 frontend/dist
```

将 `dist/` 目录交给 Web 服务器（Nginx / Caddy / 对象存储）托管即可。

### 5.4 Nginx 配置示例

关键点：`/api/` 反代到后端，并**关闭缓冲**以支持 SSE 流式：

```nginx
server {
    listen 80;
    server_name your.domain.com;

    # 前端静态资源
    location / {
        root /var/www/rag-frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # 后端 API 反代
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # SSE 流式必备：关闭缓冲、延长超时
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        chunked_transfer_encoding on;
    }
}
```

### 5.5（可选）全栈容器化

当前 `docker-compose.yml` 只包含数据层。如需「一条命令拉起全栈」，可为后端、前端各编写 `Dockerfile` 并加入同一 compose（后端依赖 Qdrant/Neo4j 健康检查，前端构建后由 Nginx 容器托管）。该方向已列入下文「升级方向」。

### 端口对照

| 服务 | 端口 | 用途 |
|------|------|------|
| Qdrant HTTP | 6333 | 向量库 REST / Dashboard |
| Qdrant gRPC | 6334 | 向量库 gRPC |
| Neo4j Bolt | 7687 | 图谱连接 |
| Neo4j Browser | 7474 | 图谱 Web 管理台 |
| 后端 FastAPI | 8000 | API 服务（`/docs` Swagger） |
| 前端 Vite | 5173 | 开发服务器（生产用 Nginx 80/443） |

---

## 六、架构与设计原则

- **模型抽象**：`services/llm_service.py` 与 `services/embedding_service.py` 是*唯一*调用大模型 API 的地方，所有业务代码只见高层方法，不接触原始 HTTP。
- **公共模块**：`core/config.py`（配置）、`core/client.py`（HTTP 封装+重试）、`core/logger.py`（日志）、`core/utils.py`（工具）统一基础设施。
- **存储解耦**：Qdrant 与 Neo4j 相互独立；图并行 fan-out 到两者，任一不可用自动降级，不阻塞主流程。
- **双图共享检索**：单路 RAG 图（`nodes.merge`）与多智能体图（`RagService.build_context`）复用同一套 `merge_results` 合并逻辑，格式零重复。
- **可扩展图**：在 `nodes.py` / `multiagent/nodes.py` 新增节点，在对应 `graph.py` 注册即可接入编排。

---

## 七、常见问题

| 问题 | 解决 |
|------|------|
| Neo4j 启动/鉴权失败 | 5.x 要求密码 ≥ 8 位；统一修改 `docker-compose.yml` 的 `NEO4J_AUTH` 与 `.env` 的 `NEO4J_PASSWORD` |
| 前端跨域报错 | 开发期 Vite 已代理 `/api`；生产期用 Nginx 同源反代，或在 `.env` 配 `CORS_ORIGINS` |
| 流式答案一次性出现、不逐字 | Nginx/代理缓冲所致；确认反代 `proxy_buffering off`（后端已下发 `X-Accel-Buffering: no`） |
| 模型接口 401 | 检查 `.env` 的 `LLM_API_KEY`、`LLM_BASE_URL` 是否正确、是否生效（重启后端） |
| 健康检查 `degraded` | Qdrant 或 Neo4j 未连上；查看 `backend/logs/app.log` 与 `/api/health` 返回 |
| 检索似乎「不命中」 | 命中受 `QDRANT_SCORE_THRESHOLD` 控制；对低相关查询会自然过滤为空，属预期行为 |

---

## 八、升级方向

| 方向 | 现状 | 建议 |
|------|------|------|
| 检索质量 | 单路 cosine top_k | RRF 融合双路排序、Query 改写/HyDE、Qdrant 稀疏+稠密混合检索 |
| 图谱深度 | 1 跳模糊匹配 | 多跳 Cypher + GDS 算法（PageRank/社区发现）、Schema 约束抽取、实体消歧 |
| 流式输出 | 已实现 SSE 逐字 + 节点事件 | 进一步：LangGraph `astream` 节点级事件流、流式反思 |
| 可观测性 | 文件日志 | LangGraph checkpoint 状态回放 + LangSmith/Langfuse trace + Prometheus 指标 |
| 并发与缓存 | 同步节点 | 节点内 `asyncio.gather` 并发召回、语义缓存 |
| 安全 | 本地 Bearer | 密钥过 Vault、API 鉴权（JWT）、速率限制 |
| 工程化 | 单进程 + 测试 | 全栈 Dockerfile 一键部署、CI（lint + pytest + 前端 build） |
| 数据接入 | 纯文本 + 多格式文件 | 网页爬取、增量更新与删除、知识库分集合 |

---

## License

本项目用于学习与企业内部参考，可按需自由使用与修改。
