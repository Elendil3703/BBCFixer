#!/usr/bin/env bash
# lockfile.sh <case_id> <workdir>
#
# Evidence generation for a lockfile-harness case (paper Section 3.2). It combines the two evidence
# channels and writes one directory that is handed to the agent as a whole:
#   differential execution (Section 3.2.1)  -> diffexec/diffexec_lockfile.sh gen
#   library diff filtering  (Section 3.2.2)  -> slicing/upstream_diff.sh + slicing/slice_diff.py
#   repair contract                          -> contract_gen.py
#
# <workdir> must have been made by BBCBench/harness/lockfile/prepare.sh. The intact state, which
# differential execution needs, is built here by intact_lockfile.sh from the same pinned artifacts;
# the delivered workspace is never touched. The output is <workdir>/evidence/.
#
# Leak prevention. Source gate, by construction: this script and every tool it calls only touch
# the library release artifacts, the upstream repository mirror, copies of the downstream workspace,
# the broken-state error output and the output directory; the case directory is never mounted or
# read. Content gate: gate.py checks the finished evidence directory against the case directory.
set -u
CASE="${1:?usage: $0 <case_id> <workdir>}"; WK_IN="${2:?usage: $0 <case_id> <workdir>}"
BASE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$BASE/.." && pwd)"
V2="$BASE/diffexec"; SL="$BASE/slicing"
BBCBENCH_ROOT="${BBCBENCH_ROOT:-$ROOT/../BBCBench}"
[ -d "$BBCBENCH_ROOT/harness/lockfile" ] || { echo "error: BBCBench not found at $BBCBENCH_ROOT (set BBCBENCH_ROOT)" >&2; exit 2; }
. "$BBCBENCH_ROOT/harness/lockfile/lib.sh"
load_case "$CASE"
WK="$(cd "$WK_IN" && pwd)"; SRC="$WK/$SRCNAME"
[ -d "$SRC" ] && [ -f "$WK/_FAILING.txt" ] || die "$WK was not made by harness/lockfile/prepare.sh"
SCR="$WK/.bbcfixer"; OUT="$WK/evidence"; ERRLOG="$WK/_FAILING.txt"
OLDW="$SCR/caps/old"; NEWW="$SCR/caps/new"
TMO="${TEST_TIMEOUT:-1200}"
export OUR_UPSTREAM_CACHE="${OUR_UPSTREAM_CACHE:-$ROOT/.cache/upstream}"
fail() { echo "OUR_EVIDENCE_FAIL: $*"; exit 1; }

# Source gate: no path used for evidence generation may lie under cases/.
for _p in "$SRC" "$ERRLOG" "$OLDW" "$OUT" "$SCR"; do
  case "$(realpath -m -- "$_p")/" in
    *"/cases/"*) fail "no evidence path may lie under cases/ (source gate): $_p" ;;
  esac
done
rm -rf "$OUT"; mkdir -p "$OUT" "$SCR"

# The intact state, built from the pinned artifacts (the reference run of differential execution).
if [ ! -d "$OLDW" ]; then
  rm -rf "$OLDW"
  bash "$BASE/intact_lockfile.sh" "$CASE" "$OLDW" > "$SCR/intact.log" 2>&1 \
    || { tail -8 "$SCR/intact.log"; fail "building the intact state failed (see $SCR/intact.log)"; }
  say "intact state built at $OLDW"
fi

# The broken state as delivered to the agent, copied for the recorded run (the delivered workspace
# itself is never touched).
say "copying the broken state for differential execution"
rm -rf "$NEWW"
if [ "$ECO" = npm ]; then
  cp -a "$SRC" "$NEWW" || fail "copying the broken workspace failed"
  rm -rf "$NEWW/.git"
else
  mkdir -p "$NEWW/.home" "$NEWW/.tmp" "$NEWW/.pipcache"
  cp -a "$SRC" "$NEWW/repo" || fail "copying the downstream source failed"
  rm -rf "$NEWW/repo/.git"
  cp -a "$WK/.venv" "$NEWW/.venv" || fail "copying the venv failed"
fi
# pip cases: the library diff must be taken between the versions actually installed in the two
# states; lock-OLD.txt defines the intact state.
OLD_FOR_EV="$VOLD"
if [ "$ECO" = pip ]; then
  VOLD_LOCK="$(grep -iE "^${LIB}==" "$PIN/lock-OLD.txt" | head -1 | cut -d= -f3)"
  [ -n "$VOLD_LOCK" ] && OLD_FOR_EV="$VOLD_LOCK"
