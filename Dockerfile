# Written by `agents deploy`. It is yours to edit: deploys build it as it is.
FROM ghcr.io/astral-sh/uv:0.11-debian-slim

# The worker's Temporal client reads the system CA certificates, which the base image does not ship.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# uv installs the Python version the project asks for, so the image needs none of its own. It goes
# outside /root so that the worker's user can run it.
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/app/.venv/bin:$PATH"

# A daily check ends 2 hours after its last activity, and after 5 hours at most.
ENV AGENTS_SDK_IDLE_TIMEOUT_SECONDS=7200 \
    AGENTS_SDK_LIFETIME_LIMIT_SECONDS=18000

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev \
 && useradd --create-home --shell /usr/sbin/nologin agent \
 && chown -R agent:agent /app

# The worker does not need root.
USER agent

CMD ["agents", "run-worker"]
