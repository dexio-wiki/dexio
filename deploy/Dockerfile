# Three stages from one set of pinned layers:
#   base     the server's dependencies, exactly as locked (deploy/requirements.txt, with
#            hashes, exported from uv.lock), and the app
#   test     base plus the test dependencies and tests; CI runs the suite in it
#   runtime  what ships (the last stage, so a plain `docker build` builds it)
# The base image is pinned by digest, from AWS's mirror of Docker's official images.
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEXIO_DB=/data/dexio.db

WORKDIR /app
COPY deploy/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && mkdir -p /data

FROM base AS test
COPY deploy/requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-dev.txt
COPY tests ./tests
# The lockfile tests read the Dockerfile and requirements; test stage only, never shipped.
COPY deploy ./deploy
RUN useradd --system --uid 10002 tester && chown -R tester /app /data
USER tester
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

FROM base AS runtime
RUN useradd --system --uid 10001 dexio && chown -R dexio /data
USER dexio

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)"

CMD ["dexio", "serve", "--host", "0.0.0.0", "--port", "8080"]
