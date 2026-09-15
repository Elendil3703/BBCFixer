#!/usr/bin/env bash
# verify.sh <case_id>
# Replays the three states of a lockfile case from its pinned artifacts and checks them:
#   intact (STATE-OLD) passes, broken (STATE-NEW) fails, fixed (STATE-FIXED) passes.
# The run also re-applies the integrity gates (patch landing, dependency closure, log leakage,
# real test counts). Prints F1_OK on success. Work files go to .scratch/lockfile-verify/.
set -u
H="$(cd "$(dirname "$0")" && pwd)"
[ $# -eq 1 ] || { echo "usage: $0 <case_id>" >&2; exit 2; }
export CASES_ROOT="${CASES_ROOT:-$H/../../cases}"
export WORK_ROOT="${WORK_ROOT:-$H/../../.scratch/lockfile-verify}"
exec bash "$H/rebuild_harness.sh" "$1" verify
