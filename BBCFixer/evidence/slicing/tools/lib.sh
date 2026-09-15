#!/usr/bin/env bash
# Shared function library, sourced by the scripts under tools/. Provides the cache directory,
# Maven Central URL construction, compare-URL parsing, etc.
# Not executed on its own.

# Directories: the parent of tools/ is the slicing module root
ALGO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_ROOT="${ALGO_CACHE:-$ALGO_ROOT/cache}"
EVIDENCE_ROOT="${ALGO_EVIDENCE:-$ALGO_ROOT/evidence}"

mkdir -p "$CACHE_ROOT" "$EVIDENCE_ROOT" 2>/dev/null

# Cache directory key of the upgraded library: <art>-<old>-<new>
dep_cache_dir() {  # group art old new
  printf '%s/%s-%s-%s' "$CACHE_ROOT" "$2" "$3" "$4"
}

# group dots -> path slashes
group_path() { printf '%s' "$1" | tr '.' '/'; }

# Maven Central artifact URL
mc_url() {  # group art ver classifier(optional, e.g. sources)
  local g a v c base
  g="$(group_path "$1")"; a="$2"; v="$3"; c="${4:-}"
  base="https://repo1.maven.org/maven2/$g/$a/$v/$a-$v"
  if [ -n "$c" ]; then printf '%s-%s.jar' "$base" "$c"; else printf '%s.jar' "$base"; fi
}

# Download to the given file (skipped if it already exists). Returns 0 on success / non-zero on failure.
# Progress / notices always go to stderr, keeping stdout clean (so other scripts can capture the body).
fetch_to() {  # url dest
  local url="$1" dest="$2"
  [ -s "$dest" ] && { echo ">>> cached: $(basename "$dest")" >&2; return 0; }
  echo ">>> downloading: $url" >&2
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --retry 2 -o "$dest" "$url" 2>&2 || { rm -f "$dest"; return 1; }
  else
    wget -q -O "$dest" "$url" || { rm -f "$dest"; return 1; }
  fi
}

# Parse the compare column of cases.tsv, of the form
#   https://github.com/qos-ch/slf4j/compare/v_1.7.32...v_2.0.0
# Prints three lines: repo_slug / old_tag / new_tag
parse_compare() {  # compare_url
  local url="$1" tail
  tail="${url#*/compare/}"          # v_1.7.32...v_2.0.0
  local slug
  slug="${url#https://github.com/}" # qos-ch/slf4j/compare/...
  slug="${slug%%/compare/*}"        # qos-ch/slf4j
  printf '%s\n%s\n%s\n' "$slug" "${tail%%...*}" "${tail##*...}"
}

