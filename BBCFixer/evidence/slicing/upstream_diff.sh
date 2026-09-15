#!/usr/bin/env bash
# Library fetching for BBCFixer: obtains the raw form of the source diff and the test diff
# between the two versions of the upgraded library.
#
# The two routes of library diff filtering (Section 3.2.2), applied to both the npm and the
# pip ecosystem:
#   the source diff comes from the release artifacts: npm fetches the two tarballs, pip the two
#   sdists; they are unpacked and compared with diff -ruN;
#   the test diff comes from a clone of the upstream repository: test code is usually not
#   shipped in the release artifact, so the repository is cloned, the two tags are checked out
#   and git diff is run on the test directories.
#
# The repository URL is resolved mechanically (the repository field of the npm registry, the
# project_urls of the PyPI metadata); no field of the case directory is read.
#
# Source gate against leaking the answer: this script touches only the scratch and out
# directories; **the case directory is never mounted or read**.
# The output is the raw diff and must never be handed to the agent directly: slicing is done
# by slice_diff.py, see the hard rule there.
#
# Usage: upstream_diff.sh <eco:npm|pip> <lib> <v_old> <v_new> <image> <scratch> <out>
set -u
ECO="$1"; LIB="$2"; VOLD="$3"; VNEW="$4"; IMAGE="$5"; SCR="$6"; OUT="$7"
BASE="$(cd "$(dirname "$0")" && pwd)"
CACHE="${OUR_UPSTREAM_CACHE:-$BASE/../../.cache/upstream}"
export GIT_TERMINAL_PROMPT=0    # when the repository is unreachable, do not stop to ask for a user name; fail directly
mkdir -p "$OUT" "$SCR" "$CACHE"
UIDGID="$(id -u):$(id -g)"

fail() { echo "OUR_UPSTREAM_FAIL: $*"; exit 1; }

# Normalise the diff file headers to git-style a/ and b/ prefixes and drop the timestamp column.
normalize_diff_paths() {  # <diff file> <old root> <new root>
  python3 "$BASE/normdiff.py" "$1" "$2" "$3"
}
for _p in "$SCR" "$OUT"; do
  case "$(realpath -m -- "$_p")/" in
    *"/cases/"*) fail "no fetch path may lie under cases/ (source gate): $_p" ;;
  esac
done

TESTDIFF_STATUS=unknown
REPO_URL=""

# ---------------------------------------------------------------- source diff
if [ "$ECO" = npm ]; then
  for spec in "OLD:$VOLD" "NEW:$VNEW"; do
    tag="${spec%%:*}"; ver="${spec##*:}"
    d="$SCR/src-$tag"
    [ -d "$d/package" ] && continue
    mkdir -p "$d"
    ( cd "$d" && npm pack "$LIB@$ver" --silent >/dev/null 2>&1 ) || fail "npm pack $LIB@$ver"
    tgz="$(ls "$d"/*.tgz 2>/dev/null | head -1)"
    [ -n "$tgz" ] || fail "npm pack produced no tarball for $LIB@$ver"
    tar -xzf "$tgz" -C "$d" || fail "unpacking $LIB@$ver"
  done
  # Minified files (*.min.js) in the release artifact carry no information for adaptation, yet
  # often have single lines of tens of thousands of characters; drop them first.
  # diff runs with relative paths inside the scratch directory, and the file headers are
  # rewritten to a/ and b/ afterwards: absolute paths would stamp the executor's scratch
  # directory name and the run timestamp into the evidence, which is a needless path leak and
  # also makes the evidence of two runs of the same case differ byte for byte.
  ( cd "$SCR" && diff -ruN -x '*.min.js' -x '*.map' -x '*.md' \
      "src-OLD/package" "src-NEW/package" ) > "$SCR/source-diff.abs" 2>/dev/null || true
  normalize_diff_paths "$SCR/source-diff.abs" "src-OLD/package" "src-NEW/package" > "$OUT/source-diff.raw"
  REPO_URL="$(npm view "$LIB@$VNEW" repository.url 2>/dev/null | tr -d '\r' | tail -1)"
  [ -n "$REPO_URL" ] || REPO_URL="$(npm view "$LIB" repository.url 2>/dev/null | tr -d '\r' | tail -1)"
