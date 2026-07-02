# 后端服务（Backend）

FastAPI + LangGraph 混合检索 RAG 后端。**完整的接口说明、配置项表、架构设计**见[根目录 README](../README.md)，本文件只讲**本地启动**与 **Docker 部署**。

## 一、本地开发

### 1. 启动数据库（Qdrant + Neo4j）

在本目录执行：


```bash
docker compose -f docker-compose.dev.yaml up -d
```

启动后：

- Qdrant Dashboard：http://localhost:6333/dashboard
- Neo4j Browser：http://localhost:7474

> `docker-compose.dev.yaml` 会自动读取本目录 `.env` 的 `NEO4J_USER` / `NEO4J_PASSWORD` 给 Neo4j 设置密码，与后端读取的密码**天然一致**（Neo4j 5.x 要求密码 ≥ 8 位）。停掉：`docker compose -f docker-compose.dev.yaml down`。

### 2. 配置并启动后端

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env               # 编辑 .env，填入 LLM_API_KEY
python main.py                     # http://localhost:8000 ，接口文档 /docs
```

### 3. 测试

```bash
pip install -r requirements-dev.txt
pytest                             # 185 个用例，全 mock，无需 API Key/联网/DB
```

---

## 二、Docker 部署（生产）

后端镜像由 `Dockerfile` 构建，生产编排见 `docker-compose.yaml`（含 `backend` + `Qdrant` + `Neo4j`，三者经共享网络 `rag-net` 与前端互通）。

镜像打包与部署均在 `backend/` 目录执行：

```bash
cp .env.example .env
# 编辑 .env：填写镜像仓库配置、镜像名称、LLM_API_KEY、NEO4J_PASSWORD 等
python script/build_image.py

docker compose --env-file .env -f docker-compose.yaml up -d
```

> ⚠️ `backend/.env` 会被 compose 读取作为生产运行配置，但会被 `.dockerignore` 排除，不会进入镜像。

| 文件 | 用途 |
|------|------|
| `Dockerfile` | 后端镜像（Python 3.11 + uvicorn） |
| `.dockerignore` | 排除 `.venv` / 测试 / 临时文件 / 本地 `.env` |
| `docker-compose.yaml` | **生产**：backend + Qdrant + Neo4j |
| `docker-compose.dev.yaml` | **开发**：仅 Qdrant + Neo4j（端口暴露 localhost） |




