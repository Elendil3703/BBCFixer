#!/usr/bin/env bash
# run-agent.sh <case_id> <workdir>
#
# Runs mini-swe-agent with the BBCFixer configuration on a prepared BBCBench workspace whose
# evidence has been generated (<workdir>/evidence). The agent works inside the case image (image
# cases) or the runtime image (lockfile cases); the workspace is mounted at /work and the evidence
# read-only at /evidence. Trajectory, patch and summary go to <workdir>/.bbcfixer/agent/.
#
# Environment: MINI_CONFIG (default configs/bbc-our.yaml), MINI_MODEL (default qwen/qwen3-coder),
# OPENROUTER_API_KEY (or <package root>/secrets.env), MINI_PYTHON (python with mini-swe-agent
# installed; default <package root>/.venv/bin/python, else python3), and the budget overrides
# listed in run-mini.py.
set -u
[ $# -eq 2 ] || { echo "usage: $0 <case_id> <workdir>" >&2; exit 2; }
CASE="$1"; WK="$(cd "$2" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
BBCBENCH_ROOT="${BBCBENCH_ROOT:-$ROOT/../BBCBench}"
PY="${MINI_PYTHON:-$ROOT/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
META="$BBCBENCH_ROOT/cases/$CASE/meta.json"
[ -f "$META" ] || { echo "error: unknown case $CASE" >&2; exit 2; }
eval "$("$PY" "$ROOT/lib/caseinfo.py" "$META")"
[ -d "$WK/evidence" ] || { echo "error: $WK/evidence not found; run evidence/gen.sh first" >&2; exit 2; }
export MINI_CONFIG="${MINI_CONFIG:-$HERE/configs/bbc-our.yaml}"
export RESULTS_DIR="${RESULTS_DIR:-$WK/.bbcfixer/agent}"

if [ "$HARNESS" = image ]; then
  . "$BBCBENCH_ROOT/harness/image/config.sh"; load_case "$CASE"
  SRC="$WK/src"; [ -d "$SRC/.git" ] || die "$WK was not made by harness/image/prepare.sh"
  ensure_image; ensure_sidecar
  git -C "$SRC" tag -f baseline broken >/dev/null 2>&1
  MINI_WORK="$SRC" MINI_GIT_DIR="$SRC" MINI_CWD="/work" \
  MINI_SAVE="$CASE" MINI_PROJ="$PROJ" MINI_IMG="$IMG" MINI_TEST_CMD="$TEST_CMD" \
  MINI_KEY_DEPS="$KEY_DEPS" MINI_MIG_DATE="$MIG_DATE" \
  MINI_LANG="Python" MINI_MANIFEST="requirements / setup.py / setup.cfg" \
  MINI_RUN_ARGS="${NET_ARGS:-}" \
  EVIDENCE_DIR="$WK/evidence" INIT_ERROR_FILE="$WK/evidence/init-error.txt" \
    "$PY" "$HERE/run-mini.py"
  RC=$?
  # Files the container created as root are handed back to the user, or the judge cannot read them.
  if find "$SRC" -user 0 -print -quit 2>/dev/null | grep -q .; then
    docker run --rm -v "$SRC":/x --entrypoint chown "$IMG" -R "$(id -u):$(id -g)" /x >/dev/null 2>&1
  fi
  exit $RC
fi

. "$BBCBENCH_ROOT/harness/lockfile/lib.sh"; load_case "$CASE"
SRC="$WK/$SRCNAME"; [ -d "$SRC/.git" ] || die "$WK was not made by harness/lockfile/prepare.sh"
ensure_image
git -C "$SRC" tag -f baseline broken >/dev/null 2>&1
# The test command is the one declared by the case; the workspace's run_tests.sh wraps it in
# docker and cannot run inside the container.
case "$ECO" in
  npm) LANG_NAME="JavaScript"; MANIFEST="package.json / package-lock.json" ;;
  pip) LANG_NAME="Python"; MANIFEST="requirements*.txt / pyproject.toml / poetry.lock" ;;
esac
# Same user and HOME as the harness uses for its own test runs, so no root-owned files are left
# behind; HOME lies outside the judged source directory.
mkdir -p "$WK/agent-home"
EXTRA_ARGS="-u $(id -u):$(id -g) -v $WK/agent-home:/agent-home -e HOME=/agent-home"
EXTRA_ARGS="$EXTRA_ARGS -e npm_config_cache=/agent-home/.npm -e XDG_CACHE_HOME=/agent-home/.cache"
MINI_WORK="$WK" MINI_GIT_DIR="$SRC" MINI_CWD="/work/$SRCNAME" \
MINI_SAVE="$CASE" MINI_PROJ="$PROJ" MINI_IMG="$IMAGE" MINI_TEST_CMD="$TESTCMD" \
MINI_KEY_DEPS="$KEY_DEPS" MINI_MIG_DATE="$MIG_DATE" \
MINI_LANG="$LANG_NAME" MINI_MANIFEST="$MANIFEST" \
MINI_RUN_ARGS="$EXTRA_ARGS" \
EVIDENCE_DIR="$WK/evidence" INIT_ERROR_FILE="$WK/_FAILING.txt" \
  "$PY" "$HERE/run-mini.py"
RC=$?
if find "$WK" -user 0 -print -quit 2>/dev/null | grep -q .; then
  docker run --rm -v "$WK":/x --entrypoint chown "$IMAGE" -R "$(id -u):$(id -g)" /x >/dev/null 2>&1
fi
exit $RC
