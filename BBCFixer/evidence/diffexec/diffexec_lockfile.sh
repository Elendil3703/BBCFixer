#!/usr/bin/env bash
# Differential execution for lockfile-harness cases (paper Section 3.2.1).
#
# Same job as diffexec_image.sh: run the same downstream code once in the intact state and once in
# the broken state, record the boundary calls, and let behavior_diff.py compare them. The difference
# is where the two environments come from.
#
# This script does not build environments. Lockfile cases are pinned: the intact state is installed
# from pinned/lock-OLD and the broken state by the swap or from lock-NEW, and building them is the
# job of the BBCBench harness. This script therefore receives two prepared workspace directories and
# only runs the tests in each while recording.
#
# Leak prevention by construction: the containers only see the workspace, the output directory and
# this directory; the case directory is never mounted, so reference_fix.patch and meta.json are
# physically unreadable.
#
# Usage:
#   diffexec_lockfile.sh gen   --eco npm|pip --pkg LIB --old VOLD --new VNEW \
#       --old-work DIR --new-work DIR --image IMG --cmd 'test command' --ev EVDIR [--timeout SECONDS]
#   diffexec_lockfile.sh probe --eco npm|pip --pkg LIB --old VOLD --new VNEW \
#       --fixed-work DIR --image IMG --cmd 'test command' --ev EVDIR [--timeout SECONDS]
#
# gen writes:   <ev>/v2-runs/{old,new}/ (test-output.txt, exit-code.txt, trace.jsonl),
#               <ev>/behavior-diff.md, <ev>/behavior-diff.json
# probe writes: <ev>/v2-runs/fixed/, <ev>/probe-report.md
#
# Signals printed for the caller (the caller decides the contract completeness from these,
# not from the language of the case):
#   OUR_ATTACHED_OLD=0|1  OUR_ATTACHED_NEW=0|1  OUR_ATTACHED_FIXED=0|1
#   OUR_ESM_SEEN=0|1      OUR_DIVERGENT=<count>
set -u
MODE="${1:?usage: see the header}"; shift
V2DIR="$(cd "$(dirname "$0")" && pwd)"

ECO=""; PKG=""; VOLD=""; VNEW=""; OLDW=""; NEWW=""; FIXW=""; IMAGE=""; CMD=""; EV=""; TMO=1200
while [ $# -gt 0 ]; do
  case "$1" in
    --eco) ECO="$2"; shift 2;;
    --pkg) PKG="$2"; shift 2;;
    --old) VOLD="$2"; shift 2;;
    --new) VNEW="$2"; shift 2;;
    --old-work) OLDW="$2"; shift 2;;
    --new-work) NEWW="$2"; shift 2;;
    --fixed-work) FIXW="$2"; shift 2;;
    --image) IMAGE="$2"; shift 2;;
    --cmd) CMD="$2"; shift 2;;
    --ev) EV="$2"; shift 2;;
    --timeout) TMO="$2"; shift 2;;
    *) echo "diffexec_lockfile: unknown argument $1" >&2; exit 2;;
  esac
done
fail() { echo "DIFFEXEC_REBUILD_FAIL: $*"; exit 1; }
[ -n "$ECO" ] && [ -n "$PKG" ] && [ -n "$IMAGE" ] && [ -n "$CMD" ] && [ -n "$EV" ] || fail "missing arguments"

# Source gate (by construction): no path used for evidence generation may lie under cases/.
# The real guarantee is that the case directory is never mounted; this check stops a caller from
# passing the case directory as a workspace.
for _p in "$OLDW" "$NEWW" "$FIXW" "$EV"; do
  [ -n "$_p" ] || continue
  case "$(realpath -m -- "$_p")/" in
    *"/cases/"*) fail "no evidence path may lie under cases/ (source gate): $_p" ;;
  esac
done

mkdir -p "$EV/v2-runs"

# Package names for the recorder. npm: the package name itself. pip: the PyPI name may differ from
# the import name, so the name and its hyphen-to-underscore variant are both given; the plugin
# probes the installed directories.
trace_pkgs() {
  if [ "$ECO" = npm ]; then printf '%s' "$PKG"
  else printf '%s,%s' "$PKG" "$(printf '%s' "$PKG" | tr '-' '_')"; fi
}

# Self-check switch: point the recorder at a package that does not exist, to verify that the
# "tracer not attached" path is recognised by the caller and triggers the degradation. Prints
# loudly when set; results produced with it must never be used as experiment data.
if [ "${OUR_SELFCHECK_BREAK_TRACER:-0}" = 1 ]; then
  echo "############################################################"
  echo "!! OUR_SELFCHECK_BREAK_TRACER=1: the boundary-call recorder is deliberately pointed at a"
  echo "!! package that does not exist. Self-check only; this run must not be used as data."
  echo "############################################################"
fi
eff_pkgs() {
  if [ "${OUR_SELFCHECK_BREAK_TRACER:-0}" = 1 ]; then printf '%s' "__no_such_upstream_pkg__"
  else trace_pkgs; fi
}

