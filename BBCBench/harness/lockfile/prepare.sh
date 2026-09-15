#!/usr/bin/env bash
# prepare.sh <case_id> <workdir>
#
# Builds the broken state as a workspace for an agent:
#   <workdir>/src (npm) or <workdir>/repo (pip)   the downstream project with the pinned
#                                                 dependency tree, library at v_new
#   <workdir>/run_tests.sh                        runs the tests inside the runtime image
#   <workdir>/_FAILING.txt                        the failing test output
#   <workdir>/.judge/                             records for judge.sh; the agent must not touch it
# The workspace contains no reference fix, no metadata and no git history of the project.
# Needs docker, git, python3 and (npm cases) npm on the host. Network is used once, here.
set -u
. "$(dirname "$0")/lib.sh"
[ $# -eq 2 ] || die "usage: $0 <case_id> <workdir>"
load_case "$1"
mkdir -p "$2"; WK="$(cd "$2" && pwd)"
[ -z "$(ls -A "$WK")" ] || die "$WK is not empty"
SRC="$WK/$SRCNAME"
mkdir -p "$WK/rt-home" "$WK/.home" "$WK/.tmp" "$WK/.pipcache" "$WK/.judge" "$SRC"
ensure_image
say "$CASE  $ECO/$NEWMODE  $SLUG@${COMMIT:0:10}  $LIB $VOLD -> $VNEW  runtime $IMAGE"

# ---- runtime gate: the interpreter inside the image must match meta.json
if [ "$ECO" = npm ]; then
  OBS="$(rt net 180 'node -v' 2>/dev/null | tr -d '\r' | tail -1)"; GOT="$(printf '%s' "$OBS" | sed 's/^v//; s/\..*//')"; WANT="$NODEMAJOR"
else
  OBS="$(rt net 180 'python -V' 2>&1 | tr -d '\r' | tail -1)"; GOT="$(printf '%s' "$OBS" | sed 's/^Python //; s/^\([0-9]*\.[0-9]*\).*/\1/')"; WANT="$PYMINOR"
fi
[ -n "$OBS" ] || die "cannot read the interpreter version inside $IMAGE"
[ "$GOT" = "$WANT" ] || die "runtime mismatch: meta.json declares $WANT, $IMAGE has $OBS"

# ---- source: clone the pinned commit, then drop the git history (it may contain the real fix)
mkdir -p "$CACHE_ROOT"
MIRROR="$CACHE_ROOT/$(printf '%s' "$SLUG" | tr '/' '_').git"
if [ ! -d "$MIRROR" ]; then
  git clone --mirror -q "https://github.com/$SLUG.git" "$MIRROR" >/dev/null 2>&1 || die "cannot clone $SLUG"
fi
git -C "$MIRROR" cat-file -e "${COMMIT}^{commit}" 2>/dev/null || git -C "$MIRROR" remote update -p -q >/dev/null 2>&1
git -C "$MIRROR" cat-file -e "${COMMIT}^{commit}" 2>/dev/null || die "commit $COMMIT not found in $SLUG"
git clone -q --no-checkout "$MIRROR" "$SRC" > "$WK/.judge/clone.log" 2>&1 || die "checkout failed, see $WK/.judge/clone.log"
git -C "$SRC" checkout -q "$COMMIT" >> "$WK/.judge/clone.log" 2>&1 || die "checkout $COMMIT failed"
rm -rf "$SRC/.git"
say "source at $SLUG@${COMMIT:0:10}, git history removed"

# ---- dependency tree in the broken state (library at v_new, everything else pinned)
if [ "$ECO" = npm ]; then
  command -v npm >/dev/null 2>&1 || die "npm is needed on the host (to fetch the v_new tarball)"
  RTNPM="$(rt net 180 'npm -v' 2>/dev/null | tr -d '\r' | tail -1)"
  NPM_MAJOR="$(printf '%s' "${RTNPM:-99}" | sed 's/^[^0-9]*//; s/[.].*$//')"; case "$NPM_MAJOR" in ''|*[!0-9]*) NPM_MAJOR=99 ;; esac
  LEGACY=0; NPM_CI='npm ci --ignore-scripts --no-audit --no-fund'
  if [ "$NPM_MAJOR" -lt 5 ]; then LEGACY=1; NPM_CI='npm install --ignore-scripts --no-audit --no-fund'; fi
  [ -s "$PIN/install-flags.txt" ] && NPM_CI="$NPM_CI $(tr -s '[:space:]' ' ' < "$PIN/install-flags.txt" | sed 's/  */ /g; s/^ //; s/ $//')"
  cp "$PIN/lock-OLD.json" "$SRC/package-lock.json"
  [ "$LEGACY" = 1 ] && cp "$PIN/lock-OLD.json" "$SRC/npm-shrinkwrap.json"
  [ -f "$PIN/package-OLD.json" ] && cp "$PIN/package-OLD.json" "$SRC/package.json"
  say "installing the pinned intact tree (pinned/lock-OLD.json)"
  rt net "$INSTALL_TIMEOUT" "$NPM_CI" > "$WK/.judge/install-old.log" 2>&1
  [ -d "$SRC/node_modules/$LIB" ] || die "npm did not install $LIB, see $WK/.judge/install-old.log"
  [ "$(lib_ver_npm)" = "$VOLD" ] || die "intact tree has $LIB $(lib_ver_npm), expected $VOLD"
  if [ "$NEWMODE" = swap ]; then
    say "swapping $LIB to $VNEW in place (pinned/newlib.sha512, pinned/nested-old-copies.txt)"
    cp -a "$SRC/node_modules/$LIB" "$WK/lib_old"; mkdir -p "$WK/newlib"
    ( cd "$WK/newlib" && npm pack "$LIB@$VNEW" --silent >/dev/null 2>&1 ) || die "npm pack $LIB@$VNEW failed"
    TGZ="$(ls "$WK"/newlib/*.tgz 2>/dev/null | head -1)"; [ -n "$TGZ" ] || die "npm pack produced no tarball"
    [ "$(sha512sum "$TGZ" | cut -d' ' -f1)" = "$(cat "$PIN/newlib.sha512")" ] || die "checksum of $LIB@$VNEW differs from pinned/newlib.sha512"
    mkdir -p "$WK/newlib/x" && tar -xzf "$TGZ" -C "$WK/newlib/x"
    while read -r p; do
      [ -n "$p" ] || continue
      mkdir -p "$SRC/node_modules/$(dirname "$p")"; rm -rf "${SRC:?}/node_modules/$p"; cp -a "$WK/lib_old" "$SRC/node_modules/$p"
    done < "$PIN/nested-old-copies.txt"
    rm -rf "${SRC:?}/node_modules/$LIB"; cp -a "$WK/newlib/x/package" "$SRC/node_modules/$LIB"
    cp "$PIN/package-NEW.json" "$SRC/package.json"
  else
    say "installing the pinned broken tree (pinned/lock-NEW.json)"
    cp "$PIN/lock-NEW.json" "$SRC/package-lock.json"
    [ "$LEGACY" = 1 ] && cp "$PIN/lock-NEW.json" "$SRC/npm-shrinkwrap.json"
    cp "$PIN/package-NEW.json" "$SRC/package.json"
    rm -rf "$SRC/node_modules"
    rt net "$INSTALL_TIMEOUT" "$NPM_CI" > "$WK/.judge/install-new.log" 2>&1
  fi
  [ "$(lib_ver_npm)" = "$VNEW" ] || die "broken tree has $LIB $(lib_ver_npm), expected $VNEW"
  python3 "$H/pkginv.py" "$SRC/node_modules" > "$WK/.judge/pkginv.txt"
  if [ -f "$PIN/tree-NEW.txt" ]; then
    diff "$PIN/tree-NEW.txt" "$WK/.judge/pkginv.txt" > "$WK/.judge/tree-vs-pinned.diff" 2>&1 || die "installed tree differs from pinned/tree-NEW.txt, see $WK/.judge/tree-vs-pinned.diff"
  fi
  rm -rf "$WK/lib_old" "$WK/newlib"
