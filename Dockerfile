FROM node:22-bookworm-slim AS frontend
WORKDIR /app
RUN npm install --global pnpm@12.8.1
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN pnpm build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JUSTREAD_DATABASE_PATH=/app/data/justread.sqlite3 \
    JUSTREAD_STATIC_DIR=/app/dist \
    JUSTREAD_TEST_MODE=false
WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./backend/requirements.txt
RUN python -m pip install --no-cache-dir -r backend/requirements.txt \
    && groupadd --gid 10001 justread \
    && useradd --uid 10001 --gid justread --no-create-home justread \
    && mkdir -p /app/data \
    && chown justread:justread /app/data
COPY backend/just_read ./backend/just_read
COPY scripts/healthcheck.py ./scripts/healthcheck.py
COPY --from=frontend /app/dist ./dist
USER justread
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python /app/scripts/healthcheck.py
CMD ["python", "-m", "uvicorn", "just_read.app:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
