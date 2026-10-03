#!/bin/sh
# Fill the corpus slowly (one request per >= 2 s, one process), newest practice first, then refresh
# the latest quarters once a week. Safe to restart: finished quarters are skipped.
D="$PRIVATE_STORAGE_PATH/raw/vks-corpus"
NOW=$(date -u +%Y-%m)
YEAR=$(date -u +%Y)
echo "$(date -u +%FT%TZ) corpus: start"
legal-ai backfill-judges || echo "backfill-judges failed"
# interpretative decisions: fetched once, then again with the weekly refresh (a restart or a new
# deploy must not spend ~12 minutes fetching them again)
TR_DONE="$PRIVATE_STORAGE_PATH/raw/.tr-fetched"
if [ ! -f "$TR_DONE" ] || [ -n "$(find "$TR_DONE" -mtime +6 2>/dev/null)" ]; then
  legal-ai fetch-tr --from-year 2008 --to-year "$YEAR" && touch "$TR_DONE" || echo "fetch-tr failed, continuing"
else
  echo "fetch-tr: done within the last week, skipped"
fi
legal-ai build-corpus --from 2022-07 --to "$NOW" --newest-first || echo "build-corpus (recent) failed"
# admission rulings (чл. 288 ГПК) for the "chance of admission" check, newest first
legal-ai build-corpus --act-type определение --from 2023-01 --to "$NOW" --newest-first || echo "build-corpus (288) failed"
legal-ai build-corpus --from "${CORPUS_FROM:-2012-01}" --to 2022-06 --newest-first || echo "build-corpus (older) failed"
echo "$(date -u +%FT%TZ) corpus: initial build done"
while true; do
  sleep 604800
  NOW=$(date -u +%Y-%m)
  # the two latest quarters may have gained decisions since they were marked done
  for q in $(ls "$D"/gr "$D"/targ 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort -u | tail -n 2); do
    rm -f "$D/gr/$q/.ingested" "$D/targ/$q/.ingested" "$D/gr-opr/$q/.ingested" "$D/targ-opr/$q/.ingested"
  done
  legal-ai fetch-tr --from-year 2008 --to-year "$(date -u +%Y)" && touch "$TR_DONE" || echo "weekly fetch-tr failed"
  legal-ai build-corpus --from 2022-07 --to "$NOW" --newest-first || echo "weekly refresh failed"
  legal-ai build-corpus --act-type определение --from 2023-01 --to "$NOW" --newest-first || echo "weekly 288 refresh failed"
  echo "$(date -u +%FT%TZ) corpus: weekly refresh done"
done
