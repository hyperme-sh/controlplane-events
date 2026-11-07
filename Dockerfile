# Context is prepared from exact tested wheel bytes; no private fetch or credential.
FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f AS builder
WORKDIR /app
RUN python -m venv /app/.venv
COPY requirements.txt /opt/requirements.txt
RUN --mount=type=bind,source=wheels,target=/wheels,ro \
    /app/.venv/bin/python -m pip install --no-cache-dir --no-deps --require-hashes -r /opt/requirements.txt
COPY owner-migrations /opt/events-owner/owner-migrations
RUN /app/.venv/bin/python -c "import pathlib,shutil; p=pathlib.Path('/opt/events-owner/owner-migrations'); target=pathlib.Path('/app/.venv/lib/python3.13/site-packages/controlplane_events'); shutil.copy2(p/'migrate.py',target/'migrate.py'); shutil.copytree(p/'migrations',target/'migrations',dirs_exist_ok=True)"
RUN --network=none /app/.venv/bin/python -c "import controlplane_events.http_inbox"
FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY owner-migrations /opt/events-owner/owner-migrations
COPY selfhost/entrypoint.py /opt/events-owner/entrypoint.py
COPY runtime-receipt.json /opt/events-owner/runtime-receipt.json
ARG SOURCE_SHA
ARG BUILT_AT
LABEL org.opencontainers.image.source="https://github.com/hyperme-sh/controlplane-events" \
      org.opencontainers.image.revision="${SOURCE_SHA}"
ENV GIT_SHA=${SOURCE_SHA} EVENTS_SOURCE_SHA=${SOURCE_SHA} BUILT_AT=${BUILT_AT} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
HEALTHCHECK --interval=5s --timeout=5s --start-period=30s --retries=12 \
  CMD ["/app/.venv/bin/python", "-c", "import os,urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health/ready',timeout=3).read()"]
ENTRYPOINT ["/app/.venv/bin/python", "/opt/events-owner/entrypoint.py"]
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "controlplane_events.http_inbox:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
