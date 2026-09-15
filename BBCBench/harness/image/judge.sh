#!/usr/bin/env bash
# judge.sh <case_id> <workdir>
#
# Judges the repair an agent left in <workdir>/src (a workspace made by prepare.sh):
#   1. copies the project to a scratch directory,
#   2. restores every test file and the test entry scripts from intact/ (the tests already
#      carry the test edits of the reference fix), so edits to tests are discarded,
#   3. runs the tests inside the case image, where the library is fixed at v_new.
# Prints PASS (exit 0) when the tests pass, FAIL (exit 1) otherwise.
set -u
. "$(dirname "$0")/config.sh"
[ $# -eq 2 ] || die "usage: $0 <case_id> <workdir>"
load_case "$1"
WK="$(cd "$2" && pwd)"; SRC="$WK/src"
[ -d "$SRC" ] || die "$SRC not found"
need_intact; ensure_image

J="$SCRATCH/judge.$CASE.$$"; rm_tree "$J"; mkdir -p "$J"
cp -R "$SRC" "$J/src"; rm -rf "$J/src/.git"

# Note which test-side files the agent changed; they are restored before judging.
TAMPERED=""
if [ -d "$SRC/.git" ]; then
  TAMPERED="$(cd "$SRC" && git diff --name-only broken -- tests "setup_$CASE.sh" "test_$CASE.sh" $(cat "$CDIR/test_files.txt") 2>/dev/null | tr '\n' ' ')"
fi
restore_tests "$J/src"

run_tests "$J/src" "$J/oracle.log"; rc=$?
echo "----- last lines of the test output -----"
tail -20 "$J/oracle.log"
echo "-----------------------------------------"
[ -n "$TAMPERED" ] && echo "note: test-side edits were discarded: $TAMPERED"
if [ -d "$SRC/.git" ]; then
  n="$(cd "$SRC" && git diff --name-only broken 2>/dev/null | wc -l | tr -d ' ')"
  echo "note: $n file(s) differ from the broken state (git diff broken, in $SRC; test runs may write files too)"
fi
rm_tree "$J"
if [ "$rc" -eq 0 ]; then echo "PASS $CASE"; exit 0; else echo "FAIL $CASE (tests exit $rc)"; exit 1; fi
