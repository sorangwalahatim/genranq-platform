# ---- frontend
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ---- backend (API + worker share this image)
FROM python:3.12-slim
WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY extension/ /app/extension/
COPY --from=web /web/dist /app/frontend/dist
EXPOSE 8000
CMD ["sh", "-c", "python -m app.migrate && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