fi

# ---------------------------------------------------------------- 1. measured channel
DXLOG="$SCR/diffexec.log"
if bash "$V2/diffexec_lockfile.sh" gen --eco "$ECO" --pkg "$LIB" --old "$OLD_FOR_EV" --new "$VNEW" \
     --old-work "$OLDW" --new-work "$NEWW" --image "$IMAGE" --cmd "$TESTCMD" \
     --ev "$SCR/dx" --timeout "$TMO" > "$DXLOG" 2>&1; then
  :
else
  tail -8 "$DXLOG"
  fail "differential execution failed (see $DXLOG)"
fi
AO="$(grep -oE 'OUR_ATTACHED_OLD=[01]' "$DXLOG" | tail -1 | cut -d= -f2)"
RO="$(grep -oE 'OUR_RECORDED_OLD=[0-9]+' "$DXLOG" | tail -1 | cut -d= -f2)"
RN="$(grep -oE 'OUR_RECORDED_NEW=[0-9]+' "$DXLOG" | tail -1 | cut -d= -f2)"
: "${RO:=0}"; : "${RN:=0}"
AN="$(grep -oE 'OUR_ATTACHED_NEW=[01]' "$DXLOG" | tail -1 | cut -d= -f2)"
ES="$(grep -oE 'OUR_ESM_SEEN=[01]' "$DXLOG" | tail -1 | cut -d= -f2)"
DV="$(grep -oE 'OUR_DIVERGENT=[0-9]+' "$DXLOG" | tail -1 | cut -d= -f2)"
: "${AO:=0}"; : "${AN:=0}"; : "${ES:=0}"; : "${DV:=0}"

# The D items (measured contract) hold only when two conditions are both met.
# First, the recorder attached on both sides. The recorder's own `attached` flag is used, not the
# language of the case: an unattached recorder produces the same output as "no behavior
# difference", and reading the latter into the former is a silent wrong conclusion.
# Second, both sides actually recorded something. This came from practice: in one pydantic case
# both sides were attached, the broken state recorded 995 calls and the intact state 0, because
# pydantic 1.x ships compiled code without Python frames that sys.setprofile cannot see. One side
# empty and the other full yields neither "no difference" nor divergences but a pile of
# "only in the new state" calls, and the D items are effectively empty.
if [ "$AO" = 1 ] && [ "$AN" = 1 ] && [ "$RO" -gt 0 ] && [ "$RN" -gt 0 ]; then
  TRACER=on; TRACER_REASON=ok
elif [ "$AO" != 1 ] || [ "$AN" != 1 ]; then
  TRACER=off; TRACER_REASON=not-attached
else
  TRACER=off; TRACER_REASON=one-side-empty
fi

# ---------------------------------------------------------------- 2. declared channel
UPLOG="$SCR/upstream.log"
if bash "$SL/upstream_diff.sh" "$ECO" "$LIB" "$OLD_FOR_EV" "$VNEW" "$IMAGE" \
     "$SCR/up-scratch" "$SCR/up" > "$UPLOG" 2>&1; then
  :
else
  tail -8 "$UPLOG"
  fail "fetching the library versions failed (see $UPLOG)"
fi
TDS="$(grep -oE 'OUR_TESTDIFF_STATUS=\S+' "$UPLOG" | tail -1 | cut -d= -f2)"

# ---------------------------------------------------------------- 3. slicing
SLLOG="$SCR/slice.log"
python3 "$SL/slice_diff.py" --eco "$ECO" --lib "$LIB" --old "$OLD_FOR_EV" --new "$VNEW" \
  --src-dir "$SRC" --error-log "$ERRLOG" \
  --source-diff "$SCR/up/source-diff.raw" --test-diff "$SCR/up/test-diff.raw" \
  --test-diff-note "$SCR/up/test-diff.note" \
  --behavior-json "$SCR/dx/behavior-diff.json" \
  --scratch "$SCR/slice" \
  --out-dir "$OUT" > "$SLLOG" 2>&1 || { tail -8 "$SLLOG"; fail "slicing failed (see $SLLOG)"; }
cat "$SLLOG"

# The behavior report goes into the evidence directory; behavior-diff.json is only for the
# contract generator and stays in the scratch directory (gate.py checks this).
cp "$SCR/dx/behavior-diff.md" "$OUT/behavior-diff.md" 2>/dev/null || \
  echo "(No measured behavior difference report was produced for this case.)" > "$OUT/behavior-diff.md"

