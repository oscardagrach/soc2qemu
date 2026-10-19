#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Build a SoC's QEMU tree. Safe to call from several agents at once: builds are serialized with flock.
#   scripts/build.sh <soc> [--files a.c,b.c]
# With --files, the exit status and the printed errors only cover those files, so parallel
# agents are not blocked by each other's half-finished devices (ninja runs with -k 0).
source "$(dirname "$0")/lib.sh"
soc="${1:-}"; require_soc "$soc"; shift
files=""
[[ "${1:-}" == "--files" ]] && files="${2:-}"
build="$(soc_build "$soc")"
[[ -f "$build/build.ninja" ]] || die "not configured: run scripts/new-soc.sh $soc"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
set +e
flock "$build/.kit-build.lock" ninja -C "$build" -k 0 >"$log" 2>&1
rc=$?
set -e

if [[ -z "$files" ]]; then
  grep -E 'error:|warning:|FAILED:|undefined reference' "$log" | head -80 || true
  [[ $rc -eq 0 ]] && echo "BUILD OK" || echo "BUILD FAILED (rc=$rc)"
  exit $rc
fi

pattern="$(tr ',' '\n' <<<"$files" | xargs -n1 basename | paste -sd'|')"
mine="$(grep -E "($pattern).*(error|warning):" "$log" || true)"
if [[ -n "$mine" ]]; then
  echo "$mine" | head -80
  echo "ERRORS IN YOUR FILES"
  exit 1
fi
if [[ $rc -ne 0 ]]; then
  echo "your files are clean; build failed elsewhere:"
  grep -E 'error:|FAILED:|undefined reference' "$log" | head -20 || true
  echo "OK FOR YOUR FILES (tree not yet linking)"
  exit 0
fi
echo "BUILD OK"
