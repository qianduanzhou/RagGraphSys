import { useState } from "react";
import { CheckSquare, MessageSquarePlus, Pencil, Plus, Trash2, X } from "lucide-react";
import type { ConversationSummary } from "../types";
import { ConfirmDialog, PromptDialog } from "./Dialog";
import "./ConversationList.css";

interface Props {
  conversations: ConversationSummary[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  /** 批量删除（单删即传入长度 1 的数组）。 */
  onBatchDelete: (ids: string[]) => void;
  disabled?: boolean;
}

function formatTime(at: number): string {
  if (!at) return "";
  const d = new Date(at * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function ConversationList({
  conversations,
  currentId,
  onSelect,
  onCreate,
  onRename,
  onBatchDelete,
  disabled,
}: Props) {
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [renameTarget, setRenameTarget] = useState<{ id: string; title: string } | null>(null);
  const [deleteTargets, setDeleteTargets] = useState<{ ids: string[]; label: string } | null>(null);

  const allSelected = conversations.length > 0 && selected.size === conversations.length;

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function enterSelectMode() {
    setSelected(new Set());
    setSelectMode(true);
  }

  function exitSelectMode() {
    setSelected(new Set());
    setSelectMode(false);
  }

  function requestDelete(ids: string[], label: string) {
    setDeleteTargets({ ids, label });
  }

  function confirmDelete() {
    if (!deleteTargets) return;
    onBatchDelete(deleteTargets.ids);
    setDeleteTargets(null);
    if (selectMode) exitSelectMode();
  }

  function confirmRename(title: string) {
    if (!renameTarget) return;
    onRename(renameTarget.id, title);
    setRenameTarget(null);
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(conversations.map((c) => c.id)));
  }

  return (
    <div className="conv-section">
      <div className="side-label">
        <MessageSquarePlus size={14} /> 对话
        {!selectMode ? (
          <>
            <button
              type="button"
              className="conv-new-btn"
              onClick={onCreate}
              disabled={disabled}
              title="新建对话"
            >
              <Plus size={13} /> 新对话
            </button>
            <button
              type="button"
              className="conv-new-btn"
              onClick={enterSelectMode}
              disabled={disabled || conversations.length === 0}
              title="多选删除"
            >
              <CheckSquare size={13} /> 选择
            </button>
          </>
        ) : (
          <button
            type="button"
            className="conv-new-btn conv-exit-select"
            onClick={exitSelectMode}
            title="退出选择"
          >
            <X size={13} /> 完成
          </button>
        )}
      </div>

      {selectMode && (
        <div className="batch-bar">
          <div className="batch-select-all" onClick={toggleAll}>
            <input type="checkbox" checked={allSelected} readOnly />
            <span>{allSelected ? "取消全选" : "全选"}</span>
          </div>
          <span className="batch-count">已选 {selected.size}</span>
          <button
            type="button"
            className="batch-btn"
            disabled={selected.size === 0 || disabled}
            onClick={() => requestDelete([...selected], `${selected.size} 个对话`)}
          >
            <Trash2 size={12} /> 删除
          </button>
        </div>
      )}

      <div className="conv-list">
        {conversations.length === 0 ? (
          <p className="empty">暂无对话</p>
        ) : (
          conversations.map((c) => {
            const isSel = selected.has(c.id);
            return (
              <div
                key={c.id}
                className={`conv-item ${c.id === currentId ? "active" : ""} ${
                  isSel ? "selected" : ""
                }`}
                onClick={() => {
                  if (disabled) return;
                  if (selectMode) toggleSelect(c.id);
                  else onSelect(c.id);
                }}
              >
                {selectMode && (
                  <input
                    type="checkbox"
                    className="conv-check"
                    checked={isSel}
                    readOnly
                  />
                )}
                <div className="conv-meta">
                  <span className="conv-title" title={c.title}>
                    {c.title}
                  </span>
                  <span className="conv-preview">
                    {c.preview || `${c.document_count} 个文档`}
                    {formatTime(c.updated_at) ? ` · ${formatTime(c.updated_at)}` : ""}
                  </span>
                </div>
                {!selectMode && (
                  <div className="conv-actions">
                    <button
                      type="button"
                      className="conv-act"
                      title="重命名"
                      disabled={disabled}
                      onClick={(e) => {
                        e.stopPropagation();
                        setRenameTarget({ id: c.id, title: c.title });
                      }}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      type="button"
                      className="conv-act conv-del"
                      title="删除对话"
                      disabled={disabled}
                      onClick={(e) => {
                        e.stopPropagation();
                        requestDelete([c.id], `「${c.title}」`);
                      }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <PromptDialog
        open={renameTarget !== null}
        title="重命名对话"
        defaultValue={renameTarget?.title ?? ""}
        onCancel={() => setRenameTarget(null)}
        onConfirm={confirmRename}
      />

      <ConfirmDialog
        open={deleteTargets !== null}
        title="删除对话"
        danger
        message={`确定删除${deleteTargets?.label ?? ""}？\n将同时清除其全部文档与聊天记录，不可恢复。`}
        confirmText="删除"
        onCancel={() => setDeleteTargets(null)}
        onConfirm={confirmDelete}
      />
    </div>
  );
}