else
  grep -v '^-e ' "$PIN/lock-NEW.txt" > "$WK/deps-NEW.txt"
  say "creating the venv from pinned/lock-NEW.txt"
  rt net "$INSTALL_TIMEOUT" 'python -m venv /work/.venv >/dev/null 2>&1 && /work/.venv/bin/pip -q install -U pip setuptools wheel >/dev/null 2>&1 && /work/.venv/bin/pip install --no-deps -r /work/deps-NEW.txt' > "$WK/.judge/install-new.log" 2>&1 || die "pip install failed, see $WK/.judge/install-new.log"
  rt net 900 'pip install -e . --no-deps' >> "$WK/.judge/install-new.log" 2>&1 || die "editable install of the project failed"
  GOTV="$(rt nonet 180 "python -c \"import importlib.metadata as m;print(m.version('$LIB'))\"" 2>/dev/null | tr -d '\r' | tail -1)"
  [ "$GOTV" = "$VNEW" ] || die "venv has $LIB ${GOTV:-?}, expected $VNEW"
  rt nonet 300 'pip freeze --all' 2>/dev/null | tr -d '\r' | sed 's/[[:space:]]*$//' | grep -v '^$' | sort > "$WK/.judge/pkginv.txt"
  python3 "$H/pipcmp.py" "$WK/deps-NEW.txt" "$WK/.judge/pkginv.txt" "$SLUG" > "$WK/.judge/tree-vs-pinned.txt" 2>&1 || die "venv differs from pinned/lock-NEW.txt, see $WK/.judge/tree-vs-pinned.txt"
