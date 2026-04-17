# Build the React/Vite UI into app/static, then run FastAPI.
FROM node:20-bookworm-slim AS ui
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=ui /build/app/static ./app/static

ENV PYTHONUNBUFFERED=1
ENV N8N_WORKFLOW_EDITOR_DATA_DIR=/data
EXPOSE 8105

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8105"]
