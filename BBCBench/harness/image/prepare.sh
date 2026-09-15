#!/usr/bin/env bash
# prepare.sh <case_id> <workdir>
#
# Builds the broken state as a workspace for an agent:
#   <workdir>/src            the downstream project; its tests fail with the upgraded library
#   <workdir>/run_tests.sh   runs the tests inside the case image; exit code 0 means they pass
#   <workdir>/_FAILING.txt   the failing test output
# The workspace contains no reference fix and no metadata. Judge the result with judge.sh.
set -u
. "$(dirname "$0")/config.sh"
[ $# -eq 2 ] || die "usage: $0 <case_id> <workdir>"
load_case "$1"
mkdir -p "$2"; WK="$(cd "$2" && pwd)"
[ -e "$WK/src" ] && die "$WK/src already exists"
need_intact; ensure_image

cp -R "$CDIR/intact" "$WK/src"
rm -rf "$WK/src/.pytest_cache" "$WK/src/.claude"
find "$WK/src" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

SIDE=""
[ -n "$SIDECAR_NAME" ] && SIDE="bash \"$BBCBENCH_ROOT/harness/image/sidecars.sh\" \"$CASE\" >/dev/null || exit 2"
cat > "$WK/run_tests.sh" <<WRAP
#!/usr/bin/env bash
# Runs the tests of $CASE inside its image. Exit code 0 means the tests pass.
$SIDE
exec docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e "PYTEST_ADDOPTS=-p no:cacheprovider" ${NET_ARGS:-} \\
  -v "$WK/src":/work -w /work --entrypoint bash "$IMG" -c $(printf '%q' "$TEST_CMD")
WRAP
chmod +x "$WK/run_tests.sh"

# A git baseline of the broken state, so the agent's edits can be listed with `git diff broken`.
( cd "$WK/src" && git init -q && git add -A >/dev/null 2>&1 \
  && git -c user.email=bbcbench@local -c user.name=bbcbench commit -qm "broken state" >/dev/null 2>&1 \
  && git tag -f broken >/dev/null 2>&1 )

bash "$WK/run_tests.sh" > "$WK/_FAILING.txt" 2>&1; rc=$?
[ "$rc" -ne 0 ] || die "the tests passed in the broken state; the case did not reproduce"
echo "ready: $WK/src  (tests exit $rc, output in $WK/_FAILING.txt)"
