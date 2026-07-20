import { useRef, useState } from "react";
import { CheckSquare, Database, Download, FileArchive, FileText, GitBranch, RefreshCw, Trash2, UploadCloud, X } from "lucide-react";
import type {
  BatchIngestResponse,
  ConversationDoc,
  ConversationSummary,
  GraphTaskInfo,
  HealthResponse,
} from "../types";
import ConversationList from "./ConversationList";
import { ConfirmDialog } from "./Dialog";
import "./Sidebar.css";

// 与后端 services/file_parser.ALLOWED_EXTS 保持一致（语义同一套）。
const ALLOWED_EXT = [
  ".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rst",
  ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss", ".vue", ".svelte",
  ".py", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".kt", ".scala",
  ".swift", ".rb", ".php", ".lua", ".dart", ".r",
  ".sh", ".bash", ".ps1", ".sql", ".xml", ".yaml", ".yml", ".toml", ".ini", ".env",
  ".pdf", ".docx", ".xlsx", ".xls",
  ".zip",
];

interface Props {
  conversations: ConversationSummary[];
  currentId: string | null;
  onSelectConversation: (id: string) => void;
  onCreateConversation: () => void;
  onRenameConversation: (id: string, title: string) => void;
  /** 批量删除对话（单删即长度 1）。 */
  onBatchDeleteConversations: (ids: string[]) => void;
  docs: ConversationDoc[];
  graphTasks: Record<string, GraphTaskInfo>;
  health: HealthResponse | null;
  onUploadFiles: (files: File[]) => Promise<BatchIngestResponse>;
  /** 批量删除文档（单删即长度 1）。 */
  onDeleteDocs: (sources: string[]) => Promise<void>;
  onDownloadDoc: (source: string) => void;
  onRefresh: () => void;
  disabled?: boolean;
}

type UploadState = "idle" | "uploading" | "done" | "error";

function isAllowed(name: string): boolean {
  const lower = name.toLowerCase();
  return ALLOWED_EXT.some((ext) => lower.endsWith(ext));
}

function filterAllowed(fileList: FileList | null | undefined): File[] {
  if (!fileList) return [];
  return Array.from(fileList).filter((f) => isAllowed(f.name) && f.size > 0);
}

