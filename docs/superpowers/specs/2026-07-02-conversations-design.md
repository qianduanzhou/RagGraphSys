# 多对话与每对话文档/记忆 — 设计

- 日期：2026-07-02
- 状态：已批准（待实现）
- 关联：`2026-06-22-multi-agent-design.md`（多智能体链路同样需按对话隔离）、聚合检索修复（`rag_service.resolve_vector_hits`）

## 1. 背景与目标

当前系统是**单用户单对话**模型：

- 后端：文档按 `owner`（用户名）全局存于 Qdrant/Neo4j；`/api/chat`、`/api/chat/stream` 接收前端发来的 `history`，后端对聊天历史无状态。
- 前端：每个用户一条全局聊天，消息存浏览器 `localStorage`（键 `rag-chat-history:<username>`）；侧栏管理全局文档列表。

用户要求：

1. **对话列表**：每个用户可有多个对话（类 ChatGPT 侧栏），可新建/切换/重命名/删除。
2. **每对话维护对应文档列表**：文档归属于对话，问答只检索该对话的文档。
3. **每对话有单独的上下文与记忆**：上下文 = 该对话的文档集合；记忆 = 该对话的聊天历史，由后端持久化。

### 两个基石决策（已与用户确认）

- **文档归属于对话**：在对话内上传的文档只属于该对话；删除对话连带删除其文档。隔离干净，贴合「每个对话维护对应的文档列表」。
- **后端持久化对话 + 历史**：对话列表、标题、消息记录都存后端（JSON，镜像 `auth_service` 模式）。换设备/重登后历史仍在。

### 两个技术子选择（已代定，可推翻）

- **检索隔离用 `conversation_id` 写入 payload**，而非「按文件名集合过滤」。后者在两个对话上传同名文件时会串；前者在存储层强制归属。
- **持久化用单个 `data/conversations.json`**（镜像 `auth_service`：JSON + `threading.Lock` + 原子写）。规模变大再迁 SQLite。

## 2. 数据模型

### 2.1 对话记录（`backend/data/conversations.json`）

```jsonc
{
  "conversations": {
    "<conv_id>": {
      "id": "<conv_id>",            // uuid
      "owner": "alice",
      "title": "铁路招聘问答",        // 首条用户消息自动截断生成，可改
      "created_at": 1780000000,
      "updated_at": 1780001000,
      "documents": [                // 该对话文档清单（展示用；分片真身在 Qdrant）
        {"name": "岗位一览表.pdf", "chunks": 18, "at": 1780000000}
      ],
      "messages": [                 // 该对话的记忆（后端持久化）
        {"role": "user", "content": "列出所有岗位", "at": 1780000050},
        {"role": "assistant", "content": "...", "at": 1780000100}
      ]
    }
  }
}
```

- `documents` 冗余存「名称/分片数/入库时间」用于快速列示；**Qdrant 是分片的单一事实来源**，`documents` 与 Qdrant 按 `(owner, conversation_id)` 扫描的结果对账。
- `messages` 只存 `{role, content, at}`；前端展示所需的 `sources/usedRag/steps` 等富信息不落库（流式当轮重建即可），避免 JSON 膨胀。

### 2.2 Qdrant 分片 payload 增加 `conversation_id`

现有：`{source, chunk_index, char_len, created_at, owner, text}` → 新增 `conversation_id`。文档归属在存储层强制，同名文件跨对话不串。

### 2.3 Neo4j 三元组增加 `conversation_id` 属性

三元组入库时打 `conversation_id`（与 `source` 并列）；检索/删除按 `(owner, conversation_id)` 过滤。

## 3. 检索隔离（关键）

所有检索按 `(owner, conversation_id)` 过滤，对话间彻底隔离。改动点：

- `QdrantStore.search(query, top_k, owner, conversation_id)` —— 普通点查，filter 加 `conversation_id`。
- `QdrantStore.scroll_by_source(source, owner, conversation_id, limit)` —— 聚合整文档拉取（已有功能，加 conv 维度）。
- `QdrantStore.delete_by_source(source, owner, conversation_id)` —— 删对话内某文档的分片（`source` 在对话内唯一）。
- `QdrantStore.delete_by_conversation(owner, conversation_id)` —— 删对话清全部分片。
- `Neo4jStore.search(keywords, owner, conversation_id, limit)`、`delete_by_source(...)`、`delete_by_conversation(owner, conversation_id)`。
- `RagService.ingest_text(text, source, owner, conversation_id)` —— 入库时写 `conversation_id`。
- `RagService.resolve_vector_hits / retrieve / build_context` 透传 `conversation_id`。

`GraphState`、`MultiAgentState` 增加 `conversation_id: str`，从 chat 请求一路传到检索层。

聚合检索三重门槛（关键词 + 相关度 + 主导源）逻辑不变，只是在 `(owner, conversation_id)` 范围内执行。

## 4. 服务层：`ConversationService`

新增 `backend/services/conversation_service.py`，镜像 `auth_service` 的持久化范式（`threading.Lock` + 临时文件原子替换）：

