FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python

FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
COPY --from=builder /app/.venv /app/.venv
RUN groupadd --gid 10001 bridge && useradd --uid 10001 --gid 10001 --no-create-home bridge \
    && mkdir /state && chown 10001:10001 /state
USER 10001:10001
WORKDIR /app
EXPOSE 18792
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD ["bridge", "container-health"]
ENTRYPOINT ["bridge"]
CMD ["run", "--config", "/state/service.json", "--container"]
