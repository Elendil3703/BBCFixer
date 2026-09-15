#!/usr/bin/env bash
# Reproducible three-state harness for rebuilt real-downstream BBC cases.
#
# STATE-OLD  is pinned by cases/<case>/pinned/lock-OLD.json and installed with `npm ci`,
#            so the whole dependency tree is fixed and cannot drift over time.
# STATE-NEW  is built in one of two modes, recorded in cases/<case>/pinned/new-mode.txt:
#            swap      the trigger library carries no dependencies of its own, so the
#                      STATE-OLD tree is reused as is and only node_modules/<lib> is
#                      replaced by <lib>@<v_new>; every nested location listed in
#                      pinned/nested-old-copies.txt receives a byte copy of the STATE-OLD
#                      <lib>. Nothing else in the tree can move.
#            lockfile  the trigger library carries its own dependency closure, which the
#                      upgrade legitimately moves with it. STATE-NEW is then installed
#                      with `npm ci` from pinned/lock-NEW.json, and the difference against
#                      STATE-OLD is required to stay inside <lib> plus its installed
#                      dependency closure, recorded in pinned/closure-allowed.txt.
# STATE-FIXED is STATE-NEW plus reference_fix.patch.
#
# npm only grew the `ci` subcommand in 5.7, so a case whose runtime predates that has no
# way to run the command at all: a case pinned to node:6 has npm 3.10.10. For
# such a runtime the pinned lockfile is additionally written out as npm-shrinkwrap.json,
# which npm 3 honours exactly, and `npm install` is used in place of `npm ci`. Nothing
# about the pinning is relaxed: the installed inventory is still compared line by line
# against pinned/tree-OLD.txt and pinned/tree-NEW.txt, and any drift fails the case.
#
# The runtime is part of the pinned form, not a note in the margin. A downstream snapshot
# from 2015 is normally tested under the node of its own era; on a modern node its test
# framework may exit 0 without running a single test, which looks exactly like a pass. A
# case whose meta.json carries a runtime object therefore has every npm, node and test
# command executed inside that image, the observed interpreter is written to
# pinned/runtime.txt, and verify asserts the declared major version.
#
# Four things decide the verdict, and all four have teeth. Every one of them was added
# after a defect that produced no error at all, only a plausible-looking wrong answer.
#
#   three exit codes   STATE-OLD passes, STATE-NEW fails, STATE-FIXED passes.
#   answer leakage     logscan.py over the archived three-state logs.
#   patch landing      patchscope.py over reference_fix.patch: non-empty, and every file it edits
#                      is production code of the project under test.
#   real test runs     every one of the three states must show its test framework's own
#                      summary line with a non-zero count, and the fresh run must agree
#                      with the archived logs on those counts.
#
# The last one covers both halves of the same failure. A state that reports no summary is
# a state whose tests did not run: for STATE-OLD that is a pass that never happened, for
# STATE-NEW it is a fabricated break, which is worse than losing the case. Nothing in the
# exit code tells either apart from the real thing. A case whose framework genuinely
# prints no summary in some state must say so in meta.json under no_summary_states, with a
# reason; the default is failure, never a pass. Disagreement with the archive on the same
# counts is ARCHIVE_DRIFT and fails the case too, so a batch script that greps only for
# F1_OK cannot walk past a case that has quietly stopped reproducing.
#
# usage: rebuild_harness.sh <case-name> [pin|repin|verify] [swap|lockfile]
#   pin    regenerates cases/<case>/pinned/* from a fresh npm resolution; the third
#          argument selects the STATE-NEW mode and defaults to swap
#   repin  regenerates only the artifacts derived from an already fixed lockfile
#          (new-mode.txt, runtime.txt, tree-OLD.txt, tree-NEW.txt, treediff-OLD-NEW.txt,
#          treecheck.txt). lock-OLD.json, lock-NEW.json, package-*.json,
#          nested-old-copies.txt, closure-allowed.txt and newlib.sha512 are kept and
#          asserted exactly as in verify, so the dependency resolution cannot drift. Use
#          it to bring a case that was built outside this harness under the harness
#          without re-resolving its tree.
#   verify (default) runs the three states from the pinned artifacts and asserts them.
#          It never rewrites cases/<case>/pinned/STATE-*.log; it compares the fresh run
#          against that archive on test counts and fails the case on a mismatch.
#          Set ARCHIVE_OVERWRITE=1 to write the archive from a verify run deliberately.
set -u
. "$(cd "$(dirname "$0")" && pwd)/portable.sh"
export PATH="$HOME/.local/bin:$PATH"
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null || true
export NO_PROXY='*'

CASE="${1:?usage: rebuild_harness.sh <case-name> [pin|repin|verify] [swap|lockfile]}"
MODE="${2:-verify}"
BASE="$(cd "$(dirname "$0")" && pwd)"
# The cases root is parameterised so a copied case can be re-checked without touching the original.
CASES_ROOT="${CASES_ROOT:-$BASE/cases}"
CDIR="$CASES_ROOT/$CASE"
PIN="$CDIR/pinned"
# The workspace path is baked into test output, so it must not name the trigger library
# or the offending method. Use a neutral, deterministic id derived from the case name.
WORKID="wk-$(printf '%s' "$CASE" | sha256sum | cut -c1-12)"
# The work root can be set with WORK_ROOT. Sharing one root lets the cleanup steps of
# different runners collide, so the root is parameterised instead of forking the harness.
WORK_ROOT="${WORK_ROOT:-$BASE/work-F1}"
WORK="$WORK_ROOT/$WORKID"
RUN="$WORK_ROOT/out/F1_$CASE"
TIMEOUT="${TEST_TIMEOUT:-1200}"

log() { echo "=== $*"; }
fail() { echo "HARNESS_FAIL: $*"; exit 1; }

case "$MODE" in pin|repin|verify) ;; *) fail "unknown mode $MODE (pin|repin|verify)" ;; esac
# repin regenerates the derived artifacts but asserts everything else exactly like verify
WRITE_TREES=0
[ "$MODE" = pin ] || [ "$MODE" = repin ] && WRITE_TREES=1

mkdir -p "$PIN" "$RUN"
rm -rf "$WORK"; mkdir -p "$WORK"

if [ "$WRITE_TREES" = 1 ]; then
  NEWMODE="${3:-$(cat "$PIN/new-mode.txt" 2>/dev/null || echo swap)}"
  echo "$NEWMODE" > "$PIN/new-mode.txt"
else
  NEWMODE="$(cat "$PIN/new-mode.txt" 2>/dev/null || echo swap)"
fi
case "$NEWMODE" in swap|lockfile) ;; *) fail "unknown STATE-NEW mode $NEWMODE" ;; esac

eval "$(python3 - "$CDIR/meta.json" <<'PY'
import json, sys, shlex
m = json.load(open(sys.argv[1], encoding='utf-8'))
rt = m.get('runtime') or {}
amb = m.get('ambient') or {}
env = amb.get('env') or {}
print('AMBENV=(%s)' % ' '.join(
    shlex.quote('%s=%s' % kv) for kv in sorted(env.items())))
