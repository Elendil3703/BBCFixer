#!/usr/bin/env bash
# Differential execution for image-harness cases (paper Section 3.2.1).
#
# gen mode (before the repair): for one upgraded library, two environments are built inside the
# same case image:
#   broken state = the image as it is (every dependency fixed at v_new);
#   intact state = the image with `pip install --no-deps <lib>==<v_old>` (only this library is
#                  downgraded, nothing else moves, so a behavior difference is attributable to it).
# The tests run once in each, boundary calls are recorded (py_boundary_trace plugin), and
# behavior_diff.py writes <ev>/behavior-diff.{md,json}; contract_gen.py writes <ev>/contract.md.
# The captures are kept in <ev>/v2-runs/{old,new}/ for probe mode.
#
# probe mode (after the repair, observational): reruns the repaired workspace in the broken-state
# environment, records the calls again and writes <ev>/probe-report.md.
#
# Usage:
#   diffexec_image.sh gen   --work DIR --image IMG --cmd 'test command' --pkg PKG --old VOLD --new VNEW \
#       --ev EVDIR --label-old TEXT --label-new TEXT [--old-pins 'pkg==old companion==x'] \
#       [--net-args '...'] [--symptom LOG] [--scratch DIR] [--timeout SECONDS]
#   diffexec_image.sh probe --work FIXED_DIR --image IMG --cmd 'test command' --pkg PKG --ev EVDIR \
#       [--net-args '...'] [--scratch DIR] [--timeout SECONDS]
#
# The intact-state install uses the real PyPI (versions are pinned, so this is deterministic).
# When the install fails, the library degrades to "test output only"; the report says so.
set -u
MODE="${1:?usage: $0 gen|probe ...}"; shift
V2DIR="$(cd "$(dirname "$0")" && pwd)"
EVROOT_DIR="$(cd "$V2DIR/.." && pwd)"

WORK=""; IMG=""; TEST_CMD=""; PKG=""; VOLD=""; VNEW=""; EV=""; OLD_PINS_RAW=""
NET_ARGS=""; SYMPTOM_LOG=""; SCRATCH=""; LABEL_OLD=""; LABEL_NEW=""
CAPTURE_TIMEOUT="${CAPTURE_TIMEOUT:-1200}"
while [ $# -gt 0 ]; do
  case "$1" in
    --work) WORK="$2"; shift 2;;
    --image) IMG="$2"; shift 2;;
    --cmd) TEST_CMD="$2"; shift 2;;
    --pkg) PKG="$2"; shift 2;;
    --old) VOLD="$2"; shift 2;;
    --new) VNEW="$2"; shift 2;;
    --ev) EV="$2"; shift 2;;
    --old-pins) OLD_PINS_RAW="$2"; shift 2;;
    --net-args) NET_ARGS="$2"; shift 2;;
    --symptom) SYMPTOM_LOG="$2"; shift 2;;
    --scratch) SCRATCH="$2"; shift 2;;
    --label-old) LABEL_OLD="$2"; shift 2;;
    --label-new) LABEL_NEW="$2"; shift 2;;
    --timeout) CAPTURE_TIMEOUT="$2"; shift 2;;
    *) echo "diffexec_image: unknown argument $1" >&2; exit 2;;
  esac
done
[ -n "$WORK" ] && [ -n "$IMG" ] && [ -n "$TEST_CMD" ] && [ -n "$PKG" ] && [ -n "$EV" ] \
  || { echo "diffexec_image: missing arguments (see the header)" >&2; exit 2; }
[ -d "$WORK" ] || { echo "diffexec_image: workspace not found: $WORK"; exit 1; }
[ -n "$SCRATCH" ] || SCRATCH="$(dirname "$EV")"
mkdir -p "$EV/v2-runs" "$SCRATCH"

# `--add-host` conflicts with `--network container:<sidecar>` (cases with a database sidecar).
ADDHOST="--add-host=host.docker.internal:host-gateway"
case "${NET_ARGS:-}" in *"container:"*) ADDHOST="";; esac

# Runs the tests once and records the boundary calls.
# $1 = source dir  $2 = capture output dir  $3 = import names to record (comma separated)  $4 = setup command (may be empty)
run_capture() {
  local src="$1" out="$2" pkgs="$3" setup="$4"
  mkdir -p "$out"
  local scratch; scratch="$(mktemp -d "$SCRATCH/.v2-scratch.XXXXXX")"
  cp -R "$src/." "$scratch/"
  # Wall-clock limit for the capture container (default 20 minutes): a repair that makes the tests
  # loop forever would otherwise hang the run indefinitely.
  local cname="v2cap-$$-$(basename "$scratch")"
  timeout -k 30 "$CAPTURE_TIMEOUT" \
  docker run --rm --name "$cname" -e PYTHONDONTWRITEBYTECODE=1 \
    -e "PYTEST_ADDOPTS=-p no:cacheprovider -p py_boundary_trace" \
    -e "PYTHONPATH=/algo-v2" \
    -e "ALGO_TRACE_PKGS=$pkgs" -e "ALGO_TRACE_OUT=/algo-out/trace.jsonl" \
    -e "ALGO_TRACE_MAX=${ALGO_TRACE_MAX:-4000}" \
    ${ADDHOST:-} ${NET_ARGS:-} \
    -v "$scratch":/work -v "$out":/algo-out -v "$V2DIR":/algo-v2:ro \
    -w /work --entrypoint bash "$IMG" -c "${setup:+$setup && }$TEST_CMD" \
    > "$out/test-output.txt" 2>&1
  local rc=$?
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    docker rm -f "$cname" >/dev/null 2>&1
    echo "[capture] timed out (${CAPTURE_TIMEOUT}s), container stopped" >> "$out/test-output.txt"
  fi
  rm -rf "$scratch"
  return $rc
}

