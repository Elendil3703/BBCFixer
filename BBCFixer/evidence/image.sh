#!/usr/bin/env bash
# image.sh <case_id> <workdir>
#
# Evidence generation for an image-harness case (paper Section 3.2). For every upgraded library
# of the case:
#   library diff filtering (Section 3.2.2)  -> slicing/extract_basis.py  (fetches both library
#                                              versions, slices the source/test diffs by entry symbols)
#   differential execution (Section 3.2.1)  -> diffexec/diffexec_image.sh gen  (records the tests in
#                                              the intact and the broken state inside the case image)
#   repair contract                          -> contract_gen.py (called by diffexec_image.sh)
#
# <workdir> must have been made by BBCBench/harness/image/prepare.sh: <workdir>/src is the broken
# state, <workdir>/_FAILING.txt its test output (the round-0 symptom). The output is
# <workdir>/evidence/<library>/ for each library plus <workdir>/evidence/init-error.txt.
set -u
CASE="${1:?usage: $0 <case_id> <workdir>}"; WK_IN="${2:?usage: $0 <case_id> <workdir>}"
BASE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$BASE/.." && pwd)"
V2="$BASE/diffexec"; SL="$BASE/slicing"
BBCBENCH_ROOT="${BBCBENCH_ROOT:-$ROOT/../BBCBench}"
[ -d "$BBCBENCH_ROOT/harness/image" ] || { echo "error: BBCBench not found at $BBCBENCH_ROOT (set BBCBENCH_ROOT)" >&2; exit 2; }
. "$BBCBENCH_ROOT/harness/image/config.sh"
load_case "$CASE"
WK="$(cd "$WK_IN" && pwd)"; SRC="$WK/src"
[ -d "$SRC" ] && [ -f "$WK/_FAILING.txt" ] || die "$WK was not made by harness/image/prepare.sh"
SCR="$WK/.bbcfixer"; OUT="$WK/evidence"
export ALGO_CACHE="${ALGO_CACHE:-$ROOT/.cache/pypi}"
export ALGO_EVIDENCE="$SCR/evidence"
mkdir -p "$SCR" "$ALGO_CACHE"
ensure_image; ensure_sidecar

# Case metadata: the project name, the upgraded libraries (upgrade.key_deps when the case upgrades
# several libraries at a dated snapshot, else lib/v_old/v_new) and the version label of the prompt.
eval "$(python3 "$ROOT/lib/caseinfo.py" "$CDIR/meta.json")"

# Round-0 symptom: the error lines of the broken-state test output (ANSI colours stripped).
distill() {
  sed -E $'s/\x1b\\[[0-9;]*m//g' "$1" \
    | grep -E "Error|error:|Traceback|assert|FAILED|cannot import|No module|has no attribute|ImportError|AttributeError|TypeError|ValidationError|\.py:[0-9]+|E  " \
    | sed 's/[`$]//g' | head -40
}
SYMPTOM_LOG="$SCR/symptom.log"
distill "$WK/_FAILING.txt" > "$SYMPTOM_LOG"
echo ">>> [$PROJ] symptom (first 5 lines):"; head -5 "$SYMPTOM_LOG" | sed 's/^/    /'

rm -rf "$OUT" "$ALGO_EVIDENCE"; mkdir -p "$OUT" "$ALGO_EVIDENCE"
RC_ALL=0
for triple in $DEPS; do
  pkg="${triple%%==*}"; rest="${triple#*==}"; old="${rest%%==*}"; new="${rest#*==}"
  EV="$ALGO_EVIDENCE/$PROJ-$pkg"
  echo ">>> [$PROJ] evidence: $pkg $old -> $new"
  # ---- library diff filtering
  python3 "$SL/extract_basis.py" --eco python \
    --proj "$PROJ-$pkg" --group pypi --art "$pkg" --old "$old" --new "$new" \
    --src-dir "$SRC" --error-log "$SYMPTOM_LOG" \
    --symptom "$(head -8 "$SYMPTOM_LOG" | tr '\n' '；')" || { echo "!! [$PROJ] $pkg: slicing partly failed"; RC_ALL=1; }
  # ---- differential execution. Intact-state pins: for a single-package upgrade the pins of the
  # intact state recorded by the harness (the library and any companion that must move with it);
  # for a dated snapshot the library alone.
  if [ "$KIND" = single-package ] && [ -n "${INTACT_PIP:-}" ]; then
    PINS="$INTACT_PIP"
  else
    PINS="$pkg==$old"
  fi
  EXTRA=""
  for p in $PINS; do case "$p" in "$pkg=="*) ;; *) EXTRA="${EXTRA:+$EXTRA,}$p";; esac; done
  bash "$V2/diffexec_image.sh" gen --work "$SRC" --image "$IMG" --cmd "$TEST_CMD" \
    --net-args "${NET_ARGS:-}" --pkg "$pkg" --old "$old" --new "$new" --old-pins "$PINS" \
    --ev "$EV" --symptom "$SYMPTOM_LOG" --scratch "$SCR" \
    --label-old "v_old（$pkg==${old}${EXTRA:+，伴随 $EXTRA}，其余依赖同快照）" \
    --label-new "v_new（$pkg==${new}，$MIG_DATE 快照）" \
    || { echo "!! [$PROJ] $pkg: differential execution partly failed (declared channel only)"; RC_ALL=1; }
  # ---- assemble what the agent sees: the evidence files without the raw captures
  mkdir -p "$OUT/$pkg"
  ( cd "$EV" && tar cf - --exclude=v2-runs --exclude=probe-report.md --exclude=guide-diff.txt . ) \
    | ( cd "$OUT/$pkg" && tar xf - )
done
cp -f "$SYMPTOM_LOG" "$OUT/init-error.txt"
echo ">>> [$PROJ] evidence assembled: $(ls -d "$OUT"/*/ 2>/dev/null | wc -l | tr -d ' ') library directories -> $OUT"
exit $RC_ALL
