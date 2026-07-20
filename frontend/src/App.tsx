import { useCallback, useEffect, useRef, useState } from "react";
import { Eraser, LogOut, Moon, Plus, Sun, UserCircle } from "lucide-react";
import Sidebar from "./components/Sidebar";
import ChatWindow from "./components/ChatWindow";
import AuthPage from "./components/AuthPage";
import {
 chatStreamInConversation,
  clearConversation,
  createConversation,
  deleteConversation,
  deleteConversationDoc,
  downloadConversationSourceFile,
  fetchCurrentUser,
  fetchConversationGraphTasks,
  fetchHealth,
 getConversation,
 listConversations,
  loginAccount,
  registerAccount,
  renameConversation,
  setAuthToken,
  uploadConversationDocs,
} from "./api/client";
import { clearSession, loadSession, saveSession } from "./auth-storage";
import {
  MULTI_AGENT_PIPELINE,
  PIPELINE,
  SOURCE_PIPELINE,
  type AuthSession,
  type BatchIngestResponse,
  type ChatMessage,
  type ChatMode,
  type Conversation,
  type ConversationDoc,
  type ConversationMessage,
  type ConversationSummary,
  type GraphTaskInfo,
  type HealthResponse,
  type StepStatus,
} from "./types";
import "./App.css";

type ThemeMode = "dark" | "light";

const THEME_STORAGE_KEY = "hybrid-rag-theme";

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

const WELCOME: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "**你好，我是 Hybrid RAG 助手。**\n\n我通过 **Qdrant 语义检索** 与 **Neo4j 知识图谱** 双路召回，再由 **大模型** 自动自我反思。\n\n先在左侧当前对话里上传一份文档，然后向我提问吧。",
};

const initialSession = loadSession();
setAuthToken(initialSession?.token ?? null);

function loadTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  return saved === "light" || saved === "dark" ? saved : "dark";
}