# PyPI name -> import name (best effort): the name itself, hyphens to underscores, both lower-cased,
# plus a few well-known aliases. When the PyPI name and the import name differ (Django/django,
# SQLAlchemy/sqlalchemy, Flask-RESTful/flask_restful, pyspellchecker/spellchecker) the recorder
# would hook nothing and behavior-diff.md would report the tracer as not attached. Extra candidates
# are harmless; the plugin probes each one.
import_names() {
  local n="$1" l out
  l="$(printf '%s' "$1" | tr 'A-Z' 'a-z')"
  out="$n,$(printf '%s' "$n" | tr '-' '_'),$l,$(printf '%s' "$l" | tr '-' '_')"
  case "$l" in
    pyspellchecker) out="$out,spellchecker";;
    scikit-learn)   out="$out,sklearn";;
    scikit-image)   out="$out,skimage";;
    beautifulsoup4) out="$out,bs4";;
    pillow)         out="$out,PIL";;
    pyyaml)         out="$out,yaml";;
  esac
  printf '%s' "$out"
}

RC_ALL=0
PKGS="$(import_names "$PKG")"
if [ "$MODE" = "gen" ]; then
  [ -n "$VNEW" ] || { echo "diffexec_image: gen needs --new" >&2; exit 2; }
  echo ">>> [diffexec/$PKG] broken state (v_new=${VNEW}): running the tests and recording boundary calls..."
  run_capture "$WORK" "$EV/v2-runs/new" "$PKGS" "" || true
  if [ -n "$VOLD" ]; then
    # Intact state: the library is pinned back to v_old. --old-pins may add companion packages
    # that must move together with it (a companion whose new version requires the new library at
    # import time would otherwise break the import); the label names them.
    OLD_PINS=""
    for ep in ${OLD_PINS_RAW:-$PKG==$VOLD}; do
      [ -n "$ep" ] && OLD_PINS="$OLD_PINS '$ep'"
    done
    OLD_PINS="${OLD_PINS# }"
    echo ">>> [diffexec/$PKG] intact state (--no-deps install of ${OLD_PINS}, nothing else moves)..."
    SETUP="pip install -q --no-deps --index-url https://pypi.org/simple $OLD_PINS 2>/dev/null || pip install -q --no-deps $OLD_PINS"
    run_capture "$WORK" "$EV/v2-runs/old" "$PKGS" "$SETUP" || true
  else
    echo "!! [diffexec/$PKG] no v_old given; the intact state is skipped (no comparison)"
    mkdir -p "$EV/v2-runs/old"; : > "$EV/v2-runs/old/test-output.txt"
  fi
  python3 "$V2DIR/behavior_diff.py" --eco python \
    --old-dir "$EV/v2-runs/old" --new-dir "$EV/v2-runs/new" \
    --label-old "${LABEL_OLD:-v_old ($PKG==${VOLD:-unknown}, every other dependency as in the snapshot)}" \
    --label-new "${LABEL_NEW:-v_new ($PKG==${VNEW})}" \
    --out "$EV/behavior-diff.md" --json "$EV/behavior-diff.json" || RC_ALL=1
  python3 "$EVROOT_DIR/contract_gen.py" --eco python \
    --evidence-dir "$EV" --pkg "$PKG" --old "${VOLD:-v_old}" --new "$VNEW" \
    ${SYMPTOM_LOG:+--symptom "$SYMPTOM_LOG"} \
    --out "$EV/contract.md" || RC_ALL=1
elif [ "$MODE" = "probe" ]; then
  [ -d "$EV/v2-runs/new" ] || { echo "!! [diffexec/$PKG] no gen-mode capture, probe skipped"; exit 1; }
  echo ">>> [diffexec/$PKG] repaired workspace in the broken-state environment (probe)..."
  run_capture "$WORK" "$EV/v2-runs/fixed" "$PKGS" "" || true
  python3 "$V2DIR/behavior_diff.py" --mode probe --eco python \
    --old-dir "$EV/v2-runs/old" --new-dir "$EV/v2-runs/new" \
    --fixed-dir "$EV/v2-runs/fixed" \
    --out "$EV/probe-report.md" || RC_ALL=1
else
  echo "diffexec_image: unknown mode $MODE (gen|probe)" >&2; exit 2
fi
exit $RC_ALL
