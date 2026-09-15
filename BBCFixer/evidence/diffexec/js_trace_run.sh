#!/usr/bin/env bash
# Run the tests once in an already prepared downstream workspace and record every boundary call from the downstream project into the library.
#
# This script does not build or modify the dependency tree and does not touch any file of the
# downstream project. The recorder is preloaded from outside the workspace via NODE_OPTIONS,
# --require for CommonJS and --import for ESM, so the per-file fingerprints of node_modules stay byte-identical.
#
# Usage:
#   js_trace_run.sh --work <downstream workspace> --pkg <library name> --cmd <test command> \
#                   --out <capture dir> [--image <docker image>] [--net] [--no-trace]
#                   [--timeout <seconds>] [--env K=V]...
#
# Output (the capture directory, in the layout behavior_diff.py expects):
#   test-output.txt   full output of this test run
#   exit-code.txt     exit code of the test command
#   trace.jsonl       merged boundary-call records (with _meta; the attached field says whether the recorder really attached)
#   proc/             raw per-process records, for investigation
set -u
ALGO_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK=""; PKG=""; CMD=""; OUT=""; IMAGE=""; NET=nonet; TRACE=1; TIMEOUT=1200
EXTRA_ENV=()
while [ $# -gt 0 ]; do
  case "$1" in
    --work) WORK="$2"; shift 2;;
    --pkg) PKG="$2"; shift 2;;
    --cmd) CMD="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --image) IMAGE="$2"; shift 2;;
    --net) NET=net; shift;;
    --no-trace) TRACE=0; shift;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --env) EXTRA_ENV+=(-e "$2"); shift 2;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
[ -n "$WORK" ] && [ -n "$PKG" ] && [ -n "$CMD" ] && [ -n "$OUT" ] || { echo "usage: see header" >&2; exit 2; }
[ -d "$WORK/node_modules" ] || { echo "js_trace_run: no node_modules in $WORK, the workspace is not prepared yet" >&2; exit 2; }

rm -rf "$OUT"; mkdir -p "$OUT/proc"
HOMEDIR="$OUT/rt-home"; mkdir -p "$HOMEDIR"

# Both injection channels are required; they cover two different module systems:
#   --require  rewrites Module._load, covers CommonJS
#   --import   registers the ESM loader hook via module.register, covers import
# With only the former, an ESM downstream project silently loses the broken-state records when the new library
# version ships a real ESM entry, and that output looks exactly like "no behavioral difference". ALGO_TRACE_ESM=0 disables the latter for comparison runs.
ESM_HOOK="${ALGO_TRACE_ESM:-1}"

# --import only exists since Node 18.19. Handing it to an older node via NODE_OPTIONS makes node refuse
# to start with "bad option", so the whole test run produces nothing, the recorder records 0 calls, and
# differential execution silently degrades, with output that looks exactly like "no behavioral difference".
# Many cases in the dataset pin their runtime to node:8 through node:16, so this version gate is mandatory, not a precaution.
node_supports_import() {
  local probe
  if [ -n "$IMAGE" ]; then
    probe=$(docker run --rm "$IMAGE" node -p "process.versions.node" 2>/dev/null)
  else
    probe=$(node -p "process.versions.node" 2>/dev/null)
  fi
  [ -n "$probe" ] || return 1
  local maj min
  maj=${probe%%.*}; min=${probe#*.}; min=${min%%.*}
  case "$maj" in ''|*[!0-9]*) return 1 ;; esac
  case "$min" in ''|*[!0-9]*) min=0 ;; esac
  [ "$maj" -gt 18 ] && return 0
  [ "$maj" -eq 18 ] && [ "$min" -ge 19 ] && return 0
  return 1
}

if [ "$ESM_HOOK" = 1 ] && ! node_supports_import; then
  ESM_HOOK=0
  echo "js_trace_run: node in this runtime is older than 18.19 and does not support --import; only the CommonJS channel is hooked this round;" \
       "if the downstream project is ESM and the new library version ships a real ESM entry, the broken-state records will be lost and this must be reported as a degradation" >&2