print('RTDEPS=(%s)' % ' '.join(
    shlex.quote(str(x)) for x in (m.get('runtime_deps') or [])))
for k, v in (('AMBDECLARED', '1' if amb else '0'),
             ('AMBDIM', amb.get('dimension', '')),
             ('AMBWHY', amb.get('why', '')),
             ('AMBCLASS', amb.get('natural_class', '')),
             ('AMBUNCON', amb.get('downstream_unconstrained', ''))):
    print('%s=%s' % (k, shlex.quote(str(v))))
for k, v in (('SLUG', m['repo']), ('COMMIT', m['commit']), ('LIB', m['lib']),
             ('VOLD', m['v_old']), ('VNEW', m['v_new']),
             ('TESTCMD', m.get('test_cmd', 'npm test')),
             ('RTIMAGE', rt.get('image', '')),
             ('RTKIND', rt.get('kind', '')),
             ('RTMAJOR', rt.get('node_major', '')),
             ('PYMINOR', rt.get('python_minor', ''))):
    print('%s=%s' % (k, shlex.quote(str(v))))
PY
)"

# ------------------------------------------------------------------ ecosystem
# npm and pip install their trees and count tests differently. The ecosystem is decided by
# meta.json runtime: node_major means npm, python_minor means pip. Cases without a runtime
# default to npm. Declaring both is contradictory and is rejected rather than guessed.
ECO=npm
if [ -n "$PYMINOR" ] && [ -n "$RTMAJOR" ]; then
  fail "meta.json runtime declares both node_major and python_minor; cannot decide the ecosystem"
elif [ -n "$PYMINOR" ]; then
  ECO=pip
fi

# ------------------------------------------------------- runtime, a pinned condition
# With no runtime declared the case runs on the host interpreter, which is how the older
# cases work. With one declared, every npm / node / test invocation is
# executed inside that image instead, so the interpreter cannot silently become whatever
# the host happens to carry today.
HOMEDIR="$WORK/rt-home"
mkdir -p "$HOMEDIR"
# A runtime object with no image would silently drop the case back onto the host node,
# which is exactly the failure the runtime condition exists to prevent. Say so instead.
[ -z "$RTKIND" ] || [ -n "$RTIMAGE" ] \
  || fail "meta.json declares runtime.kind=$RTKIND but no runtime.image"
if [ -n "$RTIMAGE" ]; then
  docker image inspect "$RTIMAGE" >/dev/null 2>&1 \
    || docker pull "$RTIMAGE" >/dev/null 2>&1 \
    || fail "runtime image $RTIMAGE is not available"
fi

# ------------------------------------------------- ambient, a pinned condition too
# The ambient condition (timezone, locale) is treated exactly like the runtime: it is part
# of the pinned form, not a note in the margin, and it is injected at the single choke
# point below so that the three states cannot drift apart. AMBIENT_OFF=1 runs the required
# default-environment control instead; that run may not write pinned artifacts, because the
# artifacts describe the declared condition and not the control.
AMBOFF="${AMBIENT_OFF:-0}"
if [ "$AMBDECLARED" = 1 ]; then
  [ ${#AMBENV[@]} -gt 0 ] || fail "meta.json declares ambient but ambient.env is empty"
  [ -n "$AMBDIM" ]   || fail "meta.json declares ambient but no ambient.dimension"
  [ -n "$AMBWHY" ] && [ "$AMBWHY" != TODO ] \
    || fail "meta.json declares ambient but ambient.why is empty or still TODO"
  [ -n "$AMBCLASS" ] || fail "meta.json declares ambient but no ambient.natural_class"
  [ -n "$AMBUNCON" ] || fail "meta.json declares ambient but no ambient.downstream_unconstrained"
  for kv in "${AMBENV[@]}"; do
    case "$kv" in *=*) ;; *) fail "ambient.env entry is not KEY=VALUE: $kv" ;; esac
  done
fi
AMBDOCKER=(); AMBHOST=()
if [ "$AMBDECLARED" = 1 ] && [ "$AMBOFF" != 1 ]; then
  for kv in "${AMBENV[@]}"; do AMBDOCKER+=(-e "$kv"); AMBHOST+=("$kv"); done
fi
if [ "$AMBDECLARED" = 1 ] && [ "$AMBOFF" = 1 ] && [ "$WRITE_TREES" = 1 ]; then
  fail "AMBIENT_OFF=1 is the default-environment control and may not write pinned artifacts"
fi
AMBLINE="none"
[ "$AMBDECLARED" = 1 ] && AMBLINE="$AMBDIM	${AMBENV[*]}"
[ "$AMBDECLARED" = 1 ] && [ "$AMBOFF" = 1 ] && AMBLINE="$AMBDIM	CONTROL-DEFAULT-ENV"