# ---------------------------------------------------------------- 4. repair contract
DEC=javascript; [ "$ECO" = pip ] && DEC=python
python3 "$BASE/contract_gen.py" --eco "$DEC" --evidence-dir "$OUT" \
  --behavior-json "$SCR/dx/behavior-diff.json" \
  --pkg "$LIB" --old "$OLD_FOR_EV" --new "$VNEW" \
  ${ERRLOG:+--symptom "$ERRLOG"} \
  --out "$OUT/contract.md" || fail "contract generation failed"

# When the recorder did not attach, the D items do not hold for this case. The contract says so
# explicitly, so that "(no diverging boundary call captured)" is not read as "no difference".
if [ "$TRACER" = off ]; then
  python3 - "$OUT/contract.md" "$AO" "$AN" "$RO" "$RN" <<'PY'
import io, sys
p, ao, an, ro, rn = sys.argv[1:6]
s = io.open(p, encoding="utf-8").read()
banner = (
    "\n> **The D items (measured contract) do not hold for this case: differential execution recorded nothing comparable**"
    " (intact state attached=%s with %s calls, broken state attached=%s with %s calls).\n"
    "> An empty recording, or a recording on one side only, only means that the recorder captured nothing here; "
    "**it does not mean that the two versions behave the same**.\n"
    "> This case runs with the C items (declared contract) and the T item (judging contract) only.\n"
    % (ao, ro, an, rn))
lines = s.split("\n")
for i, ln in enumerate(lines):
    if ln.startswith("# "):
        lines.insert(i + 1, banner)
        break
else:
    lines.insert(0, banner)
io.open(p, "w", encoding="utf-8").write("\n".join(lines))
print("contract: noted at the top that the D items do not hold")
PY
fi

# ---------------------------------------------------------------- 5. evidence guide (read by the agent)
cat > "$OUT/README.md" <<EOF
# Library evidence (generated mechanically, no manual judgement)

Two channels produce this directory independently and corroborate each other:

- **Library diff**: the source and test differences of ${LIB} between ${OLD_FOR_EV} and ${VNEW},
  sliced by entry symbols into \`code-diff-slice.txt\` and \`test-diff.txt\`.
- **Differential execution**: the same downstream code runs once with the old and once with the new
  library version; the mechanical comparison is \`behavior-diff.md\`, where the starred item is the
  **first divergence**.

| File | Content |
| --- | --- |
| \`behavior-diff.md\` | measured behavior difference: tests that went from passing to failing, asserted values, first divergence |
| \`code-diff-slice.txt\` | sliced library source diff (cut by entry symbols, not the whole diff) |
| \`test-diff.txt\` | sliced library test diff: the new expected values and usages written by the library maintainers |
| \`contract.md\` | repair contract: declared C items, measured D items, the judging T item, each checkable |
| \`entry-symbols.txt\` | how the entry symbols were derived (each of the three sources listed) |
| \`evidence-guide.md\` | evidence guide: which fragments each error symbol matched |

Recorder status: intact state attached=${AO} (${RO} calls), broken state attached=${AN} (${RN} calls);
${DV} diverging calls; library test diff ${TDS}; ESM loaded ${ES}.
EOF

# ---------------------------------------------------------------- 6. content gate
python3 "$BASE/gate.py" "$OUT" "$CDIR" || fail "the evidence did not pass the content gate"

# The workspace copies hold a full dependency tree each; they are removed once the evidence exists.
rm -rf "$SCR/caps"

echo "OUR_TRACER=$TRACER"
echo "OUR_TRACER_REASON=$TRACER_REASON"
echo "OUR_RECORDED_OLD=$RO"
echo "OUR_RECORDED_NEW=$RN"
echo "OUR_ATTACHED_OLD=$AO"
echo "OUR_ATTACHED_NEW=$AN"
echo "OUR_ESM_SEEN=$ES"
echo "OUR_DIVERGENT=$DV"
echo "OUR_TESTDIFF_STATUS=${TDS:-unknown}"
if [ "$TRACER" = on ]; then
  echo ">>> [$CASE] contract C+D+T ($DV diverging boundary calls)"
else
  echo "!! [$CASE] the recorder captured nothing comparable ($TRACER_REASON; $RO and $RN calls): contract C+T only"
fi
echo "OUR_EVIDENCE_OK $OUT"
