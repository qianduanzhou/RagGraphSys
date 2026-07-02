import type {
  BatchDeleteResponse,
  BatchIngestResponse,
  AuthResponse,
  ChatHistoryItem,
  ChatMode,
  ChatResponse,
  DeleteDocResponse,
  HealthResponse,
  IngestResponse,
  NodeUpdate,
  SourceRef,
  StreamCallbacks,
  UploadedDoc,
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
  if (lower.includes("account must be at least")) return "账号至少 5 位，只能使用数字和字母";
  if (lower.includes("password must be longer")) return "密码需超过 8 位";
  if (lower.includes("password can only contain")) return "密码只能使用数字、字母和英文符号";
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

export async function chat(
  message: string,
  history: ChatHistoryItem[],
  mode: ChatMode = "rag"
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, history, mode }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function ingestText(
  text: string,
  source = "manual"
): Promise<IngestResponse> {
  const res = await fetch(`${BASE}/ingest`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ text, source }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function ingestFile(file: File): Promise<IngestResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/ingest/file`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 批量上传多个文件（或整个文件夹的所有文件）。 */
export async function ingestFiles(files: File[]): Promise<BatchIngestResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch(`${BASE}/ingest/files`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 拉取已入库文档列表（持久化在 Qdrant，刷新界面后仍可看到）。 */
export async function fetchDocs(): Promise<UploadedDoc[]> {
  const res = await fetch(`${BASE}/docs`, { headers: authHeaders() });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 删除指定来源的文档（清除 Qdrant 分片 + Neo4j 图谱关系）。 */
export async function deleteDoc(source: string): Promise<DeleteDocResponse> {
  const res = await fetch(`${BASE}/docs/delete`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ source }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 批量删除多个文档（单次请求、逐项容错；返回每个文档的 ok/failed 明细）。 */
export async function deleteDocsBatch(sources: string[]): Promise<BatchDeleteResponse> {
  const res = await fetch(`${BASE}/docs/delete/batch`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ sources }),
  });
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

export async function chatStream(
  message: string,
  history: ChatHistoryItem[],
  cb: StreamCallbacks,
  mode: ChatMode = "rag"
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, history, mode }),
  });
  if (!res.ok) {
    cb.onError?.(await parseError(res));
    return;
  }

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
