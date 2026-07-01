# -*- coding: utf-8 -*-
# 前端镜像：多阶段构建。
#   1) node 阶段：npm run build 产出 dist/ 静态文件
#   2) nginx 阶段：托管 dist，并把 /api 反代到后端
FROM node:20-alpine AS build
WORKDIR /app

# 先装依赖（命中缓存，源码改动不重装 node_modules）
COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build


FROM nginx:alpine
# nginx 站点配置：托管 SPA + 反代 /api（含 SSE 关缓冲）
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
