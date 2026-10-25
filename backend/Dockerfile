FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src

RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH"

# Non-root runtime user; the venv stays root-owned (read-only for the app) and
# all writable state (SQLite db, memory store) lives in /data.
RUN useradd --system --uid 10001 --create-home voicefirst \
    && mkdir -p /data \
    && chown -R voicefirst:voicefirst /data
USER voicefirst

WORKDIR /data
ENV VOICEFIRST_DATABASE_URL="sqlite+aiosqlite:////data/voicefirst.db"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+port+'/health', timeout=4).status == 200 else 1)"

# --proxy-headers honors X-Forwarded-For from the edge so rate limiting keys on
# real client IPs behind a reverse proxy. Render/Railway inject PORT; fall back
# to 8000 for local docker runs.
CMD uvicorn voicefirst.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips "*"
