# Two stages: build the dashboard with Node, then run the bridge on a slim Python image.
ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.13-slim

FROM ${NODE_IMAGE} AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY backend/pyproject.toml backend/README.md ./
COPY backend/atb ./atb
RUN pip install ".[postgres]" && rm -rf /src/build /src/*.egg-info
COPY --from=web /web/dist /app/web
RUN useradd --system --uid 10001 --home-dir /data --no-create-home atb \
    && mkdir -p /data && chown atb:atb /data && chmod 700 /data
ENV ATB_DATA_DIR=/data ATB_STATIC_DIR=/app/web ATB_HOST=0.0.0.0 ATB_PORT=8750
WORKDIR /data
USER atb
VOLUME /data
EXPOSE 8750
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8750/api/health', timeout=3)"]
CMD ["atb", "serve"]
