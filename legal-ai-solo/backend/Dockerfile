FROM python:3.11.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# OCR for scanned PDFs the lawyer uploads (upload.py): pdftoppm + Tesseract with Bulgarian
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-bul \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY deploy ./deploy
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

# deploy/start.sh: migrate, optional background corpus build (CORPUS_BUILD=1), serve on $PORT.
CMD ["sh", "/app/deploy/start.sh"]
