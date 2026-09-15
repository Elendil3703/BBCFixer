#!/usr/bin/env bash
# pull-images.sh [case_id ...]
# Pulls the image of the given cases, or of all image cases when no argument is given.
set -u
. "$(dirname "$0")/config.sh"
if [ $# -gt 0 ]; then LIST="$*"; else
  LIST="$(grep -l '^HARNESS=image' "$BBCBENCH_ROOT"/cases/*/harness.env | xargs -n1 dirname | xargs -n1 basename)"
fi
rc=0
for c in $LIST; do
  load_case "$c" 2>/dev/null || { echo "skip $c"; continue; }
  if docker pull "$IMG" >/dev/null; then echo "ok   $IMG"; else echo "FAIL $IMG"; rc=1; fi
done
exit $rc
