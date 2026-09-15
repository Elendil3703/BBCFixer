#!/usr/bin/env bash
# push-images.sh [case_id ...]   (maintainer use)
# Tags the locally built images (tmb-<id>:breaking) under the public name and pushes them.
# Log in first:  echo $GITHUB_TOKEN | docker login ghcr.io -u <user> --password-stdin
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${BBCBENCH_IMAGE_PREFIX:-ghcr.io/elendil3703/bbcbench}"
if [ $# -gt 0 ]; then LIST="$*"; else LIST="$(ls "$ROOT/cases")"; fi
rc=0
for c in $LIST; do
  e="$ROOT/cases/$c/harness.env"; [ -f "$e" ] || continue
  tag="$(grep '^IMAGE=' "$e" | cut -d= -f2)"
  docker image inspect "$PREFIX:$tag" >/dev/null 2>&1 || docker tag "tmb-$tag:breaking" "$PREFIX:$tag" || { echo "no local image for $c"; rc=1; continue; }
  if docker push "$PREFIX:$tag" >/dev/null; then echo "pushed $PREFIX:$tag"; else echo "FAILED $PREFIX:$tag"; rc=1; fi
done
exit $rc
