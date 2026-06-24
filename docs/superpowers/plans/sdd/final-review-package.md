# Final Whole-Branch Review Package — 多智能体问答

项目非 git 仓库，无 merge-base。本文件汇总各任务的 baseline→current diff（modified 文件）；NEW 文件请直接 Read 实际路径。

## 设计/计划
- Spec: docs/superpowers/specs/2026-06-22-multi-agent-design.md
- Plan: docs/superpowers/plans/2026-06-22-multi-agent.md
- Ledger: docs/superpowers/plans/sdd/progress.md

## NEW 文件（请 Read 全文）
- backend/services/web_search_service.py
- backend/multiagent/__init__.py
- backend/multiagent/nodes.py
- backend/multiagent/graph.py
- backend/tests/test_web_search_service.py
- backend/tests/test_multiagent_nodes.py
- backend/tests/test_multiagent_graph.py

## MODIFIED 文件（diff 见下方）
- backend/core/config.py, backend/requirements.txt, backend/.env.example
- backend/main.py, backend/api.py, backend/tests/test_api.py
- backend/multiagent/__init__.py
- frontend/src/types.ts, frontend/src/api/client.ts
- frontend/src/App.tsx, frontend/src/components/ChatWindow.tsx
- frontend/src/components/MessageBubble.tsx, frontend/src/components/MessageBubble.css
- README.md

## 测试现状
- 后端: pytest backend/tests = 167 passed / 0 failed
- 前端: npm run build (tsc -b + vite build) 通过

## 累积 Minors（见 progress.md 末尾）

---

# 各任务 baseline→current diffs


===== Task 1 diff =====
# Review package: Task 1 (no-git baseline->current)

## Diff

=== backend/core/config.py ===
--- .sdd-baseline/task-1/config.py	2026-06-22 10:07:46.661017600 +0800
+++ backend/core/config.py	2026-06-22 10:09:28.030835500 +0800
@@ -56,6 +56,11 @@
     neo4j_user: str = "neo4j"
     neo4j_password: str = "password"
 
+    # ---- 联网搜索（Tavily） ----
+    # 多智能体模式下「联网智能体」使用。留空则联网搜索不可用，web_agent 自动降级。
+    tavily_api_key: str = ""
+    tavily_max_results: int = 5
+
     # ---- RAG 流水线 ----
     chunk_size: int = 500
     chunk_overlap: int = 80

=== backend/requirements.txt ===
--- .sdd-baseline/task-1/requirements.txt	2026-06-22 10:07:46.695203300 +0800
+++ backend/requirements.txt	2026-06-22 10:09:28.540769900 +0800
@@ -21,6 +21,7 @@
 # ===== 数据存储 =====
 qdrant-client==1.11.3
 neo4j==5.24.0
+tavily-python==0.7.26
 
 # ===== 文档解析 =====
 pypdf==5.1.0

=== backend/.env.example ===
--- .sdd-baseline/task-1/.env.example	2026-06-22 10:07:46.729005500 +0800
+++ backend/.env.example	2026-06-22 10:09:30.705945400 +0800
@@ -29,6 +29,11 @@
 NEO4J_USER=neo4j
 NEO4J_PASSWORD=123456
 
+# ---- 联网搜索（Tavily） ----
+# 多智能体模式需要；留空则联网智能体自动降级。免费额度：https://tavily.com
+TAVILY_API_KEY=
+TAVILY_MAX_RESULTS=5
+
 # =============================================================
 #  RAG 流水线调参
 # =============================================================


===== Task 4 diff =====
# Review package: Task 4

## New files (read in full):
- backend/multiagent/graph.py
- backend/tests/test_multiagent_graph.py

## Modified: backend/multiagent/__init__.py

--- .sdd-baseline/task-4/__init__.py	2026-06-22 10:27:05.567480600 +0800
+++ backend/multiagent/__init__.py	2026-06-22 10:28:18.681194300 +0800
@@ -1 +1,4 @@
 """多智能体问答：RAG + 联网 + 整合。"""
+from multiagent.graph import build_multi_agent_graph
+
+__all__ = ["build_multi_agent_graph"]


===== Task 5 diff =====
# Review package: Task 5

