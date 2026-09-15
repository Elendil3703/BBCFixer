#!/usr/bin/env bash
# Source diff: compare the source of the two versions to obtain the "source diff".
# This is the half japicmp cannot see: breakage where the signature is unchanged and only the
# internal behaviour changed hides in the source only.
#
# Requires old-src/ new-src/ unpacked by fetch_upstream.sh.
#
# Usage:
#   ./code_diff_source.sh <GROUP> <ART> <OLD> <NEW> [--symbol <name>] [<OUT_FILE>]
#   Without --symbol: print the whole source diff (possibly huge; for grep / locating only, never
#   hand the whole of it to the agent).
#   With --symbol: print only "the hunks of the changes in which the symbol appears" (slicing by
#                entry symbol).
#                --symbol takes a single symbol or several separated by | (regex match, the union
#                is sliced at once).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"; . "$DIR/lib.sh"
GROUP="$1"; ART="$2"; OLD="$3"; NEW="$4"; shift 4
SYMBOL=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --symbol) SYMBOL="$2"; shift 2;;
    *) OUT="$1"; shift;;
  esac
done
[ -z "$GROUP" ] || [ -z "$ART" ] && { echo "usage: $0 <GROUP> <ART> <OLD> <NEW> [--symbol X] [OUT]"; exit 2; }

CD="$(dep_cache_dir "$GROUP" "$ART" "$OLD" "$NEW")"
OS="$CD/old-src"; NS="$CD/new-src"
[ -d "$OS" ] && [ -d "$NS" ] || { echo "!! old-src/new-src missing, run fetch_upstream.sh first"; exit 1; }

emit() {
  if [ -z "$SYMBOL" ]; then
    diff -ruN "$OS" "$NS" 2>/dev/null
  else
    # Keep only the unified-diff hunks containing the symbol: take the full diff, then filter the
    # hunks per file for those containing SYMBOL
    diff -ruN "$OS" "$NS" 2>/dev/null | awk -v sym="$SYMBOL" '
      /^diff |^--- |^\+\+\+ / { hdr=hdr $0 "\n"; next }
      /^@@/ { if (buf!="" && hit) {printf "%s%s", fhdr, buf} buf=""; hit=0; fhdr=hdr; hdr=""; buf=$0"\n"; next }
      { buf=buf $0 "\n"; if ($0 ~ sym) hit=1 }
      END { if (buf!="" && hit) printf "%s%s", fhdr, buf }'
  fi
}

SYMNOTE=""; [ -n "$SYMBOL" ] && SYMNOTE="  (sliced by symbol: $SYMBOL)"
HEADER="# source diff: $ART $OLD -> $NEW$SYMNOTE"
if [ -n "$OUT" ]; then
  mkdir -p "$(dirname "$OUT")"; { echo "$HEADER"; echo; emit; } | tee "$OUT"
else
  echo "$HEADER"; echo; emit
fi
