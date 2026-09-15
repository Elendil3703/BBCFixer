#!/usr/bin/env bash
# get-intact.sh [url-or-file]
# Downloads intact-sources.tar.gz (the source snapshot of the 40 image cases) and unpacks it
# into cases/<id>/intact/. Pass a local path to unpack an already downloaded archive.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-https://github.com/Elendil3703/BBCFixer/releases/download/bbcbench-v1/intact-sources.tar.gz}"
TMP="$ROOT/.scratch/intact-sources.tar.gz"; mkdir -p "$ROOT/.scratch"
case "$SRC" in
  http*) curl -L --fail --progress-bar -o "$TMP" "$SRC" ;;
  *)     cp "$SRC" "$TMP" ;;
esac
tar -xzf "$TMP" -C "$ROOT/cases"
n="$(ls -d "$ROOT"/cases/*/intact 2>/dev/null | wc -l | tr -d ' ')"
echo "unpacked intact sources for $n cases"