function formatTime(at: number): string {
  if (!at) return "";
  const d = new Date(at * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function formatDuration(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.floor(seconds ?? 0));
  if (total < 60) return `${total}秒`;
  const minutes = Math.floor(total / 60);
  const rest = String(total % 60).padStart(2, "0");
  return `${minutes}分${rest}秒`;
}

function graphTaskText(task?: GraphTaskInfo): string | null {
  if (!task) return null;
  if (task.status === "pending") return "图谱排队";
  const duration = formatDuration(task.elapsed_seconds);
  if (task.status === "running") return `图谱抽取中 · ${duration}`;
  if (task.status === "failed") return `图谱失败 · ${duration}`;
  const triples = task.triples > 0 ? ` · ${task.triples} 关系` : "";
  return `图谱完成${triples} · ${duration}`;
}

export default function Sidebar({
  conversations,
  currentId,
  onSelectConversation,
  onCreateConversation,
  onRenameConversation,
  onBatchDeleteConversations,
  docs,
  graphTasks,
  health,
  onUploadFiles,
  onDeleteDocs,
  onDownloadDoc,
  onRefresh,
  disabled,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [state, setState] = useState<UploadState>("idle");
  const [notice, setNotice] = useState<string | null>(null);
  // 正在删除的文档名（单删时用于该行 spinner）；批删时为 null。
  const [deleting, setDeleting] = useState<string | null>(null);

  // 文档多选删除
  const [docSelectMode, setDocSelectMode] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const [deleteDocTargets, setDeleteDocTargets] = useState<{ sources: string[]; label: string } | null>(null);

  const allDocsSelected = docs.length > 0 && selectedDocs.size === docs.length;

  async function upload(files: File[]) {
    if (files.length === 0) {
      setState("error");
      setNotice("没有可上传的文档（支持代码 / CSV / Excel / PDF / Word / Markdown / zip）");
      return;
    }
    setState("uploading");
    setNotice(`正在入库 ${files.length} 个文件…`);
    try {
      const res = await onUploadFiles(files);
      setState("done");
      const okMsg = `已入库 ${res.succeeded} 个文件，共 ${res.chunks} 片段`;
      setNotice(res.failed > 0 ? `${okMsg}（${res.failed} 个失败）` : okMsg);
    } catch (err) {
      setState("error");
      setNotice((err as Error).message || "上传失败");
    }
  }

  function handlePick(files: FileList | null) {
    upload(filterAllowed(files));
  }

  // 文档删除：单删 / 批删统一走 ConfirmDialog → onDeleteDocs
  function requestDeleteDocs(sources: string[], label: string) {
    setDeleteDocTargets({ sources, label });
  }

  async function confirmDeleteDocs() {
    if (!deleteDocTargets) return;
    const { sources, label } = deleteDocTargets;
    setDeleteDocTargets(null);
    setDeleting(sources.length === 1 ? sources[0] : null);
    try {
      await onDeleteDocs(sources);
      setState("done");
      setNotice(`已删除 ${label}`);
      setTimeout(() => setNotice(null), 2600);
      setSelectedDocs(new Set());
      setDocSelectMode(false);
    } catch (err) {
      setState("error");
      setNotice((err as Error).message || "删除失败");
    } finally {
      setDeleting(null);
    }
  }

  function enterDocSelect() {
    setSelectedDocs(new Set());
    setDocSelectMode(true);
  }
  function exitDocSelect() {
    setSelectedDocs(new Set());
    setDocSelectMode(false);
  }
  function toggleDocSelect(name: string) {
    setSelectedDocs((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }
  function toggleAllDocs() {
    setSelectedDocs(allDocsSelected ? new Set() : new Set(docs.map((d) => d.name)));
  }

  return (
    <aside className="sidebar">
      {/* 品牌 */}
      <div className="brand">
        <svg className="brand-graph" viewBox="0 0 120 80" aria-hidden>
          <path d="M18 64 L60 16 L102 64 Z" fill="none" stroke="#2b3744" strokeWidth="1.4" />
          <path
            d="M18 64 L60 16 L102 64 L60 48 Z"
            fill="none"
            stroke="#ffb454"
            strokeWidth="1"
            strokeDasharray="4 4"
            className="graph-dash"
          />
          <circle cx="18" cy="64" r="5" fill="#ffb454" />
          <circle cx="60" cy="16" r="5" fill="#3dd68c" />
          <circle cx="102" cy="64" r="5" fill="#ffb454" />
          <circle cx="60" cy="48" r="4" fill="#3dd68c" />
        </svg>
        <h1 className="brand-name">
          Knowledge<span>Lab</span>
        </h1>
        <p className="brand-sub">Hybrid Graph + Vector RAG</p>
      </div>

      {/* 对话列表 */}
      <ConversationList
        conversations={conversations}
        currentId={currentId}
        onSelect={onSelectConversation}
        onCreate={onCreateConversation}
        onRename={onRenameConversation}
        onBatchDelete={onBatchDeleteConversations}
        disabled={disabled}
      />

      {/* 当前对话文档上传 */}
      <div className="side-section">
        <div className="side-label">
          <UploadCloud size={14} /> 当前对话 · 上传
        </div>
        <div
          className={`dropzone ${dragging ? "is-drag" : ""} ${state}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handlePick(e.dataTransfer.files);
          }}
          onClick={() => !disabled && fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_EXT.join(",")}
            multiple
            hidden
            onChange={(e) => {
              handlePick(e.target.files);
              e.target.value = "";
            }}
          />
          <input
            ref={zipInputRef}
            type="file"
            accept=".zip"
            hidden
            onChange={(e) => {
              handlePick(e.target.files);
              e.target.value = "";
            }}
          />
          {state === "uploading" ? (
            <div className="dz-uploading">
              <RefreshCw size={20} className="spin" />
              <span>正在切片 · 向量化 · 抽取三元组…</span>
            </div>
          ) : (
            <>
              <UploadCloud size={22} />
              <span className="dz-title">拖入文件 / 点击多选上传</span>
              <span className="dz-hint">归属当前对话 · 代码/PDF/Word/Excel/Markdown/zip</span>
            </>
          )}
        </div>
        <button
          type="button"
          className="folder-btn"
          onClick={(e) => {
            e.stopPropagation();
            zipInputRef.current?.click();
          }}
          disabled={state === "uploading" || disabled}
          title="上传 zip 压缩包，后端自动解包入库"
        >
          <FileArchive size={14} /> 上传 zip 压缩包
        </button>
        {notice && <div className={`notice ${state}`}>{notice}</div>}
      </div>

      {/* 当前对话文档列表 */}
      <div className="side-section side-grow">
        <div className="side-label">
          <FileText size={14} /> 当前对话文档
          <span className="side-count">{docs.length}</span>
          {!docSelectMode ? (
            <button
              type="button"
              className="conv-new-btn"
              onClick={enterDocSelect}
              disabled={disabled || docs.length === 0}
              title="多选删除"
            >
              <CheckSquare size={13} /> 选择
            </button>
          ) : (
            <button
              type="button"
              className="conv-new-btn conv-exit-select"
              onClick={exitDocSelect}
              title="退出选择"
            >
              <X size={13} /> 完成
            </button>
          )}
        </div>

        {docSelectMode && (
          <div className="batch-bar">
            <div className="batch-select-all" onClick={toggleAllDocs}>
              <input type="checkbox" checked={allDocsSelected} readOnly />
              <span>{allDocsSelected ? "取消全选" : "全选"}</span>
            </div>
            <span className="batch-count">已选 {selectedDocs.size}</span>
            <button
              type="button"
              className="batch-btn"
              disabled={selectedDocs.size === 0 || disabled}
              onClick={() => requestDeleteDocs([...selectedDocs], `${selectedDocs.size} 个文档`)}
            >
              <Trash2 size={12} /> 删除
            </button>
          </div>
        )}

        <div className="doc-list">
          {docs.length === 0 ? (
            <p className="empty">当前对话暂无文档，上传后自动切分入库。</p>
          ) : (
            docs.map((d, i) => {
              const isSel = selectedDocs.has(d.name);
              const isDeleting = deleting === d.name;
              const graphTask = graphTasks[d.name];
              const graphLabel = graphTaskText(graphTask);
              return (
                <div
                  className={`doc-item ${isSel ? "selected" : ""} ${
                    docSelectMode ? "is-selectable" : ""
                  }`}
                  key={`${d.name}-${d.at}-${i}`}
                  onClick={() => {
                    if (docSelectMode) toggleDocSelect(d.name);
                  }}
                >
                  {docSelectMode && (
                    <input
                      type="checkbox"
                      className="doc-check"
                      checked={isSel}
                      readOnly
                    />
                  )}
                  <FileText size={15} className="doc-icon" />
                  <div className="doc-meta">
                    <span className="doc-name" title={d.name}>
                      {d.name}
                    </span>
                    <span className="doc-stat">
                      {d.chunks} 片段{formatTime(d.at) ? ` · ${formatTime(d.at)}` : ""}
                    </span>
                    {graphTask && graphLabel && (
                      <span className={`graph-task-pill graph-task-${graphTask.status}`}>
                        {graphLabel}
                      </span>
                    )}
                  </div>
                  {!docSelectMode && (
                    <div className="doc-actions">
                      <button
                        type="button"
                        className="doc-action"
                        title="下载源文件"
                        disabled={disabled}
                        onClick={(e) => {
                          e.stopPropagation();
                          onDownloadDoc(d.name);
                        }}
                      >
                        <Download size={13} />
                      </button>
                      <button
                        type="button"
                        className="doc-action doc-del"
                        title="从当前对话删除该文档"
                        disabled={isDeleting || disabled}
                        onClick={(e) => {
                          e.stopPropagation();
                          requestDeleteDocs([d.name], `「${d.name}」`);
                        }}
                      >
                        {isDeleting ? <RefreshCw size={13} className="spin" /> : <Trash2 size={13} />}
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* 状态 */}
      <div className="side-status">
        <button className="status-refresh" onClick={onRefresh} title="刷新状态">
          <RefreshCw size={13} />
        </button>
        <div className="status-row">
          <Database size={14} />
          <span className="status-name">Qdrant</span>
          <span className={`pill ${health?.qdrant ? "on" : "off"}`}>
            {health?.qdrant ? "在线" : "离线"}
          </span>
          <span className="status-num">
            {health?.counts?.qdrant_points ?? "—"} pts
          </span>
        </div>
        <div className="status-row">
          <GitBranch size={14} />
          <span className="status-name">Neo4j</span>
          <span className={`pill ${health?.neo4j ? "on" : "off"}`}>
            {health?.neo4j ? "在线" : "离线"}
          </span>
          <span className="status-num">
            {health?.counts?.neo4j_entities ?? "—"} ent
          </span>
        </div>
      </div>

      {/* 文档删除确认弹框 */}
      <ConfirmDialog
        open={deleteDocTargets !== null}
        title="删除文档"
        danger
        message={`确定删除${deleteDocTargets?.label ?? ""}？\n将同时清除其向量分片与图谱关系。`}
        confirmText="删除"
        onCancel={() => setDeleteDocTargets(null)}
        onConfirm={confirmDeleteDocs}
      />
    </aside>
  );
}
