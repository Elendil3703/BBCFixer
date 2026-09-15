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
  echo "# 依赖声明差异: $PKG $OLD -> $NEW"
  echo "# （PyPI requires_dist 声明级比对；'-' 行 = 新版不再声明（传递依赖可能丢失），"
  echo "#   '+' 行 = 新版新增/改动的声明与版本区间）"
  echo
  if [ -s "$CD/old-requires.txt" ] || [ -s "$CD/new-requires.txt" ]; then
    diff -u -L "requires@$OLD" -L "requires@$NEW" \
      "$CD/old-requires.txt" "$CD/new-requires.txt" && echo "（依赖声明无差异）"
  else
    echo "（两版均无依赖声明，或 PyPI 元数据获取失败）"
  fi
}

if [ -n "$OUT" ]; then mkdir -p "$(dirname "$OUT")"; emit | tee "$OUT"; else emit; fi
