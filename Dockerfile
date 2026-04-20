# Build the React/Vite UI into app/static, then run FastAPI.
FROM node:20-bookworm-slim AS ui
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
COPY constraints.txt .
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY app ./app
COPY --from=ui /build/app/static ./app/static

ENV PYTHONUNBUFFERED=1
ENV N8N_WORKFLOW_EDITOR_DATA_DIR=/data
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data
EXPOSE 8105
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8105/api/health', timeout=4)"

USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8105"]
