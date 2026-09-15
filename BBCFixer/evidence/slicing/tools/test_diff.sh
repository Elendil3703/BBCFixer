#!/usr/bin/env bash
# Test diff: take "the diff of the test code" between two tags of the upstream repository.
# Tests are not in the sources.jar, so they must come from the repository; this is exactly the
# information source BBCFixer adds over prior work: it provides "usage examples of the new
# interface" and "the new correct expected values for behavioural breakage".
#
# Requires repo/ cloned and repo-meta.txt (slug/oldtag/newtag) written by fetch_upstream.sh.
#
# Usage:
#   ./test_diff.sh <GROUP> <ART> <OLD> <NEW> [--symbol <name>] [<OUT_FILE>]
#   Only changes under test directories (src/test, tests, test, ...) are considered; with --symbol
#   the diff is sliced by symbol.
#   --symbol takes a single symbol or several separated by | (regex match, the union is sliced at once).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"; . "$DIR/lib.sh"
GROUP="$1"; ART="$2"; OLD="$3"; NEW="$4"; shift 4
SYMBOL=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in --symbol) SYMBOL="$2"; shift 2;; *) OUT="$1"; shift;; esac
done

CD="$(dep_cache_dir "$GROUP" "$ART" "$OLD" "$NEW")"
REPO="$CD/repo"; META="$CD/repo-meta.txt"
[ -d "$REPO/.git" ] && [ -f "$META" ] || { echo "!! repository clone missing, run fetch_upstream.sh with the compare URL first"; exit 1; }
OLDTAG="$(sed -n 2p "$META")"; NEWTAG="$(sed -n 3p "$META")"

# Common test code paths (Maven standard + common Python layouts + a few variants).
# Note: without the git :(glob) magic, '**/tests/**' does not match tests/ at the repository ROOT,
# so 'tests/*' must be listed separately.
PATHS='**/src/test/** **/test/** **/tests/** tests/* test/* **/*Test.java **/*Tests.java **/*IT.java **/test_*.py **/*_test.py'

emit() {
  cd "$REPO" || return 1
  # shellcheck disable=SC2086
  if [ -z "$SYMBOL" ]; then
    git diff "$OLDTAG" "$NEWTAG" -- $PATHS 2>/dev/null
  else
    git diff "$OLDTAG" "$NEWTAG" -- $PATHS 2>/dev/null | awk -v sym="$SYMBOL" '
      /^diff |^index |^--- |^\+\+\+ / { hdr=hdr $0 "\n"; next }
      /^@@/ { if (buf!="" && hit) printf "%s%s", fhdr, buf; buf=""; hit=0; fhdr=hdr; hdr=""; buf=$0"\n"; next }
      { buf=buf $0 "\n"; if ($0 ~ sym) hit=1 }
      END { if (buf!="" && hit) printf "%s%s", fhdr, buf }'
  fi
}

SYMNOTE=""; [ -n "$SYMBOL" ] && SYMNOTE="  (sliced by symbol: $SYMBOL)"
HEADER="# test diff: $ART $OLDTAG -> $NEWTAG$SYMNOTE"
if [ -n "$OUT" ]; then
  mkdir -p "$(dirname "$OUT")"; { echo "$HEADER"; echo; emit; } | tee "$OUT"
else
  echo "$HEADER"; echo; emit
fi
