import { describe, it, expect } from "vitest";
import { buildHistory } from "./chat-history";
import type { ChatMessage } from "./types";

const msg = (id: string, role: "user" | "assistant", content: string): ChatMessage => ({
  id,
  role,
  content,
});

describe("buildHistory", () => {
  it("把历史 user/assistant 消息映射为 {role, content}", () => {
    const past = [
      msg("u1", "user", "什么是 RAG"),
      msg("a1", "assistant", "RAG 是检索增强生成"),
    ];
    expect(buildHistory(past)).toEqual([
      { role: "user", content: "什么是 RAG" },
      { role: "assistant", content: "RAG 是检索增强生成" },
    ]);
  });

  it("过滤掉 welcome 欢迎消息", () => {
    const past = [
      msg("welcome", "assistant", "你好，欢迎使用知识库"),
      msg("u1", "user", "在吗"),
    ];
    expect(buildHistory(past)).toEqual([{ role: "user", content: "在吗" }]);
  });

  it("与历史消息一一对应，不凭空增加条目（锁定本轮问题不被重复）", () => {
    const past = [
      msg("u1", "user", "q1"),
      msg("a1", "assistant", "r1"),
      msg("u2", "user", "q2"),
    ];
    // 历史 3 条、本轮 q3 由后端单独追加 —— history 必须正好 3 条。
    expect(buildHistory(past)).toHaveLength(3);
  });
});
