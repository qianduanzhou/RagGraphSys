import { useEffect, useRef, useState } from "react";
import { SendHorizontal } from "lucide-react";
import type { ChatMessage, ChatMode } from "../types";
import MessageBubble from "./MessageBubble";
import "./ChatWindow.css";

interface Props {
  messages: ChatMessage[];
  streaming: boolean;
  onSend: (text: string) => void;
  mode: ChatMode;
  onModeChange: (m: ChatMode) => void;
  webSearchAvailable: boolean;
  onDownloadSource?: (source: string) => void;
}

export default function ChatWindow({
  messages,
  streaming,
  onSend,
  mode,
  onModeChange,
  webSearchAvailable,
  onDownloadSource,
}: Props) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, streaming]);

  useEffect(() => {
    const ta = taRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
    }
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || streaming) return;
    onSend(text);
    setDraft("");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="chat-window">
      <div className="chat-scroll" ref={scrollRef}>
        <div className="chat-inner">
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} onDownloadSource={onDownloadSource} />
          ))}
        </div>
      </div>

      <div className="composer">
        <div className="mode-switch" role="tablist" aria-label="问答模式">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "source"}
            className={mode === "source" ? "active" : ""}
            title="直接读取保存的源文件，由大模型解析回答"
            onClick={() => onModeChange("source")}
          >
            源文件解析
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "rag"}
            className={mode === "rag" ? "active" : ""}
            onClick={() => onModeChange("rag")}
          >
            RAG问答
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "multi"}
            className={mode === "multi" ? "active" : ""}
            disabled={!webSearchAvailable}
            title={
              webSearchAvailable
                ? "多智能体：RAG + 联网 + 整合"
                : "未配置 TAVILY_API_KEY"
            }
            onClick={() => onModeChange("multi")}
          >
            多智能体
          </button>
        </div>
        <div className="composer-box">
          <textarea
            ref={taRef}
            rows={1}
            value={draft}
            placeholder="向知识库提问…  (Enter 发送 · Shift+Enter 换行)"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button
            className="send-btn"
            onClick={submit}
            disabled={streaming || !draft.trim()}
            title="发送"
          >
            <SendHorizontal size={18} />
          </button>
        </div>
        <p className="composer-note">
          Hybrid RAG · 向量/图谱检索 · 源文件直读 · 多智能体对比
        </p>
      </div>
    </div>
  );
}
