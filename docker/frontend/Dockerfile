# syntax=docker/dockerfile:1
# 前端镜像：构建 Vue3 产物 → nginx 托管静态资源并反代 /api
FROM node:20-alpine AS builder

WORKDIR /build
COPY frontend/package*.json ./
RUN npm install --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /build/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