## Modified: backend/main.py

--- .sdd-baseline/task-5/main.py	2026-06-22 10:39:49.131529200 +0800
+++ backend/main.py	2026-06-22 10:40:28.660953400 +0800
@@ -14,11 +14,13 @@
 from core.config import get_settings
 from core.logger import get_logger
 from graph import build_graph
+from multiagent import build_multi_agent_graph
 from rag.neo4j_store import Neo4jStore
 from rag.qdrant_store import QdrantStore
 from rag.rag_service import RagService
 from services.embedding_service import EmbeddingService
 from services.llm_service import LLMService
+from services.web_search_service import WebSearchService
 
 settings = get_settings()
 logger = get_logger(__name__)
@@ -52,6 +54,10 @@
     app.state.rag = rag
     app.state.graph = build_graph(llm, rag, settings)
 
+    web = WebSearchService(settings)
+    app.state.web = web
+    app.state.multi_agent_graph = build_multi_agent_graph(llm, rag, web, settings)
+
     logger.info("Application ready: http://%s:%d", settings.app_host, settings.app_port)
     yield
 


===== Task 6 diff =====
# Review package: Task 6

## Modified: backend/api.py

--- .sdd-baseline/task-6/api.py	2026-06-22 10:43:27.826949400 +0800
+++ backend/api.py	2026-06-22 10:50:04.139302300 +0800
@@ -11,7 +11,7 @@
 
 import json
 from pathlib import Path
-from typing import Any, Dict, List, Optional
+from typing import Any, Dict, List, Literal, Optional
 
 from fastapi import APIRouter, File, HTTPException, Request, UploadFile
 from fastapi.responses import StreamingResponse
@@ -37,6 +37,7 @@
 class ChatRequest(BaseModel):
     message: str = Field(..., min_length=1)
     history: Optional[List[ChatMessage]] = Field(default_factory=list)
+    mode: Literal["rag", "multi"] = "rag"
 
 
 class ChatResponse(BaseModel):
@@ -96,6 +97,19 @@
     return graph, rag
 
 
+def _select_graph(request: Request, mode: str):
+    """按模式选择编译图。multi_agent_graph 缺失时返回 503（正常不触发，图始终构建）。"""
+    if mode == "multi":
+        graph = getattr(request.app.state, "multi_agent_graph", None)
+        if graph is None:
+            raise HTTPException(status_code=503, detail="多智能体模式不可用")
+        return graph
+    graph = getattr(request.app.state, "graph", None)
+    if graph is None:
+        raise HTTPException(status_code=503, detail="application not initialised")
+    return graph
+
+
 def _history_to_dicts(history: Optional[List[ChatMessage]]) -> List[Dict[str, str]]:
     if not history:
         return []
@@ -108,6 +122,8 @@
 @router.get("/health")
 def health(request: Request) -> Dict[str, Any]:
     rag = getattr(request.app.state, "rag", None)
+    web = getattr(request.app.state, "web", None)
+    web_ok = bool(web.available) if web is not None else False
     qdrant_ok = neo4j_ok = False
     counts: Dict[str, Any] = {}
     if rag is not None:
@@ -125,14 +141,15 @@
         "status": "ok" if (qdrant_ok and neo4j_ok) else "degraded",
         "qdrant": qdrant_ok,
         "neo4j": neo4j_ok,
+        "web_search": web_ok,
         "counts": counts,
     }
 
 
 @router.post("/chat", response_model=ChatResponse)
 def chat(payload: ChatRequest, request: Request) -> ChatResponse:
