# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e

ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

FROM runtime AS backend

COPY --chown=app:app backend /app/backend
COPY --chown=app:app take-home /app/provided

USER app
EXPOSE 8000 9000
CMD ["python", "-m", "backend"]

FROM runtime AS frontend

COPY --chown=app:app frontend /app/frontend
COPY --chown=app:app take-home /app/provided

USER app
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=2", "--threads=2", "--timeout=30", "--graceful-timeout=10", "--error-logfile=-", "frontend.app:create_app()"]

FROM runtime AS tooling

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements-dev.txt \
    && rm /tmp/requirements-dev.txt

WORKDIR /workspace
COPY --chown=app:app . /workspace
RUN chown app:app /workspace

USER app
CMD ["python", "-m", "pytest"]
