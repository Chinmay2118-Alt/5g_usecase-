# =============================================================================
# 5G Remote Hands — Production Multi-Stage Dockerfile
# Optimized for 5G MEC / UPF Edge Server Deployment
# =============================================================================

# --- Stage 1: Build & Dependencies ---
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# Install build prerequisites if any C-extensions need compiling
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# --- Stage 2: Production Runtime ---
FROM python:3.11-slim-bookworm AS runner

LABEL maintainer="5G Edge Engineering Team" \
      description="5G Remote Hands — VR teleoperation simulation server for 5G MEC" \
      version="1.0.0"

WORKDIR /app

# Install curl for container health checks and iproute2 for network interface inspection
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    iproute2 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Copy application source code and assets
COPY server/ /app/server/
COPY web/ /app/web/
COPY tools/ /app/tools/
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

RUN chmod +x /app/docker-entrypoint.sh && \
    mkdir -p /app/certs /app/logs

# Standard 5G MEC service ports:
# 8443: Secure HTTPS / WSS (WebXR on Meta Quest)
# 8080: Plain HTTP / WS (development / fallback)
EXPOSE 8443 8080

# Health check against dedicated /api/health endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=8s --retries=3 \
    CMD curl -kfsS https://127.0.0.1:8443/api/health || curl -fsS http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "server.main"]

