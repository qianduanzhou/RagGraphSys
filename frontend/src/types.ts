export type Role = "user" | "assistant";

export interface SourceRef {
  type: "qdrant" | "neo4j" | "web";
  content: string;
  score?: number;
  source?: string;
  // web 来源额外字段
  title?: string;
  url?: string;
}

export type StepStatus = "pending" | "active" | "done";

export interface PipelineStep {
  key: string;
  label: string;
  status: StepStatus;
}

/** 固定管线，作为实时步进器展示；key 与 LangGraph 节点名一一对应。 */
export const PIPELINE: ReadonlyArray<{ key: string; label: string }> = [
  { key: "router_node", label: "路由" },
  { key: "qdrant_node", label: "向量" },
  { key: "neo4j_node", label: "图谱" },
  { key: "merge_node", label: "融合" },
  { key: "llm_node", label: "生成" },
];

export type ChatMode = "rag" | "multi";

/** 多智能体模式管线，key 与多智能体 LangGraph 节点名一一对应。 */
export const MULTI_AGENT_PIPELINE: ReadonlyArray<{ key: string; label: string }> = [
  { key: "dispatch_node", label: "调度" },
  { key: "rag_agent_node", label: "RAG智能体" },
  { key: "web_agent_node", label: "联网智能体" },
  { key: "integration_node", label: "整合" },
];

export interface NodeUpdate {
  needs_rag?: boolean;
  used_rag?: boolean;
  used_web?: boolean;
  hits?: number;
  sources?: SourceRef[];
  answer?: string; // 多智能体下两个 agent 的原始回答文本
  iterations?: number;
  passed?: boolean;
  feedback?: string;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  sources?: SourceRef[];
  usedRag?: boolean;
  usedWeb?: boolean;
  error?: boolean;
  streaming?: boolean;
  mode?: ChatMode;
  steps?: PipelineStep[];
  ragAgentAnswer?: string; // 多智能体：RAG 智能体原始回答（折叠面板）
  webAgentAnswer?: string; // 多智能体：联网智能体原始回答（折叠面板）
}

export interface StreamCallbacks {
  onMeta?: (sources: SourceRef[], usedRag: boolean) => void;
  onNode?: (node: string, update: NodeUpdate) => void;
  onDelta?: (text: string) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

export interface ChatHistoryItem {
  role: string;
  content: string;
}

export interface ChatResponse {
  answer: string;
  sources: SourceRef[];
  used_rag: boolean;
  iterations: number;
}

export interface IngestResponse {
  status: string;
  chunks: number;
  triples: number;
}

export interface FileIngestResult {
  name: string;
  chunks: number;
  triples: number;
  ok: boolean;
  error?: string;
}

export interface BatchIngestResponse {
  status: string;
  chunks: number;
  triples: number;
  succeeded: number;
  failed: number;
  files: FileIngestResult[];
}

export interface DeleteDocResponse {
  source: string;
  chunks: number;
  relations: number;
}

export interface BatchDeleteItem {
  source: string;
  chunks?: number;
  relations?: number;
  ok: boolean;
  error?: string;
}

export interface BatchDeleteResponse {
  status: string;
  deleted: number;
  failed: number;
  results: BatchDeleteItem[];
}

export interface HealthResponse {
  status: string;
  qdrant: boolean;
  neo4j: boolean;
  web_search: boolean;
  counts: {
    qdrant_points?: number;
    neo4j_entities?: number;
  };
}

export interface UploadedDoc {
  name: string;
  chunks: number;
  triples: number;
  at: number;
}
