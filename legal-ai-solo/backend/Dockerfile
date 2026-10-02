FROM python:3.11.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

CMD ["sh", "-c", "legal-ai migrate && legal-ai serve --host 0.0.0.0 --port 8000"]
