#!/usr/bin/env bash
# pack-intact.sh
# Packs cases/<id>/intact/ of every image case into .scratch/intact-sources.tar.gz (maintainer use).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.scratch"
cd "$ROOT/cases"
tar -czf "$ROOT/.scratch/intact-sources.tar.gz" --exclude '.pytest_cache' --exclude '__pycache__' */intact
ls -la "$ROOT/.scratch/intact-sources.tar.gz"
