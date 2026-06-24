import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Sparkles, User } from "lucide-react";
import type { ChatMessage } from "../types";
import SourceBadge from "./SourceBadge";
import "./MessageBubble.css";

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const sources = !isUser ? message.sources ?? [] : [];
  const showSources = sources.length > 0;
  // 来源徽章默认收起，节省空间；点击标题栏展开。
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const vectorN = sources.filter((s) => s.type === "qdrant").length;
  const graphN = sources.filter((s) => s.type === "neo4j").length;

  return (
    <div className={`msg ${isUser ? "msg-user" : "msg-bot"} ${message.error ? "is-error" : ""}`}>
      <div className="msg-avatar">
        {isUser ? <User size={16} /> : <Sparkles size={16} />}
      </div>
      <div className="msg-body">
        <div className="msg-role">{isUser ? "你" : "RAG 助手"}</div>

        {!isUser && message.streaming && message.steps && message.steps.length > 0 && (
          <div className="pipeline">
            {message.steps.map((s, i) => (
              <span key={s.key} className={`pstep ${s.status}`}>
                <span className="pstep-dot" />
                {s.label}
                {i < message.steps!.length - 1 && <span className="pstep-sep">→</span>}
              </span>
            ))}
          </div>
        )}

        {isUser ? (
          <div className="msg-text">{message.content}</div>
        ) : message.streaming && !message.content ? (
          <>
            <div className="thinking">
              <span className="think-dot" />
              <span className="think-dot" />
              <span className="think-dot" />
            </div>
            <div className="think-pipe">
              router → qdrant · neo4j → merge → llm
            </div>
          </>
        ) : (
          <div className="markdown msg-text">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeHighlight]}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        )}

        {showSources && (
          <div className="msg-sources">
            <button
              type="button"
              className="msg-sources-toggle"
              onClick={() => setSourcesOpen((o) => !o)}
              aria-expanded={sourcesOpen}
              title={sourcesOpen ? "收起来源" : "展开来源"}
            >
              <span className="ms-label">
                来源 · 共 {sources.length} 条
              </span>
              {vectorN > 0 && <span className="ms-sum ms-qd">向量 {vectorN}</span>}
              {graphN > 0 && <span className="ms-sum ms-neo">图谱 {graphN}</span>}
              {message.usedRag && <span className="rag-flag">混合检索</span>}
              <span className="ms-chev">{sourcesOpen ? "收起 ▲" : "展开 ▼"}</span>
            </button>
            {sourcesOpen && (
              <div className="msg-sources-list">
                {sources.map((s, i) =>
                  // web 来源渲染为可点击链接徽章，与既有 qdrant/neo4j 徽章分支并列。
                  // SourceRef.url 是可选字段：仅在 url 为真值时渲染 <a>，否则回退为纯文本，避免空 href。
                  s.type === "web" ? (
                    s.url ? (
                      <a
                        key={i}
                        className="source-badge web"
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={s.title || s.url}
                      >
                        {"🔗 "}
                        {s.title || s.url}
                      </a>
                    ) : (
                      <span
                        key={i}
                        className="source-badge web"
                        title={s.title || ""}
                      >
                        {"🔗 "}
                        {s.title || "（无标题）"}
                      </span>
                    )
                  ) : (
                    <SourceBadge key={i} source={s} />
                  )
                )}
              </div>
            )}
          </div>
        )}

        {/* 多智能体模式：默认折叠的 RAG / 联网 智能体原始回答面板 */}
        {message.mode === "multi" &&
          (message.ragAgentAnswer || message.webAgentAnswer) && (
            <div className="agent-panels">
              {message.ragAgentAnswer && (
                <details className="agent-panel">
                  <summary>📄 查看 RAG 智能体原始回答</summary>
                  <div className="agent-panel-body">{message.ragAgentAnswer}</div>
                </details>
              )}
              {message.webAgentAnswer && (
                <details className="agent-panel">
                  <summary>🌐 查看联网智能体原始回答</summary>
                  <div className="agent-panel-body">{message.webAgentAnswer}</div>
                </details>
              )}
            </div>
          )}
      </div>
    </div>
  );
}
