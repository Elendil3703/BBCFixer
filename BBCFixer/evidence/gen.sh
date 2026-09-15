#!/usr/bin/env bash
# gen.sh <case_id> <workdir>
# Generates the BBCFixer evidence for a prepared BBCBench workspace: image.sh for image cases,
# lockfile.sh for lockfile cases. The output is <workdir>/evidence/.
set -u
[ $# -eq 2 ] || { echo "usage: $0 <case_id> <workdir>" >&2; exit 2; }
BASE="$(cd "$(dirname "$0")" && pwd)"
BBCBENCH_ROOT="${BBCBENCH_ROOT:-$BASE/../../BBCBench}"
META="$BBCBENCH_ROOT/cases/$1/meta.json"
[ -f "$META" ] || { echo "error: unknown case $1 ($META)" >&2; exit 2; }
case "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["harness"])' "$META")" in
  image)    exec bash "$BASE/image.sh" "$1" "$2" ;;
  lockfile) exec bash "$BASE/lockfile.sh" "$1" "$2" ;;
  *) echo "error: unknown harness kind in $META" >&2; exit 2 ;;
esac
