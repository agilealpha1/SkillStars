#!/usr/bin/env bash
# Weekly data refresh: discover -> enrich -> classify -> snapshot, then a validation build.
#
# data/ always ends up consistent:
#  - discovery or enrichment failed: candidates.json and repos.jsonl are put back to their pre-run versions (so a
#    fresh candidate list is never paired with stale repo data) and no snapshot is taken; exit 0 with a warning.
#  - the validation build failed: data/ is restored from the pre-run backup; exit 1.
# data/labels/ is a cache of paid LLM labels plus the id of any in-flight Anthropic batch; it is valid for any
# repo list, so it is never rolled back (rolling it back would orphan a batch and pay for the same labels twice).
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-python3}"
MAX_WAIT="${SS_CLASSIFY_MAX_WAIT:-1800}"     # total seconds classify.py may wait on Anthropic batches

step() { echo "== $(date -u +%H:%M:%S) $*"; }
try()  { step "$@"; "$@" || { echo "WARNING: $* failed; keeping previous data for this step"; return 1; }; }

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
BACKUP="$SCRATCH/data-backup.tgz"
[ -d data ] && tar czf "$BACKUP" data

restore_files() {          # restore_files <path>...: put these data files back to their pre-run versions
  [ -f "$BACKUP" ] || return 0
  for f in "$@"; do
    if tar tzf "$BACKUP" "$f" >/dev/null 2>&1; then tar xzf "$BACKUP" "$f"; else rm -f "$f"; fi
  done
}
restore_all_but_labels() {
  echo "Restoring data/ from the pre-run backup (keeping data/labels/)"
  [ -f "$BACKUP" ] || return 0
  [ -d data/labels ] && cp -R data/labels "$SCRATCH/labels"
  rm -rf data && tar xzf "$BACKUP"
  if [ -d "$SCRATCH/labels" ]; then rm -rf data/labels && cp -R "$SCRATCH/labels" data/labels; fi
}

FRESH=1
try "$PY" pipeline/discover.py || FRESH=0
if [ "$FRESH" = 1 ]; then try "$PY" pipeline/enrich.py || FRESH=0; fi
if [ "$FRESH" != 1 ]; then
  restore_files data/candidates.json data/repos.jsonl
  echo "::warning::Refresh incomplete: discovery or enrichment failed, so candidates, repo data and star counts were kept from the last run (no snapshot this week)."
fi
try "$PY" pipeline/classify.py --max-wait "$MAX_WAIT" || true     # falls back to cached/rule labels internally
if [ "$FRESH" = 1 ]; then
  try "$PY" pipeline/snapshot.py || true
fi

step "validation build"
if ! "$PY" pipeline/build_site_data.py --out "$SCRATCH/site"; then
  restore_all_but_labels
  exit 1
fi
echo "== update done ($([ "$FRESH" = 1 ] && echo complete || echo partial)): $(date -u)"
