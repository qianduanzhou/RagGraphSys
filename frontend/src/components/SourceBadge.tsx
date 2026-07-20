import { useState } from "react";
import { Download } from "lucide-react";
import type { SourceRef } from "../types";
import "./SourceBadge.css";

export default function SourceBadge({
  source,
  onDownloadSource,
}: {
  source: SourceRef;
  onDownloadSource?: (source: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const isQdrant = source.type === "qdrant";
  const isSourceFile = source.type === "source_file";
  const label = isQdrant ? "向量" : isSourceFile ? "源文件" : "图谱";

  return (
    <div className={`src ${source.type}`}>
      <button className="src-head" onClick={() => setOpen((o) => !o)}>
        <span className="src-dot" />
        <span className="src-type">{label}</span>
        <span className="src-store">{source.type}</span>
        {typeof source.score === "number" && (
          <span className="src-score">{source.score.toFixed(3)}</span>
        )}
        <span className="src-chev">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="src-body">
          {(isQdrant || isSourceFile) && source.source && (
            <div className="src-meta">来源：{source.source}</div>
          )}
          {isSourceFile && source.source && onDownloadSource && (
            <button
              type="button"
              className="src-download"
              onClick={() => onDownloadSource(source.source!)}
              title="下载源文件"
            >
              <Download size={13} /> 下载源文件
            </button>
          )}
          <p>{source.content}</p>
        </div>
      )}
    </div>
  );
}