-    graph, _ = _state(request)
-    logger.info("/chat question=%s", payload.message[:120])
+    graph = _select_graph(request, payload.mode)
+    logger.info("/chat mode=%s question=%s", payload.mode, payload.message[:120])
     try:
         result = graph.invoke(
             {
@@ -182,15 +199,33 @@
         return {"sources": update.get("sources", []), "used_rag": update.get("used_rag")}
     if node == "llm_node":
         return {"iterations": update.get("iterations")}
+    if node == "dispatch_node":
+        return {}
+    if node == "rag_agent_node":
+        sources = update.get("rag_agent_sources", []) or []
+        return {
+            "answer": update.get("rag_agent_answer", ""),
+            "sources": sources,
+            "hits": len(sources),
+            "used_rag": update.get("used_rag"),
+        }
+    if node == "web_agent_node":
+        sources = update.get("web_sources", []) or []
+        return {
+            "answer": update.get("web_agent_answer", ""),
+            "sources": sources,
+            "hits": len(sources),
+            "used_web": update.get("used_web"),
+        }
+    if node == "integration_node":
+        return {"iterations": update.get("iterations")}
     return {}
 
 
 @router.post("/chat/stream")
 async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
     """SSE 流：`node` 帧（updates 模式的流水线进度）与 `delta`（custom 模式的字符增量）交替输出。"""
-    graph = getattr(request.app.state, "graph", None)
-    if graph is None:
-        raise HTTPException(status_code=503, detail="application not initialised")
+    graph = _select_graph(request, payload.mode)
 
     initial = {
         "question": payload.message,

## Modified: backend/tests/test_api.py

--- .sdd-baseline/task-6/test_api.py	2026-06-22 10:43:27.864190500 +0800
+++ backend/tests/test_api.py	2026-06-22 10:44:10.165038000 +0800
@@ -265,3 +265,92 @@
     assert body["failed"] == 1
     assert body["succeeded"] == 0
     assert body["files"][0]["ok"] is False
+
+
+# ------------------------------------------------------------------ #
+# 多智能体模式（mode="multi"）
+# ------------------------------------------------------------------ #
+def test_summarize_rag_agent_includes_answer():
+    out = _summarize_update("rag_agent_node", {
+        "rag_agent_answer": "RA", "rag_agent_sources": [{"type": "qdrant", "content": "c"}], "used_rag": True,
+    })
+    assert out == {"answer": "RA", "sources": [{"type": "qdrant", "content": "c"}], "hits": 1, "used_rag": True}
+
+
+def test_summarize_web_agent_includes_answer():
+    out = _summarize_update("web_agent_node", {
+        "web_agent_answer": "WA", "web_sources": [{"type": "web", "url": "http://x"}], "used_web": True,
+    })
+    assert out == {"answer": "WA", "sources": [{"type": "web", "url": "http://x"}], "hits": 1, "used_web": True}
+
+
+def test_summarize_dispatch_is_empty():
+    assert _summarize_update("dispatch_node", {}) == {}
+
+
+def test_summarize_integration_iterations():
+    assert _summarize_update("integration_node", {"answer": "x", "iterations": 1}) == {"iterations": 1}
+
+
+def test_chat_multi_routes_to_multi_graph(client):
+    class _MockMulti:
+        def invoke(self, state):
+            return {"answer": "multi-answer", "sources": [], "used_rag": True, "iterations": 1}
+
+    main.app.state.multi_agent_graph = _MockMulti()
+    r = client.post("/api/chat", json={"message": "hi", "history": [], "mode": "multi"})
+    assert r.status_code == 200
+    assert r.json()["answer"] == "multi-answer"
+
+
+def test_chat_default_mode_is_rag(client):
+    """不传 mode 时默认 rag，走原 graph。"""
+    class _MockGraph:
+        def invoke(self, state):
+            return {"answer": "rag-answer", "sources": [], "used_rag": False, "iterations": 1}
+
+    main.app.state.graph = _MockGraph()
+    r = client.post("/api/chat", json={"message": "hi", "history": []})
+    assert r.status_code == 200
+    assert r.json()["answer"] == "rag-answer"
+
+
+def test_chat_multi_503_when_graph_missing(client):
+    main.app.state.multi_agent_graph = None
+    r = client.post("/api/chat", json={"message": "hi", "history": [], "mode": "multi"})
+    assert r.status_code == 503
+
+
+def test_health_includes_web_search(client):
+    class _Web:
+        available = True
+
+    main.app.state.web = _Web()
+    r = client.get("/api/health")
+    assert r.status_code == 200
+    assert r.json()["web_search"] is True
+
+
+def test_chat_stream_multi_emits_agent_nodes(client):
+    class _MockMultiStream:
+        async def astream(self, initial, stream_mode=("updates",)):
+            yield ("updates", {"dispatch_node": {}})
+            yield ("updates", {"rag_agent_node": {"rag_agent_answer": "RA", "rag_agent_sources": [], "used_rag": True}})
+            yield ("updates", {"web_agent_node": {"web_agent_answer": "WA", "web_sources": [], "used_web": False}})
+            yield ("custom", {"type": "delta", "text": "整"})
+            yield ("custom", {"type": "delta", "text": "合"})
+            yield ("updates", {"integration_node": {"answer": "整合", "iterations": 1}})
+
+    main.app.state.multi_agent_graph = _MockMultiStream()
+    with client.stream("POST", "/api/chat/stream", json={"message": "q", "history": [], "mode": "multi"}) as r:
+        body = "".join(r.iter_text())
+    frames = []
+    for block in body.split("\n\n"):
+        data_lines = [ln for ln in block.split("\n") if ln.startswith("data:")]
+        if data_lines:
+            frames.append(json.loads(data_lines[0][len("data:"):].strip()))
+    nodes = [f.get("node") for f in frames if f["type"] == "node"]
+    assert "rag_agent_node" in nodes and "web_agent_node" in nodes and "integration_node" in nodes
+    rag = next(f for f in frames if f.get("node") == "rag_agent_node")
+    assert rag["update"]["answer"] == "RA"
+    assert frames[-1]["type"] == "done"


===== Task 7 diff =====
# Review package: Task 7

## Modified: frontend/src/types.ts

--- .sdd-baseline/task-7/types.ts	2026-06-22 11:18:39.090630600 +0800
+++ frontend/src/types.ts	2026-06-22 11:19:33.733449600 +0800
@@ -1,10 +1,13 @@
 export type Role = "user" | "assistant";
 
 export interface SourceRef {
-  type: "qdrant" | "neo4j";
+  type: "qdrant" | "neo4j" | "web";
   content: string;
   score?: number;
   source?: string;
+  // web 来源额外字段
+  title?: string;
+  url?: string;
 }
 
 export type StepStatus = "pending" | "active" | "done";
@@ -24,11 +27,23 @@
   { key: "llm_node", label: "生成" },
 ];
 
+export type ChatMode = "rag" | "multi";
+
+/** 多智能体模式管线，key 与多智能体 LangGraph 节点名一一对应。 */
+export const MULTI_AGENT_PIPELINE: ReadonlyArray<{ key: string; label: string }> = [
+  { key: "dispatch_node", label: "调度" },
+  { key: "rag_agent_node", label: "RAG智能体" },
+  { key: "web_agent_node", label: "联网智能体" },
+  { key: "integration_node", label: "整合" },
+];
+
 export interface NodeUpdate {
   needs_rag?: boolean;
   used_rag?: boolean;
+  used_web?: boolean;
   hits?: number;
   sources?: SourceRef[];
+  answer?: string; // 多智能体下两个 agent 的原始回答文本
   iterations?: number;
   passed?: boolean;
   feedback?: string;
@@ -40,9 +55,13 @@
   content: string;
   sources?: SourceRef[];
   usedRag?: boolean;
+  usedWeb?: boolean;
   error?: boolean;
   streaming?: boolean;
+  mode?: ChatMode;
   steps?: PipelineStep[];
+  ragAgentAnswer?: string; // 多智能体：RAG 智能体原始回答（折叠面板）
+  webAgentAnswer?: string; // 多智能体：联网智能体原始回答（折叠面板）
 }
 
 export interface StreamCallbacks {
@@ -98,6 +117,7 @@
   status: string;
   qdrant: boolean;
   neo4j: boolean;
+  web_search: boolean;
   counts: {
     qdrant_points?: number;
     neo4j_entities?: number;

## Modified: frontend/src/api/client.ts

--- .sdd-baseline/task-7/client.ts	2026-06-22 11:18:39.161928700 +0800
+++ frontend/src/api/client.ts	2026-06-22 11:20:14.120429100 +0800
@@ -1,6 +1,7 @@
 import type {
   BatchIngestResponse,
   ChatHistoryItem,
+  ChatMode,
   ChatResponse,
   DeleteDocResponse,
   HealthResponse,
@@ -24,12 +25,13 @@
 
 export async function chat(
   message: string,
-  history: ChatHistoryItem[]
+  history: ChatHistoryItem[],
+  mode: ChatMode = "rag"
 ): Promise<ChatResponse> {
   const res = await fetch(`${BASE}/chat`, {
     method: "POST",
     headers: { "Content-Type": "application/json" },
-    body: JSON.stringify({ message, history }),
+    body: JSON.stringify({ message, history, mode }),
   });
   if (!res.ok) throw new Error(await parseError(res));
   return res.json();
@@ -109,12 +111,13 @@
 export async function chatStream(
   message: string,
   history: ChatHistoryItem[],
-  cb: StreamCallbacks
+  cb: StreamCallbacks,
+  mode: ChatMode = "rag"
 ): Promise<void> {
   const res = await fetch(`${BASE}/chat/stream`, {
     method: "POST",
     headers: { "Content-Type": "application/json" },
-    body: JSON.stringify({ message, history }),
+    body: JSON.stringify({ message, history, mode }),
   });
   if (!res.ok) {
     cb.onError?.(await parseError(res));


===== Task 8 diff =====
# Review package: Task 8

## Modified: frontend/src/App.tsx

--- .sdd-baseline/task-8/App.tsx	2026-06-22 11:25:37.164714200 +0800
+++ frontend/src/App.tsx	2026-06-22 11:27:09.259199700 +0800
@@ -4,10 +4,12 @@
 import ChatWindow from "./components/ChatWindow";
 import { chatStream, deleteDoc, fetchDocs, fetchHealth, ingestFiles } from "./api/client";
 import {
+  MULTI_AGENT_PIPELINE,
   PIPELINE,
   type BatchIngestResponse,
   type ChatMessage,
   type ChatHistoryItem,
+  type ChatMode,
   type HealthResponse,
   type UploadedDoc,
 } from "./types";
@@ -30,15 +32,28 @@
   const [streaming, setStreaming] = useState(false);
   const [health, setHealth] = useState<HealthResponse | null>(null);
   const [docs, setDocs] = useState<UploadedDoc[]>([]);
+  // 问答模式与联网搜索可用性（来自后端 health.web_search）。
+  const [mode, setMode] = useState<ChatMode>("rag");
+  const [webSearchAvailable, setWebSearchAvailable] = useState<boolean>(true);
 
   useEffect(() => {
-    fetchHealth().then(setHealth).catch(() => setHealth(null));
+    fetchHealth()
+      .then((h) => {
+        setHealth(h);
+        setWebSearchAvailable(h.web_search);
+      })
+      .catch(() => setHealth(null));
     // 拉取已入库文档列表（持久化在后端，刷新后仍可见）。
     fetchDocs().then(setDocs).catch(() => setDocs([]));
   }, []);
 
   const refreshHealth = useCallback(() => {
-    fetchHealth().then(setHealth).catch(() => setHealth(null));
+    fetchHealth()
+      .then((h) => {
+        setHealth(h);
+        setWebSearchAvailable(h.web_search);
+      })
+      .catch(() => setHealth(null));
   }, []);
 
   const refreshDocs = useCallback(() => {
@@ -47,6 +62,8 @@
 
   const handleSend = useCallback(
     async (text: string) => {
+      // 按当前模式选择展示用管线（RAG vs 多智能体）。
+      const pipeline = mode === "multi" ? MULTI_AGENT_PIPELINE : PIPELINE;
       const userMsg: ChatMessage = { id: uid(), role: "user", content: text };
       const assistantId = uid();
       const assistantMsg: ChatMessage = {
@@ -56,7 +73,8 @@
         sources: [],
         usedRag: false,
         streaming: true,
-        steps: PIPELINE.map((p) => ({ ...p, status: "pending" as const })),
+        mode,
+        steps: pipeline.map((p) => ({ ...p, status: "pending" as const })),
       };
       const history: ChatHistoryItem[] = [...messages, userMsg]
         .filter((m) => m.id !== "welcome")
@@ -75,6 +93,23 @@
         await chatStream(text, history, {
           onNode: (node, update) =>
             patch((m) => {
+              // 多智能体：把两个 agent 的原始回答 + 来源写入消息（供折叠面板/徽章）。
+              if (node === "rag_agent_node") {
+                const next: ChatMessage = { ...m, ragAgentAnswer: update.answer };
+                if (update.sources) next.sources = update.sources;
+                if (typeof update.used_rag === "boolean")
+                  next.usedRag = update.used_rag;
+                return next;
+              }
+              if (node === "web_agent_node") {
+                const next: ChatMessage = { ...m, webAgentAnswer: update.answer };
+                // 合并联网来源到既有 sources（避免覆盖 RAG 来源）。
+                next.sources = [...(m.sources ?? []), ...(update.sources ?? [])];
+                if (typeof update.used_web === "boolean")
+                  next.usedWeb = update.used_web;
+                return next;
+              }
+              // 通用步进器更新（RAG 与多智能体共用，保持既有逻辑不变）。
               const steps = (m.steps ?? []).map((s) => ({ ...s }));
               const idx = steps.findIndex((s) => s.key === node);
               if (idx >= 0) {
@@ -101,13 +136,13 @@
               error: true,
               content: m.content || `请求失败：${msg}`,
             })),
-        });
+        }, mode);
       } finally {
         setStreaming(false);
         refreshHealth();
       }
     },
-    [messages, refreshHealth]
+    [messages, refreshHealth, mode]
   );
 
   const handleUploadFiles = useCallback(
@@ -166,6 +201,9 @@
           messages={messages}
           streaming={streaming}
           onSend={handleSend}
+          mode={mode}
+          onModeChange={setMode}
+          webSearchAvailable={webSearchAvailable}
         />
       </main>
     </div>

## Modified: frontend/src/components/ChatWindow.tsx

--- .sdd-baseline/task-8/ChatWindow.tsx	2026-06-22 11:25:37.205774100 +0800
+++ frontend/src/components/ChatWindow.tsx	2026-06-22 11:27:27.471442000 +0800
@@ -1,6 +1,6 @@
 import { useEffect, useRef, useState } from "react";
 import { SendHorizontal } from "lucide-react";
-import type { ChatMessage } from "../types";
+import type { ChatMessage, ChatMode } from "../types";
 import MessageBubble from "./MessageBubble";
 import "./ChatWindow.css";
 
@@ -8,9 +8,19 @@
   messages: ChatMessage[];
   streaming: boolean;
   onSend: (text: string) => void;
+  mode: ChatMode;
+  onModeChange: (m: ChatMode) => void;
+  webSearchAvailable: boolean;
 }
 
-export default function ChatWindow({ messages, streaming, onSend }: Props) {
+export default function ChatWindow({
+  messages,
+  streaming,
+  onSend,
+  mode,
+  onModeChange,
+  webSearchAvailable,
+}: Props) {
   const [draft, setDraft] = useState("");
   const scrollRef = useRef<HTMLDivElement>(null);
   const taRef = useRef<HTMLTextAreaElement>(null);
@@ -53,6 +63,32 @@
       </div>
 
       <div className="composer">
+        <div className="mode-switch" role="tablist" aria-label="问答模式">
+          <button
+            type="button"
+            role="tab"
+            aria-selected={mode === "rag"}
+            className={mode === "rag" ? "active" : ""}
+            onClick={() => onModeChange("rag")}
+          >
+            RAG问答
+          </button>
+          <button
+            type="button"
+            role="tab"
+            aria-selected={mode === "multi"}
+            className={mode === "multi" ? "active" : ""}
+            disabled={!webSearchAvailable}
+            title={
+              webSearchAvailable
+                ? "多智能体：RAG + 联网 + 整合"
+                : "未配置 TAVILY_API_KEY"
+            }
+            onClick={() => onModeChange("multi")}
+          >
+            多智能体
+          </button>
+        </div>
         <div className="composer-box">
           <textarea
             ref={taRef}


===== Task 9 diff =====
# Review package: Task 9

## Modified: frontend/src/components/MessageBubble.tsx

--- .sdd-baseline/task-9/MessageBubble.tsx	2026-06-22 11:32:46.851496300 +0800
+++ frontend/src/components/MessageBubble.tsx	2026-06-22 11:33:35.542614300 +0800
@@ -79,13 +79,47 @@
             </button>
             {sourcesOpen && (
               <div className="msg-sources-list">
-                {sources.map((s, i) => (
-                  <SourceBadge key={i} source={s} />
-                ))}
+                {sources.map((s, i) =>
+                  // web 来源渲染为可点击链接徽章，与既有 qdrant/neo4j 徽章分支并列。
+                  s.type === "web" ? (
+                    <a
+                      key={i}
+                      className="source-badge web"
+                      href={s.url}
+                      target="_blank"
+                      rel="noopener noreferrer"
+                      title={s.title || s.url}
+                    >
+                      {"🔗 "}
+                      {s.title || s.url}
+                    </a>
+                  ) : (
+                    <SourceBadge key={i} source={s} />
+                  )
+                )}
               </div>
             )}
           </div>
         )}
+
+        {/* 多智能体模式：默认折叠的 RAG / 联网 智能体原始回答面板 */}
+        {message.mode === "multi" &&
+          (message.ragAgentAnswer || message.webAgentAnswer) && (
+            <div className="agent-panels">
+              {message.ragAgentAnswer && (
+                <details className="agent-panel">
+                  <summary>📄 查看 RAG 智能体原始回答</summary>
+                  <div className="agent-panel-body">{message.ragAgentAnswer}</div>
+                </details>
+              )}
+              {message.webAgentAnswer && (
+                <details className="agent-panel">
+                  <summary>🌐 查看联网智能体原始回答</summary>
+                  <div className="agent-panel-body">{message.webAgentAnswer}</div>
+                </details>
+              )}
+            </div>
+          )}
       </div>
     </div>
   );

## Modified: frontend/src/components/MessageBubble.css

--- .sdd-baseline/task-9/MessageBubble.css	2026-06-22 11:32:46.902110800 +0800
+++ frontend/src/components/MessageBubble.css	2026-06-22 11:33:46.511319800 +0800
@@ -174,3 +174,71 @@
 .pstep.done .pstep-dot {
   background: var(--graph);
 }
+
+/* === Task 9：多智能体模式相关样式（追加，不覆盖既有） === */
+
+/* 模式切换控件（ChatWindow 已有 .mode-switch，补配套样式） */
+.mode-switch {
+  display: inline-flex;
+  gap: 0;
+  border: 1px solid var(--border, #d0d7de);
+  border-radius: 8px;
+  overflow: hidden;
+  margin-bottom: 8px;
+}
+.mode-switch button {
+  padding: 6px 14px;
+  border: none;
+  background: transparent;
+  cursor: pointer;
+  font-size: 13px;
+  color: inherit;
+}
+.mode-switch button.active {
+  background: var(--accent, #2563eb);
+  color: #fff;
+}
+.mode-switch button:disabled {
+  opacity: 0.45;
+  cursor: not-allowed;
+}
+
+/* web 来源徽章：渲染为可点击链接 */
+.source-badge.web {
+  color: var(--link, #2563eb);
+  text-decoration: none;
+  border: 1px solid var(--border, #d0d7de);
+  border-radius: 6px;
+  padding: 2px 8px;
+  font-size: 12px;
+  align-self: flex-start;
+}
+.source-badge.web:hover {
+  background: rgba(37, 99, 235, 0.08);
+}
+
+/* 多智能体原始回答折叠面板 */
+.agent-panels {
+  margin-top: 10px;
+  display: flex;
+  flex-direction: column;
+  gap: 6px;
+}
+.agent-panel {
+  border: 1px solid var(--border, #d0d7de);
+  border-radius: 6px;
+  padding: 6px 10px;
+  font-size: 13px;
+}
+.agent-panel summary {
+  cursor: pointer;
+  color: var(--muted, #57606a);
+  user-select: none;
+}
+.agent-panel-body {
+  margin-top: 6px;
+  padding-top: 6px;
+  border-top: 1px dashed var(--border, #d0d7de);
+  white-space: pre-wrap;
+  line-height: 1.6;
+}


===== Task 10 diff =====
# Review package: Task 10

## Modified: README.md

--- .sdd-baseline/task-10/README.md	2026-06-22 11:38:38.780768200 +0800
+++ README.md	2026-06-22 11:39:43.774212000 +0800
@@ -33,6 +33,7 @@
 - **模型抽离**：`services/` 层是唯一直连大模型 API 的地方，业务/图代码不感知 HTTP 细节。
 - **SSE 流式**：答案逐字生成 + 实时节点进度（路由 → 向量 → 图谱 → 融合 → 生成）。
 - **优雅降级**：Qdrant / Neo4j 任一不可用都不阻塞启动，检索自动降级为空。
+- **多智能体问答模式**：RAG 智能体 + 联网智能体（Tavily）并行检索，整合智能体综合后流式输出（前端可切换，默认 RAG）。
 - **工程规范**：配置集中（`.env`）、统一日志、统一 HTTP 客户端、完整错误处理。
 - **自带测试**：87 个 pytest 用例，覆盖率约 **94%**，全程 mock，无需联网。
 
@@ -146,6 +147,8 @@
 | `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 图谱连接信息 | `bolt://localhost:7687` / `neo4j` / `123456` |
 | `CHUNK_SIZE` / `CHUNK_OVERLAP` | 文档切分参数 | `500` / `80` |
 | `MAX_REFLECTION_ITERATIONS` | 非流式路径最大生成次数（含反思重试） | `2` |
+| `TAVILY_API_KEY` | Tavily 联网搜索 API key（多智能体模式用；留空则联网自动降级） | （空） |
+| `TAVILY_MAX_RESULTS` | 联网搜索返回条数上限 | `5` |
 | `APP_HOST` / `APP_PORT` | 后端监听 | `0.0.0.0` / `8000` |
 | `CORS_ORIGINS` | 允许的前端来源（逗号分隔） | `http://localhost:5173,...` |
 
@@ -157,18 +160,29 @@
 
 | 方法 | 路径 | 入参 | 返回 |
 |------|------|------|------|
-| POST | `/api/chat` | `{message, history}` | `{answer, sources, used_rag, iterations}`（完整含反思） |
-| POST | `/api/chat/stream` | `{message, history}` | **SSE 流式**：`node`*(管线进度) + `delta`*(逐字) → `done` / `error` |
+| POST | `/api/chat` | `{message, history, mode?}` | `{answer, sources, used_rag, iterations}`（完整含反思） |
+| POST | `/api/chat/stream` | `{message, history, mode?}` | **SSE 流式**：`node`*(管线进度) + `delta`*(逐字) → `done` / `error` |
 | POST | `/api/ingest` | `{text, source}` | `{status, chunks, triples}` |
 | POST | `/api/ingest/file` | multipart `file`（.txt/.md/.csv/.json/.log） | `{status, chunks, triples}` |
-| GET | `/api/health` | — | `{status, qdrant, neo4j, counts}` 存活与依赖检查 |
+| GET | `/api/health` | — | `{status, qdrant, neo4j, counts, web_search}` 存活与依赖检查 |
 | GET | `/api/stats` | — | `{qdrant_points, neo4j_entities}` |
 
+> `/api/chat`、`/api/chat/stream` 请求体新增可选字段 `mode`：`"rag"`（默认，单路 RAG 管线） / `"multi"`（多智能体模式：RAG 智能体 + 联网智能体并行 + 整合智能体）。
+
 **SSE 帧格式**（`/api/chat/stream`）：每帧 `data: {"type": ..., ...}\n\n`
 - `{"type":"node","node":"merge_node","update":{"sources":[...],"used_rag":true}}` —— 节点完成事件
 - `{"type":"delta","text":"你"}` —— 逐字 token
 - `{"type":"done"}` / `{"type":"error","message":"..."}`
 
+多智能体模式（`mode="multi"`）下 `node` 帧会改用以下节点名，且两个 agent 节点的 `update` 会额外携带 `answer`（原始回答文本，供前端默认折叠的原始回答面板展示）：
+
+| 节点名 | `update` 字段 |
+|--------|---------------|
+| `rag_agent_node` | `{answer, sources, hits, used_rag}` |
+| `web_agent_node` | `{answer, sources:[{type:"web",title,url,...}], hits, used_web}` |
+| `integration_node` | `{iterations}` |
+| `dispatch_node` | `{}` |
+
 ---
 
 ## 四、测试

