FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.9 /uv /uvx /bin/
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL=sqlite+aiosqlite:////data/stock_data.db \
    YFINANCE_CACHE_DIR=/data/yfinance-cache \
    HOST=0.0.0.0 \
    PORT=8000

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY frontend ./frontend

RUN groupadd --system tera-quant-lab \
    && useradd --system --gid tera-quant-lab --no-create-home tera-quant-lab \
    && install -d -o tera-quant-lab -g tera-quant-lab /data
USER tera-quant-lab
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:' + os.environ['PORT'] + '/health', timeout=3).close()"]
CMD ["python", "-m", "app"]
