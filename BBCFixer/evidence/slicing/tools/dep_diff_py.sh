#!/usr/bin/env bash
# Transitive-dependency / version-range layer (Python ecosystem): compare the dependency
# declarations of the two versions of the library.
# The equivalent of the Java version dep_diff.sh: PyPI requires_dist corresponds to the POM <dependencies>.
#
# Usage:
#   ./dep_diff_py.sh <PKG> <OLD> <NEW> [<OUT_FILE>]
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"; . "$DIR/lib.sh"
PKG="$1"; OLD="$2"; NEW="$3"; OUT="${4:-}"
[ -z "$PKG" ] || [ -z "$OLD" ] || [ -z "$NEW" ] && {
  echo "usage: $0 <PKG> <OLD> <NEW> [OUT]"; exit 2; }

CD="$(dep_cache_dir pypi "$PKG" "$OLD" "$NEW")"
mkdir -p "$CD"

[ -s "$CD/old-requires.txt" ] || python3 "$DIR/pypi_meta.py" requires "$PKG" "$OLD" > "$CD/old-requires.txt" 2>/dev/null || true
[ -s "$CD/new-requires.txt" ] || python3 "$DIR/pypi_meta.py" requires "$PKG" "$NEW" > "$CD/new-requires.txt" 2>/dev/null || true

emit() {
  echo "# dependency declaration diff: $PKG $OLD -> $NEW"
  echo "# (PyPI requires_dist comparison; '-' lines = no longer declared by the new version (a transitive dependency may be lost),"
  echo "#   '+' lines = declarations and version ranges added or changed by the new version)"
  echo
  if [ -s "$CD/old-requires.txt" ] || [ -s "$CD/new-requires.txt" ]; then
    diff -u -L "requires@$OLD" -L "requires@$NEW" \
      "$CD/old-requires.txt" "$CD/new-requires.txt" && echo "(no difference in the dependency declarations)"
  else
    echo "(neither version declares dependencies, or the PyPI metadata could not be fetched)"
  fi
}

if [ -n "$OUT" ]; then mkdir -p "$(dirname "$OUT")"; emit | tee "$OUT"; else emit; fi
