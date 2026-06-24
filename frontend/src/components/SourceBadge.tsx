import { useState } from "react";
import type { SourceRef } from "../types";
import "./SourceBadge.css";

export default function SourceBadge({ source }: { source: SourceRef }) {
  const [open, setOpen] = useState(false);
  const isQdrant = source.type === "qdrant";

  return (
    <div className={`src ${source.type}`}>
      <button className="src-head" onClick={() => setOpen((o) => !o)}>
        <span className="src-dot" />
        <span className="src-type">{isQdrant ? "向量" : "图谱"}</span>
        <span className="src-store">{source.type}</span>
        {typeof source.score === "number" && (
          <span className="src-score">{source.score.toFixed(3)}</span>
        )}
        <span className="src-chev">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="src-body">
          {isQdrant && source.source && (
            <div className="src-meta">来源：{source.source}</div>
          )}
          <p>{source.content}</p>
        </div>
      )}
    </div>
  );
}