fi

build_trace_env() {
  TRACE_ENV=()
  [ "$TRACE" = 1 ] || return 0
  NODE_OPTS="--require /algo/js_boundary_trace.js"
  [ "$ESM_HOOK" = 1 ] && NODE_OPTS="$NODE_OPTS --import /algo/js_esm_register.mjs"
  TRACE_ENV=(
    -e "NODE_OPTIONS=$NODE_OPTS"
    -e "ALGO_TRACE_PKGS=$PKG"
    -e "ALGO_TRACE_DIR=/algo-out/proc"
    -e "ALGO_TRACE_ROOT=/w"
    -e "ALGO_TRACE_ESM=$ESM_HOOK"
  )
}
build_trace_env

# Registering the ESM loader hook changes how node decides the module format of the entry file; in practice it
# breaks ts-node driven projects entirely (ERR_UNKNOWN_FILE_EXTENSION: Unknown file extension ".ts", with js_esm_trace_loader.mjs in the stack).
# The effect of such interference looks exactly like "no behavioral difference", so it must be detected automatically and rolled back.
esm_interference() {
  [ -s "$OUT/test-output.txt" ] || return 1
  grep -q "js_esm_trace_loader\.mjs" "$OUT/test-output.txt" && return 0
  grep -q "ERR_UNKNOWN_FILE_EXTENSION" "$OUT/test-output.txt" && return 0
  return 1
}

run_once() {
if [ -n "$IMAGE" ]; then
  netarg=(); [ "$NET" = nonet ] && netarg=(--network none)
  cname="jstrace-$$-$RANDOM"
  timeout -k 20 "$TIMEOUT" docker run --rm --name "$cname" "${netarg[@]}" \
    -u "$(id -u):$(id -g)" \
    -v "$WORK":/w -v "$HOMEDIR":/rt-home -v "$OUT":/algo-out -v "$ALGO_DIR":/algo:ro \
    -w /w -e HOME=/rt-home -e npm_config_cache=/rt-home/.npm -e NO_PROXY='*' \
    "${TRACE_ENV[@]}" "${EXTRA_ENV[@]}" \
    "$IMAGE" bash -c "$CMD" > "$OUT/test-output.txt" 2>&1
  RC=$?
  docker rm -f "$cname" >/dev/null 2>&1 || true
else
  # No image: run directly on the host, with the recorder paths as host absolute paths
  HOST_OPTS="--require $ALGO_DIR/js_boundary_trace.js"
  [ "$ESM_HOOK" = 1 ] && HOST_OPTS="$HOST_OPTS --import $ALGO_DIR/js_esm_register.mjs"
  env NODE_OPTIONS="$HOST_OPTS" \
      ALGO_TRACE_PKGS="$PKG" ALGO_TRACE_DIR="$OUT/proc" ALGO_TRACE_ROOT="$WORK" \
      ALGO_TRACE_ESM="$ESM_HOOK" \
      bash -c "cd '$WORK'; $CMD" > "$OUT/test-output.txt" 2>&1
  RC=$?
fi
}
run_once
if [ "$TRACE" = 1 ] && [ "$ESM_HOOK" = 1 ] && [ "$RC" -ne 0 ] && esm_interference; then
  echo "js_trace_run: the ESM loader hook interfered with the module format detection of the project under test; rerunning once with only the CommonJS channel" >&2
  ESM_HOOK=0
  build_trace_env
  rm -rf "$OUT/proc"; mkdir -p "$OUT/proc"
  run_once
fi
echo "$RC" > "$OUT/exit-code.txt"

if [ "$TRACE" = 1 ]; then
  python3 "$ALGO_DIR/js_boundary_merge.py" --dir "$OUT/proc" --out "$OUT/trace.jsonl"
  MRC=$?
  echo "js_trace_run: test exit code $RC, merge exit code $MRC"
else
  echo "js_trace_run: test exit code $RC (recorder not attached)"
fi
exit 0
