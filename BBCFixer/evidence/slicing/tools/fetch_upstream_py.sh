#!/usr/bin/env bash
# Fetch step of library diff filtering (Python ecosystem): prepare "the source of both versions of
# the library + the upstream repository".
# Structurally identical to the Java version fetch_upstream.sh and using THE SAME CACHE LAYOUT,
# cache/<pkg>-<old>-<new>/{old-src,new-src,repo,repo-meta.txt},
# so code_diff_source.sh and test_diff.sh are reused without any change.
#
# Three things are done:
#   1) download the sdist (source package) of both versions from PyPI and unpack them (for Python
#      the source is the distribution itself; no binary jar layer needed)
#   2) find the upstream GitHub repository from the PyPI metadata and clone it (for the test diff;
#      an sdist often contains no tests)
#   3) probe the git tag names of both versions (the common conventions vX.Y.Z / X.Y.Z /
#      <pkg>-X.Y.Z) and write repo-meta.txt
#
# Usage:
#   ./fetch_upstream_py.sh <PKG> <OLD> <NEW> [<REPO_URL>]
#   Without REPO_URL it is resolved from the PyPI metadata automatically.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"; . "$DIR/lib.sh"

PKG="$1"; OLD="$2"; NEW="$3"; REPO_URL="${4:-}"
[ -z "$PKG" ] || [ -z "$OLD" ] || [ -z "$NEW" ] && {
  echo "usage: $0 <PKG> <OLD> <NEW> [<REPO_URL>]"; exit 2; }

CD="$(dep_cache_dir pypi "$PKG" "$OLD" "$NEW")"
mkdir -p "$CD"
echo ">>> cache dir: $CD"

# 1) sdist download + unpack (strip the top-level <pkg>-<ver>/ directory so that old-src/new-src
#    are the package content directly, matching the layout of the unpacked sources.jar on the Java
#    side, so diff -ruN aligns the directories)
fetch_sdist() {  # ver dest_dir tarball
  local ver="$1" dest="$2" tb="$3" url
  url="$(python3 "$DIR/pypi_meta.py" sdist-url "$PKG" "$ver")" || { echo "!! $PKG==$ver has no sdist"; return 1; }
  fetch_to "$url" "$tb" || return 1
  rm -rf "$dest"; mkdir -p "$dest"
  case "$tb" in
    *.zip) unzip -qo "$tb" -d "$dest" >/dev/null 2>&1 ;;
    *)     tar -xzf "$tb" -C "$dest" 2>/dev/null || tar -xf "$tb" -C "$dest" ;;
  esac
  # Strip a single top-level directory
  local top
  top="$(find "$dest" -mindepth 1 -maxdepth 1 -type d | head -1)"
  if [ -n "$top" ] && [ "$(find "$dest" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]; then
    mv "$top"/* "$dest"/ 2>/dev/null; mv "$top"/.[!.]* "$dest"/ 2>/dev/null; rmdir "$top" 2>/dev/null
  fi
  # First harvest the "migration guide" files (CHANGELOG / upgrading / migration ...) into
  # <dest>-guide/: this is second-tier upstream natural-language evidence, kept as a category of
  # its own and never mixed into the first-tier source diff.
  local gdest="${dest%-src}-guide"
  rm -rf "$gdest"; mkdir -p "$gdest"
  find "$dest" -type f \( -iname 'CHANGELOG*' -o -iname 'CHANGES*' -o -iname 'HISTORY*' \
       -o -iname 'NEWS*' -o -iname '*upgrad*' -o -iname '*migrat*' \) \
       -exec sh -c 'cp -f "$1" "$0/$(basename "$1")"' "$gdest" {} \; 2>/dev/null
  # Strip the tests and docs shipped in the sdist (a Python sdist often packages tests/ and docs/;
  # a Java sources.jar contains neither): the "source diff" evidence must be non-test, non-doc
  # source; the evolution of the upstream tests goes through the repository clone (test_diff.sh).
  find "$dest" -type d \( -name tests -o -name test -o -name docs -o -name doc \) -prune -exec rm -rf {} + 2>/dev/null
  find "$dest" -type f \( -name 'test_*.py' -o -name '*_test.py' -o -name conftest.py \) -delete 2>/dev/null
  return 0
}
fetch_sdist "$OLD" "$CD/old-src" "$CD/old-sdist.tar.gz" || echo "!! old sdist failed (the source diff will rely on the repository only)"
fetch_sdist "$NEW" "$CD/new-src" "$CD/new-sdist.tar.gz" || echo "!! new sdist failed"

# 2) Upstream repository
[ -z "$REPO_URL" ] && REPO_URL="$(python3 "$DIR/pypi_meta.py" repo-url "$PKG" "$NEW" 2>/dev/null || true)"
if [ -z "$REPO_URL" ]; then
  echo ">>> no upstream repository URL found, skipping clone (test diff unavailable)"
  exit 0
fi
SLUG="${REPO_URL#https://github.com/}"
echo ">>> upstream repository: $SLUG"
if [ ! -d "$CD/repo/.git" ]; then
  rm -rf "$CD/repo"
  git clone --quiet --filter=blob:none "https://github.com/$SLUG.git" "$CD/repo" 2>/dev/null \
    || git clone --quiet "https://github.com/$SLUG.git" "$CD/repo" 2>/dev/null \
    || { echo "!! cloning $SLUG failed (test diff will be unavailable)"; exit 0; }
fi

# 3) Probe the old and new tags (try the common naming conventions one by one)
# Note: return must not be placed inside a ( ) subshell; that would only leave the subshell and the
# function would fall through to the final return 1
find_tag() {  # ver -> prints the matching tag name
  local v="$1" t
  ( cd "$CD/repo" && git fetch --quiet --tags 2>/dev/null )
  for t in "v$v" "$v" "$PKG-$v" "release-$v" "rel-$v"; do
    if ( cd "$CD/repo" && git rev-parse -q --verify "$t^{commit}" >/dev/null 2>&1 ); then
      printf '%s' "$t"; return 0
    fi
  done
  return 1
}
OLDTAG="$(find_tag "$OLD")" || { echo "!! old tag not found (${OLD})"; OLDTAG=""; }
NEWTAG="$(find_tag "$NEW")" || { echo "!! new tag not found (${NEW})"; NEWTAG=""; }
if [ -n "$OLDTAG" ] && [ -n "$NEWTAG" ]; then
  printf '%s\n%s\n%s\n' "$SLUG" "$OLDTAG" "$NEWTAG" > "$CD/repo-meta.txt"
  echo ">>> tag: $OLDTAG ... $NEWTAG"
else
  echo "!! tag probing incomplete, test diff unavailable (you may write $CD/repo-meta.txt by hand: three lines slug/oldtag/newtag)"
fi
echo ">>> fetch_upstream_py done: $CD"
