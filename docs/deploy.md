# 部署与镜像打包说明

项目不再从根目录统一执行打包或部署命令，前端、后端各自独立维护镜像打包脚本与部署编排。

## 前端

```powershell
cd frontend
Copy-Item .env.example .env
# 编辑 .env：填写 REGISTRY_ADDRESS / REGISTRY_USERNAME / REGISTRY_PASSWORD / IMAGE_PUSH_ADDRESS / IMAGE_NAME / IMAGE_TAG / FRONTEND_PORT
# 只打包镜像：npm run image:build
# 打包并推送镜像：npm run image:push
npm run image:push
```

部署机只需要放置 `frontend/docker-compose.yaml` 与已填写的 `frontend/.env`：

```powershell
cd frontend
docker compose --env-file .env -f docker-compose.yaml up -d
```

## 后端

```powershell
cd backend
Copy-Item .env.example .env
# 编辑 .env：填写镜像仓库配置、镜像名称、LLM 配置、Neo4j 密码等
python script/build_image.py
```

部署机只需要放置 `backend/docker-compose.yaml` 与已填写的 `backend/.env`：

```powershell
cd backend
docker compose --env-file .env -f docker-compose.yaml up -d
```

## 后端本地开发依赖

后端本地开发调试只启动 Qdrant 与 Neo4j：

```powershell
cd backend
docker compose --env-file .env -f docker-compose.dev.yaml up -d
```

停止：

```powershell
cd backend
docker compose --env-file .env -f docker-compose.dev.yaml down
```



