import { useCallback, useEffect, useState } from "react";
import { Eraser, LogOut, UserCircle } from "lucide-react";
import Sidebar from "./components/Sidebar";
import ChatWindow from "./components/ChatWindow";
import AuthPage from "./components/AuthPage";
import {
  chatStream,
  deleteDoc,
  deleteDocsBatch,
  fetchCurrentUser,
  fetchDocs,
  fetchHealth,
  ingestFiles,
  loginAccount,
  registerAccount,
  setAuthToken,
} from "./api/client";
import { clearSession, loadSession, saveSession } from "./auth-storage";
import {
  MULTI_AGENT_PIPELINE,
  PIPELINE,
  type BatchIngestResponse,
  type AuthSession,
  type ChatMessage,
  type ChatMode,
  type HealthResponse,
  type StepStatus,
  type UploadedDoc,
} from "./types";
import { buildHistory, clearUserMessages, loadUserMessages, saveUserMessages } from "./chat-history";
import "./App.css";

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

const WELCOME: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "**你好，我是 Hybrid RAG 助手。**\n\n我通过 **Qdrant 语义检索** 与 **Neo4j 知识图谱** 双路召回，再由 **大模型** 自动自我反思。\n\n先在左侧上传一份文档建立知识库，然后向我一提问吧。",
};

