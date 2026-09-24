# StockStrat backend: the API and the decision worker share this image
# (docker-compose.yml runs it twice, with different commands).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    STOCKSTRAT_DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/stockstrat.db \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN uv sync --frozen --no-dev && useradd --system --uid 10001 stockstrat && mkdir -p /data && chown stockstrat /data

USER stockstrat
VOLUME ["/data"]
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
