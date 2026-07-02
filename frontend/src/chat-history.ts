import type { ChatMessage, ChatHistoryItem } from "./types";

/**
 * 把界面上的历史对话转换为发给后端的 history。
 *
 * 只处理「历史」消息——本轮提问由后端以 `question` 字段单独接收并追加，
 * 因此调用方传入的列表**不应**包含本轮 user 消息，否则本轮问题会被发送两次。
 */
export function buildHistory(pastMessages: ChatMessage[]): ChatHistoryItem[] {
  return pastMessages
    .filter((m) => m.id !== "welcome")
    .map((m) => ({ role: m.role, content: m.content }));
}

function storageKey(username: string): string {
  return `rag-chat-history:${username}`;
}

export function loadUserMessages(username: string, fallback: ChatMessage[]): ChatMessage[] {
  if (typeof localStorage === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(storageKey(username));
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as ChatMessage[];
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : fallback;
  } catch {
    return fallback;
  }
}

export function saveUserMessages(username: string, messages: ChatMessage[]): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(storageKey(username), JSON.stringify(messages));
}

export function clearUserMessages(username: string): void {
  if (typeof localStorage === "undefined") return;
  localStorage.removeItem(storageKey(username));
}
