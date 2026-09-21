# worker（FastAPI）用のコンテナ。Cloud Run で動かす（design.md §11）。
# pyproject.toml が Python 3.13.15 ちょうどを求めるので、版の決まった公式イメージを使う
FROM python:3.13.15-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3

# 依存だけを先に入れて、コードの変更でキャッシュが無駄にならないようにする
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY prompts ./prompts
RUN uv sync --locked --no-dev

# 秘密は Secret Manager から環境変数で渡す。.env はコンテナに入れない（.dockerignore）
ENV PROMPTS_DIR=/app/prompts
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn ai_hackathon_team_a.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
