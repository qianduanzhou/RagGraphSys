# 前端服务（Frontend）

React 18 + Vite + TypeScript 前端，ChatGPT 风格对话界面，支持 SSE 流式逐字输出、实时流水线进度与文档库管理。

## 一、本地开发

```bash
npm install
npm run dev          # http://localhost:5173
```

> 开发期 Vite 已把 `/api` 代理到 `http://localhost:8000`（见 `vite.config.ts`），因此需**先启动后端**（见 [backend/README.md](../backend/README.md)）。

测试与构建：

```bash
npm run test         # vitest run
npm run build        # 产物输出到 dist/（生产用）
```

---

## 二、Docker 部署（生产）

前端镜像由 `Dockerfile` **多阶段构建**（Node 编译 → nginx 托管 `dist/`），生产编排见 `docker-compose.yaml`。

镜像打包与部署均在 `frontend/` 目录执行：

```bash
cp .env.example .env
# 编辑 .env：填写 REGISTRY_ADDRESS / REGISTRY_USERNAME / REGISTRY_PASSWORD / IMAGE_PUSH_ADDRESS / IMAGE_NAME / IMAGE_TAG
# 只打包镜像：npm run image:build
# 打包并推送镜像：npm run image:push
npm run image:push

docker compose --env-file .env -f docker-compose.yaml up -d
```

`nginx.conf` 做两件事：

1. **托管 SPA**：`location /` → `try_files ... /index.html`。
2. **反代 `/api`**：到后端容器（服务名 `backend`，经共享网络 `rag-net`），并 `proxy_buffering off` 支持 SSE 流式。

> 前端**可独立于后端启动**：nginx 用动态 resolver（`resolver 127.0.0.11` + 变量）反代，后端未启动或重启都不影响前端启动；后端就绪后自动连通，无需重启前端。7847 端口是前端对外端口。

| 文件 | 用途 |
|------|------|
| `Dockerfile` | 多阶段：Node 构建 → nginx 托管 |
| `.dockerignore` | 排除 `node_modules` / `dist` |
| `nginx.conf` | SPA 托管 + `/api` 反代（SSE 关缓冲 + 动态解析） |
| `docker-compose.yaml` | 生产编排：nginx，对外 7847 |






