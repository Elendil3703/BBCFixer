#!/usr/bin/env bash
# judge.sh <case_id> <workdir>
#
# Judges the repair an agent left in a workspace made by prepare.sh. The verdict is PASS only
# when the tests pass after every gate below holds:
#   manifest   no dependency manifest or lockfile was edited
#   hijack     no test-runner configuration that changes what the tests do was added or edited
#   tree       the installed dependency tree is byte-for-byte what prepare.sh delivered
#   prod       at least one production file changed
#   tests      the tests pass on an independent rerun (exit 0), with a non-zero test count
# Edits to test files are discarded before the rerun and reported as TAMPERED.
# Prints one line per gate, then PASS (exit 0) or FAIL (exit 1).
set -u
. "$(dirname "$0")/lib.sh"
[ $# -eq 2 ] || die "usage: $0 <case_id> <workdir>"
load_case "$1"
WK="$(cd "$2" && pwd)"; SRC="$WK/$SRCNAME"
[ -d "$SRC/.git" ] && [ -f "$WK/.judge/inventory.txt" ] || die "$WK was not made by prepare.sh"
[ "$(cat "$WK/.judge/case" 2>/dev/null)" = "$CASE" ] || die "$WK was prepared for another case"
J="$WK/.judge"

# ---- what the agent changed
{ git -C "$SRC" diff --name-only broken; git -C "$SRC" ls-files --others --exclude-standard; } 2>/dev/null \
  | sort -u | grep -v '^$' | grep -Ev '\.(orig|bak|rej)$' > "$J/changed-paths.txt"
git -C "$SRC" diff broken -- . ':(exclude)*.orig' ':(exclude)*.bak' ':(exclude)*.rej' > "$J/agent.patch" 2>/dev/null
python3 "$H/evalscope.py" "$J/changed-paths.txt" > "$J/classified.tsv" 2>&1

hits() { awk -F'\t' -v k="$1" '$1==k{print $2}' "$J/classified.tsv" | tr '\n' ' '; }
G_MANIFEST=ok; [ -z "$(hits manifest)" ] || G_MANIFEST="CHANGED: $(hits manifest)"
G_HIJACK=ok;   [ -z "$(hits hijack)" ]   || G_HIJACK="HIJACK: $(hits hijack)"
G_PROD=yes;    [ -n "$(hits production)" ] || G_PROD="no"

# ---- discard edits to test-side files
TEST_HITS="$(awk -F'\t' '$1=="test" || $1=="vendored"{print $2}' "$J/classified.tsv")"
G_TESTS=ok
if [ -n "$TEST_HITS" ]; then
  G_TESTS="TAMPERED (restored): $(printf '%s' "$TEST_HITS" | tr '\n' ' ')"
  while read -r p; do
    [ -n "$p" ] || continue
    if git -C "$SRC" cat-file -e "broken:$p" 2>/dev/null; then git -C "$SRC" checkout broken -- "$p" 2>/dev/null || true
    else rm -f "${SRC:?}/$p"; fi
  done <<< "$TEST_HITS"
fi

# ---- dependency tree unchanged
tree_fingerprint > "$J/inventory-after.txt" 2>/dev/null
G_TREE=ok
diff "$J/inventory.txt" "$J/inventory-after.txt" > "$J/inventory.diff" 2>&1 || G_TREE="MOVED (see $J/inventory.diff)"

# ---- independent rerun of the tests
run_tests_script > "$J/run_tests.sh"
run_suite "$J/run_tests.sh" "$J/oracle.log"; RC=$?
CNT="$(counts "$J/oracle.log")"
G_RUN="exit $RC, counts [$CNT]"
G_EMPTY=ok
if [ "$RC" = 0 ] && ! counts_nonzero "$CNT"; then G_EMPTY="EMPTY_PASS (exit 0 but no test ran)"; fi

echo "JUDGE $CASE"
echo "  manifest : $G_MANIFEST"
echo "  hijack   : $G_HIJACK"
echo "  tree     : $G_TREE"
echo "  tests    : $G_TESTS"
echo "  prod     : $G_PROD"
echo "  rerun    : $G_RUN"
[ "$G_EMPTY" = ok ] || echo "  empty    : $G_EMPTY"
echo "  patch    : $J/agent.patch"
if [ "$RC" = 0 ] && [ "$G_EMPTY" = ok ] && [ "$G_TREE" = ok ] && [ "$G_MANIFEST" = ok ] && [ "$G_HIJACK" = ok ] && [ "$G_PROD" = yes ]; then
  echo "PASS $CASE"; exit 0
fi
echo "FAIL $CASE"; exit 1
