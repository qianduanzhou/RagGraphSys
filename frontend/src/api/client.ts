import type {
  BatchDeleteResponse,
  BatchIngestResponse,
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

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return data.detail || data.message || `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}: ${res.statusText}`;
  }
}

export async function chat(
  message: string,
  history: ChatHistoryItem[],
  mode: ChatMode = "rag"
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
    headers: { "Content-Type": "application/json" },
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
    body: form,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 拉取已入库文档列表（持久化在 Qdrant，刷新界面后仍可看到）。 */
export async function fetchDocs(): Promise<UploadedDoc[]> {
  const res = await fetch(`${BASE}/docs`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 删除指定来源的文档（清除 Qdrant 分片 + Neo4j 图谱关系）。 */
export async function deleteDoc(source: string): Promise<DeleteDocResponse> {
  const res = await fetch(`${BASE}/docs/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** 批量删除多个文档（单次请求、逐项容错；返回每个文档的 ok/failed 明细）。 */
export async function deleteDocsBatch(sources: string[]): Promise<BatchDeleteResponse> {
  const res = await fetch(`${BASE}/docs/delete/batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sources }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE}/health`);
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, mode }),
  });
  if (!res.ok) {
    cb.onError?.(await parseError(res));
    return;
  }

  const reader = res.body?.getReader();
  if (!reader) {
    cb.onError?.("no response body");
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
          cb.onError?.(obj.message ?? "unknown error");
          break;
      }
    }
  }
}
