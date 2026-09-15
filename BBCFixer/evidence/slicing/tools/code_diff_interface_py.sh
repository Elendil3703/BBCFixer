#!/usr/bin/env bash
# Interface shape diff (Python ecosystem): ast-level public interface comparison of the two versions.
# The Python equivalent of japicmp; the output format matches the japicmp report, so
# extract_basis.py parses it unchanged.
#
# Requires old-src/ new-src/ unpacked by fetch_upstream_py.sh.
#
# Usage:
#   ./code_diff_interface_py.sh <PKG> <OLD> <NEW> [<OUT_FILE>]
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"; . "$DIR/lib.sh"
PKG="$1"; OLD="$2"; NEW="$3"; OUT="${4:-}"
[ -z "$PKG" ] || [ -z "$OLD" ] || [ -z "$NEW" ] && {
  echo "usage: $0 <PKG> <OLD> <NEW> [<OUT_FILE>]"; exit 2; }

CD="$(dep_cache_dir pypi "$PKG" "$OLD" "$NEW")"
[ -d "$CD/old-src" ] && [ -d "$CD/new-src" ] || { echo "!! old-src/new-src missing, run fetch_upstream_py.sh first"; exit 1; }

run() { python3 "$DIR/py_api_diff.py" "$CD/old-src" "$CD/new-src"; }

if [ -n "$OUT" ]; then
  mkdir -p "$(dirname "$OUT")"
  { echo "# interface diff (ast comparison of public signatures): $PKG $OLD -> $NEW"; echo; run; } | tee "$OUT"
else
  echo "# interface diff (ast comparison of public signatures): $PKG $OLD -> $NEW"; echo; run
fi
