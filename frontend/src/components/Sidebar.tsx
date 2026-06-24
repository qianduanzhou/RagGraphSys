import { useRef, useState } from "react";
import { Database, FileArchive, FileText, GitBranch, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import type { BatchDeleteResponse, BatchIngestResponse, HealthResponse, UploadedDoc } from "../types";
import "./Sidebar.css";

// 与后端 services/file_parser.ALLOWED_EXTS 保持一致（语义同一套）。
// 文本/代码类直接解码，CSV/Excel/PDF/Word 由后端解析；zip 由后端解包。
const ALLOWED_EXT = [
  ".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rst",
  // 前端 / Web
  ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss", ".vue", ".svelte",
  // 编程语言
  ".py", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".kt", ".scala",
  ".swift", ".rb", ".php", ".lua", ".dart", ".r",
  // 脚本 / 配置 / 数据
  ".sh", ".bash", ".ps1", ".sql", ".xml", ".yaml", ".yml", ".toml", ".ini", ".env",
  // 文档（后端解析）
  ".pdf", ".docx", ".xlsx", ".xls",
  // 压缩包（后端展开为成员）
  ".zip",
];

interface Props {
  docs: UploadedDoc[];
  health: HealthResponse | null;
  onUploadFiles: (files: File[]) => Promise<BatchIngestResponse>;
  onDeleteDoc: (source: string) => Promise<void>;
  onDeleteDocsBatch: (sources: string[]) => Promise<BatchDeleteResponse>;
  onRefresh: () => void;
}

type UploadState = "idle" | "uploading" | "done" | "error";

function isAllowed(name: string): boolean {
  const lower = name.toLowerCase();
  return ALLOWED_EXT.some((ext) => lower.endsWith(ext));
}

/** 多选 / 拖拽会混入二进制 / 系统文件，前端先按扩展名过滤一道。 */
function filterAllowed(fileList: FileList | null | undefined): File[] {
  if (!fileList) return [];
  return Array.from(fileList).filter(
    (f) => isAllowed(f.name) && f.size > 0
  );
}

/** 把秒级时间戳格式化为 MM-DD HH:mm；无时间戳返回空串。 */
function formatTime(at: number): string {
  if (!at) return "";
  const d = new Date(at * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function Sidebar({ docs, health, onUploadFiles, onDeleteDoc, onDeleteDocsBatch, onRefresh }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [state, setState] = useState<UploadState>("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deletingBatch, setDeletingBatch] = useState(false);
  const allSelected = docs.length > 0 && selected.size === docs.length;

  function toggleSelect(name: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) =>
      docs.length > 0 && prev.size === docs.length
        ? new Set()
        : new Set(docs.map((d) => d.name))
    );
  }

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
      setState(res.failed > 0 ? "done" : "done");
      const okMsg = `已入库 ${res.succeeded} 个文件，共 ${res.chunks} 片段`;
      setNotice(
        res.failed > 0 ? `${okMsg}（${res.failed} 个失败）` : okMsg
      );
    } catch (err) {
      setState("error");
      setNotice((err as Error).message || "上传失败");
    }
  }

  function handlePick(files: FileList | null) {
    upload(filterAllowed(files));
  }

  async function handleDelete(name: string) {
    if (!window.confirm(`确定删除文档「${name}」？\n将同时清除其向量分片与图谱关系，不可恢复。`)) {
      return;
    }
    setDeleting(name);
    try {
      await onDeleteDoc(name);
      // 单删后同步移出选中集合，避免计数/全选态指向已不存在文档。
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
      setState("done");
      setNotice(`已删除：${name}`);
      setTimeout(() => setNotice(null), 2600);
    } catch (err) {
      setState("error");
      setNotice((err as Error).message || "删除失败");
    } finally {
      setDeleting(null);
    }
  }

  async function handleBatchDelete() {
    if (selected.size === 0) return;
    if (
      !window.confirm(
        `确定删除选中的 ${selected.size} 个文档？\n将同时清除其向量分片与图谱关系，不可恢复。`
      )
    ) {
      return;
    }
    setDeletingBatch(true);
    try {
      const res = await onDeleteDocsBatch([...selected]);
      setSelected(new Set());
      setState(res.failed > 0 ? "error" : "done");
      const okMsg = `已删除 ${res.deleted} 个文档`;
      setNotice(res.failed > 0 ? `${okMsg}（${res.failed} 个失败）` : okMsg);
      setTimeout(() => setNotice(null), 3000);
    } catch (err) {
      setState("error");
      setNotice((err as Error).message || "批量删除失败");
    } finally {
      setDeletingBatch(false);
    }
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

      {/* 上传 */}
      <div className="side-section">
        <div className="side-label">
          <UploadCloud size={14} /> 知识库上传
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
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_EXT.join(",")}
            multiple
            hidden
            onChange={(e) => {
              handlePick(e.target.files);
              e.target.value = ""; // 允许重复选择同一文件
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
              <span className="dz-hint">代码 · PDF · Word · Excel · Markdown · zip 压缩包</span>
            </>
          )}
        </div>
        {/* zip 压缩包上传：单独按钮，后端解包为成员逐个入库 */}
        <button
          type="button"
          className="folder-btn"
          onClick={(e) => {
            e.stopPropagation();
            zipInputRef.current?.click();
          }}
          disabled={state === "uploading"}
          title="上传 zip 压缩包，后端自动解包入库（保留目录结构）"
        >
          <FileArchive size={14} /> 上传 zip 压缩包
        </button>
        {notice && <div className={`notice ${state}`}>{notice}</div>}
      </div>

      {/* 文档列表 */}
      <div className="side-section side-grow">
        <div className="side-label">
          <FileText size={14} /> 已入库文档
          <span className="side-count">{docs.length}</span>
        </div>
        {docs.length > 0 && (
          <div className="batch-bar">
            <label className="batch-select-all">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
                disabled={deletingBatch}
              />
              <span>{selected.size > 0 ? `已选 ${selected.size}/${docs.length}` : "全选"}</span>
            </label>
            <button
              type="button"
              className="batch-btn"
              onClick={handleBatchDelete}
              disabled={selected.size === 0 || deletingBatch}
              title="删除选中文档"
            >
              {deletingBatch ? <RefreshCw size={13} className="spin" /> : <Trash2 size={13} />}
              批量删除
            </button>
          </div>
        )}
        <div className="doc-list">
          {docs.length === 0 ? (
            <p className="empty">暂无文档，上传后将自动切分入库。</p>
          ) : (
            docs.map((d, i) => {
              const checked = selected.has(d.name);
              return (
                <div
                  className={`doc-item ${checked ? "selected" : ""}`}
                  key={`${d.name}-${d.at}-${i}`}
                  onClick={() => toggleSelect(d.name)}
                >
                  <input
                    type="checkbox"
                    className="doc-check"
                    checked={checked}
                    onChange={() => toggleSelect(d.name)}
                    onClick={(e) => e.stopPropagation()}
                    disabled={deleting === d.name || deletingBatch}
                  />
                  <FileText size={15} className="doc-icon" />
                  <div className="doc-meta">
                    <span className="doc-name" title={d.name}>
                      {d.name}
                    </span>
                    <span className="doc-stat">
                      {d.chunks} 片段{formatTime(d.at) ? ` · ${formatTime(d.at)}` : ""}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="doc-del"
                    title="删除该文档"
                    disabled={deleting === d.name || deletingBatch}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(d.name);
                    }}
                  >
                    {deleting === d.name ? <RefreshCw size={13} className="spin" /> : <Trash2 size={13} />}
                  </button>
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
    </aside>
  );
}