# rt <net|nonet> <seconds|0> <shell command>
#   Runs the command in the case's runtime, rooted at the repository checkout. `nonet`
#   isolates the network the way the host path always has. A non-zero timeout is enforced
#   by the harness, and in the container case the container is removed by name afterwards
#   so that a killed client cannot leave the work orphaned.
rt() {
  local net="$1" secs="$2"; shift 2
  local cmd="$*"
  local tmo=(); [ "$secs" != 0 ] && tmo=(timeout -k 20 "$secs")
  if [ -z "$RTIMAGE" ]; then
    local hostenv=(); [ ${#AMBHOST[@]} -gt 0 ] && hostenv=(env "${AMBHOST[@]}")
    if [ "$net" = nonet ]; then
      "${tmo[@]}" unshare -r -n -- "${hostenv[@]}" bash -c 'ip link set lo up 2>/dev/null; cd '"$PWD"'; '"$cmd"
    else
      "${tmo[@]}" "${hostenv[@]}" bash -c "cd $PWD; $cmd"
    fi
    return $?
  fi
  local netarg=(); [ "$net" = nonet ] && netarg=(--network none)
  local cname="rt-$WORKID-$$-$RANDOM"
  "${tmo[@]}" docker run --rm --name "$cname" "${netarg[@]}" \
    -u "$(id -u):$(id -g)" \
    -v "$WORK/src":/w -v "$HOMEDIR":/rt-home \
    -w /w -e HOME=/rt-home -e npm_config_cache=/rt-home/.npm \
    -e NO_PROXY='*' "${AMBDOCKER[@]}" \
    "$RTIMAGE" bash -c "$cmd"
  local rc=$?
  docker rm -f "$cname" >/dev/null 2>&1 || true
  return $rc
}

# ------------------------------------------- runtime_deps, a pinned condition as well
# A reference fix sometimes needs a runtime dependency the downstream project does not
# declare, for example a package that exists in its tree only as a transitive dependency of
# the very library being upgraded. Letting reference_fix.patch add that declaration
# would make STATE-NEW and STATE-FIXED run against different dependency trees, and the
# scoring would no longer compare like with like. Such a dependency is therefore declared
# in meta.json under runtime_deps as pkg@exact-version, written into package.json before
# the STATE-OLD tree is resolved, and hence installed in all three states from the pinned
# lockfiles. Every state asserts it is present at exactly the pinned version.
assert_runtime_deps() {
  local tag="$1" d p v got
  [ ${#RTDEPS[@]} -gt 0 ] || return 0
  for d in "${RTDEPS[@]}"; do
    p="${d%@*}"; v="${d##*@}"
    [ -n "$p" ] && [ -n "$v" ] && [ "$p" != "$d" ] \
      || fail "runtime_deps entry is not pkg@version: $d"
    [ -d "node_modules/$p" ] \
      || fail "$tag: runtime dep $p declared in meta.json is not installed"
    got=$(node -e "console.log(require('./node_modules/$p/package.json').version)" 2>/dev/null)
    [ "$got" = "$v" ] || fail "$tag: runtime dep $p is $got, meta.json pins $v"
  done
  log "$tag: runtime_deps installed at their pinned versions: ${RTDEPS[*]}"
}

# Summary-line forms recognised by counts(), all printed by the test framework itself:
# mocha `23 passing`, raw TAP `# tests 23` / `# pass 23` / `# fail 10`, ava
# `21 tests passed` / `21 tests failed`, vitest `Test Files  1 failed | 4 passed (5)` and
# `Tests  1 failed | 35 passed (42)`. Each pattern is anchored at both ends so that
# restatements such as `Error: 18 tests failed.`, duration lines and per-file progress
# lines are not counted.
# Test counts as the framework itself reports them. An old test framework under a modern
# node commonly exits 0 having run nothing at all, which is indistinguishable from a pass
# unless the summary line is read. npm's own banner and command echo are stripped first so
# that a package called <something>-test cannot be mistaken for a result line. Only the
# counts are extracted, never the durations beside them, because the same run repeated
# reports a different elapsed time every time and would otherwise read as a difference.
# uvu prints `  Total:     3` / `  Passed:    3` / `  Skipped:   0` on separate lines; the
# pattern is anchored at both ends so that `Duration: 12120.62ms` is not counted.
counts() {
  # pytest's summary looks like `144 passed, 1 warning in 0.21s`, possibly wrapped in `====`.
  # After stripping ANSI, only the last summary-like line (wrapped in `=` or ending in
  # ` in <seconds>s`) is used, and the counts are taken from that line. Anchoring on the
  # summary line is required, otherwise a test's own "3 passed" output would be counted.
  # Only the pip form takes this path; the npm patterns are unchanged.
  if [ "${ECO:-npm}" = pip ]; then
    sed -e 's/\x1b\[[0-9;]*m//g' "$1" \
      | grep -aE '(^=+.*=+$)|( in [0-9.]+s)' \
      | grep -aE '[0-9]+ (passed|failed|errors?|skipped|xfailed|xpassed|deselected)' \
      | tail -1 \
      | grep -aoE '[0-9]+ (passed|failed|errors?|skipped|xfailed|xpassed|deselected)' \
      | sort | tr '\n' ' '
    return
  fi
  sed -e 's/\x1b\[[0-9;]*m//g' "$1" \
    | grep -av -E '^> ' \
    | grep -aoE "[0-9]+ (passing|failing|pending)|[0-9]+ specs?, [0-9]+ failures?|Tests:[^,]*(passed|failed)[^,]*|passing: +[0-9]+|failing: +[0-9]+|OK: [0-9]+ assertions|FAILURES: [0-9]+/[0-9]+ assertions failed|^# (tests|pass|fail) +[0-9]+|^ *[0-9]+ tests? (passed|failed)$|^ *(Test Files|Tests) +[0-9]+ [^(]*\([0-9]+\)$|\\| +[0-9]+ +\\| +[0-9]+ +\\| +[0-9]+ +\\||^ *(Total|Passed|Skipped): +[0-9]+$" \
    | sort | tr '\n' ' '
}

# Compare the fresh run against the archived logs on the part that carries meaning, the
# counts the framework itself reports. Timestamps, process ids, elapsed readings, jest's
# file ordering and the iat / exp claims of a freshly minted JWT differ on every single
# run and are not differences. A missing archive is a difference: a case with nothing to
# compare against has no evidence left that it still reproduces what it was built for.
# Sets DRIFT.
archive_drift() {
  DRIFT=0
  local t a b
  for t in STATE-OLD STATE-NEW STATE-FIXED; do
    if [ ! -f "$PIN/$t.log" ]; then
      DRIFT=1
      log "ARCHIVE_DRIFT $t has no archived log to compare against"
      continue
    fi
    a=$(counts "$PIN/$t.log")
    b=$(counts "$RUN/$t.log")
    if [ "$a" = "$b" ]; then
      log "$t matches the archive on test counts [$a]"
    else
      DRIFT=1
      log "ARCHIVE_DRIFT $t archived=[$a] fresh=[$b]"
    fi
  done
}

# Each state runs on a clean workspace. STATE-OLD tests often leave artefacts covered by
# .gitignore; without restoring, a case that only passes thanks to stale artefacts would be
# accepted. The snapshot is taken right before the test run, not after checkout, so the
# restored workspace is the delivered form. node_modules is excluded and left to each
# state's own install step, so swap mode is unaffected.
WSBK="$WORK/ws-backup"
ws_snapshot() {
  rm -rf "$WSBK"; mkdir -p "$WSBK"
  ( cd "$WORK/src" && tar cf - --exclude=./node_modules . ) | ( cd "$WSBK" && tar xf - )
}
ws_restore() {
  [ -d "$WSBK" ] || return 0
  ( cd "$WORK/src" && find . -mindepth 1 -maxdepth 1 ! -name node_modules -exec rm -rf {} + )
  ( cd "$WSBK" && tar cf - . ) | ( cd "$WORK/src" && tar xf - )
}
runtests() {
  local tag="$1"
  log "$tag run: $TESTCMD"
  rt nonet "$TIMEOUT" "$TESTCMD" > "$RUN/${tag}.log" 2>&1
  local rc=$?
  echo "$tag rc=$rc" | tee -a "$RUN/${tag}.log"
  grep -E "passing|failing|pending|Tests:|Test Suites:|specs," "$RUN/${tag}.log" | tail -6
  log "$tag counts: [$(counts "$RUN/${tag}.log")]"
  eval "RC_${tag//-/_}=$rc"
}

nosummary_reason() {
  python3 - "$CDIR/meta.json" "$1" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding='utf-8'))
print(str(((m.get('no_summary_states') or {}).get(sys.argv[2]) or '')).strip())
PY
}
summary_gate() {
  EMPTYPASS=0
  local t cs why
  for t in STATE-OLD STATE-NEW STATE-FIXED; do
    cs=$(counts "$RUN/$t.log")
    why=$(nosummary_reason "$t")
    if [ -n "$cs" ] && printf '%s' "$cs" | grep -qE '[1-9]'; then
      if [ -n "$why" ]; then
        EMPTYPASS=1
        echo "EMPTY_PASS $CASE: $t is registered in no_summary_states but did report [$cs]; re-record"
      else
        log "$t really ran tests: [$cs]"
      fi
    elif [ -n "$why" ] && [ "$(printf '%s' "$why" | tr '[:lower:]' '[:upper:]')" != "TODO" ]; then
      log "$t reports no framework summary [$cs]; registered in no_summary_states: $why"
    else
      EMPTYPASS=1
      echo "EMPTY_PASS $CASE: $t reports no test framework summary with a non-zero count [$cs] and carries no justified no_summary_states entry"
    fi
  done
}

log "case=$CASE mode=$MODE state-new-mode=$NEWMODE repo=$SLUG commit=$COMMIT lib=$LIB $VOLD -> $VNEW"
log "disk before: $(df -h / | tail -1)"
log "host node $(node -v) npm $(npm -v)"

cd "$WORK" || fail "no workdir"
git clone -q "https://github.com/$SLUG.git" src >/dev/null 2>&1 || fail "clone"
cd src || fail "cd src"
git checkout -q "$COMMIT" || fail "checkout"
log "HEAD $(git rev-parse HEAD)"

# ------------------------------------------------- record and assert the actual runtime
# The pip form records the interpreter version, for the same reason as the npm side: the
# interpreter is a pinned condition, not a note. pinned/runtime.txt holds `python -V` verbatim.
RTNPM=""
if [ "$ECO" = pip ]; then
  RTPY=$(rt net "${RT_PROBE_TIMEOUT:-900}" 'python -V 2>&1' 2>/dev/null | tr -d '\r' | grep -a "^Python " | tail -1)
  [ -n "$RTPY" ] || fail "cannot read python -V inside $RTIMAGE"
  RTLINE="$RTPY"
  GOTMINOR=$(printf '%s' "$RTPY" | sed 's/^Python //; s/^\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/')
  if [ -n "$PYMINOR" ] && [ "$GOTMINOR" != "$PYMINOR" ]; then
    fail "runtime $RTIMAGE reports $RTPY, meta.json declares python_minor $PYMINOR"
  fi
fi
if [ "$ECO" != pip ]; then
RTNPM="$(npm -v)"
RTLINE="host	node $(node -v)	npm $RTNPM"
fi
if [ "$ECO" != pip ] && [ -n "$RTIMAGE" ]; then
  # The probe has to survive a loaded host. npm 3 inside node:6 has been measured at
  # 79 s for `npm -v` alone while the machine was busy; at the former 120 s ceiling the
  # probe times out, returns the empty string, and that empty version is written into
  # pinned/runtime.txt as the pinned truth. A version that cannot be read is therefore a
  # failure rather than a blank.
  RTPROBE="${RT_PROBE_TIMEOUT:-900}"
  RTNODE=$(rt net "$RTPROBE" 'node -v' 2>/dev/null | tr -d '\r')
  RTNPM=$(rt net "$RTPROBE" 'npm -v' 2>/dev/null | tr -d '\r')
  [ -n "$RTNODE" ] || fail "cannot read node -v inside $RTIMAGE"
  [ -n "$RTNPM" ] || fail "cannot read npm -v inside $RTIMAGE"
  RTLINE="$RTIMAGE	node $RTNODE	npm $RTNPM"
  GOTMAJOR=$(printf '%s' "$RTNODE" | sed 's/^v//; s/\..*//')
  if [ -n "$RTMAJOR" ] && [ "$GOTMAJOR" != "$RTMAJOR" ]; then
    fail "runtime $RTIMAGE reports node $RTNODE, meta.json declares node_major $RTMAJOR"
  fi
fi
log "runtime: $RTLINE"
# npm gained the ci subcommand in 5.7. A case pinned to an older runtime does not have it
# at all, and the harness would otherwise fail such a case for a reason that has nothing
# to do with the case: a case on node:6 has npm 3.10.10. For those runtimes
# the pinned lockfile is additionally written out as npm-shrinkwrap.json, which npm 3
# honours exactly, and the install is performed with npm install. The installed tree is
# still compared line by line against pinned/tree-OLD.txt and pinned/tree-NEW.txt, so what
# changes is the command, not the strictness of the pinning.
NPM_MAJOR="$(printf '%s' "$RTNPM" | sed 's/^[^0-9]*//; s/[.].*$//')"
case "$NPM_MAJOR" in ''|*[!0-9]*) NPM_MAJOR=99 ;; esac
LEGACY_NPM=0
CI_INSTALL='npm ci --ignore-scripts --no-audit --no-fund'
if [ "$NPM_MAJOR" -lt 5 ]; then
  LEGACY_NPM=1
  CI_INSTALL='npm install --ignore-scripts --no-audit --no-fund'
  log "runtime npm $RTNPM predates npm ci; the pinned lockfile is installed as npm-shrinkwrap.json with npm install"
fi
# A few downstream projects declare peer conflicts of their own (unrelated to this upgrade),
# which today's npm rejects with ERESOLVE. Such cases put the needed install flags in
# pinned/install-flags.txt; the same flags apply to all three states, so they are not a
# difference between states. Without the file the behaviour is unchanged.
if [ -s "$PIN/install-flags.txt" ]; then
  EXTRA_FLAGS="$(tr -s '[:space:]' ' ' < "$PIN/install-flags.txt" | sed 's/  */ /g; s/^ //; s/ $//')"
  CI_INSTALL="$CI_INSTALL $EXTRA_FLAGS"
  log "install flags from pinned/install-flags.txt: $EXTRA_FLAGS"
fi
if [ "$WRITE_TREES" = 1 ]; then
  printf '%s\n' "$RTLINE" > "$PIN/runtime.txt"
else
  if [ -f "$PIN/runtime.txt" ]; then
    [ "$(cat "$PIN/runtime.txt")" = "$RTLINE" ] \
      && log "runtime matches pinned/runtime.txt exactly" \
      || fail "runtime drifted: pinned [$(cat "$PIN/runtime.txt")] now [$RTLINE]"
  else
    log "no pinned/runtime.txt for this case (built before the runtime became a pinned condition)"
  fi
fi

log "ambient: $AMBLINE"
if [ "$WRITE_TREES" = 1 ]; then
  printf '%s\n' "$AMBLINE" > "$PIN/ambient.txt"
elif [ "$AMBOFF" = 1 ]; then
  log "ambient comparison skipped: this is the default-environment control run"
elif [ -f "$PIN/ambient.txt" ]; then
  [ "$(cat "$PIN/ambient.txt")" = "$AMBLINE" ] \
    && log "ambient matches pinned/ambient.txt exactly" \
    || fail "ambient drifted: pinned [$(cat "$PIN/ambient.txt")] now [$AMBLINE]"
elif [ "$AMBDECLARED" = 1 ]; then
  fail "meta.json declares ambient but pinned/ambient.txt is missing"
else
  log "no ambient condition declared for this case"
fi


# ================================================================= pip (PyPI) form
# The pip branch is the npm branch with two things replaced and nothing relaxed.
#
#   installing the tree   pinned/lock-OLD.txt and pinned/lock-NEW.txt are `pip freeze --all`
#                         listings. Each state installs from its own list with
#                         `pip install --no-deps -r`, inside the image declared in
#                         meta.json.runtime, then freezes the result and compares it against
#                         the pinned list line by line (pipcmp.py, which normalises
#                         distribution names per PEP 503 first: pip does not echo back the
#                         spelling it was given, so a literal comparison reports a perfectly
#                         good tree as dozens of missing packages). What moved between
#                         STATE-OLD and STATE-NEW must lie inside pinned/closure-allowed.txt
#                         (pipdrift.py), which is the pip form of the npm closure check.
#   counting tests        the framework summary is pytest's own, not mocha's; counts() reads
#                         it under ECO=pip.
#
# All four machine gates are applied exactly as on the npm side: logscan over the archived
# three-state logs, patchscope over reference_fix.patch, archive drift on test counts, EMPTY_PASS on
# a state whose tests did not really run.
#
# One thing is deliberately stricter than the npm branch. npm runs the three states in a
# single checkout, so anything STATE-OLD's tests leave behind shapes the two states that
# follow, and if those artefacts are covered by .gitignore then `git status --porcelain`
# cannot see them either; one case has already been rejected for exactly that. Here every
# state starts from `git checkout -- . && git clean -xfd` and from a venv rebuilt from
# scratch, so no state can inherit the residue of another. SKIP_STATE_OLD=1 additionally
# runs only STATE-NEW and STATE-FIXED, which is the independent clean-checkout re-check the
# rebuild protocol requires; it may not write pinned artefacts.
if [ "$ECO" = pip ]; then
  [ -n "$RTIMAGE" ] || fail "the pip form requires runtime.image in meta.json"
  [ -f "$PIN/lock-OLD.txt" ] || fail "missing $PIN/lock-OLD.txt (it defines the STATE-OLD dependency tree in the pip form)"
  [ -f "$PIN/lock-NEW.txt" ] || fail "missing $PIN/lock-NEW.txt (it defines the STATE-NEW dependency tree in the pip form)"
  VENV="$WORK/venv"; PIPCACHE="$WORK/pipcache"
  mkdir -p "$VENV" "$PIPCACHE" "$HOMEDIR/tmp"
  SKIPOLD="${SKIP_STATE_OLD:-0}"
  if [ "$SKIPOLD" = 1 ] && [ "$WRITE_TREES" = 1 ]; then
    fail "SKIP_STATE_OLD=1 is the independent clean-checkout re-check and may not write pinned artefacts"
  fi

  # pip_rt <net|nonet> <seconds|0> <command>
  #   Same contract as rt(): run inside the declared image, rooted at the checkout, with the
  #   network isolated for test runs. The venv lives outside the checkout so that
  #   `git clean -xfd` between states cannot delete it and so that it never shows up in
  #   `git status --porcelain`.
  pip_rt() {
    local net="$1" secs="$2"; shift 2
    local cmd="$*"
    local tmo=(); [ "$secs" != 0 ] && tmo=(timeout -k 20 "$secs")
    local netarg=(); [ "$net" = nonet ] && netarg=(--network none)
    local cname="rt-$WORKID-$$-$RANDOM"
    "${tmo[@]}" docker run --rm --name "$cname" "${netarg[@]}" \
      -u "$(id -u):$(id -g)" \
      -v "$WORK/src":/w -v "$VENV":/venv -v "$HOMEDIR":/rt-home -v "$PIPCACHE":/pipcache \
      -w /w -e HOME=/rt-home -e TMPDIR=/rt-home/tmp \
      -e PIP_CACHE_DIR=/pipcache -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
      -e PYTHONDONTWRITEBYTECODE=1 -e NO_PROXY='*' "${AMBDOCKER[@]}" \
      "$RTIMAGE" bash -c "export PATH=/venv/bin:\$PATH; $cmd"
    local rc=$?
    docker rm -f "$cname" >/dev/null 2>&1 || true
    return $rc
  }

  # pip_state <STATE-tag> <OLD|NEW> <patch|nopatch>
  #   Builds one state from nothing: clean checkout, fresh venv from the named lock file,
  #   reference_fix.patch if this is STATE-FIXED, editable install of the project under test, then
  #   the assertions and the test run. The three states differ only in the lock file and
  #   in whether the patch is applied.
  pip_state() {
    local tag="$1" which="$2" dopatch="$3"
    local lock="$PIN/lock-$which.txt"
    local want="$VOLD"; [ "$which" = NEW ] && want="$VNEW"

    log "$tag: restoring a clean checkout"
    git checkout -q -- . || fail "$tag: git checkout -- . failed"
    git clean -qxfd || fail "$tag: git clean -xfd failed"

    if [ "$dopatch" = patch ]; then
      log "applying reference_fix.patch"
      git apply --check "$CDIR/reference_fix.patch" || fail "reference_fix.patch does not apply"
      git apply "$CDIR/reference_fix.patch" || fail "reference_fix.patch apply"
      git diff --name-only | tee "$RUN/patched-files.txt"
    fi

    log "$tag: installing the dependency tree from pinned/lock-$which.txt"
    rm -rf "$VENV"; mkdir -p "$VENV"
    grep -v '^-e ' "$lock" | grep -v '^[[:space:]]*$' > "$HOMEDIR/deps-$which.txt"
    pip_rt net 2400 "python -m venv /venv && /venv/bin/pip -q install -U pip setuptools wheel && /venv/bin/pip install --no-deps -r /rt-home/deps-$which.txt" \
      > "$RUN/install-$tag.log" 2>&1 \
      || fail "$tag: installing the dependency tree from pinned/lock-$which.txt failed, see $RUN/install-$tag.log"
    if grep -q '^-e ' "$lock"; then
      pip_rt net 900 'pip install -e . --no-deps' >> "$RUN/install-$tag.log" 2>&1 \
        || fail "$tag: editable install of the project under test failed, see $RUN/install-$tag.log"
    fi

    local got
    got=$(pip_rt nonet 300 "python -c \"import importlib.metadata as m; print(m.version('$LIB'))\"" 2>/dev/null | tr -d '\r' | tail -1)
    [ "$got" = "$want" ] || fail "$tag: $LIB installed as [$got], meta.json records $want"
    log "$tag: $LIB = $got"

    pip_rt nonet 300 'pip freeze --all' 2>/dev/null | tr -d '\r' | sed 's/[[:space:]]*$//' \
      | grep -v '^$' | sort > "$RUN/tree-$tag.txt"
    [ -s "$RUN/tree-$tag.txt" ] || fail "$tag: pip freeze produced no listing"
    python3 "$BASE/pipcmp.py" "$lock" "$RUN/tree-$tag.txt" "$SLUG" | tee "$RUN/pipcmp-$tag.txt"
    [ "${PIPESTATUS[0]}" = 0 ] \
      || fail "$tag: the installed listing differs from pinned/lock-$which.txt, see $RUN/pipcmp-$tag.txt"

    # tree-OLD.txt / tree-NEW.txt are the pinned inventories, written by pin and repin and
    # asserted line by line by verify. A case built before they existed carries only the
    # lock files; the comparison above already covers it, and repin brings it up to date.
    if [ "$dopatch" != patch ]; then
      if [ "$WRITE_TREES" = 1 ]; then
        cp "$RUN/tree-$tag.txt" "$PIN/tree-$which.txt"
      elif [ -f "$PIN/tree-$which.txt" ]; then
        diff "$PIN/tree-$which.txt" "$RUN/tree-$tag.txt" > "$RUN/treediff-$which.txt" \
          && log "$tag tree matches pinned/tree-$which.txt line by line" \
          || fail "$tag tree differs from pinned/tree-$which.txt, see $RUN/treediff-$which.txt"
      else
        log "no pinned/tree-$which.txt for this case yet (run repin to generate it); this run checks line by line against pinned/lock-$which.txt"
      fi
    fi

    log "$tag: tracked files that differ from the pinned commit:"
    git status --porcelain

    log "$tag run: $TESTCMD"
    pip_rt nonet "$TIMEOUT" "$TESTCMD" > "$RUN/${tag}.log" 2>&1
    local rc=$?
    echo "$tag rc=$rc" | tee -a "$RUN/${tag}.log"
    tail -4 "$RUN/${tag}.log"
    log "$tag counts: [$(counts "$RUN/${tag}.log")]"
    eval "RC_${tag//-/_}=$rc"
  }

  RC_STATE_OLD=0
  if [ "$SKIPOLD" = 1 ]; then
    log "SKIP_STATE_OLD=1: only STATE-NEW and STATE-FIXED are built on a clean checkout; STATE-OLD is not run"
  else
    pip_state STATE-OLD OLD nopatch
  fi

  # the patch-landing gate is asserted before reference_fix.patch is ever applied, as on the npm side
  log "patch-landing gate on reference_fix.patch"
  python3 "$BASE/patchscope.py" "$CDIR" | tee "$RUN/patchscope.txt"
  PSRC=${PIPESTATUS[0]}
  [ "$PSRC" = 0 ] || echo "PATCHSCOPE_GATE_FAILED $CASE"

  pip_state STATE-NEW NEW nopatch

  [ -f "$PIN/closure-allowed.txt" ] || echo "$LIB" > "$PIN/closure-allowed.txt"
  if [ "$SKIPOLD" != 1 ]; then
    log "packages that moved between STATE-OLD and STATE-NEW:"
    python3 "$BASE/pipdrift.py" "$RUN/tree-STATE-OLD.txt" "$RUN/tree-STATE-NEW.txt" \
      "$PIN/closure-allowed.txt" "$SLUG" > "$RUN/treediff-OLD-NEW.txt" 2>&1
    TCRC=$?
    cat "$RUN/treediff-OLD-NEW.txt"
    [ "$TCRC" = 0 ] || fail "a package outside closure-allowed.txt moved between STATE-OLD and STATE-NEW"
    [ "$WRITE_TREES" = 1 ] && cp "$RUN/treediff-OLD-NEW.txt" "$PIN/treediff-OLD-NEW.txt"
  fi

  pip_state STATE-FIXED NEW patch

  log "cleanup"
  rm -rf "$VENV" "$PIPCACHE"
  log "disk after: $(df -h / | tail -1)"

  log "answer-leakage gate on the archived three-state logs"
  DRIFT=0
  if [ "$SKIPOLD" = 1 ]; then
    log "SKIP_STATE_OLD=1: no comparison against the archived logs (STATE-OLD did not run) and no archive written"
  elif [ "$MODE" = pin ] || [ "${ARCHIVE_OVERWRITE:-0}" = "1" ]; then
    for t in STATE-OLD STATE-NEW STATE-FIXED; do cp "$RUN/$t.log" "$PIN/$t.log"; done
    log "archived three-state logs written (mode=$MODE)"
  else
    archive_drift
    [ "$DRIFT" = 0 ] || echo "ARCHIVE_DRIFT $CASE: fresh run disagrees with the archived logs on test counts"
  fi

  EMPTYPASS=0
  if [ "$SKIPOLD" = 1 ]; then
    for t in STATE-NEW STATE-FIXED; do
      cs=$(counts "$RUN/$t.log")
      if [ -n "$cs" ] && printf '%s' "$cs" | grep -qE '[1-9]'; then
        log "$t really ran tests: [$cs]"
      else
        EMPTYPASS=1
        echo "EMPTY_PASS $CASE: $t reports no test framework summary with a non-zero count [$cs]"
      fi
    done
  else
    summary_gate
  fi

  if [ "$MODE" = pin ] || [ "${LOGSCAN_RECORD:-0}" = "1" ]; then
    python3 "$BASE/logscan.py" "$CDIR" --record --workroot "$(basename "$WORK_ROOT")" >/dev/null 2>&1 || true
  fi
  LSRC=0
  if [ "$SKIPOLD" = 1 ]; then
    log "SKIP_STATE_OLD=1: logscan covers the archived logs and is not re-applied in this run"
  else
    python3 "$BASE/logscan.py" "$CDIR" --workroot "$(basename "$WORK_ROOT")"
    LSRC=$?
  fi
  echo "VERDICT $CASE: STATE-OLD rc=$RC_STATE_OLD STATE-NEW rc=$RC_STATE_NEW STATE-FIXED rc=$RC_STATE_FIXED logscan=$LSRC patchscope=$PSRC drift=$DRIFT emptypass=$EMPTYPASS runtime=$RTLINE ambient=$AMBLINE eco=pip skip_old=$SKIPOLD"
  [ "$LSRC" = "0" ] || echo "LOGSCAN_GATE_FAILED $CASE"
  if [ "$RC_STATE_OLD" = 0 ] && [ "$RC_STATE_NEW" != 0 ] && [ "$RC_STATE_FIXED" = 0 ] \
     && [ "$LSRC" = 0 ] && [ "$PSRC" = 0 ] && [ "$EMPTYPASS" = 0 ] && [ "$DRIFT" = 0 ]; then
    echo "F1_OK $CASE"
  else
    echo "F1_BAD $CASE"
  fi
  echo "F1_DONE $CASE"
  exit 0
fi

# ---------------------------------------------------------------- STATE-OLD
if [ "$MODE" = pin ]; then
  log "resolving STATE-OLD tree from the repository's own declaration"
  if [ ${#RTDEPS[@]} -gt 0 ]; then
    log "declaring runtime_deps in package.json before the resolution: ${RTDEPS[*]}"
    python3 - package.json "${RTDEPS[@]}" <<'PYRTD'
import io, json, sys
path = sys.argv[1]
m = json.load(io.open(path, encoding='utf-8'))
deps = m.setdefault('dependencies', {})
for spec in sys.argv[2:]:
    i = spec.rindex('@')
    name, ver = spec[:i], spec[i + 1:]
    if not name or not ver:
        raise SystemExit('runtime_deps entry is not pkg@version: %s' % spec)
    if name in deps:
        raise SystemExit(
            'runtime_deps %s is already a declared dependency (%s); '
            'runtime_deps is only for dependencies the downstream does not declare'
            % (name, deps[name]))
    deps[name] = ver
io.open(path, 'w', encoding='utf-8').write(
    json.dumps(m, ensure_ascii=False, indent=2) + '\n')
PYRTD
    [ $? = 0 ] || fail "cannot declare runtime_deps in package.json"
  fi
  rt net 1800 'npm install --ignore-scripts --no-audit --no-fund --package-lock=true' > "$RUN/install-old.log" 2>&1
  echo "npm install rc=$?"
  # A repository that carries a lockfileVersion 1 package-lock.json gets an incomplete v3
  # lockfile out of a single npm install: the node_modules tree is complete, but transitive
  # entries are missing from the file npm writes back, and the npm ci that follows fails
  # with Missing: <pkg> from lock file for a reason that has nothing to do with the case.
  # A second npm install converges the lockfile against the tree already on disk, so no
  # version range is re-resolved and nothing installed moves. For a repository whose
  # lockfile is already complete the second pass changes nothing.
  rt net 1800 'npm install --ignore-scripts --no-audit --no-fund --package-lock=true' > "$RUN/install-old-2.log" 2>&1
  echo "npm install (lockfile convergence pass) rc=$?"
  [ -f package-lock.json ] || fail "npm produced no package-lock.json"
  cp package-lock.json "$PIN/lock-OLD.json"
  cp package.json "$PIN/package-OLD.json"
  rm -rf node_modules
fi

[ -f "$PIN/lock-OLD.json" ] || fail "missing $PIN/lock-OLD.json (run mode=pin first)"
cp "$PIN/lock-OLD.json" package-lock.json
[ "$LEGACY_NPM" = 1 ] && cp "$PIN/lock-OLD.json" npm-shrinkwrap.json
[ -f "$PIN/package-OLD.json" ] && cp "$PIN/package-OLD.json" package.json
log "STATE-OLD install from pinned lockfile"
rt net 1800 "$CI_INSTALL" > "$RUN/install-ci.log" 2>&1
echo "STATE-OLD install rc=$? via ${CI_INSTALL%% --*}"
[ -d node_modules/"$LIB" ] || fail "the STATE-OLD install did not install $LIB"
GOT_OLD=$(node -e "console.log(require('./node_modules/$LIB/package.json').version)")
[ "$GOT_OLD" = "$VOLD" ] || fail "STATE-OLD $LIB is $GOT_OLD, expected $VOLD"
log "STATE-OLD $LIB = $GOT_OLD"
assert_runtime_deps STATE-OLD
python3 "$BASE/pkginv.py" node_modules > "$RUN/tree-OLD.txt"
if [ "$WRITE_TREES" = 1 ]; then
  [ -f "$PIN/tree-OLD.txt" ] && cp "$PIN/tree-OLD.txt" "$RUN/tree-OLD-previous.txt"
  cp "$RUN/tree-OLD.txt" "$PIN/tree-OLD.txt"
  if [ "$MODE" = pin ]; then
    python3 "$BASE/depclosure.py" node_modules "$LIB" > "$WORK/closure-old.txt" \
      || fail "closure of $LIB in STATE-OLD"
    log "STATE-OLD closure of $LIB: $(wc -l < "$WORK/closure-old.txt") packages"
  fi
else
  diff "$PIN/tree-OLD.txt" "$RUN/tree-OLD.txt" > "$RUN/treediff-OLD.txt" \
    && log "STATE-OLD tree matches the pinned inventory exactly" \
    || fail "STATE-OLD tree drifted from the pinned inventory, see $RUN/treediff-OLD.txt"
fi
ws_snapshot
runtests STATE-OLD
ws_restore

# ---------------------------------------------------------------- STATE-NEW
if [ "$NEWMODE" = swap ]; then
  cp -a "node_modules/$LIB" "$WORK/lib_old"

  # The tarball is fetched with the host npm even for a case that declares a runtime:
  # `npm pack <pkg>@<ver>` only stores the registry artifact, and the sha512 it produces
  # is identical across npm 6 and npm 10, which is exactly what pinned/newlib.sha512
  # asserts on every run.
  log "fetching $LIB@$VNEW as a standalone tarball"
  mkdir -p "$WORK/newlib" && (
    cd "$WORK/newlib" && npm pack "$LIB@$VNEW" --silent >/dev/null 2>&1
  ) || fail "npm pack $LIB@$VNEW"
  TGZ=$(ls "$WORK"/newlib/*.tgz | head -1)
  SHA=$(sha512sum "$TGZ" | cut -d' ' -f1)
  if [ "$MODE" = pin ]; then
    echo "$SHA" > "$PIN/newlib.sha512"
  else
    [ "$SHA" = "$(cat "$PIN/newlib.sha512")" ] || fail "$LIB@$VNEW tarball hash differs from the pinned value"
  fi
  log "$LIB@$VNEW tarball sha512 ok"
  mkdir -p "$WORK/newlib/x" && tar -xzf "$TGZ" -C "$WORK/newlib/x"

  if [ "$MODE" = pin ]; then
    log "learning which packages need their own $LIB@$VOLD copy once $LIB@$VNEW sits on top"
    cp -a node_modules "$WORK/nm_old"
    rt net 1800 "npm install --ignore-scripts --no-audit --no-fund --package-lock=true --save-exact $LIB@$VNEW" \
      > "$RUN/install-new.log" 2>&1
    echo "npm install rc=$?"
    python3 "$BASE/pkginv.py" node_modules > "$RUN/tree-npm.txt"
    grep -P "^.+/node_modules/$LIB\t" "$RUN/tree-npm.txt" | cut -f1 | while read -r q; do
      grep -qP "^${q}\t" "$PIN/tree-OLD.txt" || echo "$q"
    done > "$PIN/nested-old-copies.txt"
    cp package.json "$PIN/package-NEW.json"
    echo "$LIB" > "$PIN/closure-allowed.txt"
    rm -rf node_modules
    cp -a "$WORK/nm_old" node_modules
    rm -rf "$WORK/nm_old"
    cp "$PIN/lock-OLD.json" package-lock.json
  fi

  log "nested locations that receive a copy of $LIB@$VOLD: $(tr '\n' ' ' < "$PIN/nested-old-copies.txt")"
  while read -r p; do
    [ -n "$p" ] || continue
    mkdir -p "node_modules/$(dirname "$p")"
    rm -rf "node_modules/$p"
    cp -a "$WORK/lib_old" "node_modules/$p"
  done < "$PIN/nested-old-copies.txt"
  rm -rf "node_modules/$LIB"
  cp -a "$WORK/newlib/x/package" "node_modules/$LIB"
  cp "$PIN/package-NEW.json" package.json
else
  if [ "$MODE" = pin ]; then
    log "resolving STATE-NEW by installing $LIB@$VNEW on top of the STATE-OLD tree"
    rt net 1800 "npm install --ignore-scripts --no-audit --no-fund --package-lock=true --save-exact $LIB@$VNEW" \
      > "$RUN/install-new.log" 2>&1
    echo "npm install rc=$?"
    [ -f package-lock.json ] || fail "npm produced no STATE-NEW package-lock.json"
    cp package-lock.json "$PIN/lock-NEW.json"
    cp package.json "$PIN/package-NEW.json"
    python3 "$BASE/depclosure.py" node_modules "$LIB" > "$WORK/closure-new.txt" \
      || fail "closure of $LIB in STATE-NEW"
    log "STATE-NEW closure of $LIB: $(wc -l < "$WORK/closure-new.txt") packages"
    cat "$WORK/closure-old.txt" "$WORK/closure-new.txt" | sort -u > "$PIN/closure-allowed.txt"
    log "allowed closure (union of the old and new closures): $(wc -l < "$PIN/closure-allowed.txt") packages"
    rm -rf node_modules
  fi
  [ -f "$PIN/lock-NEW.json" ] || fail "missing $PIN/lock-NEW.json"
  cp "$PIN/lock-NEW.json" package-lock.json
  [ "$LEGACY_NPM" = 1 ] && cp "$PIN/lock-NEW.json" npm-shrinkwrap.json
  cp "$PIN/package-NEW.json" package.json
  log "STATE-NEW install from pinned lockfile"
  rm -rf node_modules
  rt net 1800 "$CI_INSTALL" > "$RUN/install-ci-new.log" 2>&1
  echo "STATE-NEW install rc=$? via ${CI_INSTALL%% --*}"
fi

GOT_NEW=$(node -e "console.log(require('./node_modules/$LIB/package.json').version)")
[ "$GOT_NEW" = "$VNEW" ] || fail "STATE-NEW $LIB is $GOT_NEW, expected $VNEW"
assert_runtime_deps STATE-NEW
python3 "$BASE/pkginv.py" node_modules > "$RUN/tree-NEW.txt"
diff "$RUN/tree-OLD.txt" "$RUN/tree-NEW.txt" > "$RUN/treediff-OLD-NEW.txt"
[ -f "$PIN/closure-allowed.txt" ] || echo "$LIB" > "$PIN/closure-allowed.txt"
log "packages that moved between STATE-OLD and STATE-NEW:"
python3 "$BASE/treecheck.py" "$RUN/tree-OLD.txt" "$RUN/tree-NEW.txt" "$PIN/closure-allowed.txt" \
  > "$RUN/treecheck.txt" 2>&1
TCRC=$?
cat "$RUN/treecheck.txt"
[ "$TCRC" = "0" ] || fail "a package outside the allowed closure moved between STATE-OLD and STATE-NEW"
if [ "$WRITE_TREES" = 1 ]; then
  cp "$RUN/tree-NEW.txt" "$PIN/tree-NEW.txt"
  cp "$RUN/treediff-OLD-NEW.txt" "$PIN/treediff-OLD-NEW.txt"
  cp "$RUN/treecheck.txt" "$PIN/treecheck.txt"
else
  [ -f "$PIN/tree-NEW.txt" ] || fail "missing $PIN/tree-NEW.txt (run mode=repin to derive it from the pinned lockfile)"
  diff "$PIN/tree-NEW.txt" "$RUN/tree-NEW.txt" >/dev/null \
    && log "STATE-NEW tree matches the pinned inventory exactly" \
    || fail "STATE-NEW tree drifted from the pinned inventory"
fi
log "tracked files changed by the upgrade:"
git status --porcelain
ws_snapshot
runtests STATE-NEW
ws_restore

# -------------------------------------------------------------- STATE-FIXED
# Where the reference repair lands decides whether STATE-FIXED means anything. An empty
# patch makes STATE-FIXED a copy of STATE-NEW, and a patch that edits the test suite turns
# the assertion green without repairing the project, which is the fake fix this dataset is
# built to detect. Assert both before the patch is applied, not after.
log "patch-landing gate on reference_fix.patch"
python3 "$BASE/patchscope.py" "$CDIR" | tee "$RUN/patchscope.txt"
PSRC=${PIPESTATUS[0]}
[ "$PSRC" = 0 ] || echo "PATCHSCOPE_GATE_FAILED $CASE"

log "applying reference_fix.patch"
git apply --check "$CDIR/reference_fix.patch" || fail "reference_fix.patch does not apply"
git apply "$CDIR/reference_fix.patch" || fail "reference_fix.patch apply"
log "files touched by reference_fix.patch:"
git diff --name-only -- . ':(exclude)package.json' ':(exclude)package-lock.json' | tee "$RUN/patched-files.txt"
runtests STATE-FIXED

# ------------------------------------------------------------------ verdict
log "cleanup"
rm -rf node_modules "$WORK/lib_old" "$WORK/newlib"
log "disk after: $(df -h / | tail -1)"

# ----------------------------------------- answer-leakage gate on the archived logs
log "answer-leakage gate on the archived three-state logs"
# The archived logs are the case's authoritative artifact. Only `pin` may write them;
# a routine `verify` must never rewrite what it is checking, or the two sides drift in
# silence. In verify mode the fresh logs stay in $RUN and are compared against the
# archive on the parts that carry meaning (pass/fail counts and failing test names);
# timestamps, pids, durations and file ordering are expected to differ every run.
DRIFT=0
if [ "$MODE" = pin ] || [ "${ARCHIVE_OVERWRITE:-0}" = "1" ]; then
  for t in STATE-OLD STATE-NEW STATE-FIXED; do
    cp "$RUN/$t.log" "$PIN/$t.log"
  done
  log "archived three-state logs written (mode=$MODE)"
else
  archive_drift
  [ "$DRIFT" = 0 ] || echo "ARCHIVE_DRIFT $CASE: fresh run disagrees with the archived logs on test counts"
fi

# ------------------------------------- every state must be a state that really ran tests
# An old test framework under a modern node can exit 0 having run zero tests. Nothing in the exit code tells
# that apart from a pass, so each of the three states must also show the framework's own
# summary line with a non-zero count. The check is not about STATE-OLD alone. A STATE-NEW
# whose non-zero exit code comes from the tests never starting is a fabricated break, and
# a case built on one is worse than a case never built at all; a STATE-FIXED that runs
# nothing turns a patch that silences the suite into a repair.
#
# A framework that genuinely prints no summary in some state is a real possibility: mocha
# 2.x aborts the process on a Fatal AssertionError and never reaches its epilogue. Such a state may be registered in meta.json under
# no_summary_states as {state: reason}, in the same shape logscan.py uses for the
# leakage record. Unregistered is a failure, and a registration that turns out to be
# wrong, i.e. the state does report a summary after all, is a failure as well, so the
# registration cannot be left behind after the case has changed.
summary_gate
if [ "$MODE" = pin ] || [ "${LOGSCAN_RECORD:-0}" = "1" ]; then
  python3 "$BASE/logscan.py" "$CDIR" --record --workroot work-F1 >/dev/null 2>&1 || true
fi
python3 "$BASE/logscan.py" "$CDIR" --workroot work-F1
LSRC=$?
echo "VERDICT $CASE: STATE-OLD rc=$RC_STATE_OLD STATE-NEW rc=$RC_STATE_NEW STATE-FIXED rc=$RC_STATE_FIXED logscan=$LSRC patchscope=$PSRC drift=$DRIFT emptypass=$EMPTYPASS runtime=$RTLINE ambient=$AMBLINE"
[ "$LSRC" = "0" ] || echo "LOGSCAN_GATE_FAILED $CASE"
if [ "$RC_STATE_OLD" = 0 ] && [ "$RC_STATE_NEW" != 0 ] && [ "$RC_STATE_FIXED" = 0 ] \
   && [ "$LSRC" = 0 ] && [ "$PSRC" = 0 ] && [ "$EMPTYPASS" = 0 ] && [ "$DRIFT" = 0 ]; then
  echo "F1_OK $CASE"
else
  echo "F1_BAD $CASE"
fi
echo "F1_DONE $CASE"
