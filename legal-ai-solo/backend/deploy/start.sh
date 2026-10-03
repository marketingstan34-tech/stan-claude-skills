#!/bin/sh
# Container start on a host (Railway): migrate, optionally keep the corpus filling in the
# background, then serve the web app. The corpus job is resumable, so restarts are safe.
set -e
export PRIVATE_STORAGE_PATH="${PRIVATE_STORAGE_PATH:-/data}"
mkdir -p "$PRIVATE_STORAGE_PATH"
# the corpus build and the analyses share one request every 2 s per court site
export SOURCE_LOCK_DIR="${SOURCE_LOCK_DIR:-$PRIVATE_STORAGE_PATH/.ratelimit}"
legal-ai migrate
if [ "${CORPUS_BUILD:-0}" = "1" ]; then
  # also to stdout, so progress shows in the host's logs
  (sh /app/deploy/corpus.sh 2>&1 | tee -a "$PRIVATE_STORAGE_PATH/corpus.log") &
fi
exec legal-ai serve --host 0.0.0.0 --port "${PORT:-8000}"
