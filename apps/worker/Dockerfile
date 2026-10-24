# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# git is required at runtime: the Git provider shells out to `git rev-parse` for
# commit evidence before executing validation jobs.
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

# Run as an unprivileged user (contract: containers must not run as root).
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

CMD ["python", "-m", "apps.worker.main"]
