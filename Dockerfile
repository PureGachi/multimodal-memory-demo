FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
    MEMORY_DATA_DIR=/data/official/evaluation/runtime
WORKDIR /app
COPY requirements-lock.txt pyproject.toml ./
COPY src ./src
RUN python -m pip install --no-cache-dir -r requirements-lock.txt \
    && python -m pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 memory \
    && useradd --uid 10001 --gid 10001 --no-create-home memory \
    && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).close()"]
CMD ["python", "-m", "uvicorn", "memory_app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--timeout-keep-alive", "5", "--timeout-graceful-shutdown", "1800"]
