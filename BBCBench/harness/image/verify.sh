#!/usr/bin/env bash
# verify.sh <case_id> [--intact]
#
# Replays the states of an image case and checks their outcome:
#   broken   intact/ as shipped, run inside the image (library at v_new): the tests must fail
#   fixed    intact/ plus reference_fix.patch, run inside the image: the tests must pass
#   intact   (only with --intact, needs network) the library is downgraded to v_old inside a
#            throw-away container and the tests must pass. Cases from TimeMachine-bench have no
#            single-package downgrade (their intact state is a dated snapshot of every dependency),
#            so this check reports n/a for them.
# Exits 0 when every replayed state has the expected outcome.
set -u
. "$(dirname "$0")/config.sh"
[ $# -ge 1 ] || die "usage: $0 <case_id> [--intact]"
load_case "$1"; WANT_INTACT="${2:-}"
need_intact; ensure_image
W="$SCRATCH/verify.$CASE.$$"; rm_tree "$W"; mkdir -p "$W"
ok=1

# broken
cp -R "$CDIR/intact" "$W/broken"
run_tests "$W/broken" "$W/broken.log"; rc=$?
if [ "$rc" -ne 0 ]; then BROKEN="fail (exit $rc), as expected"; else BROKEN="PASS, unexpected"; ok=0; fi

# fixed
cp -R "$CDIR/intact" "$W/fixed"
if patch -p0 -s -t -E -d "$W/fixed" < "$CDIR/reference_fix.patch" > "$W/patch.log" 2>&1; then
  run_tests "$W/fixed" "$W/fixed.log"; rc=$?
  if [ "$rc" -eq 0 ]; then FIXED="pass, as expected"; else FIXED="FAIL (exit $rc), unexpected"; ok=0; fi
else FIXED="reference_fix.patch does not apply"; ok=0; fi

# intact
INTACT="not requested"
if [ "$WANT_INTACT" = "--intact" ]; then
  if [ -z "${INTACT_PIP:-}" ]; then INTACT="n/a (dated snapshot, see meta.json)"; else
    cp -R "$CDIR/intact" "$W/intact"
    ensure_sidecar
    docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e "PYTEST_ADDOPTS=-p no:cacheprovider" ${NET_ARGS:-} \
      -v "$W/intact":/work -w /work --entrypoint bash "$IMG" \
      -c "pip install --no-deps -q $INTACT_PIP >/dev/null 2>&1 || exit 97; $TEST_CMD" > "$W/intact.log" 2>&1; rc=$?
    if [ "$rc" -eq 0 ]; then INTACT="pass, as expected"
    elif [ "$rc" -eq 97 ]; then INTACT="inconclusive (pip cannot install $INTACT_PIP in this image)"
    else INTACT="FAIL (exit $rc), see $W/intact.log"; ok=0; fi
  fi
fi

echo "VERIFY $CASE"
echo "  broken : $BROKEN"
echo "  fixed  : $FIXED"
echo "  intact : $INTACT"
[ "$ok" -eq 1 ] && { rm_tree "$W"; exit 0; }
echo "  logs   : $W"; exit 1
