# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# git is a runtime dependency, not a build convenience: the managed Git provider
# shells out to `git rev-parse` for the commit SHA recorded in health and audit
# evidence. slim images omit it, which surfaced as a 500 with
# FileNotFoundError: 'git'.
RUN apt-get update \
 && apt-get install --no-install-recommends -y git \
 && rm -rf /var/lib/apt/lists/*

# Install the pinned lock file, not the human-readable range file, so image
# builds are reproducible across time (plan §2.4).
COPY requirements-stage1.lock.txt .
RUN pip install --no-cache-dir -r requirements-stage1.lock.txt

COPY apps apps
COPY schemas schemas
COPY resources resources
# The image also runs the one-shot seed gate (scripts/seed_demo_resources.py),
# which imports the apps package from /app and reads the dev token registry.
COPY scripts scripts

# Run as an unprivileged user (contract: containers must not run as root).
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
