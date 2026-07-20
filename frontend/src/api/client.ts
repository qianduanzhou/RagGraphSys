import type {
  BatchIngestResponse,
  AuthResponse,
  ChatMode,
  Conversation,
  ConversationSummary,
  DeleteDocResponse,
  GraphTaskListResponse,
  HealthResponse,
  NodeUpdate,
  SourceRef,
  StreamCallbacks,
} from "../types";

const BASE = "/api";
let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  return authToken ? { ...base, Authorization: `Bearer ${authToken}` } : base;
}

function normalizeErrorMessage(message: string): string {
  const text = message.trim();
  const lower = text.toLowerCase();
  if (lower.includes("invalid username or password")) return "账号或密码错误";
  if (lower.includes("account already exists")) return "该账号已存在";
  if (lower.includes("missing bearer token")) return "请先登录";
  if (lower.includes("invalid token")) return "登录已失效，请重新登录";
  if (lower.includes("authentication not initialised")) return "认证服务尚未初始化";
  if (lower.includes("application not initialised")) return "服务尚未初始化";
  if (lower.includes("graph failed")) return text.replace(/graph failed/i, "问答流程执行失败");
  if (lower.includes("ingest failed")) return text.replace(/ingest failed/i, "入库失败");
  if (lower.includes("delete failed")) return text.replace(/delete failed/i, "删除失败");
  if (lower.includes("unsupported file type")) return text.replace(/unsupported file type/i, "不支持的文件类型");
  return text || "未知错误";
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return normalizeErrorMessage(data.detail);
    if (Array.isArray(data.detail)) return "请求参数不合法，请检查输入内容";
    if (typeof data.message === "string") return normalizeErrorMessage(data.message);
    return `请求失败（HTTP ${res.status}）`;
  } catch {
    return `请求失败（HTTP ${res.status}）`;
  }
}

export async function registerAccount(username: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function loginAccount(username: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchCurrentUser(): Promise<{ username: string }> {
  const res = await fetch(`${BASE}/auth/me`, { headers: authHeaders() });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE}/health`, { headers: authHeaders() });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

// ------------------------------------------------------------------
// 基于 Server-Sent Events 的流式对话。
// 服务端帧：{type:"meta"} -> {type:"delta"}* -> {type:"done"|"error"}
// ------------------------------------------------------------------
type StreamFrame =
  | { type: "meta"; sources?: SourceRef[]; used_rag?: boolean }
  | { type: "node"; node?: string; update?: NodeUpdate }
  | { type: "delta"; text?: string }
  | { type: "done" }
  | { type: "error"; message?: string };

/** 消费一个已就绪的 SSE 响应流，按帧分发到回调。 */
async function consumeChatStream(res: Response, cb: StreamCallbacks): Promise<void> {
  const reader = res.body?.getReader();
  if (!reader) {
    cb.onError?.("服务未返回响应内容");
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 帧之间以空行分隔。
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const raw = dataLine.slice(5).trim();
      if (!raw) continue;
      let obj: StreamFrame;
      try {
        obj = JSON.parse(raw) as StreamFrame;
      } catch {
        continue;
      }
      switch (obj.type) {
        case "meta":
          cb.onMeta?.(obj.sources ?? [], !!obj.used_rag);
          break;
        case "node":
          cb.onNode?.(obj.node ?? "", obj.update ?? {});
          break;
        case "delta":
          cb.onDelta?.(obj.text ?? "");
          break;
        case "done":
          cb.onDone?.();
          break;
        case "error":
          cb.onError?.(normalizeErrorMessage(obj.message ?? "未知错误"));
          break;
      }
    }
  }
}

// ------------------------------------------------------------------
// 对话管理（多对话：CRUD + 文档 + 对话级流式问答）
// ------------------------------------------------------------------
export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/conversations`, { headers: authHeaders() });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createConversation(title?: string): Promise<Conversation> {
  const res = await fetch(`${BASE}/conversations`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title: title ?? null }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getConversation(id: string): Promise<Conversation> {
  const res = await fetch(`${BASE}/conversations/${id}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function renameConversation(id: string, title: string): Promise<Conversation> {
  const res = await fetch(`${BASE}/conversations/${id}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(await parseError(res));
}

/** 清空指定对话的消息历史（保留对话本身与文档清单）。 */
export async function clearConversation(id: string): Promise<Conversation> {
  const res = await fetch(`${BASE}/conversations/${id}/clear`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 往指定对话上传文档（多文件；zip 由后端解包）。 */
export async function uploadConversationDocs(
  id: string,
  files: File[]
): Promise<BatchIngestResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch(`${BASE}/conversations/${id}/documents`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 删除对话内某文档。 */
export async function fetchConversationGraphTasks(id: string): Promise<GraphTaskListResponse> {
  const res = await fetch(`${BASE}/conversations/${id}/graph-tasks`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteConversationDoc(
  id: string,
  source: string
): Promise<DeleteDocResponse> {
  const res = await fetch(`${BASE}/conversations/${id}/documents`, {
    method: "DELETE",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ source }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function downloadConversationSourceFile(
  conversationId: string,
  source: string
): Promise<void> {
  const res = await fetch(
    `${BASE}/conversations/${conversationId}/documents/download?source=${encodeURIComponent(source)}`,
    {
      headers: authHeaders(),
    }
  );
  if (!res.ok) throw new Error(await parseError(res));
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = source.split(/[\\/]/).pop() || source || "download";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * 对话级流式问答：后端从对话记录加载历史并写回，前端只发 message + mode。
 */
export async function chatStreamInConversation(
  conversationId: string,
  message: string,
  cb: StreamCallbacks,
  mode: ChatMode = "rag"
): Promise<void> {
  const res = await fetch(`${BASE}/conversations/${conversationId}/chat/stream`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, mode }),
  });
  if (!res.ok) {
    cb.onError?.(await parseError(res));
    return;
  }
  await consumeChatStream(res, cb);
}
