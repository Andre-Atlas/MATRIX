FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
RUN groupadd --system --gid 10001 matrix \
    && useradd --system --uid 10001 --gid matrix --home-dir /app --no-create-home --shell /usr/sbin/nologin matrix
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev \
    && mkdir -p /app/artifacts \
    && chown matrix:matrix /app /app/artifacts
ENV PATH="/app/.venv/bin:$PATH"
# Code and virtualenv stay root-owned (read-only for the service); only /app/artifacts (local
# artifact store) and /app itself are writable by the unprivileged user.
USER matrix
EXPOSE 8000
CMD ["matrix-api"]
