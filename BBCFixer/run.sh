#!/usr/bin/env bash
# run.sh <case_id> <workdir> [--evidence-only] [--no-judge]
#
# Runs BBCFixer on one BBCBench case end to end:
#   1. BBCBench/harness/<kind>/prepare.sh   builds the broken state in <workdir>
#   2. evidence/gen.sh                      differential execution + library diff filtering + contract
#   3. agent/run-agent.sh                   mini-swe-agent repairs the project with the evidence
#   4. BBCBench/harness/<kind>/judge.sh     judges the result (PASS / FAIL)
# Environment: see agent/run-agent.sh (model, API key, budgets) and BBCBench/README.md.
set -u
CASE="${1:?usage: $0 <case_id> <workdir> [--evidence-only] [--no-judge]}"; WK="${2:?usage: $0 <case_id> <workdir>}"; shift 2
EVIDENCE_ONLY=0; JUDGE=1
for a in "$@"; do case "$a" in --evidence-only) EVIDENCE_ONLY=1;; --no-judge) JUDGE=0;; *) echo "unknown option $a" >&2; exit 2;; esac; done
ROOT="$(cd "$(dirname "$0")" && pwd)"
export BBCBENCH_ROOT="${BBCBENCH_ROOT:-$ROOT/../BBCBench}"
META="$BBCBENCH_ROOT/cases/$CASE/meta.json"
[ -f "$META" ] || { echo "error: unknown case $CASE ($META)" >&2; exit 2; }
KIND="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["harness"])' "$META")"
H="$BBCBENCH_ROOT/harness/$KIND"
mkdir -p "$WK"; WK="$(cd "$WK" && pwd)"

echo "==== [$CASE] 1/4 prepare ($KIND harness)"
bash "$H/prepare.sh" "$CASE" "$WK" || exit $?
echo "==== [$CASE] 2/4 evidence"
bash "$ROOT/evidence/gen.sh" "$CASE" "$WK" || echo "!! [$CASE] evidence generation reported problems (see $WK/.bbcfixer)"
[ "$EVIDENCE_ONLY" = 1 ] && { echo "==== [$CASE] evidence in $WK/evidence"; exit 0; }
echo "==== [$CASE] 3/4 agent"
bash "$ROOT/agent/run-agent.sh" "$CASE" "$WK"
[ "$JUDGE" = 1 ] || exit 0
echo "==== [$CASE] 4/4 judge"
bash "$H/judge.sh" "$CASE" "$WK"
