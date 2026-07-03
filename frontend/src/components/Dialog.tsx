import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle, X } from "lucide-react";
import "./Dialog.css";

/* =====================================================================
   自写轻量弹框：Modal 底座 + ConfirmDialog + PromptDialog。
   零外部依赖，复用 index.css 的主题变量与 fadeIn/fadeUp 动画。
   用于替换散落各处的 window.confirm / window.prompt。
   ===================================================================== */

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  /** 标题前的图标（如危险操作的警告三角）。 */
  icon?: ReactNode;
  children: ReactNode;
}

function Modal({ open, onClose, title, icon, children }: ModalProps) {
  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // 打开时锁滚动，避免背景跟着滚
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="dlg-overlay" onClick={onClose}>
      <div
        className="dlg-panel"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="dlg-header">
            <div className="dlg-title">
              {icon && <span className="dlg-icon">{icon}</span>}
              {title}
            </div>
            <button className="dlg-close" onClick={onClose} aria-label="关闭">
              <X size={16} />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ConfirmDialog —— 替换 window.confirm                               */
/* ------------------------------------------------------------------ */
export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  /** 危险操作：标题加警告图标、确认键用红色。 */
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  // 打开时聚焦「取消」（danger 场景下避免回车误触确认）
  useEffect(() => {
    if (open) cancelRef.current?.focus();
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      icon={danger ? <AlertTriangle size={16} /> : undefined}
    >
      <div className="dlg-body">{message}</div>
      <div className="dlg-footer">
        <button ref={cancelRef} className="dlg-btn dlg-btn-ghost" onClick={onCancel}>
          {cancelText}
        </button>
        <button
          className={`dlg-btn ${danger ? "dlg-btn-danger" : "dlg-btn-primary"}`}
          onClick={onConfirm}
        >
          {confirmText}
        </button>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* PromptDialog —— 替换 window.prompt                                 */
/* ------------------------------------------------------------------ */
export interface PromptDialogProps {
  open: boolean;
  title: string;
  message?: ReactNode;
  defaultValue?: string;
  /** 输入为空时禁止提交；默认开启。 */
  allowEmpty?: boolean;
  confirmText?: string;
  cancelText?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function PromptDialog({
  open,
  title,
  message,
  defaultValue = "",
  allowEmpty = false,
  confirmText = "确定",
  cancelText = "取消",
  onConfirm,
  onCancel,
}: PromptDialogProps) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  // 每次打开：重置为默认值，并聚焦 + 全选
  useEffect(() => {
    if (!open) return;
    setValue(defaultValue);
    const id = requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
    return () => cancelAnimationFrame(id);
  }, [open, defaultValue]);

  const trimmed = value.trim();
  const canSubmit = allowEmpty ? value.length > 0 : trimmed.length > 0;

  const submit = () => {
    if (!canSubmit) return;
    onConfirm(allowEmpty ? value : trimmed);
  };

  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <div className="dlg-body">
        {message && <div className="dlg-message">{message}</div>}
        <input
          ref={inputRef}
          className="dlg-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
      </div>
      <div className="dlg-footer">
        <button className="dlg-btn dlg-btn-ghost" onClick={onCancel}>
          {cancelText}
        </button>
        <button
          className="dlg-btn dlg-btn-primary"
          onClick={submit}
          disabled={!canSubmit}
        >
          {confirmText}
        </button>
      </div>
    </Modal>
  );
}
