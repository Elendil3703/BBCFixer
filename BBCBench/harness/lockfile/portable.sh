#!/usr/bin/env bash
# Portability shims. Sourced by the lockfile harness scripts.
# macOS ships neither GNU coreutils `timeout` nor `sha256sum`/`sha512sum`.
if ! command -v timeout >/dev/null 2>&1; then
  if command -v gtimeout >/dev/null 2>&1; then timeout() { gtimeout "$@"; }
  else
    timeout() { # drop the "-k N SECS" prefix and run the command without a limit
      [ "$1" = -k ] && shift 2; shift; "$@"; }
  fi
fi
command -v sha256sum >/dev/null 2>&1 || sha256sum() { shasum -a 256 "$@"; }
command -v sha512sum >/dev/null 2>&1 || sha512sum() { shasum -a 512 "$@"; }
