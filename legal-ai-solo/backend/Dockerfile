FROM python:3.11.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY deploy ./deploy
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

# deploy/start.sh: migrate, optional background corpus build (CORPUS_BUILD=1), serve on $PORT.
CMD ["sh", "/app/deploy/start.sh"]
