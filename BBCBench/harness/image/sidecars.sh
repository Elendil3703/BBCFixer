#!/usr/bin/env bash
# sidecars.sh <case_id>
# Starts the helper container (a database) that the case needs, if any. Safe to call repeatedly.
set -u
. "$(dirname "$0")/config.sh"
[ $# -eq 1 ] || die "usage: $0 <case_id>"
load_case "$1"
[ -n "$SIDECAR_NAME" ] || { echo "$CASE needs no sidecar"; exit 0; }
state="$(docker inspect -f '{{.State.Status}}' "$SIDECAR_NAME" 2>/dev/null | tr -d '[:space:]')"
case "$state" in
  running) echo "ok $SIDECAR_NAME is running"; exit 0 ;;
  "")      eval "$SIDECAR_RUN" >/dev/null || die "cannot start $SIDECAR_NAME" ;;
  *)       docker start "$SIDECAR_NAME" >/dev/null || die "cannot restart $SIDECAR_NAME" ;;
esac
[ -n "$SIDECAR_WAIT" ] && eval "$SIDECAR_WAIT" >/dev/null 2>&1
state="$(docker inspect -f '{{.State.Status}}' "$SIDECAR_NAME" 2>/dev/null | tr -d '[:space:]')"
[ "$state" = running ] && echo "ok $SIDECAR_NAME started" || die "$SIDECAR_NAME is $state"
