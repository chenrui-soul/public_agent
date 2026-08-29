ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml requirements.lock README.md LICENSE ./
COPY src ./src

RUN python -m pip wheel --wheel-dir /wheels --constraint requirements.lock .


FROM ${PYTHON_IMAGE} AS runtime

ENV PATH=/home/public_agent/.local/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 public_agent \
    && useradd --system --uid 10001 --gid public_agent --create-home public_agent

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels public-agent \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=public_agent:public_agent alembic.ini ./
COPY --chown=public_agent:public_agent migrations ./migrations

USER 10001:10001

EXPOSE 8000

CMD ["public-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
