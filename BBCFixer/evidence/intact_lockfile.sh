#!/usr/bin/env bash
# intact_lockfile.sh <case_id> <destination>
#
# Builds the intact state of a lockfile-harness case, which differential execution needs as its
# reference run (paper Section 3.2.1). The harness delivers only the broken state, so the intact
# state is built here from the same pinned artifacts:
#   npm: the pinned commit is checked out and pinned/lock-OLD.json is installed with `npm ci`;
#        the installed tree is compared against pinned/tree-OLD.txt file by file.
#   pip: a venv is created from pinned/lock-OLD.txt with `pip install --no-deps`.
# The library version is asserted to be v_old afterwards. Nothing in the delivered workspace is
# touched, so the three settings see exactly the same workspace and differ only in the evidence.
#
# The case directory is never mounted into a container; only the pinned dependency files are read.
set -u
CASE="${1:?usage: $0 <case_id> <destination>}"; DEST="${2:?usage: $0 <case_id> <destination>}"
BASE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$BASE/.." && pwd)"
BBCBENCH_ROOT="${BBCBENCH_ROOT:-$ROOT/../BBCBench}"
[ -d "$BBCBENCH_ROOT/harness/lockfile" ] || { echo "error: BBCBench not found at $BBCBENCH_ROOT (set BBCBENCH_ROOT)" >&2; exit 2; }
. "$BBCBENCH_ROOT/harness/lockfile/lib.sh"
load_case "$CASE"
ensure_image

# The capture container expects a fixed layout: npm mounts the project directory itself (its
# node_modules must be at the top), pip mounts the parent with repo/ and .venv/ inside. The tree is
# therefore built next to the destination and moved into place at the end.
[ -e "$DEST" ] && die "$DEST already exists"
mkdir -p "$(dirname "$DEST")"; DEST="$(cd "$(dirname "$DEST")" && pwd)/$(basename "$DEST")"
BUILD="$DEST.build"; rm -rf "$BUILD"
# lib.sh helpers (rt, lib_ver_npm) read WK and SRC.
WK="$BUILD"; SRC="$WK/$SRCNAME"
mkdir -p "$WK/rt-home" "$WK/.home" "$WK/.tmp" "$WK/.pipcache" "$SRC"

# ---- source at the pinned commit, without the git history
mkdir -p "$CACHE_ROOT"
MIRROR="$CACHE_ROOT/$(printf '%s' "$SLUG" | tr '/' '_').git"
if [ ! -d "$MIRROR" ]; then
  git clone --mirror -q "https://github.com/$SLUG.git" "$MIRROR" >/dev/null 2>&1 || die "cannot clone $SLUG"
fi
git -C "$MIRROR" cat-file -e "${COMMIT}^{commit}" 2>/dev/null || git -C "$MIRROR" remote update -p -q >/dev/null 2>&1
git -C "$MIRROR" cat-file -e "${COMMIT}^{commit}" 2>/dev/null || die "commit $COMMIT not found in $SLUG"
git clone -q --no-checkout "$MIRROR" "$SRC" > "$WK/clone.log" 2>&1 || die "checkout failed, see $WK/clone.log"
git -C "$SRC" checkout -q "$COMMIT" >> "$WK/clone.log" 2>&1 || die "checkout $COMMIT failed"
rm -rf "$SRC/.git"

# ---- intact dependency tree
if [ "$ECO" = npm ]; then
  command -v npm >/dev/null 2>&1 || die "npm is needed on the host"
  RTNPM="$(rt net 180 'npm -v' 2>/dev/null | tr -d '\r' | tail -1)"
  NPM_MAJOR="$(printf '%s' "${RTNPM:-99}" | sed 's/^[^0-9]*//; s/[.].*$//')"; case "$NPM_MAJOR" in ''|*[!0-9]*) NPM_MAJOR=99 ;; esac
  NPM_CI='npm ci --ignore-scripts --no-audit --no-fund'
  # npm gained the `ci` subcommand in 5.7; older runtimes obey npm-shrinkwrap.json with `npm install`
  # instead. The tree is still fixed by the same lockfile; only the install command changes.
  if [ "$NPM_MAJOR" -lt 5 ]; then NPM_CI='npm install --ignore-scripts --no-audit --no-fund'; cp "$PIN/lock-OLD.json" "$SRC/npm-shrinkwrap.json"; fi
  [ -s "$PIN/install-flags.txt" ] && NPM_CI="$NPM_CI $(tr -s '[:space:]' ' ' < "$PIN/install-flags.txt" | sed 's/  */ /g; s/^ //; s/ $//')"
  cp "$PIN/lock-OLD.json" "$SRC/package-lock.json"
  [ -f "$PIN/package-OLD.json" ] && cp "$PIN/package-OLD.json" "$SRC/package.json"
  say "intact state: installing pinned/lock-OLD.json"
  rt net "$INSTALL_TIMEOUT" "$NPM_CI" > "$WK/install-old.log" 2>&1
  [ -d "$SRC/node_modules/$LIB" ] || die "npm did not install $LIB, see $WK/install-old.log"
  [ "$(lib_ver_npm)" = "$VOLD" ] || die "intact tree has $LIB $(lib_ver_npm), expected $VOLD"
  if [ -f "$PIN/tree-OLD.txt" ]; then
    python3 "$BBCBENCH_ROOT/harness/lockfile/pkginv.py" "$SRC/node_modules" > "$WK/pkginv.txt"
    diff "$PIN/tree-OLD.txt" "$WK/pkginv.txt" > "$WK/tree-vs-pinned.diff" 2>&1 \
      || die "the intact tree differs from pinned/tree-OLD.txt, see $WK/tree-vs-pinned.diff"
  fi
else
  grep -v '^-e ' "$PIN/lock-OLD.txt" > "$WK/deps-OLD.txt"
  VOLD_LOCK="$(grep -iE "^${LIB}==" "$PIN/lock-OLD.txt" | head -1 | cut -d= -f3)"
  [ -n "$VOLD_LOCK" ] || die "pinned/lock-OLD.txt does not pin $LIB"
  say "intact state: creating the venv from pinned/lock-OLD.txt"
  rt net "$INSTALL_TIMEOUT" \
    'python -m venv /work/.venv >/dev/null 2>&1 && /work/.venv/bin/pip -q install -U pip setuptools wheel >/dev/null 2>&1 && /work/.venv/bin/pip install --no-deps -r /work/deps-OLD.txt' \
    > "$WK/install-old.log" 2>&1 || die "pip install failed, see $WK/install-old.log"
  rt net 900 'pip install -e . --no-deps' >> "$WK/install-old.log" 2>&1 || die "editable install of the project failed"
  GOTV="$(rt nonet 180 "python -c \"import importlib.metadata as m;print(m.version('$LIB'))\"" 2>/dev/null | tr -d '\r' | tail -1)"
  [ "$GOTV" = "$VOLD_LOCK" ] || die "the intact venv has $LIB ${GOTV:-?}, expected $VOLD_LOCK (the comparison would not be single-variable)"
fi

if [ "$ECO" = npm ]; then mv "$SRC" "$DEST"; rm -rf "$BUILD"; else mv "$BUILD" "$DEST"; fi
echo "INTACT_OK $DEST"