function toMessages(conv: Conversation | null): ChatMessage[] {
  const msgs: ChatMessage[] = (conv?.messages ?? []).map((m: ConversationMessage, i: number) => ({
    id: `${conv?.id ?? "c"}-${i}`,
    role: (m.role === "assistant" ? "assistant" : "user") as "assistant" | "user",
    content: m.content,
  }));
  return msgs.length > 0 ? msgs : [WELCOME];
}

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(initialSession);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  const [streaming, setStreaming] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [graphTasks, setGraphTasks] = useState<Record<string, GraphTaskInfo>>({});
  const [mode, setMode] = useState<ChatMode>("rag");
  const [theme, setTheme] = useState<ThemeMode>(loadTheme);
  const [webSearchAvailable, setWebSearchAvailable] = useState<boolean>(true);
  const loadedRef = useRef<string | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  const resetSession = useCallback(() => {
    setAuthToken(null);
    clearSession();
    setSession(null);
    setConversations([]);
    setCurrentId(null);
    setCurrent(null);
    setMessages([WELCOME]);
    loadedRef.current = null;
  }, []);

  const refreshHealth = useCallback(() => {
    fetchHealth()
      .then((h) => {
        setHealth(h);
        setWebSearchAvailable(h.web_search);
      })
      .catch(() => setHealth(null));
  }, []);

  const refreshGraphTasks = useCallback(
    async (id: string | null) => {
      if (!id || !session) {
        setGraphTasks({});
        return;
      }
      try {
        const res = await fetchConversationGraphTasks(id);
        setGraphTasks(Object.fromEntries(res.tasks.map((task) => [task.source, task])));
      } catch {
        setGraphTasks({});
      }
    },
    [session]
  );

  useEffect(() => {
    void refreshGraphTasks(currentId);
  }, [currentId, refreshGraphTasks]);

  const hasActiveGraphTask = Object.values(graphTasks).some(
    (task) => task.status === "pending" || task.status === "running"
  );

  useEffect(() => {
    if (!currentId || !hasActiveGraphTask) return;
    const timer = window.setInterval(() => {
      void refreshGraphTasks(currentId);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [currentId, hasActiveGraphTask, refreshGraphTasks]);

  const selectConversation = useCallback(async (id: string) => {
    setCurrentId(id);
    try {
      const conv = await getConversation(id);
      setCurrent(conv);
      setMessages(toMessages(conv));
      loadedRef.current = id;
      await refreshGraphTasks(id);
    } catch {
      setCurrent(null);
      setMessages([WELCOME]);
      setGraphTasks({});
    }
  }, [refreshGraphTasks]);

  const loadConversations = useCallback(async () => {
    try {
      let list = await listConversations();
      if (list.length === 0) {
        const created = await createConversation();
        list = [{ id: created.id, title: created.title, updated_at: created.updated_at, document_count: 0, preview: "" }];
      }
      setConversations(list);
      await selectConversation(list[0].id);
    } catch {
      // 忽略：未登录或会话列表暂时不可用。
    }
  }, [selectConversation]);

  useEffect(() => {
    fetchHealth()
      .then((h) => {
        setHealth(h);
        setWebSearchAvailable(h.web_search);
      })
      .catch(() => setHealth(null));
    if (!session) return;
    fetchCurrentUser().then(loadConversations).catch(resetSession);
  }, [resetSession, session, loadConversations]);

  const handleAuth = useCallback(
    async (authMode: "login" | "register", username: string, password: string) => {
      const next =
        authMode === "login"
          ? await loginAccount(username, password)
          : await registerAccount(username, password);
      setAuthToken(next.token);
      saveSession(next);
      setSession(next);
      refreshHealth();
      await loadConversations();
    },
    [loadConversations, refreshHealth]
  );

  const handleLogout = useCallback(() => resetSession(), [resetSession]);

  const handleNewConversation = useCallback(async () => {
    const conv = await createConversation();
    setConversations((prev) => [
      { id: conv.id, title: conv.title, updated_at: conv.updated_at, document_count: 0, preview: "" },
      ...prev,
    ]);
    await selectConversation(conv.id);
  }, [selectConversation]);

  const handleRenameConversation = useCallback(
    async (id: string, title: string) => {
      const conv = await renameConversation(id, title);
      setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title: conv.title } : c)));
      if (id === currentId) setCurrent((c) => (c ? { ...c, title: conv.title } : c));
    },
    [currentId]
  );

  const handleDeleteConversations = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      await Promise.all(ids.map((id) => deleteConversation(id)));
      const removed = new Set(ids);
      setConversations((prev) => {
        const next = prev.filter((c) => !removed.has(c.id));
        if (currentId && removed.has(currentId)) {
          if (next.length > 0) {
            selectConversation(next[0].id);
          } else {
            createConversation().then((c) => {
              setConversations([{ id: c.id, title: c.title, updated_at: c.updated_at, document_count: 0, preview: "" }]);
              selectConversation(c.id);
            });
          }
        }
        return next;
      });
      refreshHealth();
    },
    [currentId, selectConversation, refreshHealth]
  );

  const refreshCurrent = useCallback(async () => {
    if (!currentId) return;
    try {
      const conv = await getConversation(currentId);
      setCurrent(conv);
      setMessages(toMessages(conv));
      setConversations((prev) =>
        prev.map((c) =>
          c.id === currentId
            ? {
                ...c,
                title: conv.title,
                updated_at: conv.updated_at,
                document_count: conv.documents.length,
                preview: conv.messages[conv.messages.length - 1]?.content?.slice(0, 40) ?? "",
              }
            : c
        )
      );
    } catch {
      // 刷新失败时保留当前界面状态。
    }
  }, [currentId]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!currentId) return;
      const pipeline = mode === "multi" ? MULTI_AGENT_PIPELINE : mode === "source" ? SOURCE_PIPELINE : PIPELINE;
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

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);

      const patch = (updater: (m: ChatMessage) => ChatMessage) =>
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? updater(m) : m)));

      try {
        await chatStreamInConversation(
          currentId,
          text,
          {
            onNode: (node, update) =>
              patch((m) => {
                const steps = (m.steps ?? []).map((s) => ({ ...s }));
                const setStep = (key: string, status: StepStatus) => {
                  const i = steps.findIndex((s) => s.key === key);
                  if (i >= 0) steps[i] = { ...steps[i], status };
                };

                if (mode === "multi") {
                  if (node === "dispatch_node") {
                    setStep("dispatch_node", "done");
                    setStep("rag_agent_node", "active");
                    setStep("source_agent_node", "active");
                    setStep("web_agent_node", "active");
                  } else if (node === "rag_agent_node") {
                    setStep("rag_agent_node", "done");
                  } else if (node === "source_agent_node") {
                    setStep("source_agent_node", "done");
                  } else if (node === "web_agent_node") {
                    setStep("web_agent_node", "done");
                  } else if (node === "integration_node") {
                    setStep("integration_node", "done");
                  }
                } else {
                  const idx = steps.findIndex((s) => s.key === node);
                  if (idx >= 0) {
                    const nextStatus =
                      update.status === "active" || update.status === "done"
                        ? update.status
                        : "done";
                    steps[idx] = { ...steps[idx], status: nextStatus };
                    if (nextStatus === "done" && idx + 1 < steps.length && steps[idx + 1].status !== "done") {
                      steps[idx + 1] = { ...steps[idx + 1], status: "active" };
                    }
                  }
                }

                const next: ChatMessage = { ...m, steps };
                if (node === "rag_agent_node") {
                  next.ragAgentAnswer = update.answer;
                  if (update.sources) next.sources = update.sources;
                  if (typeof update.used_rag === "boolean") next.usedRag = update.used_rag;
                } else if (node === "source_agent_node") {
                  next.sourceAgentAnswer = update.answer;
                  next.sources = [...(m.sources ?? []), ...(update.sources ?? [])];
                  if (typeof update.used_source === "boolean") next.usedSource = update.used_source;
                } else if (node === "web_agent_node") {
                  next.webAgentAnswer = update.answer;
                  next.sources = [...(m.sources ?? []), ...(update.sources ?? [])];
                  if (typeof update.used_web === "boolean") next.usedWeb = update.used_web;
                } else {
                  if (update.sources) next.sources = update.sources;
                  if (typeof update.used_rag === "boolean") next.usedRag = update.used_rag;
                  if (typeof update.used_source === "boolean") next.usedSource = update.used_source;
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
          },
          mode
        );
        await refreshCurrent();
      } finally {
        setStreaming(false);
        refreshHealth();
      }
    },
    [currentId, mode, refreshCurrent, refreshHealth]
  );

  const handleUploadFiles = useCallback(
    async (files: File[]): Promise<BatchIngestResponse> => {
      if (!currentId) throw new Error("未选择对话");
      const res = await uploadConversationDocs(currentId, files);
      await refreshCurrent();
      await refreshGraphTasks(currentId);
      refreshHealth();
      return res;
    },
    [currentId, refreshCurrent, refreshGraphTasks, refreshHealth]
  );

  const handleDeleteDocs = useCallback(
    async (sources: string[]) => {
      if (!currentId || sources.length === 0) return;
      await Promise.all(sources.map((s) => deleteConversationDoc(currentId, s)));
      await refreshCurrent();
      setGraphTasks((prev) => {
        const next = { ...prev };
        for (const source of sources) delete next[source];
        return next;
      });
      await refreshGraphTasks(currentId);
      refreshHealth();
    },
    [currentId, refreshCurrent, refreshGraphTasks, refreshHealth]
  );

  const handleDownloadSource = useCallback(
    async (source: string) => {
      if (!currentId) return;
      try {
        await downloadConversationSourceFile(currentId, source);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: uid(),
            role: "assistant",
            content: `下载失败：${(err as Error).message || "源文件不可用"}`,
            error: true,
          },
        ]);
      }
    },
    [currentId]
  );

  const handleClear = useCallback(async () => {
    if (!currentId) {
      setMessages([WELCOME]);
      return;
    }
    try {
      const conv = await clearConversation(currentId);
      setCurrent(conv);
      setMessages(toMessages(conv));
      setConversations((prev) =>
        prev.map((c) => (c.id === currentId ? { ...c, preview: "", updated_at: conv.updated_at } : c))
      );
    } catch {
      setMessages([WELCOME]);
    }
  }, [currentId]);

  const handleToggleTheme = useCallback(() => {
    setTheme((currentTheme) => (currentTheme === "dark" ? "light" : "dark"));
  }, []);

  if (!session) {
    return <AuthPage onSubmit={handleAuth} />;
  }

  const docs: ConversationDoc[] = current?.documents ?? [];
  const isLightTheme = theme === "light";

  return (
    <div className="app-shell">
      <Sidebar
        conversations={conversations}
        currentId={currentId}
        onSelectConversation={selectConversation}
        onCreateConversation={handleNewConversation}
        onRenameConversation={handleRenameConversation}
        onBatchDeleteConversations={handleDeleteConversations}
        docs={docs}
        graphTasks={graphTasks}
        health={health}
        onUploadFiles={handleUploadFiles}
        onDeleteDocs={handleDeleteDocs}
        onDownloadDoc={handleDownloadSource}
        onRefresh={refreshHealth}
        disabled={streaming}
      />

      <main className="app-main">
        <header className="app-topbar">
          <div className="topbar-title">
            <span className="topbar-kicker">HYBRID RAG</span>
            <span className="topbar-model">
              <span className="dot-accent" /> {current?.title ?? "对话"}
            </span>
          </div>
          <div className="topbar-actions">
            <span className="user-chip">
              <UserCircle size={15} /> {session.username}
            </span>
            <button
              className="ghost-btn theme-toggle"
              onClick={handleToggleTheme}
              title={isLightTheme ? "切换到暗黑主题" : "切换到白色主题"}
              aria-label={isLightTheme ? "切换到暗黑主题" : "切换到白色主题"}
            >
              {isLightTheme ? <Moon size={15} /> : <Sun size={15} />}
              {isLightTheme ? "暗黑" : "白色"}
            </button>
            <button
              className="ghost-btn"
              onClick={handleNewConversation}
              title="新对话"
              disabled={streaming}
            >
              <Plus size={15} /> 新对话
            </button>
            <button className="ghost-btn" onClick={handleClear} title="清空显示" disabled={streaming}>
              <Eraser size={15} /> 清空
            </button>
            <button className="ghost-btn" onClick={handleLogout} title="退出登录" disabled={streaming}>
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
          onDownloadSource={handleDownloadSource}
        />
      </main>
    </div>
  );
}
