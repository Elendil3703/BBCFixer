#!/usr/bin/env bash
# Shared settings and helpers for the image harness. Sourced by the other scripts.
BBCBENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_PREFIX="${BBCBENCH_IMAGE_PREFIX:-ghcr.io/elendil3703/bbcbench}"
SCRATCH="${BBCBENCH_SCRATCH:-$BBCBENCH_ROOT/.scratch}"

die() { echo "error: $*" >&2; exit 2; }

# load_case <case_id>
# Sets CASE, CDIR, IMG, TEST_CMD, NET_ARGS and the sidecar variables from cases/<case_id>/harness.env.
load_case() {
  CASE="$1"; CDIR="$BBCBENCH_ROOT/cases/$CASE"
  [ -f "$CDIR/harness.env" ] || die "unknown case: $CASE"
  NET_ARGS=""; SIDECAR_NAME=""; SIDECAR_RUN=""; SIDECAR_WAIT=""
  . "$CDIR/harness.env"
  [ "${HARNESS:-}" = image ] || die "$CASE is a lockfile case; use harness/lockfile"
  IMG="$IMAGE_PREFIX:$IMAGE"
}

# need_intact: the source snapshot must be present (scripts/get-intact.sh).
need_intact() { [ -d "$CDIR/intact" ] || die "cases/$CASE/intact is missing; run scripts/get-intact.sh first"; }

# ensure_image: pull the case image if it is not available locally.
ensure_image() {
  docker image inspect "$IMG" >/dev/null 2>&1 && return 0
  docker pull "$IMG" >/dev/null || die "cannot pull $IMG"
}

# ensure_sidecar: start the helper container of the case (a database), if the case needs one.
ensure_sidecar() {
  [ -n "$SIDECAR_NAME" ] || return 0
  bash "$BBCBENCH_ROOT/harness/image/sidecars.sh" "$CASE" >/dev/null || die "sidecar of $CASE failed to start"
}

# run_tests <project dir> <log file>
# Runs the test command of the case inside its image. Returns the exit code of the tests.
run_tests() {
  ensure_sidecar
  docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e "PYTEST_ADDOPTS=-p no:cacheprovider" ${NET_ARGS:-} \
    -v "$1":/work -w /work --entrypoint bash "$IMG" -c "$TEST_CMD" > "$2" 2>&1
}

# rm_tree <dir>: remove a directory; files created by root inside the container are removed through the image.
rm_tree() {
  [ -e "$1" ] || return 0
  rm -rf "$1" 2>/dev/null && return 0
  docker run --rm -v "$(dirname "$1")":/s --entrypoint rm "$IMG" -rf "/s/$(basename "$1")" >/dev/null 2>&1
}

# restore_tests <project dir>
# Overwrites every test file, the tests/ directory and the test entry scripts with the copies from intact/.
restore_tests() {
  local dst="$1" tf
  if [ -d "$CDIR/intact/tests" ]; then rm -rf "$dst/tests"; cp -R "$CDIR/intact/tests" "$dst/tests"; fi
  while IFS= read -r tf; do
    [ -n "$tf" ] || continue
    [ -f "$CDIR/intact/$tf" ] || continue
    mkdir -p "$dst/$(dirname "$tf")"; cp -f "$CDIR/intact/$tf" "$dst/$tf"
  done < "$CDIR/test_files.txt"
  for s in "setup_$CASE.sh" "test_$CASE.sh"; do
    [ -f "$CDIR/intact/$s" ] && cp -f "$CDIR/intact/$s" "$dst/$s"
  done
}
