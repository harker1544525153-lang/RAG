# 多阶段构建：前端编译产物 + 后端运行时，单容器单服务部署
# 通用镜像（含本地 BGE / Chroma）：
#   docker build -t rag-system .
# 云端精简镜像（pgvector + 云端 Embedding API，镜像更小）：
#   docker build --build-arg REQUIREMENTS=backend/requirements-cloud.txt -t rag-system .

FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

ARG REQUIREMENTS=backend/requirements.txt
COPY backend/requirements.txt backend/requirements-cloud.txt ./backend/
RUN pip install --no-cache-dir -r ${REQUIREMENTS}

COPY backend/app ./backend/app
COPY backend/.env.example ./backend/.env.example
COPY DOC ./DOC
COPY user ./user
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    DATA_DIR=/app/backend/data

EXPOSE 8080
CMD ["python", "-m", "app.main"]