# ---- one capture: <workspace> <capture output dir>
capture() {
  local work="$1" out="$2"
  [ -d "$work" ] || fail "workspace not found: $work"
  rm -rf "$out"; mkdir -p "$out/proc"
  if [ "$ECO" = npm ]; then
    bash "$V2DIR/js_trace_run.sh" --work "$work" --pkg "$(eff_pkgs)" \
      --cmd "$CMD" --out "$out" --image "$IMAGE" --timeout "$TMO" \
      || fail "js_trace_run failed ($work)"
  else
    # pip: the whole workspace is mounted at /work, the same layout the harness uses to run the
    # tests, so the shebangs inside the venv that name /work/.venv keep working.
    local cname="dxr-$$-$RANDOM"
    timeout -k 20 "$TMO" docker run --rm --name "$cname" --network none \
      -u "$(id -u):$(id -g)" \
      -v "$work":/work -v "$out":/algo-out -v "$V2DIR":/algo-v2:ro -w /work/repo \
      -e HOME=/work/.home -e TMPDIR=/work/.tmp -e CI=true -e PYTHONDONTWRITEBYTECODE=1 \
      -e NO_PROXY='*' -e PYTHONPATH=/algo-v2 \
      -e "PYTEST_ADDOPTS=-p py_boundary_trace" \
      -e "ALGO_TRACE_PKGS=$(eff_pkgs)" -e "ALGO_TRACE_OUT=/algo-out/trace.jsonl" \
      -e "ALGO_TRACE_MAX=${ALGO_TRACE_MAX:-4000}" \
      "$IMAGE" bash -c "export PATH=/work/.venv/bin:\$PATH; $CMD" \
      > "$out/test-output.txt" 2>&1
    echo $? > "$out/exit-code.txt"
    docker rm -f "$cname" >/dev/null 2>&1 || true
  fi
  # When the recorder did not attach, trace.jsonl may not exist at all. A missing file and
  # "attached but zero records" must be told apart, so an explicit _meta record is written here;
  # downstream must never read this as "no behavior difference".
  if [ ! -s "$out/trace.jsonl" ]; then
    printf '%s\n' '{"_meta":true,"attached":false,"recorded":0,"error":"no trace.jsonl was produced: the recorder wrote nothing, probably because injection failed or the process was killed"}' \
      > "$out/trace.jsonl"
  fi
}

attached_of() {  # <capture dir> -> 0/1
  python3 - "$1/trace.jsonl" <<'PY'
import json, sys
a = 0
try:
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("_meta") and r.get("attached"):
            a = 1
except OSError:
    pass
print(a)
PY
}
recorded_of() {  # <capture dir> -> number of boundary calls recorded on that side
  python3 - "$1/trace.jsonl" <<'PY'
import json, sys
c = 0
try:
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get("_meta"):
            c += 1
except OSError:
    pass
print(c)
PY
}
esm_of() {
  python3 - "$1/trace.jsonl" <<'PY'
import json, sys
e = 0
try:
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("_meta") and r.get("esm_seen"):
            e = 1
except OSError:
    pass
print(e)
PY
}

if [ "$MODE" = gen ]; then
  [ -n "$OLDW" ] && [ -n "$NEWW" ] || fail "gen needs --old-work and --new-work"
  echo ">>> [diffexec_lockfile] intact state ($PKG==$VOLD): running the tests and recording boundary calls"
  capture "$OLDW" "$EV/v2-runs/old"
  echo ">>> [diffexec_lockfile] broken state ($PKG==$VNEW): running the tests and recording boundary calls"
  capture "$NEWW" "$EV/v2-runs/new"
  AO="$(attached_of "$EV/v2-runs/old")"; AN="$(attached_of "$EV/v2-runs/new")"
  ES="$(esm_of "$EV/v2-runs/new")"
  DEC="javascript"; [ "$ECO" = pip ] && DEC="python"
  python3 "$V2DIR/behavior_diff.py" --eco "$DEC" \
    --old-dir "$EV/v2-runs/old" --new-dir "$EV/v2-runs/new" \
    --label-old "v_old ($PKG==$VOLD, pinned intact state, every other dependency unchanged)" \
    --label-new "v_new ($PKG==$VNEW, pinned broken state)" \
    --out "$EV/behavior-diff.md" --json "$EV/behavior-diff.json" || fail "behavior_diff gen failed"
  DV="$(python3 - "$EV/behavior-diff.json" <<'PY'
import json, sys
try:
    print(len(json.load(open(sys.argv[1], encoding="utf-8")).get("divergent_calls") or []))
except Exception:
    print(0)
PY
)"
  RO="$(recorded_of "$EV/v2-runs/old")"; RN="$(recorded_of "$EV/v2-runs/new")"
  echo "OUR_ATTACHED_OLD=$AO"
  echo "OUR_ATTACHED_NEW=$AN"
  echo "OUR_RECORDED_OLD=$RO"
  echo "OUR_RECORDED_NEW=$RN"
  echo "OUR_ESM_SEEN=$ES"
  echo "OUR_DIVERGENT=$DV"
  echo "DIFFEXEC_REBUILD_OK gen $EV"
elif [ "$MODE" = probe ]; then
  [ -n "$FIXW" ] || fail "probe needs --fixed-work"
  [ -d "$EV/v2-runs/new" ] || fail "no gen-mode capture to compare against"
  echo ">>> [diffexec_lockfile] repaired workspace in the broken-state environment (probe)"
  capture "$FIXW" "$EV/v2-runs/fixed"
  AF="$(attached_of "$EV/v2-runs/fixed")"
  DEC="javascript"; [ "$ECO" = pip ] && DEC="python"
  python3 "$V2DIR/behavior_diff.py" --mode probe --eco "$DEC" \
    --old-dir "$EV/v2-runs/old" --new-dir "$EV/v2-runs/new" \
    --fixed-dir "$EV/v2-runs/fixed" --out "$EV/probe-report.md" || fail "behavior_diff probe failed"
  echo "OUR_ATTACHED_FIXED=$AF"
  echo "DIFFEXEC_REBUILD_OK probe $EV"
else
  fail "unknown mode $MODE (gen|probe)"
fi