fi

# ---- test wrapper and the failing output
run_tests_script > "$WK/run_tests.sh"; chmod +x "$WK/run_tests.sh"
run_suite "$WK/run_tests.sh" "$WK/_FAILING.txt"; RC=$?
CNT="$(counts "$WK/_FAILING.txt")"
[ "$RC" -ne 0 ] || die "the tests passed in the broken state; the case did not reproduce"
ARCH=""; for f in "$PIN/STATE-NEW.log" "$CDIR/STATE-NEW.log"; do [ -f "$f" ] && { ARCH="$(counts "$f")"; break; }; done
if [ -n "$ARCH" ] && [ "$ARCH" != "$CNT" ]; then die "test counts [$CNT] differ from the archived broken state [$ARCH]"; fi
say "broken state reproduced (exit $RC, counts [$CNT])"

# ---- records for judge.sh: dependency-tree fingerprint and a git baseline of the source
tree_fingerprint > "$WK/.judge/inventory.txt"
printf '%s\n' "$CASE" > "$WK/.judge/case"
git -C "$SRC" init -q
printf 'node_modules/\n.venv/\n__pycache__/\n*.pyc\n' > "$SRC/.git/info/exclude"
git -C "$SRC" add -A >/dev/null 2>&1
git -C "$SRC" -c user.email=bbcbench@local -c user.name=bbcbench commit -qm "broken state" >/dev/null 2>&1
git -C "$SRC" tag -f broken >/dev/null 2>&1

# ---- self-check: nothing from the case directory may have leaked into the workspace
python3 - "$CDIR" "$WK" "$CASE" > "$WK/.judge/leakscan.txt" 2>&1 <<'PY'
import hashlib, os, sys
cdir, wk, case = sys.argv[1], sys.argv[2], sys.argv[3]
SKIP = {'node_modules', '.venv', '.git', '.pipcache', '.npm', 'rt-home', '.home', '.tmp', '.judge'}
DELIVERED = {'package-OLD.json', 'package-NEW.json', 'lock-OLD.json', 'lock-NEW.json', 'lock-OLD.txt', 'lock-NEW.txt'}
FORBIDDEN = {'reference_fix.patch', 'meta.json', 'log-mentions.txt', 'STATE-OLD.log', 'STATE-NEW.log',
             'STATE-FIXED.log', 'verify.log', 'closure-allowed.txt', 'treecheck.txt', 'new-mode.txt'}
def digest(p):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 16), b''): h.update(b)
    return h.hexdigest()
case_hashes = {}
for dp, dn, fn in os.walk(cdir):
    for f in fn:
        if f in DELIVERED: continue
        p = os.path.join(dp, f)
        try:
            if os.path.getsize(p) == 0: continue
            case_hashes.setdefault(digest(p), []).append(os.path.relpath(p, cdir))
        except OSError: pass
problems = []
for dp, dn, fn in os.walk(wk):
    dn[:] = [d for d in dn if d not in SKIP]
    for f in fn:
        if f in ('_FAILING.txt', 'run_tests.sh'): continue
        p = os.path.join(dp, f); rel = os.path.relpath(p, wk)
        if f in FORBIDDEN: problems.append('forbidden file name in workspace: %s' % rel)
        if case in rel: problems.append('case name appears in a workspace path: %s' % rel)
        try:
            if os.path.getsize(p) == 0: continue
            d = digest(p)
        except OSError: continue
        if d in case_hashes: problems.append('%s is identical to %s of the case directory' % (rel, case_hashes[d][0]))
for p in problems: print('BAD', p)
print('LEAKSCAN_FAIL %d' % len(problems) if problems else 'LEAKSCAN_OK')
sys.exit(1 if problems else 0)
PY
[ $? -eq 0 ] || die "leak check failed, see $WK/.judge/leakscan.txt"
echo "ready: $SRC  (tests exit $RC, output in $WK/_FAILING.txt)"
