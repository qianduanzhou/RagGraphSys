# Task 7 报告：前端类型 + 客户端

## 状态

完成（Done）

## 改动文件

- `frontend/src/types.ts`
  - `SourceRef.type` 扩展为 `"qdrant" | "neo4j" | "web"`，并新增可选 `title?` / `url?`（web 来源字段）。
  - 在 `PIPELINE` 常量之后新增：
    - `export type ChatMode = "rag" | "multi";`
    - `export const MULTI_AGENT_PIPELINE: ReadonlyArray<{ key: string; label: string }>`（4 节点：调度 / RAG智能体 / 联网智能体 / 整合）。
  - `NodeUpdate` 新增 `used_web?: boolean;` 与 `answer?: string;`。
  - `ChatMessage` 新增 `usedWeb?: boolean;`、`mode?: ChatMode;`、`ragAgentAnswer?: string;`、`webAgentAnswer?: string;`。
  - `HealthResponse` 新增 `web_search: boolean;`。
- `frontend/src/api/client.ts`
  - import 中新增 `ChatMode`。
  - `chat(message, history, mode: ChatMode = "rag")`：请求体改为 `{ message, history, mode }`。
  - `chatStream(message, history, cb, mode: ChatMode = "rag")`：请求体改为 `{ message, history, mode }`。

所有改动逐字按 brief 的代码块；文件其余部分保持不变。

## 构建结果

命令：`npm --prefix "D:/project/customer/AI/RagGraphSys/frontend" run build`（= `tsc -b && vite build`）

- `tsc -b`：通过，无 TS 错误。`mode` 参数带默认值 `"rag"`，故 App.tsx 现有的三参 `chatStream` 调用与双参 `chat` 调用编译兼容，未报签名不匹配。
- `vite build`：构建成功。2041 模块转换，产物：
  - `dist/index.html`：0.87 kB（gzip 0.48 kB）
  - `dist/assets/index-VNpwTo2Z.css`：16.17 kB（gzip 4.06 kB）
  - `dist/assets/index-DZMiq27_.js`：496.57 kB（gzip 154.36 kB）
  - 用时约 11.05s。

实际输出摘要（关键行）：
```
> tsc -b && vite build
✓ 2041 modules transformed.
✓ built in 11.05s
```

## 疑虑

- 无功能性疑虑。`mode` 参数默认值 `"rag"` 已确保 App.tsx 现有调用无需改动即可编译通过；Task 8 会切换调用方传入实际 mode。
- 仅一处执行备注：在 Git Bash 工具下用反斜杠 `--prefix D:\...\frontend` 路径会被路径归一化吃掉斜杠导致 `ENOENT`，已改用正斜杠 `D:/...` 形式运行成功。这与代码改动无关，仅影响构建命令的调用方式。