- `create(owner, title=None) -> conv` —— 新建，标题默认「新对话」。
- `list(owner) -> [conv 摘要]` —— id/标题/updated_at/文档数/最后一条消息预览。
- `get(owner, conv_id) -> conv | None` —— 含 messages + documents（鉴权校验 owner）。
- `rename(owner, conv_id, title) -> conv`。
- `delete(owner, conv_id)` —— 删 JSON 记录；返回 `(conv_id, doc_sources)` 供调用方清 Qdrant/Neo4j。
- `append_message(owner, conv_id, role, content)` —— 追加消息、更新 title（首条用户消息时）/updated_at。
- `list_documents(owner, conv_id)`、`add_document(owner, conv_id, doc_info)`、`remove_document(owner, conv_id, name)`。
- 所有写操作 `owner` 校验，越权返回 `None`/抛错。

title 自动生成：首次 `append_message(role="user")` 时，若 title 仍为默认「新对话」，取消息前 ~20 字符作 title。

## 5. API

新建对话级端点；旧端点改造或弃用。所有端点 `Depends(current_user)`，`{id}` 路径段做 owner 鉴权。

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/conversations` | 新建对话，body `{title?}`，返回完整 conv |
| GET | `/api/conversations` | 列出当前用户对话 |
| GET | `/api/conversations/{id}` | 取对话详情（messages + documents） |
| PATCH | `/api/conversations/{id}` | body `{title}`，改名 |
| DELETE | `/api/conversations/{id}` | 删对话 + 清 Qdrant/Neo4j |
| POST | `/api/conversations/{id}/documents` | 往该对话上传文档（多文件/zip/文件夹，复刻 `/ingest/files` 范式） |
| DELETE | `/api/conversations/{id}/documents` | body `{source}`，删该对话内某文档 |
| POST | `/api/conversations/{id}/chat` | 非流式问答（在该对话内） |
| POST | `/api/conversations/{id}/chat/stream` | SSE 流式问答（在该对话内） |

### chat 流程变化（「记忆」核心）

请求体由 `{message, history, mode}` 改为 `{message, mode}`（**不再发 history**）+ 路径 `conversation_id`。后端：

1. `conv = conversation_service.get(owner, conv_id)`，取 `messages` 构造 `history`。
2. 追加本轮 user 消息到状态，跑图（`owner`+`conversation_id` 注入 `GraphState`）。
3. 流式/非流式拿到 answer 后，`append_message(user)` + `append_message(assistant)` 写回。
4. 前端消息一律从 `GET /api/conversations/{id}` 拉取渲染。

旧 `/api/chat`、`/api/chat/stream`、`/api/docs`、`/ingest*`：保留可用（无 conv 时退回旧行为）或一并移除——**实现时定为「移除前端调用，后端端点保留一轮便于回归，随后删」**，避免破坏中间状态。

## 6. 前端

- **侧栏两段式**：上方「对话列表」（新建/切换/重命名/删除），下方「当前对话的文档列表」（上传/删除，仅作用于当前对话）。
- 新增状态：`conversations: Conversation[]`、`currentConversationId`、`currentConv`（其 messages + docs）。切换对话 → 拉取该对话详情、替换消息与文档面板。
- 历史改走后端：登录后 `GET /api/conversations`，自动选中 `updated_at` 最新一条；不再读写 `localStorage` 聊天记录（`chat-history.ts` 弃用）。
- `handleSend` 改为 `chatStream(conversationId, message, mode)`；不再本地拼 history。
- `api/client.ts` 增加对话相关方法；`types.ts` 增加 `Conversation`、`ConversationSummary`、`ConversationDoc`。

## 7. 迁移（默认策略，已确认方向）

升级时为每个**已有全局文档**的用户自动建一条「导入的文档」对话，把其现存文档（按 `source` 扫 Qdrant 得到）归入其中：给这些分片回填 `conversation_id`，并在 JSON 建对应 conv 记录。旧的 `localStorage` 聊天记录**不迁移**（对话从新开始）。无文档的用户不建空对话。

迁移脚本可独立运行（`backend/script/migrate_conversations.py`），幂等（重跑不重复建）。

## 8. 测试与验证

**后端（pytest）**
- `ConversationService`：CRUD、history 追加、title 自动生成、owner 越权返回空、并发写（Lock）、原子写。
- `QdrantStore` / `Neo4jStore`：`conversation_id` 过滤（A 对话分片不被 B 命中）、`delete_by_conversation` 只清本对话。
- `RagService`/`nodes`：按 conv 隔离检索；聚合检索在 conv 范围内仍生效。
- API：对话 CRUD、上传归属当前对话、chat 在对话内记忆累积、跨对话隔离。
- **回归**：现有 211 用例不破（旧端点保留期内）。

**前端（vitest）**
- 对话切换隔离（A 的消息/文档不渗到 B）、上传归属当前对话。

**端到端**
- 建对话 A 传「岗位一览表.pdf」、对话 B 传另一文档；A 内问「列出所有岗位」得 98 条且不命中 B；切到 B 问 A 的内容应答「无相关内容」。
- 删 A → A 的 Qdrant 分片/Neo4j 三元组清空，B 不受影响。

## 9. 范围与风险

- **规模**：本特性跨前后端、改动检索层与 chat 流程，属大型特性。实现计划将分阶段（持久化与服务 → 检索隔离 → API → 前端 → 迁移 → 测试）。
- **JSON 膨胀**：长对话历史写单文件有增长风险；当前规模可接受，`messages` 仅存必要字段，后续可迁 SQLite 或分文件。
- **旧端点兼容**：保留期内 `/api/chat*` 与新对话端点并存；前端切完后移除旧端点，避免长期双套。
