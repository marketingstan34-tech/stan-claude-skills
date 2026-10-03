#!/bin/sh
# Container start on a host (Railway): migrate, optionally keep the corpus filling in the
# background, then serve the web app. The corpus job is resumable, so restarts are safe.
set -e
export PRIVATE_STORAGE_PATH="${PRIVATE_STORAGE_PATH:-/data}"
mkdir -p "$PRIVATE_STORAGE_PATH"
legal-ai migrate
if [ "${CORPUS_BUILD:-0}" = "1" ]; then
  sh /app/deploy/corpus.sh >> "$PRIVATE_STORAGE_PATH/corpus.log" 2>&1 &
fi
exec legal-ai serve --host 0.0.0.0 --port "${PORT:-8000}"
