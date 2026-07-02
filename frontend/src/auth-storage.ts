import type { AuthSession } from "./types";

const SESSION_KEY = "rag-auth-session";

export function loadSession(): AuthSession | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as AuthSession;
    if (!data?.username || !data?.token) return null;
    return data;
  } catch {
    return null;
  }
}

export function saveSession(session: AuthSession): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  if (typeof localStorage === "undefined") return;
  localStorage.removeItem(SESSION_KEY);
}