elif [ "$ECO" = pip ]; then
  for spec in "OLD:$VOLD" "NEW:$VNEW"; do
    tag="${spec%%:*}"; ver="${spec##*:}"
    d="$SCR/src-$tag"
    [ -d "$d/x" ] && continue
    mkdir -p "$d/x"
    docker run --rm -u "$UIDGID" -v "$d":/d -w /d -e HOME=/tmp "$IMAGE" bash -c \
      "pip download --no-deps --no-binary :all: $LIB==$ver -d /d >/dev/null 2>&1 || pip download --no-deps $LIB==$ver -d /d >/dev/null 2>&1" \
      || fail "failed to fetch the release artifact of $LIB==$ver"
    art="$(ls "$d"/*.tar.gz "$d"/*.whl 2>/dev/null | head -1)"
    [ -n "$art" ] || fail "$LIB==$ver has no usable release artifact"
    case "$art" in
      *.tar.gz) tar -xzf "$art" -C "$d/x" --strip-components=1 ;;
      *.whl) ( cd "$d/x" && unzip -qo "$art" ) ;;
    esac
  done
  ( cd "$SCR" && diff -ruN -x '*.md' "src-OLD/x" "src-NEW/x" ) > "$SCR/source-diff.abs" 2>/dev/null || true
  normalize_diff_paths "$SCR/source-diff.abs" "src-OLD/x" "src-NEW/x" > "$OUT/source-diff.raw"
  REPO_URL="$(python3 - "$LIB" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen("https://pypi.org/pypi/%s/json" % sys.argv[1], timeout=60) as r:
        info = json.load(r).get("info") or {}
except Exception:
    print(""); raise SystemExit
urls = info.get("project_urls") or {}
for k in ("Source", "Source Code", "Repository", "Homepage", "Code", "GitHub"):
    v = urls.get(k) or ""
    if "github.com" in v:
        print(v); raise SystemExit
for v in urls.values():
    if isinstance(v, str) and "github.com" in v:
        print(v); raise SystemExit
print(info.get("home_page") or "")
PY
)"
else
  fail "unknown ecosystem $ECO"
fi
[ -f "$OUT/source-diff.raw" ] || : > "$OUT/source-diff.raw"

# ---------------------------------------------------------------- test diff
SLUG="$(printf '%s' "$REPO_URL" | sed -e 's#^git+##' -e 's#^git://#https://#' -e 's#^ssh://git@#https://#' \
        -e 's#^git@github.com:#https://github.com/#' -e 's#\.git$##' \
        | grep -oE 'github\.com/[^/]+/[^/#?]+' | head -1 | sed 's#^github\.com/##')"
: > "$OUT/test-diff.raw"
if [ -z "$SLUG" ]; then
  TESTDIFF_STATUS=no-repo
  echo "(The upstream repository of $LIB could not be resolved mechanically; no library test diff is available for this case.)" > "$OUT/test-diff.note"
