FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINANCE_DATA_DIR=/data

WORKDIR /app

RUN groupadd --gid 10001 finance \
    && useradd --uid 10001 --gid finance --no-create-home --shell /usr/sbin/nologin finance \
    && mkdir -p /data \
    && chown finance:finance /data

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY --chown=finance:finance backend/ ./backend/
COPY --from=frontend-build --chown=finance:finance /build/frontend/dist ./frontend/dist/

USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]

CMD ["uvicorn", "backend.finance_app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