const initialSession = loadSession();
setAuthToken(initialSession?.token ?? null);

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(initialSession);
  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    initialSession ? loadUserMessages(initialSession.username, [WELCOME]) : [WELCOME]
  );
  const [streaming, setStreaming] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [docs, setDocs] = useState<UploadedDoc[]>([]);
  // 问答模式与联网搜索可用性（来自后端 health.web_search）。
  const [mode, setMode] = useState<ChatMode>("rag");
  const [webSearchAvailable, setWebSearchAvailable] = useState<boolean>(true);

  const resetSession = useCallback(() => {
    setAuthToken(null);
    clearSession();
    setSession(null);
    setDocs([]);
    setMessages([WELCOME]);
  }, []);

  useEffect(() => {
    fetchHealth()
      .then((h) => {
        setHealth(h);
        setWebSearchAvailable(h.web_search);
      })
      .catch(() => setHealth(null));
    // 拉取已入库文档列表（持久化在后端，刷新后仍可见）。
    if (!session) {
      setDocs([]);
      return;
    }

    fetchCurrentUser()
      .then(() => fetchDocs())
      .then(setDocs)
      .catch(resetSession);
  }, [resetSession, session]);

  const refreshHealth = useCallback(() => {
    fetchHealth()
      .then((h) => {
        setHealth(h);
        setWebSearchAvailable(h.web_search);
      })
      .catch(() => setHealth(null));
  }, []);

  const refreshDocs = useCallback(() => {
    if (!session) {
      setDocs([]);
      return;
    }
    fetchDocs().then(setDocs).catch(() => setDocs([]));
  }, [session]);

  useEffect(() => {
    if (session) saveUserMessages(session.username, messages);
  }, [messages, session]);

  const handleAuth = useCallback(
    async (authMode: "login" | "register", username: string, password: string) => {
      const next =
        authMode === "login"
          ? await loginAccount(username, password)
          : await registerAccount(username, password);
      setAuthToken(next.token);
      saveSession(next);
      setSession(next);
      setMessages(loadUserMessages(next.username, [WELCOME]));
      setDocs([]);
      refreshHealth();
    },
    [refreshHealth]
  );

  const handleLogout = useCallback(() => {
    resetSession();
  }, [resetSession]);

  const handleSend = useCallback(
    async (text: string) => {
      // 按当前模式选择展示用管线（RAG vs 多智能体）。
      const pipeline = mode === "multi" ? MULTI_AGENT_PIPELINE : PIPELINE;
      const userMsg: ChatMessage = { id: uid(), role: "user", content: text };
      const assistantId = uid();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        sources: [],
        usedRag: false,
        streaming: true,
        mode,
        steps: pipeline.map((p) => ({ ...p, status: "pending" as const })),
      };
      // history 只含历史对话；本轮问题以 question 字段单独传给后端追加，
      // 不把本轮 userMsg 算进 history，否则大模型会收到两遍同一问题。
      const history = buildHistory(messages);

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);

      // 帧到达时只更新正在流式输出的助手气泡。
      const patch = (updater: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? updater(m) : m))
        );

      try {
        await chatStream(text, history, {
          onNode: (node, update) =>
            patch((m) => {
              const steps = (m.steps ?? []).map((s) => ({ ...s }));
              const setStep = (key: string, status: StepStatus) => {
                const i = steps.findIndex((s) => s.key === key);
                if (i >= 0) steps[i] = { ...steps[i], status };
              };

              if (mode === "multi") {
                // 多智能体并行 stepper：dispatch 完成时同时点亮 RAG 与联网两个
                // agent（并行启动）；各 agent / integration 完成时自身置 done。
                // 此前特判分支只回填 agent 回答、未推进 steps，导致 rag_agent 永远
                // 卡在 active、web_agent 永远停在 pending（截图所示现象）。
                if (node === "dispatch_node") {
                  setStep("dispatch_node", "done");
                  setStep("rag_agent_node", "active");
                  setStep("web_agent_node", "active");
                } else if (node === "rag_agent_node") {
                  setStep("rag_agent_node", "done");
                } else if (node === "web_agent_node") {
                  setStep("web_agent_node", "done");
                } else if (node === "integration_node") {
                  setStep("integration_node", "done");
                }
              } else {
                // 单 RAG 图：串行 stepper（完成当前 → 点亮下一个）。
                const idx = steps.findIndex((s) => s.key === node);
                if (idx >= 0) {
                  steps[idx] = { ...steps[idx], status: "done" };
                  if (
                    idx + 1 < steps.length &&
                    steps[idx + 1].status !== "done"
                  ) {
                    steps[idx + 1] = { ...steps[idx + 1], status: "active" };
                  }
                }
              }

              const next: ChatMessage = { ...m, steps };
              // 多智能体：把两个 agent 的原始回答 + 来源写入消息（供折叠面板/徽章）。
              if (node === "rag_agent_node") {
                next.ragAgentAnswer = update.answer;
                if (update.sources) next.sources = update.sources;
                if (typeof update.used_rag === "boolean")
                  next.usedRag = update.used_rag;
              } else if (node === "web_agent_node") {
                next.webAgentAnswer = update.answer;
                // 合并联网来源到既有 sources（避免覆盖 RAG 来源）。
                next.sources = [...(m.sources ?? []), ...(update.sources ?? [])];
                if (typeof update.used_web === "boolean")
                  next.usedWeb = update.used_web;
              } else {
                if (update.sources) next.sources = update.sources;
                if (typeof update.used_rag === "boolean")
                  next.usedRag = update.used_rag;
              }
              return next;
            }),
          onDelta: (delta) => patch((m) => ({ ...m, content: m.content + delta })),
          onDone: () => patch((m) => ({ ...m, streaming: false })),
          onError: (msg) =>
            patch((m) => ({
              ...m,
              streaming: false,
              error: true,
              content: m.content || `请求失败：${msg}`,
            })),
        }, mode);
      } finally {
        setStreaming(false);
        refreshHealth();
      }
    },
    [messages, refreshHealth, mode]
  );

  const handleUploadFiles = useCallback(
    async (files: File[]): Promise<BatchIngestResponse> => {
      const res = await ingestFiles(files);
      // 以服务端为准刷新文档列表（持久化、准确）。
      refreshDocs();
      refreshHealth();
      return res;
    },
    [refreshDocs, refreshHealth]
  );

  const handleDeleteDoc = useCallback(
    async (source: string) => {
      await deleteDoc(source);
      refreshDocs();
      refreshHealth();
    },
    [refreshDocs, refreshHealth]
  );

  const handleDeleteDocsBatch = useCallback(
    async (sources: string[]) => {
      const res = await deleteDocsBatch(sources);
      refreshDocs();
      refreshHealth();
      return res;
    },
    [refreshDocs, refreshHealth]
  );

  const handleClear = useCallback(() => {
    if (session) clearUserMessages(session.username);
    setMessages([WELCOME]);
  }, [session]);

  if (!session) {
    return <AuthPage onSubmit={handleAuth} />;
  }

  return (
    <div className="app-shell">
      <Sidebar
        docs={docs}
        onUploadFiles={handleUploadFiles}
        onDeleteDoc={handleDeleteDoc}
        onDeleteDocsBatch={handleDeleteDocsBatch}
        health={health}
        onRefresh={refreshHealth}
      />

      <main className="app-main">
        <header className="app-topbar">
          <div className="topbar-title">
            <span className="topbar-kicker">HYBRID RAG</span>
            <span className="topbar-model">
              <span className="dot-accent" /> llm · embedding
            </span>
          </div>
          <div className="topbar-actions">
            <span className="user-chip">
              <UserCircle size={15} /> {session.username}
            </span>
            <button
              className="ghost-btn"
              onClick={handleClear}
              title="清空对话"
              disabled={streaming}
            >
              <Eraser size={15} /> 清空
            </button>
            <button
              className="ghost-btn"
              onClick={handleLogout}
              title="退出登录"
              disabled={streaming}
            >
              <LogOut size={15} /> 退出
            </button>
          </div>
        </header>

        <ChatWindow
          messages={messages}
          streaming={streaming}
          onSend={handleSend}
          mode={mode}
          onModeChange={setMode}
          webSearchAvailable={webSearchAvailable}
        />
      </main>
    </div>
  );
}