else
  MIR="$CACHE/$(printf '%s' "$SLUG" | tr '/' '_').git"
  if [ ! -d "$MIR" ]; then
    git clone --mirror -q "https://github.com/$SLUG.git" "$MIR" >/dev/null 2>&1 \
      || { TESTDIFF_STATUS=no-clone; }
  fi
  if [ -d "$MIR" ]; then
    resolve_tag() {  # <version> -> tag name, or empty
      local v="$1" c
      for c in "v$v" "$v" "$LIB@$v" "$LIB-$v" "release-$v" "REL_$v"; do
        if git -C "$MIR" rev-parse -q --verify "refs/tags/$c^{commit}" >/dev/null 2>&1; then
          printf '%s' "$c"; return 0
        fi
      done
      return 1
    }
    TO="$(resolve_tag "$VOLD" || true)"; TN="$(resolve_tag "$VNEW" || true)"
    if [ -z "$TO" ] || [ -z "$TN" ]; then
      git -C "$MIR" remote update -p -q >/dev/null 2>&1 || true
      TO="$(resolve_tag "$VOLD" || true)"; TN="$(resolve_tag "$VNEW" || true)"
    fi
    if [ -n "$TO" ] && [ -n "$TN" ]; then
      # Take only the changes under test directories. The criterion is the path, not the file
      # content: test/ tests/ spec/ __tests__/ and names such as *.test.* / *.spec.* / *_test.py.
      # Small repositories often keep the whole test suite in a single file at the repository
      # root (chokidar 4's test.mjs, decamelize's test.js and index.test-d.ts), with no test
      # directory and no .test. infix; matching only directories and infixes would miss them
      # entirely, and the test diff would be misreported as "no file changed". Two single-file
      # forms at the repository root are therefore added: test.js / tests.js (including the ts,
      # jsx, cjs and mjs variants) and *.test-d.ts type tests. Anchored to the root only, not
      # relaxed to any directory: the any-directory form would also pick up bluebird's
      # tools/test.js (a test driver script, not a test) and commander's typings/index.test-d.ts,
      # needlessly changing the evidence of several existing cases.
      # Take the changes under test directories, but exclude third-party tests vendored into the
      # library.
      # Lesson from measurement: the lodash repository carries a copy of vendor/underscore/test/;
      # between 3.10.1 and 4.17.21 it was migrated wholesale to the QUnit style, with hundreds
      # of assertion lines deleted and re-added in pairs. Those lines are neither lodash's own
      # behavioural expectations nor do they point to any behaviour change, yet they would fill
      # all the C items of the repair contract: the generated C1 to C12 were all
      # "equal(...) changed to assert.equal(...)" assertion-style migrations, which a reader
      # would mistake for the library changing its expected values. fixtures and node_modules
      # are excluded for the same reason.
      git -C "$MIR" diff --name-only "$TO" "$TN" 2>/dev/null \
        | grep -avE '(^|/)(vendor|vendors|third_party|thirdparty|node_modules|fixtures?|__fixtures__|snapshots?|__snapshots__)/' \
        | grep -aE '(^|/)(test|tests|spec|specs|__tests__)/|(\.|_)(test|spec)\.[a-z]+$|_test\.py$|^tests?\.[cm]?[jt]sx?$|^[^/]+\.test-d\.ts$' \
        > "$SCR/testfiles.txt" || true
      if [ -s "$SCR/testfiles.txt" ]; then
        # shellcheck disable=SC2046
        git -C "$MIR" diff "$TO" "$TN" -- $(tr '\n' ' ' < "$SCR/testfiles.txt") \
          > "$OUT/test-diff.raw" 2>/dev/null || true
        TESTDIFF_STATUS=ok
        echo "Upstream repository $SLUG, tags $TO -> $TN, $(wc -l < "$SCR/testfiles.txt") test files changed." \
          > "$OUT/test-diff.note"
      else
        TESTDIFF_STATUS=no-testchange
        echo "Upstream repository $SLUG: no file under the test directories changed between $TO and $TN." > "$OUT/test-diff.note"
      fi
    else
      TESTDIFF_STATUS=no-tags
      echo "Upstream repository $SLUG has no tags for $VOLD / $VNEW (tried a v prefix, the bare version, package@version and similar forms); no library test diff is available for this case." \
        > "$OUT/test-diff.note"
    fi
  else
    [ "$TESTDIFF_STATUS" = unknown ] && TESTDIFF_STATUS=no-clone
    echo "Cloning the upstream repository $SLUG failed; no library test diff is available for this case." > "$OUT/test-diff.note"
  fi
fi

echo "OUR_REPO_SLUG=${SLUG:-none}"
echo "OUR_SRCDIFF_LINES=$(wc -l < "$OUT/source-diff.raw")"
echo "OUR_TESTDIFF_LINES=$(wc -l < "$OUT/test-diff.raw")"
echo "OUR_TESTDIFF_STATUS=$TESTDIFF_STATUS"
echo "OUR_UPSTREAM_OK $OUT"
